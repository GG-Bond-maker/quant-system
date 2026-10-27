"""模拟盘（Paper Trading）执行引擎 —— Order Desk 核心。

真实口径（无任何假装的"实盘"）：
- 明确定位为**模拟盘**：本地撮合、真实行情、真实费用；
- 撮合：子单在执行日以**真实开盘价**成交，sqrt 冲击滑点、佣金最低 5 元、
  卖出印花税（ETF 豁免）、100 股整手；
- 算法分批：market=次日一次成交；vwap/twap/pov=分 split_days 日等金额/等量，
  子单在各自执行日撮合（截至下一有效交易日）；
- 基差（basis）：决策价（下单时点最新收盘价）vs 实际成交价，逐笔 bps 落库；
- 风控闸门：kill_switch 激活时拒绝新订单；一键撤单将 PENDING/PART_FILLED
  母单置 CANCELLED（已成交部分保留）；
- 合规禁买池：下单时强校验（st/退市/流动性差/手工黑名单）。

账户推导：cash = 初始资金 − Σ买入净额 + Σ卖出净额；持仓 = Σ按方向累加。
"""
from __future__ import annotations

import math
from datetime import date, datetime, timedelta

import numpy as np
import polars as pl
import sqlalchemy as sa
from loguru import logger
from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker

from ..data.parquet_store import read_symbol_dataset
from ..db.models import (
    AppState,
    ExclusionItem,
    PaperAccount,
    PaperFill,
    PaperOrder,
    TradeCalendar,
)
from ..domain.a_share_rules import effective_stamp_duty, is_etf_symbol
from ..domain.trading_rules import (
    COMMISSION_MIN,
    COMMISSION_RATE_DEFAULT,
)

LOT_SIZE = 100
IMPACT_COEF_BPS = 10.0
INIT_CASH = 1_000_000.0

KILL_SWITCH_KEY = "kill_switch"
HALT_BUYS_KEY = "halt_buys"

# 事前量化风控门限
MAX_POSITION_RATIO = 0.55            # 单标的持仓集中度上限 55%
MAX_SINGLE_ORDER_AMOUNT = 1_000_000.0  # 防肥手指，单笔最大报单 100 万元
MIN_CASH_BUFFER_RATIO = 0.02        # 最低保留 2% 现金缓冲备付印花税与佣金

# 同步引擎（API 层经 asyncio.to_thread 调用；WAL 允许与异步侧并发读）
_SYNC_ENGINE = None


def sync_session_factory() -> sessionmaker:
    global _SYNC_ENGINE
    from ..core.config import get_settings

    if _SYNC_ENGINE is None:
        s = get_settings()
        _SYNC_ENGINE = create_engine(
            f"sqlite:///{s.SQLITE_PATH}",
            connect_args={"check_same_thread": False, "timeout": 30})
    return sessionmaker(bind=_SYNC_ENGINE, expire_on_commit=False)


def _norm_symbol(code: str) -> str:
    code = code.strip().upper()
    if "." in code:
        return code
    return f"{code}.{'SH' if code.startswith(('6', '9', '5')) else 'SZ'}"


def _is_etf(sym: str) -> bool:
    """场内基金判定 —— 审计 B5-10：改用全项目唯一事实来源。

    此前本函数自带前缀表 ``("5", "15", "16")``，与 ``ma_cross`` / ``watchlist``
    三份互不一致（且都漏 18xxxx 深市封闭式基金）。
    """
    return is_etf_symbol(sym)


def get_kill_switch(session) -> bool:
    row = session.execute(
        sa.select(AppState).where(AppState.key == KILL_SWITCH_KEY)).scalar_one_or_none()
    return bool(row and row.value == "ON")


def set_kill_switch(session, on: bool, reason: str = "") -> int:
    lock_ledger(session)
    row = session.execute(
        sa.select(AppState).where(AppState.key == KILL_SWITCH_KEY)).scalar_one_or_none()
    if row:
        row.value = "ON" if on else "OFF"
    else:
        session.add(AppState(key=KILL_SWITCH_KEY, value="ON" if on else "OFF"))
    session.flush()
    return cancel_all(session, account_id=None) if on else 0


def get_halt_buys(session) -> bool:
    row = session.execute(
        sa.select(AppState).where(AppState.key == HALT_BUYS_KEY)).scalar_one_or_none()
    return bool(row and row.value == "ON")


def set_halt_buys(session, on: bool, reason: str = "") -> None:
    row = session.execute(
        sa.select(AppState).where(AppState.key == HALT_BUYS_KEY)).scalar_one_or_none()
    if row:
        row.value = "ON" if on else "OFF"
    else:
        session.add(AppState(key=HALT_BUYS_KEY, value="ON" if on else "OFF"))


