"""实盘/模拟盘执行中心 + 实时风控闸门 + 容量与业绩归因 API。

诚实定位：**模拟盘**（本地撮合、真实行情、真实费用、真实持久化）——
系统未接入券商柜台，不假装实盘。Kill Switch / 禁买池 / 算法分批 /
基差分析全部对模拟盘引擎真实生效。
"""
from __future__ import annotations

from contextlib import contextmanager

import numpy as np
import polars as pl
import sqlalchemy as sa
from fastapi import APIRouter, Depends, Query
from loguru import logger
from pydantic import BaseModel, Field

from ...core.auth import require_role
from ...core.compute_guard import compute_slot
from ...core.compute_pool import run_in_compute_pool
from ...core.config import get_settings
from ...core.errors import APIResponse, AQPException, ERR_DATA_EMPTY, ERR_PARAMS, ERR_NOT_FOUND, ok
from ...data.parquet_store import (as_py_float, read_parquet_columns,
                                   read_symbol_dataset, require_columns)
from ...db.models import ExclusionItem, PaperFill, PaperOrder
from ...domain.attribution import (
    brinson_attribution,
    capacity_decay_curve,
    multi_period_brinson_attribution,
    portfolio_liquidity_bottleneck,
    stock_level_contributions,
    strategy_capacity,
    style_factor_returns,
    style_regression_attribution,
)
from ...trading import paper

router = APIRouter()


@contextmanager
def _db():
    """同步会话（to_thread 内使用；WAL 允许并发读）。"""
    s = paper.sync_session_factory()
    session = s()
    try:
        yield session
        session.commit()
    except ValueError as exc:
        session.rollback()
        raise AQPException(ERR_PARAMS, str(exc)) from exc
    except Exception:
        session.rollback()
        raise
    finally:
        session.close()


class PaperAccountRequest(BaseModel):
    account_id: str = Field(..., pattern=r"^[A-Za-z0-9_-]{1,64}$")
    initial_cash: float = Field(1_000_000, gt=0, le=1e10)
    strategy_version: str | None = Field(None, max_length=128)
    backtest_id: int | None = Field(None, gt=0)
    benchmark: str = Field("000300.SH", max_length=24)


@router.get("/accounts", response_model=APIResponse[list])
async def paper_accounts(_user: dict = Depends(require_role("researcher"))) -> APIResponse[list]:
    from ...db.models import PaperAccount
    def _run():
        with _db() as session:
            rows = [{"account_id": row.id, "initial_cash": row.initial_cash,
                     "strategy_version": row.strategy_version, "benchmark": row.benchmark}
                    for row in session.scalars(sa.select(PaperAccount).order_by(PaperAccount.id))]
            if not any(row["account_id"] == paper.DEFAULT_ACCOUNT for row in rows):
                rows.insert(0, {"account_id": paper.DEFAULT_ACCOUNT, "initial_cash": paper.INIT_CASH,
                                "strategy_version": None, "benchmark": "000300.SH"})
            return rows
    return ok(await run_in_compute_pool(_run))


@router.post("/accounts", response_model=APIResponse[dict])
async def create_paper_account(req: PaperAccountRequest,
                               _user: dict = Depends(require_role("researcher"))) -> APIResponse[dict]:
    from ...db.models import PaperAccount, BacktestRun
    def _run():
        with _db() as session:
            paper.lock_ledger(session)
            if req.account_id == paper.DEFAULT_ACCOUNT or session.get(PaperAccount, req.account_id):
                raise AQPException(ERR_PARAMS, "账户已存在，不能重置资金")
            version = req.strategy_version
            if req.backtest_id:
                run = session.get(BacktestRun, req.backtest_id)
                if run is None or run.status != "success":
                    raise AQPException(ERR_NOT_FOUND, "成功的回测记录不存在")
                version = run.strategy_version
                if float(run.init_cash) != req.initial_cash:
                    raise AQPException(ERR_PARAMS, "对照账户初始资金须与回测一致")
            row = PaperAccount(id=req.account_id, initial_cash=req.initial_cash,
                               strategy_version=version, benchmark=req.benchmark)
            session.add(row)
            return {"account_id": row.id, "initial_cash": row.initial_cash, "strategy_version": version}
    return ok(await run_in_compute_pool(_run))


@router.get("/accounts/compare", response_model=APIResponse[dict])
async def compare_paper_accounts(accounts: str = Query("paper_default", max_length=650),
                                  benchmark: str = "000300.SH",
                                  _user: dict = Depends(require_role("researcher"))) -> APIResponse[dict]:
    from ...trading.comparison import compare_accounts
    ids = list(dict.fromkeys(account.strip() for account in accounts.split(",") if account.strip()))
    if not 1 <= len(ids) <= 10:
        raise AQPException(ERR_PARAMS, "请选择1至10个账户")
    def _run():
        with _db() as session:
            try:
                return compare_accounts(session, ids, benchmark)
            except ValueError as exc:
                raise AQPException(ERR_PARAMS, str(exc)) from exc
    return ok(await run_in_compute_pool(_run))


# ==================== 多级量化风控中心 ====================
@router.get("/risk/status", response_model=APIResponse[dict])
async def get_risk_status(
    _user: dict = Depends(require_role("researcher")),
) -> APIResponse[dict]:
    """获取多级量化风控状态及限额参数。"""
    def _run() -> dict:
        with _db() as session:
            ks = paper.get_kill_switch(session)
            hb = paper.get_halt_buys(session)
            n_pending = session.execute(
                sa.select(sa.func.count()).select_from(PaperOrder)
                .where(PaperOrder.status.in_(["PENDING", "PART_FILLED"]))
            ).scalar()
            return {
                "kill_switch": ks,
                "halt_buys": hb,
                "pending_orders": int(n_pending or 0),
                "rules": {
                    "max_position_ratio": paper.MAX_POSITION_RATIO,
                    "max_single_order_amount": paper.MAX_SINGLE_ORDER_AMOUNT,
                    "min_cash_buffer_ratio": paper.MIN_CASH_BUFFER_RATIO,
                },
            }
    return ok(await run_in_compute_pool(_run))


class HaltBuysRequest(BaseModel):
    active: bool
    reason: str = Field("", max_length=200)


