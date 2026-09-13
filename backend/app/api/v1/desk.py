"""实盘/模拟盘执行中心 + 实时风控闸门 + 容量与业绩归因 API。

诚实定位：**模拟盘**（本地撮合、真实行情、真实费用、真实持久化）——
系统未接入券商柜台，不假装实盘。Kill Switch / 禁买池 / 算法分批 /
基差分析全部对模拟盘引擎真实生效。
"""
from __future__ import annotations

import asyncio
from contextlib import contextmanager

import numpy as np
import polars as pl
import sqlalchemy as sa
from fastapi import APIRouter, Depends
from loguru import logger
from pydantic import BaseModel, Field

from ...core.auth import require_role
from ...core.compute_guard import compute_slot
from ...core.config import get_settings
from ...core.errors import APIResponse, AQPException, ERR_DATA_EMPTY, ok
from ...data.parquet_store import as_py_float, read_symbol_dataset
from ...db.models import ExclusionItem, PaperFill, PaperOrder
from ...domain.attribution import (
    brindon_attribution,
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
    except Exception:
        session.rollback()
        raise
    finally:
        session.close()


# ==================== Kill Switch 控制台 ====================
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
    return ok(await asyncio.to_thread(_run))


class KillSwitchRequest(BaseModel):
    active: bool
    reason: str = Field("", max_length=200)


@router.post("/kill-switch", response_model=APIResponse[dict])
async def set_kill_switch(
    req: KillSwitchRequest,
    _user: dict = Depends(require_role("researcher")),
) -> APIResponse[dict]:
    """熔断开关：ON = 拒绝一切新订单并撤销全部未完成母单（已成交保留）。"""
    def _run() -> dict:
        with _db() as session:
            paper.set_kill_switch(session, req.active, req.reason)
            n_cancelled = paper.cancel_all(session) if req.active else 0
        logger.warning(f"[desk] kill_switch -> {req.active} "
                       f"(cancelled {n_cancelled})")
        return {"kill_switch": req.active, "cancelled_orders": n_cancelled}
    return ok(await asyncio.to_thread(_run))


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
    return ok(await asyncio.to_thread(_run))


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
    return ok(await asyncio.to_thread(_run))


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
    return ok(await asyncio.to_thread(_run))


@router.get("/exclusion/screen", response_model=APIResponse[list])
async def screen_candidates(
    _user: dict = Depends(require_role("researcher")),
) -> APIResponse[list]:
    """从 universe 最新截面筛 ST / 流动性差候选（真实 is_st 与成交额）。"""
    def _run() -> list[dict]:
        with _db() as session:
            return paper.screen_universe_candidates(session)
    return ok(await asyncio.to_thread(_run))


# ==================== 模拟盘订单 ====================
class OrderRequest(BaseModel):
    symbol: str = Field(..., min_length=1, max_length=16)
    side: str = Field("buy", pattern=r"^(buy|sell)$")
    order_amount: float = Field(..., gt=0, le=1e10)
    algo: str = Field("market", pattern=r"^(market|vwap|twap|pov)$")
    split_days: int = Field(5, ge=1, le=20)
    participation_cap: float = Field(0.05, ge=0.001, le=0.3)


@router.post("/orders", response_model=APIResponse[dict])
async def place_order(
    req: OrderRequest,
    _user: dict = Depends(require_role("researcher")),
) -> APIResponse[dict]:
    """提交母单（真实校验：kill switch / 禁买池 / 本地行情）。"""
    def _run() -> dict:
        with _db() as session:
            res = paper.place_order(
                session, symbol=req.symbol, side=req.side,
                order_amount=req.order_amount, algo=req.algo,
                split_days=req.split_days,
                participation_cap=req.participation_cap)
            if res.get("ok"):
                session.commit()
        return res
    return ok(await asyncio.to_thread(_run))


@router.post("/fills/run", response_model=APIResponse[dict])
async def run_fills(
    _user: dict = Depends(require_role("researcher")),
) -> APIResponse[dict]:
    """撮合所有到期子单（真实开盘价 + sqrt 冲击；D0+1 起成交，无未来函数）。"""
    def _run() -> dict:
        with _db() as session:
            res = paper.run_fills(session)
        logger.info(f"[desk] fills run: {res}")
        return res
    return ok(await asyncio.to_thread(_run))


@router.get("/orders", response_model=APIResponse[list])
async def list_orders(
    limit: int = 50,
    _user: dict = Depends(require_role("researcher")),
) -> APIResponse[list]:
    def _run() -> list[dict]:
        with _db() as session:
            orders = session.execute(
                sa.select(PaperOrder).order_by(PaperOrder.id.desc())
                .limit(limit)).scalars().all()
            out = []
            for o in orders:
                fills = session.execute(
                    sa.select(PaperFill).where(PaperFill.order_id == o.id)
                    .order_by(PaperFill.id)).scalars().all()
                out.append({
                    "id": o.id, "symbol": o.symbol, "side": o.side,
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
        return out
    return ok(await asyncio.to_thread(_run))


@router.get("/account", response_model=APIResponse[dict])
async def paper_account(
    _user: dict = Depends(require_role("researcher")),
) -> APIResponse[dict]:
    """模拟盘账户（全部由真实成交推导：现金/持仓/费用/冲击）。"""
    return ok(await asyncio.to_thread(
        lambda: _run_account()))


def _run_account() -> dict:
    with _db() as session:
        return paper.account_summary(session)


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
        amt = (uni["close"] * uni["volume"]).drop_nulls()
        if amt.len() == 0:
            raise AQPException(ERR_DATA_EMPTY, "universe 成交额不可得")
        return strategy_capacity(as_py_float(amt.median()), participation_cap,
                                 holdings, rebalance_per_year)
    return ok(await asyncio.to_thread(_run))


class AttributionRequest(BaseModel):
    assets: list[dict] = Field(..., min_length=1, max_length=20,
                               description="[{code, weight}]")
    window_days: int = Field(120, ge=40, le=500)
    # 基准类型：universe 等权（默认，保持原行为）/ 自定义组合 / 单一标的
    benchmark_type: str = Field(
        "universe_equal",
        pattern=r"^(universe_equal|custom_portfolio|single_symbol)$")
    # benchmark_type=custom_portfolio 时必填：[{code, weight}]
    benchmark_assets: list[dict] = Field(default_factory=list, max_length=50,
                                         description="自定义基准组合")
    # benchmark_type=single_symbol 时必填：标的代码
    benchmark_symbol: str | None = Field(None, min_length=1, max_length=16)


@router.post("/attribution", response_model=APIResponse[dict])
async def attribution(req: AttributionRequest,
                      _user: dict = Depends(require_role("researcher")),
                      _compute: None = Depends(compute_slot)) -> APIResponse[dict]:
    """Brinson 行业归因 + 风格回归归因（真实收益 + universe 真实 industry）。"""
    def _run() -> dict:
        s = get_settings()
        codes = [paper._norm_symbol(a["code"]) for a in req.assets]  # noqa: SLF001
        weights = {c: float(a.get("weight", 0)) for c, a in zip(codes, req.assets)}
        tot = sum(weights.values()) or 1.0
        weights = {c: w / tot for c, w in weights.items()}

        closes: dict[str, "pl.DataFrame"] = {}
        for sym in codes:
            df = read_symbol_dataset("daily_bar_hfq", sym)
            if not df.is_empty():
                if df.schema["date"] != pl.Date:
                    df = df.with_columns(pl.col("date").cast(pl.Date))
                closes[sym] = df.sort("date").select(["date", "close"])
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

        # 行业映射（Brinson 用）：universe 最新真实截面
        ufiles = sorted((s.DATA_ROOT / "universe_daily").rglob("*.parquet"))
        uni = pl.concat([pl.read_parquet(f) for f in ufiles],
                        how="diagonal_relaxed")
        if uni.schema["date"] != pl.Date:
            uni = uni.with_columns(pl.col("date").cast(pl.Date))
        uni_last = uni.filter(pl.col("date") == uni["date"].max())
        industry = dict(zip(uni_last["symbol"], uni_last["industry"]))

        port_ret = rets_wide.mul(
            pd.Series({c: weights.get(c, 0.0) for c in rets_wide.columns}),
            axis=1).sum(axis=1)

        # ---- 基准收益（三选一：universe 等权 / 自定义组合 / 单一标的） ----
        #
        # ⚠️ reindex 之后**必须再 ffill 一次**（2026-09-11 补测发现的 P1 除零根因）：
        # 基准源（universe_daily）可能比组合资产（daily_bar_hfq）**旧**，
        # 先 ffill 只能填源索引内部的空洞；`reindex(rets_wide.index)` 落在源数据
        # 最新日期之后的那些行会整行变 NaN —— 下游 `iloc[-1] / iloc[0]` 随即全 NaN，
        # `dropna()` 后 size=0，`1.0 / size` 抛 ZeroDivisionError（HTTP 200 + 50000）。
        # 正确顺序：先 reindex 选出目标行，再 ffill 把最后已知值顺延到新日期。
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
            bench_ret = uni.select(["date", "symbol", "close"]).to_pandas()
            bench_ret["date"] = pd.to_datetime(bench_ret["date"])
            bench_close = bench_ret.pivot_table(
                index="date", columns="symbol", values="close",
                aggfunc="last").ffill().reindex(rets_wide.index).ffill()
            bench_daily = bench_close.pct_change().mean(axis=1).dropna()
            bench_desc = f"本地 universe 等权（{bench_close.shape[1]} 只）"
        bench_win = float((1 + bench_daily).prod() - 1)
        port_win = float((1 + port_ret).prod() - 1)

        # 单期 Brinson：窗口收益 per symbol vs 基准 per industry
        sym_ret = (close_pd.iloc[-1] / close_pd.iloc[0] - 1.0)
        bench_sym_ret = (bench_close.iloc[-1] / bench_close.iloc[0] - 1.0).dropna()
        # 兜底：基准与组合窗口无重叠交易日时 bench_sym_ret 会为空，
        # 下面 `1.0 / bench_sym_ret.size` 会抛 ZeroDivisionError（HTTP 200 + 50000）。
        # 宁可如实报"无数据"，也不要未分类异常。
        if bench_sym_ret.size == 0:
            raise AQPException(
                ERR_DATA_EMPTY,
                "基准收益序列为空（基准数据与组合窗口无重叠交易日）")
        if req.benchmark_type == "custom_portfolio":
            # 基准权重 = 自定义权重；收益并入基准独有标的（无收益的丢弃）
            all_ret = sym_ret.combine_first(bench_sym_ret)
            bench_w = pd.Series({c: b_w.get(c, 0.0)
                                 for c in bench_close.columns}).fillna(0.0)
        elif req.benchmark_type == "single_symbol":
            all_ret = sym_ret.combine_first(bench_sym_ret)
            bench_w = pd.Series({b_sym: 1.0})
        else:
            # universe_equal 的基准是窗口内所有有有效收益的基准成分，
            # 不能只保留组合持仓，否则组合是基准子集时权重会悄悄小于 1。
            all_ret = sym_ret.combine_first(bench_sym_ret)
            bench_w = pd.Series(1.0 / bench_sym_ret.size, index=bench_sym_ret.index)
        brinson = brindon_attribution(
            portfolio_w=weights,
            benchmark_w=bench_w.to_dict(),
            returns=all_ret,
            industry=pd.Series(
                {sym: industry.get(sym) or "未分类" for sym in all_ret.index}))

        # 风格回归：features 截面 zscore（Momentum/Volatility/Size/Reversal）
        from ...api.v1.research import _load_features  # 复用 features 加载与缓存
        from ...domain.research import STYLE_FACTOR_MAP
        feat = _load_features(days=req.window_days + 40)
        ret_long = rets_wide.stack().rename("ret").reset_index()
        ret_long.columns = ["date", "symbol", "ret"]
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
        style_attr = style_regression_attribution(port_ret, style_rets)

        return {
            "window_days": req.window_days,
            "n_obs": int(len(rets_wide)),
            "portfolio_return": round(port_win, 6),
            "benchmark_return": round(bench_win, 6),
            "excess_return": round(port_win - bench_win, 6),
            "brinson": brinson,
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
    return ok(await asyncio.to_thread(_run))