def check_exclusion(session, symbol: str) -> str | None:
    """返回禁买原因（None = 允许）。"""
    row = session.execute(
        sa.select(ExclusionItem).where(
            ExclusionItem.symbol == symbol, ExclusionItem.active.is_(True))
        .where(ExclusionItem.category != "manual_whitelist")
        .order_by(ExclusionItem.id.desc())).first()
    if row:
        return f"{row[0].category}: {row[0].reason or '合规限制'}"
    return None


def screen_universe_candidates(session, top: int = 30) -> list[dict]:
    """合规池辅助筛选：从 universe 最新截面挑 ST / 退市风险 / 流动性差候选。"""
    from ..core.config import get_settings

    s = get_settings()
    files = sorted((s.DATA_ROOT / "universe_daily").rglob("*.parquet"))
    if not files:
        return []
    uni = pl.concat([pl.read_parquet(f) for f in files[-1:]],
                    how="diagonal_relaxed")
    last = uni
    if "date" in uni.columns:
        last = uni.filter(pl.col("date") == uni["date"].max())
    else:
        logger.warning("[paper] universe 快照缺 `date` 列（schema 漂移），按全部行当最新截面")
    out: list[dict] = []
    # [AQP 第 10 轮] 旧/部分快照可能缺 `is_st`/`close`/`volume`：原实现直接
    # `pl.col("is_st")` / `close*volume` ⇒ ColumnNotFoundError → 裸 50000
    # （`/desk/exclusion/screen` 整页不可用）。本端点返回**裸 list**、无披露通道，
    # 故按"该维度不可评估 ⇒ 该类别不产出候选"降级并留 warning（绝不静默、绝不能 500）。
    if "is_st" in last.columns:
        st = last.filter(pl.col("is_st") == True)
        for r in st.head(top).to_dicts():
            out.append({"symbol": r["symbol"], "name": r.get("name"),
                        "category": "st", "reason": "交易所 ST 标记"})
    else:
        logger.warning("[paper] universe 快照缺 `is_st` 列 ⇒ ST 候选段跳过")
    missing = [c for c in ("symbol", "close", "volume") if c not in last.columns]
    if missing:
        logger.warning(f"[paper] universe 快照缺列 {missing} ⇒ 流动性候选段跳过")
    else:
        illiq = last.with_columns((pl.col("close") * pl.col("volume")).alias("amt")) \
            .filter(pl.col("amt") > 0).sort("amt").head(top)
        for r in illiq.to_dicts():
            out.append({"symbol": r["symbol"], "name": r.get("name"),
                        "category": "illiquid",
                        "reason": f"日成交额仅 {r['amt']/1e4:.0f} 万元"})
    return out



DEFAULT_ACCOUNT = "paper_default"
ACTIVE_STATUSES = ("PENDING", "PART_FILLED")


def lock_ledger(session: Session) -> None:
    """Acquire SQLite's writer lock before reading balances, including across processes."""
    connection = session.connection()
    if connection.dialect.name == "sqlite":
        raw = connection.connection.driver_connection
        if raw is None:
            raise RuntimeError("SQLite ledger connection is unavailable")
        if not raw.in_transaction:
            connection.exec_driver_sql("BEGIN IMMEDIATE")


def _initial_cash(session: Session, account_id: str) -> float:
    account = session.get(PaperAccount, account_id)
    if account is None:
        if account_id != DEFAULT_ACCOUNT:
            raise ValueError(f"模拟账户不存在：{account_id}")
        return INIT_CASH
    return float(account.initial_cash)


def _fills(session: Session, account_id: str, asof: date | None = None) -> list[PaperFill]:
    stmt = sa.select(PaperFill).where(PaperFill.account_id == account_id)
    stmt = stmt.where(PaperFill.exec_date <= (asof or datetime.now().astimezone().date()))
    return list(session.scalars(stmt.order_by(PaperFill.exec_date, PaperFill.id)))


def _held_qty(session: Session, symbol: str, account_id: str = DEFAULT_ACCOUNT,
              asof: date | None = None, settled: bool = False) -> int:
    fills = _fills(session, account_id, asof)
    return sum(f.qty if f.side == "buy" else -f.qty for f in fills
               if f.symbol == symbol and not (
                   settled and f.side == "buy" and asof is not None and f.exec_date == asof))