@router.post("/risk/halt-buys", response_model=APIResponse[dict])
async def set_halt_buys(
    req: HaltBuysRequest,
    _user: dict = Depends(require_role("researcher")),
) -> APIResponse[dict]:
    """L1 暂停新开仓：ON = 禁止买入，仅允许持仓平仓。"""
    def _run() -> dict:
        with _db() as session:
            paper.set_halt_buys(session, req.active, req.reason)
        logger.warning(f"[desk] halt_buys -> {req.active}")
        return {"halt_buys": req.active}
    return ok(await run_in_compute_pool(_run))


@router.post("/risk/liquidate-all", response_model=APIResponse[dict])
async def emergency_liquidate(
    account_id: str = "paper_default",
    _user: dict = Depends(require_role("researcher")),
) -> APIResponse[dict]:
    """L3 一键紧急清盘：撤销在途单并市价平仓全部多头持仓。"""
    def _run() -> dict:
        with _db() as session:
            res = paper.emergency_liquidate_all(session, account_id)
        logger.critical(f"[desk] emergency_liquidate_all executed: {res}")
        return res
    return ok(await run_in_compute_pool(_run))


@router.get("/kill-switch", response_model=APIResponse[dict])
async def get_kill_switch(
    _user: dict = Depends(require_role("researcher")),
) -> APIResponse[dict]:
    def _run() -> dict:
        with _db() as session:
            on = paper.get_kill_switch(session)
            n_pending = session.execute(
                sa.select(sa.func.count()).select_from(PaperOrder)
                .where(PaperOrder.status.in_(["PENDING", "PART_FILLED"]))
            ).scalar()
            return {"kill_switch": on, "pending_orders": int(n_pending or 0)}
    return ok(await run_in_compute_pool(_run))


class KillSwitchRequest(BaseModel):
    active: bool
    reason: str = Field("", max_length=200)


@router.post("/kill-switch", response_model=APIResponse[dict])
async def set_kill_switch(
    req: KillSwitchRequest,
    _user: dict = Depends(require_role("researcher")),
) -> APIResponse[dict]:
    """L2 熔断开关：ON = 拒绝一切新订单并撤销全部未完成母单（已成交保留）。"""
    def _run() -> dict:
        with _db() as session:
            n_cancelled = paper.set_kill_switch(session, req.active, req.reason)
        logger.warning(f"[desk] kill_switch -> {req.active} "
                       f"(cancelled {n_cancelled})")
        return {"kill_switch": req.active, "cancelled_orders": n_cancelled}
    return ok(await run_in_compute_pool(_run))


# ==================== 合规禁买池 ====================
@router.get("/exclusion", response_model=APIResponse[list])
async def list_exclusion(
    _user: dict = Depends(require_role("researcher")),
) -> APIResponse[list]:
    def _run() -> list[dict]:
        with _db() as session:
            rows = session.execute(
                sa.select(ExclusionItem).order_by(ExclusionItem.id.desc())
                .limit(200)).scalars().all()
        return [{"id": r.id, "symbol": r.symbol, "category": r.category,
                 "reason": r.reason, "active": r.active,
                 "created_at": str(r.created_at or "")} for r in rows]
    return ok(await run_in_compute_pool(_run))


class ExclusionAddRequest(BaseModel):
    symbol: str = Field(..., min_length=1, max_length=16)
    category: str = Field("manual_blacklist",
                          pattern=r"^(st|delist_risk|illiquid|manual_blacklist)$")
    reason: str = Field("", max_length=200)


@router.post("/exclusion", response_model=APIResponse[dict])
async def add_exclusion(
    req: ExclusionAddRequest,
    _user: dict = Depends(require_role("researcher")),
) -> APIResponse[dict]:
    """加入禁买池（去重）；立即对模拟盘下单生效。"""
    def _run() -> dict:
        sym = paper._norm_symbol(req.symbol)  # noqa: SLF001
        with _db() as session:
            dup = session.execute(
                sa.select(ExclusionItem).where(
                    ExclusionItem.symbol == sym,
                    ExclusionItem.category == req.category)
            ).scalar_one_or_none()
            if dup:
                dup.active = True
                dup.reason = req.reason or dup.reason
                return {"ok": True, "id": dup.id, "updated": True}
            item = ExclusionItem(symbol=sym, category=req.category,
                                    reason=req.reason, active=True)
            session.add(item)
            session.flush()
            return {"ok": True, "id": item.id, "updated": False}
    return ok(await run_in_compute_pool(_run))


class ExclusionToggleRequest(BaseModel):
    id: int
    active: bool


@router.post("/exclusion/toggle", response_model=APIResponse[dict])
async def toggle_exclusion(
    req: ExclusionToggleRequest,
    _user: dict = Depends(require_role("researcher")),
) -> APIResponse[dict]:
    def _run() -> dict:
        with _db() as session:
            item = session.get(ExclusionItem, req.id)
            if item is None:
                raise AQPException(ERR_DATA_EMPTY, "条目不存在")
            item.active = req.active
            return {"ok": True, "id": req.id, "active": req.active}
    return ok(await run_in_compute_pool(_run))


@router.get("/exclusion/screen", response_model=APIResponse[list])
async def screen_candidates(
    _user: dict = Depends(require_role("researcher")),
) -> APIResponse[list]:
    """从 universe 最新截面筛 ST / 流动性差候选（真实 is_st 与成交额）。"""
    def _run() -> list[dict]:
        with _db() as session:
            return paper.screen_universe_candidates(session)
    return ok(await run_in_compute_pool(_run))


# ==================== 模拟盘订单 ====================
class OrderPreflightRequest(BaseModel):
    account_id: str = Field("paper_default", min_length=1, max_length=64)
    symbol: str = Field(..., min_length=1, max_length=16)
    side: str = Field("buy", pattern=r"^(buy|sell)$")
    order_amount: float = Field(..., gt=0, le=1e10)


