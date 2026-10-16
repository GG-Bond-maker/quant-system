"""期货（衍生品）中心与量化分析 API 端点。

对标机构级期货投研系统（Wind / Bloomberg / JoinQuant / TradingView）：
1. 主力合约全景行情与分类筛选（股指、国债、贵金属、黑色、有色、能化、农产品）；
2. 基差与年化升贴水率监控看板；
3. 期限结构（Term Structure: 近月/下月/当季/隔季 Contango 与 Backwardation 曲线）；
4. 期货日 K 线行情；
5. 期货 CTA 双向动量/做空对冲回测（含从区间高点计算的回测表、水下回撤、多空独立胜率）；
6. 保证金占用与股票组合 Beta 套期保值手数计算器。
"""
from __future__ import annotations

from typing import Any
from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel, Field

from ...core.auth import require_role
from ...core.compute_pool import run_in_compute_pool
from ...core.errors import APIResponse, ERR_PARAMS, fail, ok
from ...data.futures import (
    get_basis_history,
    get_futures_calendar_spread,
    get_futures_cot,
    get_futures_instrument_profile,
    get_futures_kline,
    get_futures_market_snapshot,
    get_futures_term_structure,
    get_inter_commodity_spread,
    get_parameter_sensitivity,
    get_roll_yield_matrix,
    run_futures_cta_backtest,
)
from ...domain.futures_rules import (
    FUTURES_SPECS,
    calc_contract_value,
    calc_hedge_contracts,
    calc_margin,
    get_futures_spec,
)

router = APIRouter()


@router.get("/contracts")
async def get_contracts(
    category: str | None = Query(None, description="品种大类过滤：index/bond/precious/metal/ferrous/energy/agri/all"),
    _user: dict = Depends(require_role("viewer")),
) -> APIResponse[dict[str, Any]]:
    """获取期货全市场主力合约看板（compute_pool 异步隔离，保障事件循环高吞吐）。"""
    snapshot = await run_in_compute_pool(get_futures_market_snapshot)
    items = snapshot.get("items", [])
    if category and category != "all":
        items = [it for it in items if it.get("category") == category]
        snapshot["items"] = items
        snapshot["count"] = len(items)
    return ok(snapshot)


@router.get("/basis")
async def get_basis_overview(
    category: str | None = Query("index", description="品种分类：index/all"),
    _user: dict = Depends(require_role("viewer")),
) -> APIResponse[dict[str, Any]]:
    """基差与年化升贴水率监控看板。"""
    snapshot = await run_in_compute_pool(get_futures_market_snapshot)
    items = snapshot.get("items", [])
    if category and category != "all":
        items = [it for it in items if it.get("category") == category]

    # 按年化升贴水率排序
    basis_items = sorted(items, key=lambda x: x.get("annualized_basis_rate", 0.0), reverse=True)
    return ok({
        "as_of": snapshot.get("as_of"),
        "degraded": snapshot.get("degraded", False),
        "count": len(basis_items),
        "items": basis_items,
    })


@router.get("/term-structure")
async def get_term_structure(
    symbol: str = Query("IF", description="品种前缀（如 IF, IC, IM, IH, AU, RB）"),
    _user: dict = Depends(require_role("viewer")),
) -> APIResponse[dict[str, Any]]:
    """计算指定期货品种的期限结构（近月、下月、当季、隔季曲线）。"""
    res = await run_in_compute_pool(get_futures_term_structure, symbol)
    return ok(res)


@router.get("/calendar-spread")
async def get_calendar_spread(
    symbol: str = Query("IF", description="品种前缀（如 IF, IC, AU, RB）"),
    window: int = Query(20, ge=5, le=60, description="滚动统计窗口天数"),
    _user: dict = Depends(require_role("viewer")),
) -> APIResponse[dict[str, Any]]:
    """获取跨期套利价差时序与滚动统计指标（近月 vs 远月 Calendar Spread 与 Z-Score）。"""
    res = await run_in_compute_pool(get_futures_calendar_spread, symbol, window=window)
    return ok(res)