def _bars(symbol: str, asof: date | None = None) -> pl.DataFrame:
    frame = read_symbol_dataset("daily_bar", symbol)
    if frame.is_empty():
        return frame
    frame = frame.with_columns(pl.col("date").cast(pl.Date)).sort("date")
    return frame.filter(pl.col("date") <= min(asof or datetime.now().astimezone().date(), datetime.now().astimezone().date()))


def _replay(fills: list[PaperFill], initial: float,
            per_order: dict[int, float] | None = None) -> tuple[float, dict, float, list[str]]:
    cash, realized = initial, 0.0
    positions: dict[str, dict] = {}
    warnings: list[str] = []
    for fill in fills:
        pos = positions.setdefault(fill.symbol, {"qty": 0, "cost": 0.0})
        if fill.side == "buy":
            cash -= fill.amount + fill.fee
            pos["qty"] += fill.qty
            pos["cost"] += fill.amount + fill.fee
        else:
            average = pos["cost"] / pos["qty"] if pos["qty"] else 0.0
            if fill.qty > pos["qty"]:
                warnings.append(f"{fill.symbol} {fill.exec_date} 卖出 {fill.qty} 股 > 当时持仓 {pos['qty']} 股；需人工处置")
            cash += fill.amount - fill.fee
            pnl = fill.amount - fill.fee - average * fill.qty
            realized += pnl
            if per_order is not None:
                per_order[fill.order_id] = per_order.get(fill.order_id, 0.0) + pnl
            pos["cost"] -= average * fill.qty
            pos["qty"] -= fill.qty
    return cash, positions, realized, warnings


def _reservations(session: Session, account_id: str, exclude_id: int | None = None,
                  asof: date | None = None) -> tuple[float, dict, dict]:
    orders = session.scalars(sa.select(PaperOrder).where(
        PaperOrder.account_id == account_id, PaperOrder.status.in_(ACTIVE_STATUSES)))
    frozen = 0.0
    buys: dict[str, float] = {}
    sells: dict[str, int] = {}
    for order in orders:
        if order.id == exclude_id:
            continue
        if asof is not None and order.created_at is not None and order.created_at.date() > asof:
            continue
        done = float(session.scalar(sa.select(sa.func.coalesce(sa.func.sum(PaperFill.amount), 0.0)).where(
            PaperFill.order_id == order.id)) or 0.0)
        left = max(0.0, order.order_amount - done)
        if order.side == "buy":
            reserve = order.reserved_cash or _reserve_budget(left, order.split_days)
            frozen += reserve
            buys[order.symbol] = buys.get(order.symbol, 0.0) + max(left, reserve)
        else:
            qty_done = int(session.scalar(sa.select(sa.func.coalesce(sa.func.sum(PaperFill.qty), 0)).where(
                PaperFill.order_id == order.id)) or 0)
            target = order.target_qty or int(order.order_amount / max(order.decision_price or 1.0, 1e-9) / LOT_SIZE) * LOT_SIZE
            sells[order.symbol] = sells.get(order.symbol, 0) + max(0, target - qty_done)
    return frozen, buys, sells


def _reserve_budget(amount: float, days: int) -> float:
    return amount + max(COMMISSION_MIN * max(days, 1), amount * COMMISSION_RATE_DEFAULT)


def preflight_check(session: Session, *, symbol: str, side: str, order_amount: float,
                    account_id: str = DEFAULT_ACCOUNT, bypass_kill_switch: bool = False,
                    split_days: int = 1, target_qty: int | None = None) -> dict:
    """Include unfilled orders in both cash and concentration limits."""
    symbol = _norm_symbol(symbol)
    reasons: list[str] = []
    if side not in ("buy", "sell") or not math.isfinite(order_amount) or order_amount <= 0:
        reasons.append("参数非法")
    if get_kill_switch(session) and not bypass_kill_switch:
        reasons.append("kill_switch_activated：风控全盘熔断中")
    if bypass_kill_switch and side != "sell":
        reasons.append("紧急熔断豁免仅允许卖出")
    if side == "buy" and get_halt_buys(session):
        reasons.append("halt_buys_activated：暂停新开仓")
    excluded = check_exclusion(session, symbol) if side == "buy" else None
    if excluded:
        reasons.append(f"合规禁买（{excluded}）")
    acc = account_summary(session, account_id=account_id, include_nav=False)
    equity = max(acc["equity"] or 0.0, 1.0)
    frozen, pending_buys, pending_sells = _reservations(session, account_id)
    available = acc["cash"] - frozen
    if side == "buy":
        if acc["equity"] is None:
            reasons.append("持仓估值行情缺失，禁止新增风险敞口")
        if order_amount > MAX_SINGLE_ORDER_AMOUNT and order_amount > equity * .40:
            reasons.append("防肥手指超限")
        if available - _reserve_budget(order_amount, split_days) < equity * MIN_CASH_BUFFER_RATIO:
            reasons.append(f"现金不足或触碰2%现金缓冲：可用现金 {available:,.0f} 元")
        mv = acc["positions"].get(symbol, {}).get("market_value") or 0.0
        ratio = (mv + pending_buys.get(symbol, 0.0) + order_amount) / equity
        if ratio > MAX_POSITION_RATIO:
            reasons.append(f"单标的集中度超限：含在途委托预计占比 {ratio:.1%}")
    elif side == "sell":
        held = _held_qty(session, symbol, account_id) - pending_sells.get(symbol, 0)
        bars = _bars(symbol)
        px = float(bars["close"][-1]) if not bars.is_empty() else 0.0
        want = target_qty if target_qty is not None else int(order_amount / px / LOT_SIZE) * LOT_SIZE if px > 0 else 0
        if held <= 0 or want <= 0 or want > held:
            reasons.append(f"持仓不足：{symbol} 可卖 {max(held, 0)} 股（可卖0股时禁止报单）")
    return {"passed": not reasons, "reasons": reasons, "warnings": [],
            "metrics": {"order_amount": order_amount, "current_cash": available,
                        "frozen_cash": frozen, "total_equity": equity,
                        "max_position_ratio": MAX_POSITION_RATIO}}