@router.post("/orders/preflight", response_model=APIResponse[dict])
async def order_preflight(
    req: OrderPreflightRequest,
    _user: dict = Depends(require_role("researcher")),
) -> APIResponse[dict]:
    """事前量化风控预检（集中度、现金缓冲、肥手指、合规与禁买池预先试算）。"""
    def _run() -> dict:
        with _db() as session:
            return paper.preflight_check(
                session,
                symbol=req.symbol,
                side=req.side,
                order_amount=req.order_amount,
                account_id=req.account_id,
            )
    return ok(await run_in_compute_pool(_run))


class OrderRequest(BaseModel):
    model_config = {"protected_namespaces": ()}
    account_id: str = Field("paper_default", min_length=1, max_length=64)
    price_mode: str = Field("market", pattern=r"^(market|limit|opposite)$")
    limit_price: float | None = Field(None, gt=0)
    client_order_id: str | None = Field(None, max_length=128)
    strategy_version: str | None = Field(None, max_length=128)
    model_version: str | None = Field(None, max_length=128)
    signal_id: str | None = Field(None, max_length=128)
    backtest_id: str | None = Field(None, max_length=128)
    run_id: str | None = Field(None, max_length=128)
    symbol: str = Field(..., min_length=1, max_length=16)
    side: str = Field("buy", pattern=r"^(buy|sell)$")
    order_amount: float = Field(..., gt=0, le=1e10)
    algo: str = Field("market", pattern=r"^(market|vwap|twap|pov)$")
    split_days: int = Field(5, ge=1, le=20)
    participation_cap: float = Field(0.05, ge=0.001, le=0.3)


def _resolve_order_reference(session, req: OrderRequest) -> dict:
    """Resolve references from stored runs/signals; clients cannot invent provenance."""
    import json
    from ...db.models import BacktestRun
    from ...ml.signal_contract import validate_signals
    parameters = req.model_dump()
    source = None
    if req.backtest_id is not None:
        if not req.backtest_id.isdigit():
            raise AQPException(ERR_PARAMS, "回测ID无效")
        run = session.get(BacktestRun, int(req.backtest_id))
        if run is None or run.status != "success":
            raise AQPException(ERR_NOT_FOUND, "成功的回测记录不存在")
        summary = json.loads(run.summary_json or "{}")
        if req.strategy_version is not None and req.strategy_version != run.strategy_version:
            raise AQPException(ERR_PARAMS, "委托策略与回测版本不一致")
        if req.run_id is not None and req.run_id != run.run_id:
            raise AQPException(ERR_PARAMS, "委托run_id与回测记录不一致")
        parameters.update(strategy_version=run.strategy_version, run_id=run.run_id)
        if req.signal_id:
            source = next((row for row in summary.get("signal_sources", [])
                           if row.get("signal_id") == req.signal_id and row.get("symbol") == req.symbol), None)
            if source is None:
                raise AQPException(ERR_PARAMS, "回测中不存在该标的/信号来源")
    elif req.signal_id:
        for path in sorted((get_settings().DATA_ROOT / "predictions").glob("date=*.parquet"), reverse=True):
            scan = pl.scan_parquet(path)
            if "signal_id" not in scan.schema:
                continue
            selected = scan.filter(pl.col("signal_id") == req.signal_id).collect()
            if not selected.is_empty():
                validate_signals(selected.to_pandas())
                source = selected.to_dicts()[0]
                break
        if source is None or source.get("symbol") != req.symbol:
            raise AQPException(ERR_NOT_FOUND, "信号来源不存在或标的不匹配")
        parameters["run_id"] = source["run_id"]
    if source:
        if req.model_version and req.model_version != source["model_version"]:
            raise AQPException(ERR_PARAMS, "委托模型与信号来源不一致")
        parameters["model_version"] = source["model_version"]
    return parameters


@router.post("/orders", response_model=APIResponse[dict])
async def place_order(
    req: OrderRequest,
    _user: dict = Depends(require_role("researcher")),
) -> APIResponse[dict]:
    """提交母单（真实校验：kill switch / 禁买池 / 本地行情 / **卖出持仓**）。"""
    def _run() -> dict:
        with _db() as session:
            res = paper.place_order(session, **_resolve_order_reference(session, req))
            if res.get("ok"):
                session.commit()
        return res
    return ok(await run_in_compute_pool(_run))


@router.post("/orders/{order_id}/cancel", response_model=APIResponse[dict])
async def cancel_order(
    order_id: int,
    account_id: str = "paper_default",
    _user: dict = Depends(require_role("researcher")),
) -> APIResponse[dict]:
    """撤销指定在途母单（仅 PENDING/PART_FILLED 可撤销）。"""
    def _run() -> dict:
        with _db() as session:
            success = paper.cancel_order(session, order_id, account_id)
            if success:
                session.commit()
            return {"ok": success, "order_id": order_id}
    return ok(await run_in_compute_pool(_run))


@router.post("/orders/cancel-all", response_model=APIResponse[dict])
async def cancel_all_orders(
    account_id: str = "paper_default",
    _user: dict = Depends(require_role("researcher")),
) -> APIResponse[dict]:
    """一键撤销全部在途母单。"""
    def _run() -> dict:
        with _db() as session:
            count = paper.cancel_all(session, account_id)
            session.commit()
            return {"ok": True, "cancelled_count": count}
    return ok(await run_in_compute_pool(_run))


@router.post("/fills/run", response_model=APIResponse[dict])
async def run_fills(
    account_id: str = "paper_default",
    _user: dict = Depends(require_role("researcher")),
    _compute: None = Depends(compute_slot),
) -> APIResponse[dict]:
    """撮合所有到期子单（真实开盘价 + sqrt 冲击；D0+1 起成交，无未来函数）。"""
    def _run() -> dict:
        with _db() as session:
            res = paper.run_fills(session, account_id=account_id)
        logger.info(f"[desk] fills run: {res}")
        return res
    return ok(await run_in_compute_pool(_run))


