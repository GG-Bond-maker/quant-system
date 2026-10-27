"""云端条件单与网格交易机器人引擎（S2-1）。

支持：
1. 价格条件单：price_ge (突破买入/止盈) 与 price_le (破位卖出/止损)；
2. 网格交易机器人：在价格区间 [lower_price, upper_price] 自动布单与网格低吸高抛撮合；
3. 真实行情触发：根据 quotes_snapshot 或实时报价评估触发，严格调用 paper.place_order 报单。
"""
from __future__ import annotations

from datetime import datetime

import sqlalchemy as sa
from loguru import logger

from ..db.models import ConditionalOrder, GridBot, PaperAccount, PaperFill, PaperOrder
from .paper import (
    DEFAULT_ACCOUNT,
    _fills,
    _initial_cash,
    _replay,
    lock_ledger,
    place_order,
)


def create_conditional_order(
    session,
    *,
    symbol: str,
    side: str,
    trigger_type: str,
    trigger_price: float,
    order_amount: float,
    order_type: str = "market",
    algo_type: str = "market",
    user_id: str = "default",
    account_id: str = DEFAULT_ACCOUNT,
) -> dict:
    """创建云端价格条件单。"""
    if trigger_price <= 0:
        raise ValueError("trigger_price 必须大于 0")
    if order_amount <= 0:
        raise ValueError("order_amount 必须大于 0")
    if trigger_type not in ("price_ge", "price_le"):
        raise ValueError("trigger_type 必须为 price_ge 或 price_le")
    if side not in ("buy", "sell"):
        raise ValueError("side 必须为 buy 或 sell")

    sym = symbol.strip().upper()
    order = ConditionalOrder(
        account_id=account_id,
        user_id=user_id,
        symbol=sym,
        side=side,
        order_type=order_type,
        trigger_type=trigger_type,
        trigger_price=float(trigger_price),
        order_amount=float(order_amount),
        algo_type=algo_type,
        status="ACTIVE",
    )
    session.add(order)
    session.commit()
    session.refresh(order)
    return {
        "id": order.id,
        "symbol": order.symbol,
        "side": order.side,
        "trigger_type": order.trigger_type,
        "trigger_price": order.trigger_price,
        "order_amount": order.order_amount,
        "status": order.status,
        "created_at": order.created_at.isoformat() if order.created_at else None,
    }


def list_conditional_orders(
    session,
    status: str | None = None,
    limit: int = 50,
    account_id: str = DEFAULT_ACCOUNT,
) -> list[dict]:
    """查询条件单列表。"""
    refresh_trade_states(session)
    stmt = sa.select(ConditionalOrder).where(ConditionalOrder.account_id == account_id).order_by(ConditionalOrder.id.desc())
    if status:
        stmt = stmt.where(ConditionalOrder.status == status)
    stmt = stmt.limit(limit)
    rows = session.execute(stmt).scalars().all()
    return [
        {
            "id": r.id,
            "user_id": r.user_id,
            "symbol": r.symbol,
            "side": r.side,
            "order_type": r.order_type,
            "trigger_type": r.trigger_type,
            "trigger_price": r.trigger_price,
            "order_amount": r.order_amount,
            "algo_type": r.algo_type,
            "status": r.status,
            "message": r.message,
            "triggered_at": r.triggered_at.isoformat() if r.triggered_at else None,
            "fill_order_id": r.fill_order_id,
            "created_at": r.created_at.isoformat() if r.created_at else None,
        }
        for r in rows
    ]


def cancel_conditional_order(session, order_id: int, account_id: str = DEFAULT_ACCOUNT) -> bool:
    """撤销未触发的条件单。"""
    row = session.execute(
        sa.select(ConditionalOrder).where(ConditionalOrder.id == order_id)
    ).scalar_one_or_none()
    if not row or row.account_id != account_id or row.status != "ACTIVE":
        return False
    row.status = "CANCELLED"
    row.message = "用户手动撤单"
    session.commit()
    return True