def place_order(session: Session, *, symbol: str, side: str, order_amount: float,
                algo: str, split_days: int, participation_cap: float,
                bypass_kill_switch: bool = False, account_id: str = DEFAULT_ACCOUNT,
                price_mode: str = "market", limit_price: float | None = None,
                target_qty: int | None = None, client_order_id: str | None = None,
                strategy_version: str | None = None, model_version: str | None = None,
                signal_id: str | None = None, backtest_id: str | None = None,
                run_id: str | None = None, grid_bot_id: int | None = None) -> dict:
    """Atomically reserve funds and persist an idempotent, traceable mother order."""
    lock_ledger(session)
    symbol = _norm_symbol(symbol)
    if price_mode not in ("market", "limit", "opposite"):
        return {"ok": False, "reason": "无效报价模式"}
    if price_mode != "market" and (limit_price is None or not math.isfinite(limit_price) or limit_price <= 0):
        return {"ok": False, "reason": "限价/对手价须提供有效价格上限或下限"}
    if not math.isfinite(participation_cap) or not 0 < participation_cap <= .3:
        return {"ok": False, "reason": "参与率必须在(0, 0.3]内"}
    algo = algo if algo in ("market", "vwap", "twap", "pov") else "market"
    days = 1 if algo == "market" else min(20, max(1, int(split_days)))
    if target_qty is not None and (not isinstance(target_qty, int) or isinstance(target_qty, bool) or target_qty <= 0 or target_qty % LOT_SIZE):
        return {"ok": False, "reason": "委托股数须为正整手"}
    account = session.get(PaperAccount, account_id)
    if account is not None and account.strategy_version:
        if strategy_version is not None and strategy_version != account.strategy_version:
            return {"ok": False, "reason": "委托策略版本与账户不一致"}
        strategy_version = account.strategy_version
    if client_order_id:
        old = session.scalar(sa.select(PaperOrder).where(
            PaperOrder.account_id == account_id, PaperOrder.client_order_id == client_order_id))
        if old is not None:
            payload = {"symbol": symbol, "side": side, "order_amount": order_amount,
                       "algo": algo, "split_days": days, "participation_cap": participation_cap,
                       "price_mode": price_mode, "limit_price": limit_price, "strategy_version": strategy_version,
                       "model_version": model_version, "signal_id": signal_id, "backtest_id": backtest_id,
                       "run_id": run_id, "grid_bot_id": grid_bot_id}
            if any(getattr(old, key) != value for key, value in payload.items()) or (
                target_qty is not None and old.target_qty != target_qty):
                return {"ok": False, "reason": "重复请求标识对应不同委托"}
            return {"ok": True, "order_id": old.id, "decision_price": old.decision_price, "replayed": True}
    preflight = preflight_check(session, symbol=symbol, side=side, order_amount=order_amount,
                                account_id=account_id, bypass_kill_switch=bypass_kill_switch,
                                split_days=days, target_qty=target_qty)
    if not preflight["passed"]:
        return {"ok": False, "reason": "；".join(preflight["reasons"])}
    bars = _bars(symbol, datetime.now().astimezone().date())
    if bars.is_empty():
        return {"ok": False, "reason": f"{symbol} 无本地行情"}
    decision = float(bars["close"][-1])
    if not math.isfinite(decision) or decision <= 0:
        return {"ok": False, "reason": "决策价无效"}
    if side == "buy" and target_qty is not None and order_amount + 1e-8 < target_qty * (limit_price or decision):
        return {"ok": False, "reason": "委托金额不足以覆盖目标股数，无法正确冻结资金"}
    if side == "sell" and target_qty is None:
        target_qty = int(order_amount / decision / LOT_SIZE) * LOT_SIZE
    order = PaperOrder(
        symbol=symbol, side=side, algo=algo, order_amount=float(order_amount), split_days=days,
        participation_cap=participation_cap, decision_price=decision, status="PENDING",
        created_at=datetime.now().astimezone().replace(tzinfo=None),
        account_id=account_id, client_order_id=client_order_id, price_mode=price_mode,
        limit_price=limit_price, target_qty=target_qty,
        reserved_cash=_reserve_budget(order_amount, days) if side == "buy" else 0.0,
        strategy_version=strategy_version, model_version=model_version, signal_id=signal_id,
        backtest_id=backtest_id, run_id=run_id, grid_bot_id=grid_bot_id)
    session.add(order)
    session.flush()
    return {"ok": True, "order_id": order.id, "decision_price": decision,
            "account_id": account_id, "reserved_cash": order.reserved_cash}