@router.get("/orders", response_model=APIResponse[dict])
async def list_orders(
    account_id: str = "paper_default",
    limit: int = Query(50, ge=1, le=1000),
    _user: dict = Depends(require_role("researcher")),
) -> APIResponse[dict]:
    """母单列表（按 id 倒序，最多 `limit` 条）。

    P2-8：响应由裸数组改为信封 `{items,total,returned,limit,truncated}`。
    `total` 是独立 COUNT 的**真实总数**（不受 `limit` 影响），`truncated` 披露
    本次是否被截断；调用方据此展示真实总量，不再把窗口条数当全量。
    """
    def _run() -> dict:
        with _db() as session:
            total = int(session.execute(
                sa.select(sa.func.count()).select_from(PaperOrder).where(PaperOrder.account_id == account_id)).scalar_one())
            orders = session.execute(
                sa.select(PaperOrder).where(PaperOrder.account_id == account_id).order_by(PaperOrder.id.desc())
                .limit(limit)).scalars().all()
            # P0（N+1 修复）：原先对每个 order 各查一次 PaperFill —— 前端
            # ORDER_WINDOW=200 ⇒ 单请求 202 条 SQL。改为**一次 IN 查询**后按
            # order_id 分组，排序口径不变（PaperFill.id 升序）。
            # 注：PaperOrder 上**没有** fills relationship，故不引入 selectinload
            # （不新增/改模型），仅用批量查询。
            order_ids = [o.id for o in orders]
            fills_by_order: dict[int, list[PaperFill]] = {}
            if order_ids:
                all_fills = session.execute(
                    sa.select(PaperFill)
                    .where(PaperFill.order_id.in_(order_ids))
                    .order_by(PaperFill.id)).scalars().all()
                for f in all_fills:
                    fills_by_order.setdefault(f.order_id, []).append(f)
            out = []
            for o in orders:
                fills = fills_by_order.get(o.id, [])
                out.append({
                    "id": o.id, "symbol": o.symbol, "side": o.side,
                    "account_id": o.account_id, "strategy_version": o.strategy_version,
                    "model_version": o.model_version, "signal_id": o.signal_id,
                    "backtest_id": o.backtest_id, "run_id": o.run_id,
                    "price_mode": o.price_mode, "limit_price": o.limit_price,
                    "reserved_cash": o.reserved_cash,
                    "algo": o.algo, "order_amount": o.order_amount,
                    "filled_amount": round(o.filled_amount, 2),
                    "split_days": o.split_days, "status": o.status,
                    "decision_price": o.decision_price,
                    "reject_reason": o.reject_reason,
                    "created_at": str(o.created_at or ""),
                    "fills": [{
                        "exec_date": str(f.exec_date), "qty": f.qty,
                        "price": round(f.price, 4), "amount": round(f.amount, 2),
                        "fee": round(f.fee, 2), "impact_bps": round(f.impact_bps, 2),
                        "participation": round(f.participation, 5),
                        "basis_bps": round(f.basis_bps, 1),
                    } for f in fills],
                })
        return {"items": out, "total": total, "returned": len(out),
                "limit": limit, "truncated": total > len(out)}
    return ok(await run_in_compute_pool(_run))


@router.get("/account", response_model=APIResponse[dict])
async def paper_account(
    account_id: str = "paper_default",
    _user: dict = Depends(require_role("researcher")),
) -> APIResponse[dict]:
    """模拟盘账户（全部由真实成交推导：现金/持仓/费用/冲击）。"""
    return ok(await run_in_compute_pool(
        lambda: _run_account(account_id)))


def _run_account(account_id: str = "paper_default") -> dict:
    with _db() as session:
        return paper.account_summary(session, account_id)


# ==================== 策略容量与业绩归因 ====================
@router.get("/capacity", response_model=APIResponse[dict])
async def capacity(
    participation_cap: float = 0.01, holdings: int = 20,
    rebalance_per_year: int = 12,
    _user: dict = Depends(require_role("researcher")),
) -> APIResponse[dict]:
    """策略容量：真实 ADV 中位数推导，公式透明。"""
    def _run() -> dict:
        s = get_settings()
        files = sorted((s.DATA_ROOT / "universe_daily").rglob("*.parquet"))
        if not files:
            raise AQPException(ERR_DATA_EMPTY, "universe_daily 不存在")
        uni = pl.concat([pl.read_parquet(f) for f in files[-1:]],
                        how="diagonal_relaxed")
        # [AQP 第 10 轮] 成交额必须由 close×volume 得出：缺任一列时原实现抛
        # ColumnNotFoundError → 裸 50000。此处与下方"成交额不可得"同一口径，
        # 报 ERR_DATA_EMPTY 并点名缺列（`require_columns` 消息含列名清单）。
        require_columns(uni, ("close", "volume"),
                        dataset="universe_daily", context=str(files[-1].name))
        amt = (uni["close"] * uni["volume"]).drop_nulls()
        if amt.len() == 0:
            raise AQPException(ERR_DATA_EMPTY, "universe 成交额不可得")
        return strategy_capacity(as_py_float(amt.median()), participation_cap,
                                 holdings, rebalance_per_year)
    return ok(await run_in_compute_pool(_run))


class AttributionRequest(BaseModel):
    assets: list[dict] = Field(..., min_length=1, max_length=100,
                               description="[{code, weight}]")
    window_days: int = Field(120, ge=30, le=500)
    # 基准类型：universe 等权（默认，保持原行为）/ 自定义组合 / 单一标的
    benchmark_type: str = Field(
        "universe_equal",
        pattern=r"^(universe_equal|custom_portfolio|single_symbol)$")
    # benchmark_type=custom_portfolio 时必填：[{code, weight}]
    benchmark_assets: list[dict] = Field(default_factory=list, max_length=100,
                                         description="自定义基准组合")
    # benchmark_type=single_symbol 时必填：标的代码
    benchmark_symbol: str | None = Field(None, min_length=1, max_length=16)
    target_aum: float = Field(5e7, ge=1e4, le=1e11, description="组合容量测算目标 AUM(元)")
    participation_cap: float = Field(0.10, ge=0.001, le=0.50, description="流动性变现参与率上限")
    liquidation_days_target: int = Field(3, ge=1, le=30, description="目标变现天数约束")


class PortfolioCapacityRequest(BaseModel):
    assets: list[dict] = Field(..., min_length=1, max_length=100,
                               description="[{code, weight}]")
    target_aum: float = Field(5e7, ge=1e4, le=1e11, description="组合容量测算目标 AUM(元)")
    participation_cap: float = Field(0.10, ge=0.001, le=0.50, description="流动性变现参与率上限")
    liquidation_days_target: int = Field(3, ge=1, le=30, description="目标变现天数约束")


