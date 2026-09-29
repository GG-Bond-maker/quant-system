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
from datetime import date

import numpy as np
import polars as pl
import sqlalchemy as sa
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from loguru import logger

from ..data.parquet_store import read_symbol_dataset
from ..db.models import AppState, ExclusionItem, PaperFill, PaperOrder
from ..domain.a_share_rules import is_etf_symbol
from ..domain.trading_rules import (
    COMMISSION_MIN,
    COMMISSION_RATE_DEFAULT,
    STAMP_DUTY_STOCK_RATE,
)

LOT_SIZE = 100
IMPACT_COEF_BPS = 10.0
INIT_CASH = 1_000_000.0

KILL_SWITCH_KEY = "kill_switch"

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


def set_kill_switch(session, on: bool, reason: str = "") -> None:
    row = session.execute(
        sa.select(AppState).where(AppState.key == KILL_SWITCH_KEY)).scalar_one_or_none()
    if row:
        row.value = "ON" if on else "OFF"
    else:
        session.add(AppState(key=KILL_SWITCH_KEY, value="ON" if on else "OFF"))


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
        st = last.filter(pl.col("is_st") == True)  # noqa: E712
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


def _held_qty(session, symbol: str) -> int:
    """由**全部真实成交**推算的持仓股数（与 ``account_summary`` 同一事实来源）。

    P1-3 修复的核心查询：下单期与撮合期都需要知道"现在到底持有多少股"。
    注意 SQLAlchemy 在 ``execute`` 前会 autoflush，因此在同一 transaction 里
    刚 ``session.add`` 的 ``PaperFill`` **立刻可见**——这正是 `run_fills`
    顺序处理多张卖单时不会互相越卖的原因。
    """
    q = sa.select(sa.func.coalesce(sa.func.sum(
        sa.case((PaperFill.side == "buy", PaperFill.qty),
                else_=-PaperFill.qty)), 0)).where(PaperFill.symbol == symbol)
    return int(session.execute(q).scalar() or 0)


def place_order(session, *, symbol: str, side: str, order_amount: float,
                algo: str, split_days: int, participation_cap: float) -> dict:
    """下单（含风控闸门、合规校验与**持仓校验**），返回母单 dict。"""
    symbol = _norm_symbol(symbol)
    if get_kill_switch(session):
        return {"ok": False, "reason": "kill_switch_activated：风控熔断中，禁止新订单"}
    excluded = check_exclusion(session, symbol)
    if excluded:
        return {"ok": False, "reason": f"合规禁买（{excluded}）"}
    if side not in ("buy", "sell") or order_amount <= 0:
        return {"ok": False, "reason": "参数非法"}
    algo = algo if algo in ("market", "vwap", "twap", "pov") else "market"

    # 决策价 = 下单时点最新真实收盘价（回测理想价基准）
    df = read_symbol_dataset("daily_bar", symbol)
    if df.is_empty():
        return {"ok": False, "reason": f"{symbol} 无本地行情"}
    dcol = df.schema["date"]
    if dcol != pl.Date:
        df = df.with_columns(pl.col("date").cast(pl.Date))
    last = df.sort("date").tail(1).to_dicts()[0]
    decision_price = float(last["close"])

    # P1-3：卖出必须校验持仓。本平台是**多头模拟盘**（A 股散户不可裸卖），
    # 零持仓/超持仓卖出以往会照常撮合 ⇒ `account_summary` 的
    # `cash += f.amount` 凭空造出现金（实测零持仓卖 10 万元 → 权益 1,098,871）。
    # 此处按决策价把金额折算成股数并**整手**比对；拒绝时**不落单**，与
    # `kill_switch` / 合规禁买两条既有拒绝路径同形。
    if side == "sell" and decision_price > 0:
        held = _held_qty(session, symbol)
        want_qty = int(order_amount / decision_price / LOT_SIZE) * LOT_SIZE
        if held <= 0:
            return {"ok": False,
                    "reason": f"持仓不足：{symbol} 当前无可卖持仓（可卖 0 股）"}
        if want_qty > held:
            return {"ok": False,
                    "reason": (f"持仓不足：{symbol} 可卖 {held} 股"
                               f"（约 {held * decision_price:,.0f} 元），"
                               f"本单约需 {want_qty} 股"
                               f"（{order_amount:,.0f} 元）")}
    order = PaperOrder(symbol=symbol, side=side, algo=algo,
                       order_amount=float(order_amount),
                       split_days=1 if algo == "market" else max(1, int(split_days)),
                       participation_cap=float(participation_cap),
                       decision_price=decision_price, status="PENDING")
    session.add(order)
    session.flush()
    return {"ok": True, "order_id": order.id, "decision_price": decision_price}