def evaluate_conditional_orders(
    session,
    quotes: dict[str, float],
    account_id: str = DEFAULT_ACCOUNT,
) -> list[dict]:
    """批量评估条件单：满足阈值时自动调用 paper.place_order 报单。"""
    lock_ledger(session)
    active_orders = session.execute(
        sa.select(ConditionalOrder).where(ConditionalOrder.status == "ACTIVE", ConditionalOrder.account_id == account_id)
    ).scalars().all()

    triggered = []
    for co in active_orders:
        sym = co.symbol
        price = quotes.get(sym)
        if price is None or price <= 0:
            continue

        is_hit = False
        if co.trigger_type == "price_ge" and price >= co.trigger_price or co.trigger_type == "price_le" and price <= co.trigger_price:
            is_hit = True

        if is_hit:
            try:
                account = session.get(PaperAccount, co.account_id)
                res = place_order(
                    session,
                    symbol=co.symbol,
                    side=co.side,
                    order_amount=co.order_amount,
                    algo=co.algo_type,
                    split_days=1,
                    participation_cap=0.05,
                    account_id=co.account_id,
                    client_order_id=f"conditional:{co.id}",
                    price_mode=co.order_type,
                    limit_price=co.trigger_price if co.order_type == "limit" else None,
                    strategy_version=account.strategy_version if account and account.strategy_version else f"conditional:{co.id}",
                )
                if not res.get("ok"):
                    co.status = "REJECTED"
                    co.message = res.get("reason", "报单被拒")
                    continue
                co.status = "SUBMITTED"
                co.triggered_at = datetime.now().astimezone().replace(tzinfo=None)
                co.fill_order_id = res.get("order_id")
                co.message = f"触发价 {price:.2f} 达成，生成订单 #{res.get('order_id')}"
                triggered.append({
                    "id": co.id,
                    "symbol": co.symbol,
                    "trigger_price": co.trigger_price,
                    "actual_price": price,
                    "order_id": res.get("order_id"),
                })
            except Exception as e:  # noqa: BLE001 one failed trigger must not suppress other orders
                logger.error(f"[conditional] 订单触发失败 #{co.id}: {e}")
                co.status = "FAILED"
                co.message = f"触发报单异常: {e!s}"
    session.commit()
    return triggered


def create_grid_bot(
    session,
    *,
    symbol: str,
    lower_price: float,
    upper_price: float,
    grid_count: int = 10,
    amount_per_grid: float = 10000.0,
    user_id: str = "default",
    account_id: str = DEFAULT_ACCOUNT,
) -> dict:
    """创建网格交易机器人。"""
    if lower_price >= upper_price:
        raise ValueError("lower_price 必须小于 upper_price")
    if grid_count < 2 or grid_count > 100:
        raise ValueError("grid_count 需在 2~100 之间")
    if amount_per_grid <= 0:
        raise ValueError("amount_per_grid 必须大于 0")

    sym = symbol.strip().upper()
    bot = GridBot(
        account_id=account_id,
        user_id=user_id,
        symbol=sym,
        lower_price=float(lower_price),
        upper_price=float(upper_price),
        grid_count=int(grid_count),
        amount_per_grid=float(amount_per_grid),
        status="RUNNING",
    )
    session.add(bot)
    session.commit()
    session.refresh(bot)
    return {
        "id": bot.id,
        "symbol": bot.symbol,
        "lower_price": bot.lower_price,
        "upper_price": bot.upper_price,
        "grid_count": bot.grid_count,
        "amount_per_grid": bot.amount_per_grid,
        "status": bot.status,
        "created_at": bot.created_at.isoformat() if bot.created_at else None,
    }


def list_grid_bots(session, status: str | None = None, account_id: str = DEFAULT_ACCOUNT) -> list[dict]:
    """查询网格机器人列表。"""
    refresh_trade_states(session)
    stmt = sa.select(GridBot).where(GridBot.account_id == account_id).order_by(GridBot.id.desc())
    if status:
        stmt = stmt.where(GridBot.status == status)
    rows = session.execute(stmt).scalars().all()
    return [
        {
            "id": r.id,
            "user_id": r.user_id,
            "symbol": r.symbol,
            "lower_price": r.lower_price,
            "upper_price": r.upper_price,
            "grid_count": r.grid_count,
            "amount_per_grid": r.amount_per_grid,
            "status": r.status,
            "last_trigger_price": r.last_trigger_price,
            "total_trades": r.total_trades,
            "realized_pnl": r.realized_pnl,
            "created_at": r.created_at.isoformat() if r.created_at else None,
        }
        for r in rows
    ]