@router.post("/portfolio-capacity", response_model=APIResponse[dict])
async def portfolio_capacity_api(req: PortfolioCapacityRequest,
                                 _user: dict = Depends(require_role("researcher"))) -> APIResponse[dict]:
    """快速持仓流动性短板与变现天数（DtL）测算（毫秒级响应，无需全量风格回归）。"""
    def _run() -> dict:
        s = get_settings()
        codes = [paper._norm_symbol(a["code"]) for a in req.assets]  # noqa: SLF001
        weights = {c: float(a.get("weight", 0)) for c, a in zip(codes, req.assets)}
        tot = sum(weights.values()) or 1.0
        weights = {c: w / tot for c, w in weights.items()}

        adv_map: dict[str, float] = {}
        for sym in codes:
            df = read_symbol_dataset("daily_bar_hfq", sym)
            if not df.is_empty():
                if "amount" in df.columns:
                    amt_s = df["amount"].drop_nulls()
                elif "volume" in df.columns:
                    amt_s = (df["close"] * df["volume"]).drop_nulls()
                else:
                    amt_s = None
                adv_map[sym] = as_py_float(amt_s.tail(20).mean()) if amt_s is not None and amt_s.len() > 0 else 5e7

        ufiles = sorted((s.DATA_ROOT / "universe_daily").rglob("*.parquet"))
        uni = read_parquet_columns(ufiles, ["date", "symbol", "name", "industry"])
        if uni.is_empty():
            names = {}
            industry = {}
        else:
            if uni.schema["date"] != pl.Date:
                uni = uni.with_columns(pl.col("date").cast(pl.Date))
            uni_last = uni.filter(pl.col("date") == uni["date"].max())
            names = dict(zip(uni_last["symbol"], uni_last["name"])) if "name" in uni_last.columns else {}
            industry = dict(zip(uni_last["symbol"], uni_last["industry"]))

        return portfolio_liquidity_bottleneck(
            weights=weights,
            adv_map=adv_map,
            target_aum=req.target_aum,
            participation_cap=req.participation_cap,
            liquidation_days_target=req.liquidation_days_target,
            names=names,
            industry=industry,
        )
    return ok(await run_in_compute_pool(_run))