def run_fills(session, max_orders: int = 50) -> dict:
    """撮合所有到期子单（用各子单执行日的真实开盘价 + sqrt 冲击）。

    子单执行日排布：母单创建日为 D0（首笔成交不得早于 D0+1 —— 无未来函数），
    vwap/twap/pov 的第 k 笔目标执行日 = D0 + k 个交易日。
    """
    orders = session.execute(
        sa.select(PaperOrder).where(
            PaperOrder.status.in_(["PENDING", "PART_FILLED"]))
        .order_by(PaperOrder.id).limit(max_orders)).scalars().all()
    filled_cnt, deferred, rejected = 0, 0, 0
    for order in orders:
        # P1-3 兜底：下单期校验通过后持仓仍可能被别的单卖掉（或存在历史脏数据），
        # `_held_qty` 依 autoflush 可见本 transaction 内刚落的 `PaperFill`，
        # 故顺序处理多张卖单时不会互相越卖。持仓为 0 ⇒ 置终态 REJECTED，
        # **不产生任何 PaperFill**（否则 account_summary 会凭空造出现金）。
        if order.side == "sell" and _held_qty(session, order.symbol) <= 0:
            order.status = "REJECTED"
            order.reject_reason = (f"持仓不足：{order.symbol} 撮合时无可卖持仓"
                                   f"（0 股），卖出未成交")
            rejected += 1
            continue
        bars = read_symbol_dataset("daily_bar", order.symbol)
        if bars.is_empty():
            deferred += 1
            continue
        dcol = bars.schema["date"]
        if dcol != pl.Date:
            bars = bars.with_columns(pl.col("date").cast(pl.Date))
        bars = bars.sort("date")
        trade_days = [d for d in bars["date"].to_list()]
        base_idx = _created_day_index(order.created_at, trade_days)
        if base_idx is None:
            deferred += 1
            continue

        per = order.order_amount / max(1, order.split_days)
        # 已有成交按金额扣减剩余
        done = session.execute(
            sa.select(sa.func.coalesce(sa.func.sum(PaperFill.amount), 0.0))
            .where(PaperFill.order_id == order.id)).scalar() or 0.0
        remaining_total = order.order_amount - float(done)
        if remaining_total <= 1e-6:
            order.status = "FILLED"
            continue
        filled_today = False
        for k in range(order.split_days):
            idx = base_idx + 1 + k           # D0+1 起撮合（严禁当日信号当日成交）
            if idx >= len(trade_days):
                deferred += 1
                continue
            exec_date = trade_days[idx]
            already = session.execute(
                sa.select(sa.func.count()).select_from(PaperFill)
                .where(PaperFill.order_id == order.id)
                .where(PaperFill.exec_date == exec_date)).scalar() or 0
            if already:
                continue
            day = bars.filter(pl.col("date") == exec_date).to_dicts()[0]
            open_px = float(day["open"])
            day_amount = float(day.get("amount") or 0.0) or \
                float(day["close"]) * float(day.get("volume") or 0.0)
            if open_px <= 0:
                deferred += 1
                continue
            # 参与率闸门
            amt = min(per, remaining_total,
                      day_amount * order.participation_cap)
            # P1-3：卖出金额再按**当时实际持仓市值**封顶。`_held_qty` 每次都重算
            # （含本 transaction 内已落的成交），故同一母单的多笔子单、以及同
            # 标的的多张卖单都不会越卖。
            if order.side == "sell":
                held_now = _held_qty(session, order.symbol)
                if held_now <= 0:
                    order.status = "REJECTED"
                    order.reject_reason = (
                        f"持仓不足：{order.symbol} 剩余 "
                        f"{remaining_total:,.0f} 元无可卖持仓，卖出提前终止")
                    rejected += 1
                    break
                amt = min(amt, held_now * open_px)
            if amt <= 1.0:
                deferred += 1
                continue
            part = amt / day_amount if day_amount > 0 else 1.0
            impact_bps = IMPACT_COEF_BPS * math.sqrt(min(part, 1.0))
            exec_px = open_px * (1 + impact_bps / 1e4 if order.side == "buy"
                                 else 1 - impact_bps / 1e4)
            qty = int(math.floor(amt / exec_px / LOT_SIZE) * LOT_SIZE)
            if qty <= 0:
                deferred += 1
                continue
            amount = qty * exec_px
            fee = max(COMMISSION_MIN, amount * COMMISSION_RATE_DEFAULT)
            if order.side == "sell" and not _is_etf(order.symbol):
                fee += amount * STAMP_DUTY_STOCK_RATE
            basis_bps = (exec_px / order.decision_price - 1.0) * 1e4 \
                if order.decision_price else 0.0
            session.add(PaperFill(
                order_id=order.id, symbol=order.symbol, side=order.side,
                exec_date=exec_date, qty=qty, price=exec_px, amount=amount,
                fee=fee, impact_bps=impact_bps, participation=part,
                basis_bps=basis_bps))
            remaining_total -= amount
            filled_cnt += 1
            filled_today = True
            if remaining_total <= 1e-6:
                break
        if order.status == "REJECTED":
            pass          # 终态：不得被下面的收尾分支覆盖
        elif remaining_total <= 1e-6:
            order.status = "FILLED"
        elif filled_today:
            order.status = "PART_FILLED"
    return {"orders_scanned": len(orders), "fills_created": filled_cnt,
            "deferred_children": deferred, "rejected_orders": rejected}