@router.get("/kline")
async def get_kline(
    symbol: str = Query("IF", description="品种前缀（如 IF, IC, AU）"),
    days: int = Query(180, ge=20, le=500, description="历史日线天数"),
    continuous: bool = Query(False, description="是否获取后复权连续合约（消除换月跳空）"),
    _user: dict = Depends(require_role("viewer")),
) -> APIResponse[list[dict[str, Any]]]:
    """获取期货主力合约或复权连续合约历史日 K 线数据。"""
    bars = await run_in_compute_pool(get_futures_kline, symbol, days=days, continuous=continuous)
    return ok(bars)


class FuturesBacktestRequest(BaseModel):
    symbol: str = Field(default="IF", description="品种代码前缀")
    strategy_type: str = Field(default="dual_ma", description="策略类型：dual_ma / donchian / hedge")
    start_date: str | None = Field(default=None, description="回测开始日期 YYYY-MM-DD")
    end_date: str | None = Field(default=None, description="回测结束日期 YYYY-MM-DD")
    init_cash: float = Field(default=1_000_000.0, ge=10_000.0, description="初始本金 (元)")
    leverage: float = Field(default=1.0, ge=0.1, le=5.0, description="杠杆倍数 (0.1~5.0)")
    fast_period: int = Field(default=5, ge=2, le=60, description="快线均线周期 / 通道上界")
    slow_period: int = Field(default=20, ge=5, le=120, description="慢线均线周期 / 通道下界")
    direction: str = Field(default="both", description="交易方向：both (双向多空) / long_only (仅多) / short_only (仅空)")
    beta: float = Field(default=1.0, ge=0.0, le=3.0, description="组合对冲 Beta 系数")
    continuous: bool = Field(default=True, description="是否采用后复权连续合约进行回测")
    slippage_ticks: float = Field(default=1.0, ge=0.0, le=10.0, description="执行滑点 (最小跳价数)")
    fee_multiplier: float = Field(default=1.0, ge=0.1, le=10.0, description="手续费应力测试倍数")
    history_days: int = Field(default=500, ge=30, le=3000, description="回测历史天数")


@router.post("/backtest")
async def run_backtest(
    req: FuturesBacktestRequest,
    _user: dict = Depends(require_role("researcher")),
) -> APIResponse[dict[str, Any]]:
    """期货 CTA 双向交易/做空盈利及股指套保回测引擎。

    返回包含：
    - 累计净值曲线 (Equity Curve)
    - 水下回撤曲线 (Drawdown Curve)
    - 从区间最高点计算的回测表（Drawdown from Peak Breakdown Table，含高点/谷底/修复周期）
    - 独立多头胜率、空头胜率、盈亏比与卡尔玛比率 (Calmar Ratio)
    - 真实滑点与微观手续费、买入持有基准相对指标（Alpha, Beta, IR）
    - 极端尾部风险 (VaR 95%, CVaR 95%) 与防过拟合指标 (PSR, DSR)
    - 二维成本应力测试矩阵 (Cost Stress Matrix)
    """
    if req.fast_period >= req.slow_period and req.strategy_type == "dual_ma":
        return fail(ERR_PARAMS, "快线周期必须小于慢线周期")

    res = await run_in_compute_pool(
        run_futures_cta_backtest,
        symbol_prefix=req.symbol,
        strategy_type=req.strategy_type,
        start_date=req.start_date,
        end_date=req.end_date,
        init_cash=req.init_cash,
        leverage=req.leverage,
        fast_period=req.fast_period,
        slow_period=req.slow_period,
        direction=req.direction,
        beta=req.beta,
        continuous=req.continuous,
        slippage_ticks=req.slippage_ticks,
        fee_multiplier=req.fee_multiplier,
        days=req.history_days,
    )
    if res.get("status") == "error":
        return fail(ERR_PARAMS, res.get("message", "回测执行失败"))
    return ok(res)