def stop_grid_bot(session, bot_id: int, account_id: str = DEFAULT_ACCOUNT) -> bool:
    """终止网格机器人。"""
    row = session.execute(
        sa.select(GridBot).where(GridBot.id == bot_id)
    ).scalar_one_or_none()
    if not row or row.account_id != account_id or row.status != "RUNNING":
        return False
    row.status = "STOPPED"
    session.commit()
    return True


def evaluate_grid_bots(session, quotes: dict[str, float], account_id: str = DEFAULT_ACCOUNT) -> list[dict]:
    """评估运行中的网格机器人。"""
    lock_ledger(session)
    refresh_trade_states(session)
    active_bots = session.execute(
        sa.select(GridBot).where(GridBot.status == "RUNNING", GridBot.account_id == account_id)
    ).scalars().all()

    triggered = []
    for bot in active_bots:
        pending = session.scalar(sa.select(PaperOrder.id).where(
            PaperOrder.grid_bot_id == bot.id, PaperOrder.status.in_(("PENDING", "PART_FILLED"))).limit(1))
        if pending is not None:
            continue
        price = quotes.get(bot.symbol)
        if price is None or price < bot.lower_price or price > bot.upper_price:
            continue

        step = (bot.upper_price - bot.lower_price) / float(bot.grid_count)
        if step <= 0:
            continue

        last_p = bot.last_trigger_price
        if last_p is None:
            # 首次初始化挂单基准价
            bot.last_trigger_price = price
            session.commit()
            continue

        diff = price - last_p
        if abs(diff) >= step:
            side = "sell" if diff > 0 else "buy"
            try:
                account = session.get(PaperAccount, bot.account_id)
                res = place_order(
                    session,
                    symbol=bot.symbol,
                    side=side,
                    order_amount=bot.amount_per_grid,
                    algo="market",
                    split_days=1,
                    participation_cap=0.05,
                    account_id=bot.account_id,
                    grid_bot_id=bot.id,
                    strategy_version=account.strategy_version if account and account.strategy_version else f"grid:{bot.id}",
                )
                if not res.get("ok"):
                    continue
                bot.last_trigger_price = price
                triggered.append({
                    "bot_id": bot.id,
                    "symbol": bot.symbol,
                    "side": side,
                    "price": price,
                    "order_id": res.get("order_id"),
                })
            except Exception as e:  # noqa: BLE001 one failed bot must not suppress other bots
                logger.error(f"[grid_bot] 执行失败 #{bot.id}: {e}")
    if triggered:
        session.commit()
    return triggered


def refresh_trade_states(session) -> None:
    """Reconcile trigger state and grid statistics against persisted executions."""
    for co in session.scalars(sa.select(ConditionalOrder).where(
        ConditionalOrder.fill_order_id.is_not(None))):
        order = session.get(PaperOrder, co.fill_order_id)
        if order is not None:
            co.status = "SUBMITTED" if order.status == "PENDING" else order.status
            if order.reject_reason:
                co.message = order.reject_reason
    for bot in session.scalars(sa.select(GridBot)):
        per_order: dict[int, float] = {}
        _replay(_fills(session, bot.account_id), _initial_cash(session, bot.account_id), per_order)
        ids = list(session.scalars(sa.select(PaperOrder.id).where(PaperOrder.grid_bot_id == bot.id)))
        bot.total_trades = int(session.scalar(sa.select(sa.func.count()).select_from(PaperFill).where(
            PaperFill.order_id.in_(ids))) or 0)
        bot.realized_pnl = sum(per_order.get(order_id, 0.0) for order_id in ids)
    session.flush()