def _created_day_index(created_at, trade_days: list[date]) -> int | None:
    """母单创建时刻在交易日轴上的落点（此后一天起才可成交）。"""
    if created_at is None:
        return 0
    d = created_at.date() if hasattr(created_at, "date") else created_at
    prior = [i for i, td in enumerate(trade_days) if td <= d]
    return prior[-1] if prior else 0


def _build_nav_series(fills) -> list[dict]:
    """逐日重建模拟盘净值序列（cash + 持仓市值，全部由真实成交推导）。

    算法：
    1. fills 按 exec_date 升序，收集涉及 symbols 的 daily_bar close（各读一次）；
    2. 交易日轴 = 各 symbol close 日期的并集（自首笔成交日起）；
    3. 逐日累加 cash 变动（含费用与冲击）并按当日 close 重算持仓市值；
    4. 返回 [{date, cash, mv, equity}, ...]。
    无本地行情 / 无成交时返回 []。
    """
    if not fills:
        return []
    fills = sorted(fills, key=lambda f: (f.exec_date, f.id))
    close_cache: dict[str, dict] = {}
    for sym in sorted({f.symbol for f in fills}):
        df = read_symbol_dataset("daily_bar", sym)
        if df.is_empty():
            continue
        if df.schema["date"] != pl.Date:
            df = df.with_columns(pl.col("date").cast(pl.Date))
        close_cache[sym] = dict(zip(df["date"].to_list(),
                                    df["close"].to_list()))
    if not close_cache:
        return []
    fills_by_day: dict = {}
    for f in fills:
        fills_by_day.setdefault(f.exec_date, []).append(f)
    # 交易日轴 = 行情日期并集 ∪ 成交日（防行情缺失漏记 cash），自首笔成交日起
    all_days = sorted(
        set().union(*(c.keys() for c in close_cache.values()))
        | set(fills_by_day))
    first_day = min(fills_by_day)
    all_days = [d for d in all_days if d >= first_day]
    cash = INIT_CASH
    positions: dict[str, int] = {}
    nav: list[dict] = []
    for day in all_days:
        for f in fills_by_day.get(day, ()):
            fee = f.fee + f.amount * f.impact_bps / 1e4
            if f.side == "buy":
                cash -= f.amount + fee
                positions[f.symbol] = positions.get(f.symbol, 0) + f.qty
            else:
                cash += f.amount - fee
                positions[f.symbol] = positions.get(f.symbol, 0) - f.qty
                if positions[f.symbol] <= 0:
                    positions.pop(f.symbol, None)
        mv = 0.0
        for sym, qty in positions.items():
            px = close_cache.get(sym, {}).get(day)
            if px:
                mv += qty * px
        nav.append({"date": str(day), "cash": round(cash, 2),
                    "mv": round(mv, 2), "equity": round(cash + mv, 2)})
    return nav