@router.post("/attribution", response_model=APIResponse[dict])
async def attribution(req: AttributionRequest,
                      _user: dict = Depends(require_role("researcher")),
                      _compute: None = Depends(compute_slot)) -> APIResponse[dict]:
    """Brinson 行业归因（单期+多期连乘平滑）+ 风格回归与风险分解 + 持仓流动性短板穿透。"""
    def _run() -> dict:
        s = get_settings()
        codes = [paper._norm_symbol(a["code"]) for a in req.assets]  # noqa: SLF001
        weights = {c: float(a.get("weight", 0)) for c, a in zip(codes, req.assets)}
        tot = sum(weights.values()) or 1.0
        weights = {c: w / tot for c, w in weights.items()}

        closes: dict[str, "pl.DataFrame"] = {}
        adv_map: dict[str, float] = {}
        for sym in codes:
            df = read_symbol_dataset("daily_bar_hfq", sym)
            if not df.is_empty():
                if df.schema["date"] != pl.Date:
                    df = df.with_columns(pl.col("date").cast(pl.Date))
                closes[sym] = df.sort("date").select(["date", "close"])
                if "amount" in df.columns:
                    amt_s = df["amount"].drop_nulls()
                elif "volume" in df.columns:
                    amt_s = (df["close"] * df["volume"]).drop_nulls()
                else:
                    amt_s = None
                adv_map[sym] = as_py_float(amt_s.tail(20).mean()) if amt_s is not None and amt_s.len() > 0 else 5e7

        if not closes:
            raise AQPException(ERR_DATA_EMPTY, "所选资产无本地 hfq 行情")
        close_pd = None
        import pandas as pd
        close_pd = pd.DataFrame(
            {sym: df.to_pandas().set_index("date")["close"]
             for sym, df in closes.items()}).sort_index().tail(req.window_days + 1)
        rets_wide = close_pd.pct_change().dropna()
        if len(rets_wide) < 30:
            raise AQPException(ERR_DATA_EMPTY, "有效收益样本不足（<30 日）")

        # 行业映射（Brinson 用）＋ universe 等权基准：universe 最新真实截面
        ufiles = sorted((s.DATA_ROOT / "universe_daily").rglob("*.parquet"))
        uni = read_parquet_columns(ufiles, ["date", "symbol", "name", "industry", "close"])
        if uni.is_empty():
            uni = read_parquet_columns(ufiles, ["date", "symbol", "industry", "close"])
        if uni.is_empty():
            industry = {}
            names = {}
        else:
            if uni.schema["date"] != pl.Date:
                uni = uni.with_columns(pl.col("date").cast(pl.Date))
            uni_last = uni.filter(pl.col("date") == uni["date"].max())
            industry = dict(zip(uni_last["symbol"], uni_last["industry"]))
            names = dict(zip(uni_last["symbol"], uni_last["name"])) if "name" in uni_last.columns else {}

        port_ret = rets_wide.mul(
            pd.Series({c: weights.get(c, 0.0) for c in rets_wide.columns}),
            axis=1).sum(axis=1)

        # ---- 基准收益（三选一：universe 等权 / 自定义组合 / 单一标的） ----
        if req.benchmark_type == "custom_portfolio":
            if not req.benchmark_assets:
                raise AQPException(
                    ERR_DATA_EMPTY, "custom_portfolio 需提供 benchmark_assets")
            b_codes = [paper._norm_symbol(a["code"])  # noqa: SLF001
                       for a in req.benchmark_assets]
            b_w: dict[str, float] = {}
            for c, a in zip(b_codes, req.benchmark_assets):
                b_w[c] = b_w.get(c, 0.0) + float(a.get("weight", 0))
            b_tot = sum(b_w.values())
            if b_tot <= 0:
                raise AQPException(ERR_DATA_EMPTY, "benchmark_assets 权重和须为正")
            b_w = {c: w / b_tot for c, w in b_w.items()}
            b_closes: dict[str, pl.DataFrame] = {}
            for sym in b_w:
                df = read_symbol_dataset("daily_bar_hfq", sym)
                if not df.is_empty():
                    if df.schema["date"] != pl.Date:
                        df = df.with_columns(pl.col("date").cast(pl.Date))
                    b_closes[sym] = df.sort("date").select(["date", "close"])
            if not b_closes:
                raise AQPException(ERR_DATA_EMPTY, "基准组合无本地 hfq 行情")
            bench_close = pd.DataFrame(
                {sym: df.to_pandas().set_index("date")["close"]
                 for sym, df in b_closes.items()}).sort_index() \
                .ffill().reindex(rets_wide.index).ffill()
            bench_daily = bench_close.pct_change() \
                .mul(pd.Series({c: b_w.get(c, 0.0)
                                for c in bench_close.columns}), axis=1) \
                .sum(axis=1).dropna()
            bench_desc = f"自定义基准组合（{len(b_closes)} 只，按权重加权）"
        elif req.benchmark_type == "single_symbol":
            if not req.benchmark_symbol:
                raise AQPException(
                    ERR_DATA_EMPTY, "single_symbol 需提供 benchmark_symbol")
            b_sym = paper._norm_symbol(req.benchmark_symbol)  # noqa: SLF001
            df = read_symbol_dataset("daily_bar_hfq", b_sym)
            if df.is_empty():
                raise AQPException(ERR_DATA_EMPTY, f"{b_sym} 无本地 hfq 行情")
            if df.schema["date"] != pl.Date:
                df = df.with_columns(pl.col("date").cast(pl.Date))
            bench_close = df.sort("date").to_pandas() \
                .set_index("date")[["close"]] \
                .rename(columns={"close": b_sym}) \
                .ffill().reindex(rets_wide.index).ffill()
            bench_daily = bench_close[b_sym].pct_change().dropna()
            bench_desc = f"单一基准标的 {b_sym}（后复权）"
        else:
            # 默认（原行为）：本地全 universe 等权（真实截面）
            if uni.is_empty() or "date" not in uni.columns:
                raise AQPException(ERR_DATA_EMPTY, "全市场基准 universe_daily 数据为空")
            bench_ret = uni.select(["date", "symbol", "close"]).to_pandas()
            bench_ret["date"] = pd.to_datetime(bench_ret["date"])
            bench_close = bench_ret.pivot_table(
                index="date", columns="symbol", values="close",
                aggfunc="last").ffill().reindex(rets_wide.index).ffill()
            bench_daily = bench_close.pct_change().mean(axis=1).dropna()
            bench_desc = f"本地 universe 等权（{bench_close.shape[1]} 只）"

        bench_win = float((1 + bench_daily).prod() - 1)
        port_win = float((1 + port_ret).prod() - 1)

        # 单期与多期 Brinson：
        sym_ret = (close_pd.iloc[-1] / close_pd.iloc[0] - 1.0)
        bench_sym_ret = (bench_close.iloc[-1] / bench_close.iloc[0] - 1.0).dropna()
        if bench_sym_ret.size == 0:
            raise AQPException(
                ERR_DATA_EMPTY,
                "基准收益序列为空（基准数据与组合窗口无重叠交易日）")

        if req.benchmark_type == "custom_portfolio":
            all_ret = sym_ret.combine_first(bench_sym_ret)
            bench_w = pd.Series({c: b_w.get(c, 0.0)
                                 for c in bench_close.columns}).fillna(0.0)
        elif req.benchmark_type == "single_symbol":
            all_ret = sym_ret.combine_first(bench_sym_ret)
            bench_w = pd.Series({b_sym: 1.0})
        else:
            all_ret = sym_ret.combine_first(bench_sym_ret)
            bench_w = pd.Series(1.0 / bench_sym_ret.size, index=bench_sym_ret.index)

        ind_series = pd.Series({sym: industry.get(sym) or "未分类" for sym in all_ret.index})

        # 1. 经典单期 Brinson-Fachler
        brinson = brinson_attribution(
            portfolio_w=weights,
            benchmark_w=bench_w.to_dict(),
            returns=all_ret,
            industry=ind_series)

        # 2. 多期 Menchero / Carino 复合归因与时序曲线
        bench_daily_rets_df = bench_close.pct_change().dropna()
        multi_period = multi_period_brinson_attribution(
            port_rets_wide=rets_wide,
            bench_rets_wide=bench_daily_rets_df,
            port_weights=weights,
            bench_weights=bench_w.to_dict(),
            industry=ind_series,
        )

        # 3. 个股微观贡献穿透（Top 5 赢家与拖累）
        stock_contribs = stock_level_contributions(
            port_rets_wide=rets_wide,
            port_weights=weights,
            bench_cum_ret=bench_win,
            industry=ind_series,
            names=names,
        )

        # 4. 持仓流动性短板与锁喉瓶颈分析
        liquidity_bottleneck = portfolio_liquidity_bottleneck(
            weights=weights,
            adv_map=adv_map,
            target_aum=req.target_aum,
            participation_cap=req.participation_cap,
            liquidation_days_target=req.liquidation_days_target,
            names=names,
            industry=industry,
        )

        # 5. Barra 风格回归与跟踪误差风险分解
        from ...api.v1.research import _load_features
        from ...domain.research import STYLE_FACTOR_MAP
        feat = _load_features(days=req.window_days + 40)
        style_rets: dict[str, pd.Series] = {}
        for style, col in STYLE_FACTOR_MAP.items():
            if col not in feat.columns:
                continue
            zw = feat.pivot_table(index="date", columns="symbol",
                                  values=col, aggfunc="last")
            mu = zw.mean(axis=1)
            sd = zw.std(axis=1)
            zw = zw.sub(mu, axis=0).div(sd.replace(0, np.nan), axis=0)
            sr = style_factor_returns(zw, rets_wide)
            if len(sr) >= 30:
                style_rets[style] = sr
        style_attr = style_regression_attribution(port_ret, style_rets, bench_ret=bench_daily)

        return {
            "window_days": req.window_days,
            "n_obs": int(len(rets_wide)),
            "portfolio_return": round(port_win, 6),
            "benchmark_return": round(bench_win, 6),
            "excess_return": round(port_win - bench_win, 6),
            "brinson": brinson,
            "multi_period_brinson": multi_period,
            "stock_contributions": stock_contribs,
            "liquidity_bottleneck": liquidity_bottleneck,
            "style": style_attr,
            "benchmark_desc": bench_desc,
            "benchmark_policy": {
                "type": req.benchmark_type,
                "weight_basis": (
                    "equal_weight_all_available_universe_symbols"
                    if req.benchmark_type == "universe_equal"
                    else "request_defined_weights"
                ),
                "symbol_count": int(bench_sym_ret.size),
                "as_of": str(bench_close.index[-1].date()),
            },
        }
    return ok(await run_in_compute_pool(_run))