def _finish(order: PaperOrder, status: str, reason: str | None = None) -> None:
    order.status, order.reject_reason, order.reserved_cash = status, reason, 0.0


def run_fills(session: Session, max_orders: int = 50, account_id: str | None = None,
              asof: date | None = None, force_sells: bool = False) -> dict:
    """Match once per order/day, using only balances and quotes known on that day."""
    lock_ledger(session)
    asof = min(asof or datetime.now().astimezone().date(), datetime.now().astimezone().date())
    if get_kill_switch(session) and not force_sells:
        return {"orders_scanned": 0, "fills_created": 0, "deferred_children": 0, "rejected_orders": 0}
    stmt = sa.select(PaperOrder).where(PaperOrder.status.in_(ACTIVE_STATUSES))
    if force_sells:
        stmt = stmt.where(PaperOrder.side == "sell")
    if account_id:
        stmt = stmt.where(PaperOrder.account_id == account_id)
    orders = list(session.scalars(stmt.order_by(PaperOrder.id)))
    filled_cnt = deferred = rejected = 0
    # Match in chronological order across mother orders, not all days of one order first.
    events: list[tuple[date, int, PaperOrder, dict, int]] = []
    scheduled_orders = 0
    scheduled_ids: set[int] = set()
    days_by_order: dict[int, list[date]] = {}
    calendar_days = list(session.scalars(sa.select(TradeCalendar.trade_date).where(
        TradeCalendar.trade_date <= asof).order_by(TradeCalendar.trade_date)))
    for order in orders:
        bars = _bars(order.symbol, asof)
        days = bars["date"].to_list() if not bars.is_empty() else []
        if calendar_days and order.created_at is not None and calendar_days[0] <= order.created_at.date() and (
            not days or calendar_days[-1] >= days[-1]):
            days = calendar_days
        days_by_order[order.id] = days
        base = _created_day_index(order.created_at, days)
        if base is None:
            deferred += 1
            continue
        window = days[base + 1:base + 1 + order.split_days]
        rows = {row["date"]: row for row in bars.to_dicts()}
        if window:
            has_quotes = any(day in rows for day in window)
            if has_quotes and scheduled_orders >= max_orders:
                continue
            scheduled_orders += int(has_quotes)
            scheduled_ids.add(order.id)
        for k, day in enumerate(window):
            row = rows.get(day)
            if row is None:
                deferred += 1
                continue
            events.append((day, order.id, order, row, k))
    for day, _, order, row, k in sorted(events, key=lambda e: (e[0], e[1])):
        if order.status not in ACTIVE_STATUSES:
            continue
        done_fills = list(session.scalars(sa.select(PaperFill).where(PaperFill.order_id == order.id)))
        if any(f.exec_date == day for f in done_fills):
            continue
        watermark = session.scalar(sa.select(sa.func.max(PaperFill.exec_date)).where(PaperFill.account_id == order.account_id))
        if watermark is not None and day < watermark:
            _finish(order, "CANCELLED", "账户已有更晚成交，禁止向历史账本追插成交")
            continue
        done = sum(f.amount for f in done_fills)
        done_qty = sum(f.qty for f in done_fills)
        remaining = max(0.0, order.order_amount - done)
        px = float(row.get("open") or 0.0)
        volume = float(row.get("volume") or 0.0)
        day_amount = float(row.get("amount") or 0.0) or px * volume
        if not all(math.isfinite(value) and value > 0 for value in (px, volume, day_amount)) or row.get("is_halted"):
            deferred += 1
            continue
        limit = row.get("limit_up" if order.side == "buy" else "limit_down")
        if limit and (px >= float(limit) * .9999 if order.side == "buy" else px <= float(limit) * 1.0001):
            deferred += 1
            continue
        used = float(session.scalar(sa.select(sa.func.coalesce(sa.func.sum(PaperFill.amount), 0.0)).where(
            PaperFill.symbol == order.symbol, PaperFill.exec_date == day)) or 0)
        cap = max(0.0, day_amount * order.participation_cap - used)
        target = remaining if order.algo in ("market", "pov") else min(remaining, order.order_amount / order.split_days)
        if order.target_qty is not None:
            quantity_left = max(0, order.target_qty - done_qty)
            quantity_slice = quantity_left if order.algo in ("market", "pov") else min(quantity_left, math.ceil(order.target_qty / order.split_days / LOT_SIZE) * LOT_SIZE)
            target = quantity_slice * px
        amount_budget = min(target, cap)
        impact_bps = (8.0 if day_amount > 2e8 else 11.0 if day_amount > 5e7 else 16.0) * math.sqrt(min(amount_budget / day_amount, 1.0))
        exec_px = px * (1 + (1 if order.side == "buy" else -1) * impact_bps / 1e4)
        if order.target_qty is not None:
            amount_budget = min(quantity_slice * exec_px, cap)
        if order.limit_price is not None and (
            exec_px > order.limit_price if order.side == "buy" else exec_px < order.limit_price):
            deferred += 1
            continue
        fills = _fills(session, order.account_id, day)
        cash, positions, _, _ = _replay(fills, _initial_cash(session, order.account_id))
        qty = int(amount_budget / exec_px / LOT_SIZE + 1e-10) * LOT_SIZE
        if order.target_qty is not None:
            qty = min(qty, max(0, order.target_qty - done_qty))
        if order.side == "buy":
            frozen, pending, _ = _reservations(session, order.account_id, order.id, asof=day)
            mv = 0.0
            sym_mv = 0.0
            for sym, position in positions.items():
                marks = _bars(sym, day)
                mark = float(marks["open"][-1]) if not marks.is_empty() and marks["date"][-1] == day else 0.0
                if position["qty"] > 0 and (not math.isfinite(mark) or mark <= 0):
                    _finish(order, "REJECTED", "撮合时持仓缺少当日估值行情")
                    rejected += 1
                    break
                value = max(0, position["qty"]) * mark
                mv += value
                if sym == order.symbol:
                    sym_mv = value
            if order.status not in ACTIVE_STATUSES:
                continue
            equity = max(0.0, cash + mv)
            capacity = max(0.0, equity * MAX_POSITION_RATIO - sym_mv - pending.get(order.symbol, 0.0))
            budget = max(0.0, min(cash - frozen - equity * MIN_CASH_BUFFER_RATIO, capacity))
            qty = min(qty, int(max(0.0, budget - COMMISSION_MIN) / (exec_px * (1 + COMMISSION_RATE_DEFAULT)) / LOT_SIZE) * LOT_SIZE)
        else:
            available = _held_qty(session, order.symbol, order.account_id, day, settled=not _is_etf(order.symbol))
            qty = min(qty, max(0, available) // LOT_SIZE * LOT_SIZE)
            if available <= 0:
                _finish(order, "REJECTED", "持仓不足：撮合时无可卖持仓（含T+1锁定）")
                rejected += 1
                continue
        if qty <= 0:
            if order.side == "buy" and cash <= COMMISSION_MIN:
                _finish(order, "REJECTED", "撮合时可用资金不足")
                rejected += 1
            else:
                deferred += 1
            continue
        amount = qty * exec_px
        fee = max(COMMISSION_MIN, amount * COMMISSION_RATE_DEFAULT)
        if order.side == "sell":
            fee += effective_stamp_duty(order.symbol, day) * amount
        if order.side == "buy" and amount + fee > cash + 1e-8:
            _finish(order, "REJECTED", "撮合时现金不足")
            rejected += 1
            continue
        session.add(PaperFill(account_id=order.account_id, order_id=order.id, symbol=order.symbol,
            side=order.side, exec_date=day, qty=qty, price=exec_px, amount=amount, fee=fee,
            impact_bps=impact_bps, participation=amount / day_amount,
            basis_bps=(exec_px / order.decision_price - 1) * 1e4 if order.decision_price else 0.0))
        session.flush()
        order.filled_amount = done + amount
        left = max(0.0, order.order_amount - order.filled_amount)
        if (order.target_qty is not None and done_qty + qty >= order.target_qty) or (order.target_qty is None and left < exec_px * LOT_SIZE):
            _finish(order, "FILLED", "不足一手残额已释放" if left > 1e-6 else None)
        else:
            order.status = "PART_FILLED"
            if order.target_qty is not None:
                left = (order.target_qty - done_qty - qty) * exec_px
            order.reserved_cash = _reserve_budget(left, max(1, order.split_days - k - 1)) if order.side == "buy" else 0.0
        filled_cnt += 1
    # Expired windows are terminal, so old partial orders never starve the queue.
    for order in orders:
        if order.status not in ACTIVE_STATUSES or order.id not in scheduled_ids:
            continue
        days = days_by_order[order.id]
        base = _created_day_index(order.created_at, days)
        if base is not None and len(days) > base + order.split_days:
            _finish(order, "CANCELLED", "执行窗口结束，未成交余量已取消")
    session.flush()
    from .conditional import refresh_trade_states
    refresh_trade_states(session)
    return {"orders_scanned": len(orders), "fills_created": filled_cnt,
            "deferred_children": deferred, "rejected_orders": rejected}


def _created_day_index(created_at: datetime | date | None, trade_days: list[date]) -> int | None:
    if not trade_days:
        return None
    if created_at is None:
        return 0
    day = created_at.date() if isinstance(created_at, datetime) else created_at
    prior = [i for i, value in enumerate(trade_days) if value <= day]
    return prior[-1] if prior else -1


def _build_nav_series(fills: list[PaperFill], initial_cash: float = INIT_CASH,
                      asof: date | None = None, calendar_days: list[date] | None = None) -> list[dict]:
    if not fills:
        return []
    dates = {f.exec_date for f in fills}
    dates.update(calendar_days or [])
    marks = {}
    for symbol in {f.symbol for f in fills}:
        bars = _bars(symbol, asof)
        marks[symbol] = dict(zip(bars["date"], bars["close"])) if not bars.is_empty() else {}
        dates.update(marks[symbol])
    output = []
    for day in sorted(d for d in dates if d >= min(f.exec_date for f in fills)):
        cash, positions, _, _ = _replay([f for f in fills if f.exec_date <= day], initial_cash)
        valid = {sym: values.get(day) for sym, values in marks.items()}
        missing = [sym for sym, p in positions.items() if p["qty"] > 0 and (
            (value := valid.get(sym)) is None or not math.isfinite(value) or value <= 0)]
        mv = sum(max(0, p["qty"]) * (valid.get(sym) or 0.0) for sym, p in positions.items() if sym not in missing)
        output.append({"date": str(day), "cash": round(cash, 2), "mv": round(mv, 2) if not missing else None,
                       "equity": round(cash + mv, 2) if not missing else None,
                       "missing_marks": missing})
    return output


def account_summary(session: Session, account_id: str = DEFAULT_ACCOUNT,
                    include_nav: bool = True, asof: date | None = None) -> dict:
    explicit_asof = asof is not None
    asof = min(asof or datetime.now().astimezone().date(), datetime.now().astimezone().date())
    initial = _initial_cash(session, account_id)
    fills = _fills(session, account_id, asof)
    cash, positions, realized, warnings = _replay(fills, initial)
    marks = {}
    bar_frames = {symbol: _bars(symbol, asof) for symbol, pos in positions.items() if pos["qty"] > 0}
    now = datetime.now().astimezone().replace(tzinfo=None)
    completed = asof if explicit_asof or (now.hour, now.minute) >= (15, 30) else asof - timedelta(days=1)
    calendar_days = list(session.scalars(sa.select(TradeCalendar.trade_date).where(TradeCalendar.trade_date <= completed)))
    coverage = session.scalar(sa.select(sa.func.max(TradeCalendar.trade_date)))
    if calendar_days and coverage is not None and coverage >= completed:
        expected_day = max(calendar_days)
    else:
        expected_day = completed
        while expected_day.weekday() >= 5:
            expected_day -= timedelta(days=1)
    mark_day = max([expected_day, *(frame["date"][-1] for frame in bar_frames.values() if not frame.is_empty())])
    missing = []
    for symbol, pos in positions.items():
        if pos["qty"] <= 0:
            continue
        bars = bar_frames[symbol]
        px = float(bars["close"][-1]) if not bars.is_empty() else None
        valid_mark = px is not None and math.isfinite(px) and px > 0 and bars["date"][-1] == mark_day
        if not valid_mark:
            warnings.append(f"{symbol} 缺少持仓估值行情")
            missing.append(symbol)
        marks[symbol] = {"qty": pos["qty"], "last_price": px if valid_mark else None,
                         "market_value": pos["qty"] * px if valid_mark else None,
                         "mark_date": str(bars["date"][-1]) if not bars.is_empty() else None,
                         "cost_price": pos["cost"] / pos["qty"],
                         "pnl": pos["qty"] * px - pos["cost"] if valid_mark else None}
    mv = sum(p["market_value"] or 0.0 for p in marks.values())
    frozen, _, reserved_sells = _reservations(session, account_id, asof=asof)
    for symbol, pos in marks.items():
        pos["available_qty"] = max(0, _held_qty(session, symbol, account_id, asof, settled=not _is_etf(symbol)) - reserved_sells.get(symbol, 0))
    nav = _build_nav_series(fills, initial, asof, calendar_days) if include_nav else []
    valid_equities = [n["equity"] for n in nav if n["equity"] is not None]
    max_dd = sharpe = annualized = None
    if len(valid_equities) >= 30 and len(valid_equities) == len(nav):
        peak, max_dd = initial, 0.0
        for equity in valid_equities:
            peak = max(peak, equity)
            max_dd = min(max_dd, equity / peak - 1)
        rets = np.diff(valid_equities) / np.asarray(valid_equities[:-1])
        std = float(np.std(rets, ddof=1))
        sharpe = float(np.mean(rets)) / std * math.sqrt(252) if std > 1e-12 else None
        annualized = (valid_equities[-1] / initial) ** (252 / len(valid_equities)) - 1 if valid_equities[-1] > 0 else None
    return {"account_id": account_id, "initial_cash": initial, "cash": round(cash, 2),
            "available_cash": round(cash - frozen, 2), "frozen_cash": round(frozen, 2),
            "market_value": round(mv, 2) if not missing else None,
            "equity": round(cash + mv, 2) if not missing else None, "positions": marks,
            "realized_pnl": round(realized, 2), "total_fees": round(sum(f.fee for f in fills), 2),
            "total_impact_cost": round(sum(f.amount * f.impact_bps / 1e4 / (1 + (1 if f.side == "buy" else -1) * f.impact_bps / 1e4) for f in fills), 2),
            "n_fills": len(fills), "max_drawdown": max_dd, "sharpe": sharpe,
            "annualized_return": annualized, "n_active_days": len(nav), "nav_series": nav,
            "integrity_warnings": warnings}


def cancel_all(session: Session, account_id: str | None = DEFAULT_ACCOUNT) -> int:
    lock_ledger(session)
    orders = list(session.scalars(sa.select(PaperOrder).where(
        (PaperOrder.account_id == account_id if account_id is not None else sa.true()), PaperOrder.status.in_(ACTIVE_STATUSES))))
    for order in orders:
        _finish(order, "CANCELLED", "用户撤单")
    session.flush()
    return len(orders)


def cancel_order(session: Session, order_id: int, account_id: str = DEFAULT_ACCOUNT) -> bool:
    lock_ledger(session)
    order = session.get(PaperOrder, order_id)
    if order is None or order.account_id != account_id or order.status not in ACTIVE_STATUSES:
        return False
    _finish(order, "CANCELLED", "用户撤单")
    session.flush()
    return True


def emergency_liquidate_all(session: Session, account_id: str = DEFAULT_ACCOUNT) -> dict:
    cancelled = cancel_all(session, account_id)
    account = account_summary(session, account_id)
    submitted, failures = [], []
    for symbol, position in account["positions"].items():
        if position["market_value"] is None:
            failures.append({"symbol": symbol, "reason": "缺少估值行情，无法生成清盘委托"})
            continue
        result = place_order(session, symbol=symbol, side="sell", order_amount=position["market_value"],
                             algo="market", split_days=1, participation_cap=.3,
                             target_qty=position["qty"], bypass_kill_switch=True, account_id=account_id)
        if result["ok"]:
            submitted.append(symbol)
        else:
            failures.append({"symbol": symbol, "reason": result["reason"]})
    fills = run_fills(session, account_id=account_id, force_sells=True)
    remaining = account_summary(session, account_id, include_nav=False)["positions"]
    return {"ok": not failures and not remaining, "cancelled_orders": cancelled,
            "liquidated_positions": len(set(submitted) - set(remaining)),
            "submitted_positions": len(submitted), "symbols": submitted,
            "remaining_symbols": list(remaining), "failures": failures, "fills": fills}