def account_summary(session) -> dict:
    """由全部真实成交推导账户：现金 / 持仓 / 已实现费用。

    P1-3 附带：本函数是**对已记录成交的纯推导**（单一事实来源，不改数字），
    但会逐笔重放检出"卖出股数 > 当时持仓"的历史成交，写入
    ``integrity_warnings``——这类成交曾凭空造出现金（修复前产生），
    必须**可见**而不是被静默当成真实权益。
    """
    fills = session.execute(sa.select(PaperFill).order_by(PaperFill.id)).scalars().all()
    cash = INIT_CASH
    positions: dict[str, int] = {}
    cost_basis: dict[str, float] = {}
    total_fees = total_impact = 0.0
    warnings: list[str] = []
    replay: dict[str, int] = {}
    for f in fills:
        fee = f.fee + f.amount * f.impact_bps / 1e4
        total_fees += f.fee
        total_impact += f.amount * f.impact_bps / 1e4
        if f.side == "buy":
            cash -= f.amount + fee
            old_q = positions.get(f.symbol, 0)
            cost_basis[f.symbol] = (cost_basis.get(f.symbol, 0.0) * old_q
                                    + f.amount) / (old_q + f.qty)
            positions[f.symbol] = old_q + f.qty
            replay[f.symbol] = replay.get(f.symbol, 0) + f.qty
        else:
            had = replay.get(f.symbol, 0)
            if f.qty > had:
                excess = f.qty - had
                warnings.append(
                    f"{f.symbol} {f.exec_date} 卖出 {f.qty} 股 > 当时持仓 {had} 股"
                    f"（越卖 {excess} 股，约 {excess * f.price:,.0f} 元系凭空造出）"
                    f" ⇒ 现金与权益偏高，需人工处置该笔成交")
            replay[f.symbol] = had - f.qty
            cash += f.amount - fee
            remaining = positions.get(f.symbol, 0) - f.qty
            if remaining > 0:
                positions[f.symbol] = remaining
            else:
                positions.pop(f.symbol, None)
                cost_basis.pop(f.symbol, None)
    # 持仓市值：最新真实收盘价
    marks: dict[str, dict] = {}
    for sym, qty in positions.items():
        df = read_symbol_dataset("daily_bar", sym)
        px = None
        if not df.is_empty():
            dcol = df.schema["date"]
            if dcol != pl.Date:
                df = df.with_columns(pl.col("date").cast(pl.Date))
            last = df.sort("date").tail(1).to_dicts()[0]
            px = float(last["close"])
            marks[sym] = {"qty": qty, "last_price": px,
                          "market_value": px * qty,
                          "cost_price": round(cost_basis.get(sym, 0.0), 4),
                          "pnl": px * qty - cost_basis.get(sym, 0.0) * qty}
    mv = sum(m["market_value"] for m in marks.values())

    # 逐日净值序列 + 派生指标（样本 < 30 交易日视为不足，指标返回 None）
    nav = _build_nav_series(fills)
    max_dd = sharpe = annualized = None
    if len(nav) >= 30:
        equities = [n["equity"] for n in nav]
        # 最大回撤（负数）
        peak, mdd = equities[0], 0.0
        for e in equities:
            peak = max(peak, e)
            if peak > 0:
                mdd = min(mdd, (e - peak) / peak)
        max_dd = round(mdd, 4)
        # 夏普（日收益年化，rf=0，252 交易日）
        rets = [equities[i] / equities[i - 1] - 1.0
                for i in range(1, len(equities)) if equities[i - 1] > 0]
        if len(rets) >= 2:
            mean_r = float(np.mean(rets))
            std_r = float(np.std(rets, ddof=1))
            sharpe = round(mean_r / std_r * (252 ** 0.5), 3) \
                if std_r > 1e-12 else None
        # 年化收益（以初始资金为基数）
        total_ret = equities[-1] / INIT_CASH - 1.0
        annualized = round((1 + total_ret) ** (252 / len(equities)) - 1.0, 4)

    return {
        "initial_cash": INIT_CASH,
        "cash": round(cash, 2),
        "market_value": round(mv, 2),
        "equity": round(cash + mv, 2),
        "total_fees": round(total_fees, 2),
        "total_impact_cost": round(total_impact, 2),
        "positions": marks,
        "n_fills": len(fills),
        "max_drawdown": max_dd,
        "sharpe": sharpe,
        "annualized_return": annualized,
        "n_active_days": len(nav),
        "nav_series": nav,
        "integrity_warnings": warnings,
    }


def cancel_all(session) -> int:
    """一键撤单：全部 PENDING / PART_FILLED 母单置 CANCELLED。"""
    orders = session.execute(
        sa.select(PaperOrder).where(
            PaperOrder.status.in_(["PENDING", "PART_FILLED"]))).scalars().all()
    for o in orders:
        o.status = "CANCELLED"
    return len(orders)