@router.get("/tca", response_model=APIResponse[dict])
async def transaction_cost_analysis(
    account_id: str = "paper_default",
    days: int = Query(60, ge=1, le=365),
    _user: dict = Depends(require_role("researcher")),
) -> APIResponse[dict]:
    """TCA 交易成本与执行落差归因（Perold Implementation Shortfall）。

    基于真实模拟母单与撮合明细，分解：直接税费、市场冲击、时机价格漂移与总执行落差。
    """
    def _run() -> dict:
        with _db() as session:
            orders = session.execute(
                sa.select(PaperOrder).where(PaperOrder.account_id == account_id).order_by(PaperOrder.id.desc()).limit(300)
            ).scalars().all()
            fills = session.execute(
                sa.select(PaperFill).where(PaperFill.account_id == account_id)
                .order_by(PaperFill.id.desc()).limit(1000)
            ).scalars().all()

            if not fills:
                return {
                    "has_data": False,
                    "total_orders": len(orders),
                    "total_fills": 0,
                    "message": "暂无真实撮合订单数据（本地采用真实行情模拟撮合，请在上方下单后触发撮合）",
                    "summary": {
                        "total_volume": 0.0,
                        "total_fee": 0.0,
                        "fee_bps": 0.0,
                        "avg_impact_bps": 0.0,
                        "delay_bps": 0.0,
                        "avg_basis_bps": 0.0,
                        "total_shortfall_bps": 0.0,
                        "waterfall": [
                            {"name": "规费/佣金 (Fee)", "value": 0.0},
                            {"name": "时机延迟 (Delay)", "value": 0.0},
                            {"name": "冲击成本 (Impact)", "value": 0.0},
                            {"name": "总执行落差 (Total IS)", "value": 0.0},
                        ],
                    },
                    "algo_breakdown": [],
                    "recent_executions": [],
                }

            total_volume = sum(f.amount for f in fills)
            total_fee = sum(f.fee for f in fills)
            fee_bps = (total_fee / total_volume * 10000) if total_volume > 0 else 0.0
            avg_impact_bps = float(np.mean([f.impact_bps for f in fills])) if fills else 0.0
            avg_basis_bps = float(np.mean([f.basis_bps for f in fills])) if fills else 0.0
            delay_bps = avg_basis_bps - avg_impact_bps

            order_algo_map = {o.id: o.algo for o in orders}
            algo_stats: dict[str, dict] = {}
            for f in fills:
                algo = order_algo_map.get(f.order_id, "market")
                if algo not in algo_stats:
                    algo_stats[algo] = {
                        "algo": algo,
                        "count": 0,
                        "volume": 0.0,
                        "impact_bps_list": [],
                        "basis_bps_list": [],
                    }
                algo_stats[algo]["count"] += 1
                algo_stats[algo]["volume"] += f.amount
                algo_stats[algo]["impact_bps_list"].append(f.impact_bps)
                algo_stats[algo]["basis_bps_list"].append(f.basis_bps)

            algo_list = []
            for a, s in algo_stats.items():
                algo_list.append({
                    "algo": a,
                    "fill_count": s["count"],
                    "volume": round(s["volume"], 2),
                    "avg_impact_bps": round(float(np.mean(s["impact_bps_list"])), 2),
                    "avg_basis_bps": round(float(np.mean(s["basis_bps_list"])), 2),
                })

            recent_ex = []
            for f in fills[:15]:
                recent_ex.append({
                    "id": f.id,
                    "order_id": f.order_id,
                    "symbol": f.symbol,
                    "side": f.side,
                    "exec_date": str(f.exec_date),
                    "price": round(f.price, 3),
                    "qty": f.qty,
                    "amount": round(f.amount, 2),
                    "fee": round(f.fee, 2),
                    "impact_bps": round(f.impact_bps, 2),
                    "basis_bps": round(f.basis_bps, 2),
                })

            return {
                "has_data": True,
                "total_orders": len(orders),
                "total_fills": len(fills),
                "summary": {
                    "total_volume": round(total_volume, 2),
                    "total_fee": round(total_fee, 2),
                    "fee_bps": round(fee_bps, 2),
                    "avg_impact_bps": round(avg_impact_bps, 2),
                    "delay_bps": round(delay_bps, 2),
                    "avg_basis_bps": round(avg_basis_bps, 2),
                    "total_shortfall_bps": round(fee_bps + avg_basis_bps, 2),
                    "waterfall": [
                        {"name": "规费/佣金 (Fee)", "value": round(fee_bps, 2)},
                        {"name": "时机延迟 (Delay)", "value": round(delay_bps, 2)},
                        {"name": "冲击成本 (Impact)", "value": round(avg_impact_bps, 2)},
                        {"name": "总执行落差 (Total IS)", "value": round(fee_bps + avg_basis_bps, 2)},
                    ],
                },
                "algo_breakdown": sorted(algo_list, key=lambda x: -x["volume"]),
                "recent_executions": recent_ex,
            }

    return ok(await run_in_compute_pool(_run))