@router.get("/hedge-calc")
async def calculate_hedge(
    portfolio_value: float = Query(..., ge=1000.0, description="待对冲股票组合市值 (元)"),
    futures_price: float = Query(..., ge=1.0, description="期货参考价格"),
    symbol: str = Query("IF", description="对冲期货品种 (如 IF, IC, IM, IH)"),
    beta: float = Query(1.0, ge=0.0, le=3.0, description="组合 Beta 系数"),
    _user: dict = Depends(require_role("viewer")),
) -> APIResponse[dict[str, Any]]:
    """测算股指期货套期保值合约张数、保证金占用与有效杠杆。"""
    spec = get_futures_spec(symbol) or FUTURES_SPECS["IF"]
    contract_val = calc_contract_value(futures_price, 1, spec.multiplier)
    required_lots = calc_hedge_contracts(portfolio_value, futures_price, spec.multiplier, beta)
    total_futures_val = contract_val * required_lots
    margin_needed = calc_margin(futures_price, required_lots, spec.multiplier, spec.margin_rate)

    return ok({
        "symbol": symbol,
        "name": spec.name,
        "portfolio_value": round(portfolio_value, 2),
        "futures_price": round(futures_price, 2),
        "multiplier": spec.multiplier,
        "contract_value_per_lot": round(contract_val, 2),
        "margin_rate": spec.margin_rate,
        "beta": beta,
        "required_contracts": required_lots,
        "total_notional_value": round(total_futures_val, 2),
        "total_margin_required": round(margin_needed, 2),
        "hedge_ratio_pct": round(total_futures_val / portfolio_value * 100.0, 2) if portfolio_value > 0 else 0.0,
    })


@router.get("/cot")
async def get_cot(
    symbol: str = Query("IF", description="品种前缀（如 IF, IC, IM, IH, AU, RB）"),
    _user: dict = Depends(require_role("viewer")),
) -> APIResponse[dict[str, Any]]:
    """获取主力席位持仓龙虎榜（前 20 期货会员多空持仓与净多净空排名）。"""
    res = await run_in_compute_pool(get_futures_cot, symbol)
    return ok(res)


@router.get("/inter-commodity")
async def get_inter_commodity(
    pair: str = Query("AU_AG", description="跨品种配对代码（AU_AG, RB_I, IM_IH, STEEL_PROFIT）"),
    _user: dict = Depends(require_role("viewer")),
) -> APIResponse[dict[str, Any]]:
    """获取产业链与跨品种比价套利时序与均值回归检验（金银比、螺矿比、IM/IH风格轮动等）。"""
    res = await run_in_compute_pool(get_inter_commodity_spread, pair)
    if res.get("status") == "error":
        return fail(ERR_PARAMS, res.get("message", "无法获取跨品种比价"))
    return ok(res)


@router.get("/roll-yield-matrix")
async def get_roll_yields(
    _user: dict = Depends(require_role("viewer")),
) -> APIResponse[dict[str, Any]]:
    """获取全市场主力期货合约年化展期收益率天梯榜与升贴水结构矩阵。"""
    res = await run_in_compute_pool(get_roll_yield_matrix)
    return ok(res)


@router.get("/basis-history")
async def get_basis_hist(
    symbol: str = Query("IF", description="品种前缀（如 IF, IC, IM, IH, AU, RB）"),
    days: int = Query(180, ge=30, le=500, description="历史天数"),
    _user: dict = Depends(require_role("viewer")),
) -> APIResponse[dict[str, Any]]:
    """获取指定品种的历史基差走势、年化升贴水率与历史百分位雷达。"""
    res = await run_in_compute_pool(get_basis_history, symbol, days=days)
    return ok(res)


@router.get("/instrument-profile")
async def get_instrument_profile(
    symbol: str = Query("IF", description="品种前缀（如 IF, IC, IM, IH, AU, RB）"),
    days: int = Query(250, ge=20, le=2000, description="K线与指标展示历史天数"),
    continuous: bool = Query(True, description="是否采用后复权连续合约"),
    _user: dict = Depends(require_role("viewer")),
) -> APIResponse[dict[str, Any]]:
    """获取单个期货品种的微观画像、基础要素规范与量价+持仓量分析数据。"""
    res = await run_in_compute_pool(get_futures_instrument_profile, symbol, days=days, continuous=continuous)
    return ok(res)


@router.get("/param-sensitivity")
async def get_param_sensitivity(
    symbol: str = Query("IF", description="品种前缀（如 IF, IC, IM, IH, AU, RB）"),
    strategy_type: str = Query("dual_ma", description="策略类型"),
    _user: dict = Depends(require_role("viewer")),
) -> APIResponse[dict[str, Any]]:
    """获取快慢均线参数敏感性网格寻优二维热力图数据。"""
    res = await run_in_compute_pool(get_parameter_sensitivity, symbol, strategy_type=strategy_type)
    return ok(res)