@router.get("/capacity-curve", response_model=APIResponse[dict])
async def capacity_curve_api(
    participation_cap: float = 0.01,
    holdings: int = 20,
    rebalance_per_year: int = 12,
    gross_return: float = 0.20,
    gross_vol: float = 0.18,
    _user: dict = Depends(require_role("researcher")),
) -> APIResponse[dict]:
    """平方根冲击容量衰减曲面与最优黄金 AUM 测算。"""
    def _run() -> dict:

        s = get_settings()
        files = sorted((s.DATA_ROOT / "universe_daily").rglob("*.parquet"))
        if not files:
            raise AQPException(ERR_DATA_EMPTY, "universe_daily 不存在")
        uni = pl.concat([pl.read_parquet(f) for f in files[-1:]],
                        how="diagonal_relaxed")
        require_columns(uni, ("close", "volume"),
                        dataset="universe_daily", context=str(files[-1].name))
        amt = (uni["close"] * uni["volume"]).drop_nulls()
        if amt.len() == 0:
            raise AQPException(ERR_DATA_EMPTY, "universe 成交额不可得")
        adv_median = as_py_float(amt.median())
        return capacity_decay_curve(
            adv_median=adv_median,
            participation_cap=participation_cap,
            holdings=holdings,
            rebalance_per_year=rebalance_per_year,
            gross_return=gross_return,
            gross_vol=gross_vol,
        )

    return ok(await run_in_compute_pool(_run))


# ---------- S2-1: 条件单与网格交易机器人 ----------
class ConditionalOrderRequest(BaseModel):
    account_id: str = Field("paper_default", min_length=1, max_length=64)
    symbol: str
    side: str = Field(..., pattern=r"^(buy|sell)$")
    trigger_type: str = Field("price_ge", pattern=r"^(price_ge|price_le)$")
    trigger_price: float = Field(..., gt=0)
    order_amount: float = Field(..., gt=0)
    order_type: str = Field("market", pattern=r"^(market|limit)$")
    algo_type: str = Field("market", pattern=r"^(market|vwap|twap)$")


class GridBotRequest(BaseModel):
    account_id: str = Field("paper_default", min_length=1, max_length=64)
    symbol: str
    lower_price: float = Field(..., gt=0)
    upper_price: float = Field(..., gt=0)
    grid_count: int = Field(10, ge=2, le=100)
    amount_per_grid: float = Field(10000.0, gt=0)


async def _conditional_operation(fn, *args, **kwargs):
    def _run():
        with _db() as session:
            try:
                return fn(session, *args, **kwargs)
            except ValueError as exc:
                raise AQPException(ERR_PARAMS, str(exc)) from exc
    return await run_in_compute_pool(_run)


@router.get("/conditional-orders", response_model=APIResponse[list])
async def list_conditional_orders_api(
    status: str | None = None, account_id: str = "paper_default",
    _user: dict = Depends(require_role("researcher")),
) -> APIResponse[list]:
    from ...trading.conditional import list_conditional_orders
    return ok(await _conditional_operation(list_conditional_orders, status=status, account_id=account_id))


@router.post("/conditional-orders", response_model=APIResponse[dict])
async def create_conditional_order_api(
    req: ConditionalOrderRequest, _user: dict = Depends(require_role("researcher")),
) -> APIResponse[dict]:
    from ...trading.conditional import create_conditional_order
    return ok(await _conditional_operation(create_conditional_order, **req.model_dump(), user_id=_user.get("sub", "default")))


@router.post("/conditional-orders/{order_id}/cancel", response_model=APIResponse[dict])
async def cancel_conditional_order_api(
    order_id: int, account_id: str = "paper_default", _user: dict = Depends(require_role("researcher")),
) -> APIResponse[dict]:
    from ...trading.conditional import cancel_conditional_order
    if not await _conditional_operation(cancel_conditional_order, order_id, account_id):
        raise AQPException(ERR_NOT_FOUND, "条件单不存在或不可撤销")
    return ok({"order_id": order_id, "status": "CANCELLED"})


@router.get("/grid-bots", response_model=APIResponse[list])
async def list_grid_bots_api(
    status: str | None = None, account_id: str = "paper_default", _user: dict = Depends(require_role("researcher")),
) -> APIResponse[list]:
    from ...trading.conditional import list_grid_bots
    return ok(await _conditional_operation(list_grid_bots, status=status, account_id=account_id))


@router.post("/grid-bots", response_model=APIResponse[dict])
async def create_grid_bot_api(
    req: GridBotRequest, _user: dict = Depends(require_role("researcher")),
) -> APIResponse[dict]:
    from ...trading.conditional import create_grid_bot
    return ok(await _conditional_operation(create_grid_bot, **req.model_dump(), user_id=_user.get("sub", "default")))


@router.post("/grid-bots/{bot_id}/stop", response_model=APIResponse[dict])
async def stop_grid_bot_api(
    bot_id: int, account_id: str = "paper_default", _user: dict = Depends(require_role("researcher")),
) -> APIResponse[dict]:
    from ...trading.conditional import stop_grid_bot
    if not await _conditional_operation(stop_grid_bot, bot_id, account_id):
        raise AQPException(ERR_NOT_FOUND, "网格机器人不存在或已停止")
    return ok({"bot_id": bot_id, "status": "STOPPED"})


@router.post("/conditional-orders/evaluate", response_model=APIResponse[dict])
async def evaluate_conditional_and_grid_api(
    account_id: str = "paper_default", _user: dict = Depends(require_role("researcher")),
) -> APIResponse[dict]:
    from ...data.quotes_hub import quotes_snapshot
    from ...db.models import ConditionalOrder, GridBot
    from ...trading.conditional import evaluate_conditional_orders, evaluate_grid_bots
    def _symbols():
        with _db() as session:
            return list(set(session.scalars(sa.select(ConditionalOrder.symbol).where(
                ConditionalOrder.status == "ACTIVE", ConditionalOrder.account_id == account_id))) |
                set(session.scalars(sa.select(GridBot.symbol).where(
                    GridBot.status == "RUNNING", GridBot.account_id == account_id))))
    snap = await quotes_snapshot(await run_in_compute_pool(_symbols))
    quotes_map = {q["symbol"]: float(q["price"]) for q in snap.get("quotes", []) if q.get("price")}
    def _evaluate(session):
        return {"quotes_scanned": len(quotes_map),
                "triggered_conditional_orders": evaluate_conditional_orders(session, quotes_map, account_id),
                "triggered_grid_trades": evaluate_grid_bots(session, quotes_map, account_id)}
    return ok(await _conditional_operation(_evaluate))
