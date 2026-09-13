"""Backtest API（P1-8）：同步最小回测（Top-K 等权，真实引擎）。"""
from __future__ import annotations

import asyncio
from datetime import date

import numpy as np
import orjson
import pandas as pd
import polars as pl
from fastapi import APIRouter, Depends
from typing import Any

from loguru import logger
from pydantic import BaseModel, Field

from ...backtest.broker import BrokerConfig
from ...backtest.engine import run_backtest
from ...cache.keys import k_backtest
from ...cache.redis_client import RedisClient
from ...core.config import get_settings
from ...core.auth import require_role
from ...core.compute_guard import compute_slot
from ...core.errors import ERR_DATA_EMPTY, APIResponse, AQPException, ok

router = APIRouter()


class BacktestRequest(BaseModel):
    model_config = __import__('pydantic').ConfigDict(protected_namespaces=())
    start: str = Field(..., pattern=r"^\d{4}-\d{2}-\d{2}$")
    end: str = Field(..., pattern=r"^\d{4}-\d{2}-\d{2}$")
    top_k: int = Field(10, ge=1, le=100)
    init_cash: float = Field(1_000_000, gt=0)
    rebalance_freq: str = Field("daily", pattern=r"^(daily|weekly)$")
    enable_friction: bool = Field(False, description="是否启用滑点/衰减/冲击成本")
    slippage_bps: float = Field(5.0)
    decay_bps: float = Field(10.0)
    impact_pct: float = Field(0.02)
    impact_linear_bps: float = Field(30.0)
    # 机构级摩擦：平方根冲击模型 + 参与率上限（流动性闸门）
    impact_model: str = Field("linear", pattern=r"^(linear|sqrt)$")
    impact_sqrt_coef_bps: float = Field(10.0, ge=0, le=200)
    max_participation: float = Field(0.0, ge=0.0, le=1.0,
                                     description="单笔订单占日成交额上限（0=不限，如 0.05=5%）")
    # 机构级组合构建：权重方案
    weighting: str = Field("equal", pattern=r"^(equal|score_weighted|risk_parity|max_div|inverse_vol)$")
    cov_window: int = Field(60, ge=20, le=250)
    weight_cap: float = Field(0.0, ge=0.0, le=1.0, description="单资产权重上限（0=不限）")
    n_trials: int = Field(1, ge=1, le=10000,
                          description="Deflated Sharpe 的多重试验次数")
    dropout_n: int = Field(0, ge=0, le=50,
                           description="TopkDropout 容忍名次（0=朴素 Top-K，>0 降换手）")
    # Task 6（整改 A-P1-2）：退市强平
    delist_haircut: float = Field(0.5, ge=0.0, le=1.0,
                                  description="退市强平折价（按最后收盘×haircut 清仓；0=关闭）")
    delist_grace_days: int = Field(60, ge=1, le=500,
                                   description="持仓退出宇宙多少个交易日后强平")
    model_version: str = Field("latest", description="信号模型版本（入缓存键）")


def _friction_config(req: BacktestRequest) -> BrokerConfig | None:
    if not req.enable_friction:
        return None
    return BrokerConfig(slippage_bps=req.slippage_bps, decay_bps=req.decay_bps,
                        impact_pct=req.impact_pct,
                        impact_linear_bps=req.impact_linear_bps,
                        impact_model=req.impact_model,
                        impact_sqrt_coef_bps=req.impact_sqrt_coef_bps,
                        max_participation=req.max_participation, enabled=True)


def _load_universe_and_signals(
    start: str, end: str, model_version: str = "latest"
) -> tuple["pl.DataFrame", "pl.DataFrame", list[str]]:
    """加载 hfq 口径回测宇宙 + predictions 并过滤区间（供回测与信号分析共用）。

    Task 1（整改）：universe 数据源从 raw 版 ``universe_daily`` 切换到
    ``universe_daily_bt``（hfq 口径，见 app.data.universe.build_universe_backtest）。
    引擎/broker 同列名零改动；除权缺口不再被当成虚假亏损。

    Task 2（整改）：信号按 model_version 隔离——``latest`` 取请求窗口内
    **信号日覆盖最广**的版本（并列取字典序最大；时间戳命名下即最新），
    否则精确匹配。跨代模型分数不可比（评分口径可能突变），混排会使
    Top-K 排序失去意义；同时避免"最新版本只有零星覆盖日"的空窗陷阱。

    :returns: (universe, signals, used_model_versions)
    """
    s = get_settings()
    uni_files = sorted((s.DATA_ROOT / "universe_daily_bt" / "symbol=__all__").glob("year=*.parquet"))
    if not uni_files:
        raise AQPException(
            ERR_DATA_EMPTY,
            "universe_daily_bt 不存在：请先构建 hfq 回测宇宙——"
            "python -c \"from app.data.universe import build_universe_backtest; "
            "build_universe_backtest()\"")
    uni = pl.concat([pl.read_parquet(f) for f in uni_files], how="diagonal_relaxed")
    dcol = uni.schema["date"]
    if dcol == pl.Utf8:
        uni = uni.with_columns(pl.col("date").str.to_date())
    elif dcol != pl.Date:
        uni = uni.with_columns(pl.col("date").cast(pl.Date))
    uni = uni.filter((pl.col("date") >= date.fromisoformat(start))
                     & (pl.col("date") <= date.fromisoformat(end)))
    if uni.is_empty():
        raise AQPException(ERR_DATA_EMPTY, "回测区间内无 universe 数据")

    pred_files = sorted((s.DATA_ROOT / "predictions").glob("date=*.parquet"))
    if not pred_files:
        raise AQPException(ERR_DATA_EMPTY, "predictions 不存在，无法生成信号")
    sig_parts = []
    for f in pred_files:
        d = pl.read_parquet(f)
        cols = ["date", "symbol", "pred_score"]
        if "model_version" in d.columns:
            cols.append("model_version")
        d = d.select(cols)
        dcol = d.schema["date"]
        if dcol == pl.Utf8:
            d = d.with_columns(pl.col("date").str.to_date())
        elif dcol != pl.Date:
            d = d.with_columns(pl.col("date").cast(pl.Date))
        sig_parts.append(d)
    sig = pl.concat(sig_parts, how="diagonal_relaxed")
    if "model_version" not in sig.columns:
        raise AQPException(ERR_DATA_EMPTY,
                           "predictions 缺少 model_version 列，无法隔离信号版本")
    # 版本选择基于请求窗口内的覆盖度（窗口外日期对本次回测无意义）
    sig = sig.filter((pl.col("date") >= date.fromisoformat(start))
                     & (pl.col("date") <= date.fromisoformat(end)))
    if sig.is_empty():
        raise AQPException(ERR_DATA_EMPTY, "回测区间内无 predictions 数据")
    counts = (sig.group_by("model_version")
              .agg(pl.col("date").n_unique().alias("n_days"))
              .drop_nulls("model_version")
              .sort(["n_days", "model_version"]))
    versions = counts["model_version"].to_list()
    if model_version == "latest":
        want = versions[-1]  # 覆盖日最多；并列取字典序最大（时间戳命名=最新）
    elif model_version in versions:
        want = model_version
    else:
        raise AQPException(
            ERR_DATA_EMPTY,
            f"predictions 中不存在模型版本 {model_version!r}（可用: {versions[-5:]}）")
    sig = sig.filter(pl.col("model_version") == want)
    return uni, sig, [str(want)]


def _run(req: BacktestRequest) -> dict:
    uni, sig, used_versions = _load_universe_and_signals(
        req.start, req.end, req.model_version)
    friction = _friction_config(req)

    uni_pd = uni.to_pandas()
    sig_pd = sig.to_pandas()
    res = run_backtest(uni_pd, sig_pd, init_cash=req.init_cash, top_k=req.top_k,
                       rebalance_freq=req.rebalance_freq, friction=friction,
                       weighting=req.weighting, cov_window=req.cov_window,
                       weight_cap=req.weight_cap, n_trials=req.n_trials,
                       dropout_n=req.dropout_n,
                       delist_haircut=req.delist_haircut,
                       delist_grace_days=req.delist_grace_days)
    rejects: dict[str, int] = {}
    for t in res.trades:
        if t["reason"] != "filled":
            rejects[t["reason"]] = rejects.get(t["reason"], 0) + 1
    m = res.metrics
    # 曲线序列（日期统一字符串）
    curve = res.nav_df[["date", "nav", "equity", "cash"]].copy()
    curve["date"] = curve["date"].astype(str)
    running_peak = res.nav_df["nav"].cummax()
    drawdown = 1.0 - res.nav_df["nav"] / running_peak
    drawdown = pd.DataFrame({"date": res.nav_df["date"].astype(str), "drawdown": drawdown})
    yearly = res.nav_df.assign(year=res.nav_df["date"].astype(str).str[:4])
    year_end = yearly.groupby("year")["nav"].last()
    annual_returns = {}
    prev = None
    for y, v in year_end.items():
        annual_returns[y] = round(float(v / prev - 1), 6) if prev else             round(float(v - 1), 6)
        prev = v
    holdings_tail = [{"date": str(h["date"]), "holdings": h["holdings"]}
                     for h in res.holdings_history[-20:]]
    trades = res.trades
    for t in trades:
        t["date"] = str(t["date"])[:10]
    return {
        "start": req.start, "end": req.end, "top_k": req.top_k,
        "enable_friction": req.enable_friction,
        "impact_model": req.impact_model,
        "max_participation": req.max_participation,
        "weighting": req.weighting,
        "weight_cap": req.weight_cap,
        "model_version": req.model_version,
        "used_model_versions": used_versions,
        "universe_scope": {
            "dataset": "universe_daily_bt",
            "n_symbols": int(uni["symbol"].n_unique()),
            "note": "股票池=本地已下载数据集（含退市证券的历史 bar，直至其 delist_date）；"
                    "delist_date 之后的日期已从宇宙剔除，超期持仓按最后收盘×haircut 强平减记",
        },
        "trading_days": len(res.nav_df),
        "filled_trades": sum(1 for t in trades if t["reason"] == "filled"),
        "rejected_trades": rejects,
        # v == v 是 NaN 判定的标准写法（NaN != 自身），并非笔误
        "metrics": {k: (round(float(v), 6)
                        if isinstance(v, (int, float)) and v == v else v)  # noqa: PLR0124
                    for k, v in m.items()},
        "equity_curve": curve.to_dict("records"),
        "drawdown_curve": drawdown.to_dict("records"),
        "annual_returns": annual_returns,
        "holdings": holdings_tail,
        "trades": trades,
        "friction_costs": {k: round(float(v), 2) for k, v in res.friction_costs.items()},
        # Task 5（整改 A-P1-3）：流动性口径如实披露——universe_daily_bt 已携带
        # 成交额，冲击成本/参与率上限真实生效（此前恒 0 且无提示）。
        "liquidity": {
            "source": "universe_daily_bt",
            "impact_cost_included": req.enable_friction,
            "participation_cap": req.max_participation,
            "note": ("冲击成本按当日真实成交额参与率计算，单笔受 max_participation 闸门约束"
                     if req.enable_friction else "未启用摩擦成本（enable_friction=false）"),
        },
    }


def _run_cache_key(req: BacktestRequest) -> str:
    """Task 4（整改 A-P0-1）：全参数入键——漏参 = 不同请求共享缓存 = 返回他人结果。

    此前键漏 init_cash（100 万与 1000 万的请求互取缓存）。键字段必须与
    BacktestRequest 的全部影响结果的字段一一对应；新增请求字段时此处必须同步。
    """
    return k_backtest(
        f"run_{req.start}_{req.end}_{req.top_k}_{req.rebalance_freq}_"
        f"{req.init_cash}_{req.enable_friction}_{req.slippage_bps}_{req.decay_bps}_"
        f"{req.impact_pct}_{req.impact_linear_bps}_{req.impact_model}_"
        f"{req.impact_sqrt_coef_bps}_{req.max_participation}_"
        f"{req.weighting}_{req.cov_window}_{req.weight_cap}_"
        f"{req.n_trials}_{req.dropout_n}_{req.delist_haircut}_"
        f"{req.delist_grace_days}_{req.model_version}")


def _strategy_cache_key(req: StrategyBacktestRequest) -> str:
    """Task 4（整改 A-P0-1）：策略回测全参数入键（此前漏 walk_forward/wf_folds）。"""
    opt_key = ""
    if req.optimize_params:
        opt_key = f"_opt{req.optimize_method}_" + orjson.dumps(
            req.optimize_params, option=orjson.OPT_SORT_KEYS).decode()
    return k_backtest(
        f"strategy_{req.strategy_type}_{req.start}_{req.end}_"
        f"{req.init_cash}_{req.commission_rate}_{req.slippage_bps}_"
        f"{req.short_ma}_{req.long_ma}_{req.trailing_stop_pct}_"
        f"wf{req.walk_forward}_{req.wf_folds}"
        f"{opt_key}_{','.join(sorted(req.symbols))}")


@router.post("/run", response_model=APIResponse[dict])
async def run_backtest_api(req: BacktestRequest,
                           _user: dict = Depends(require_role("researcher")),
                           _compute: None = Depends(compute_slot)) -> APIResponse[dict]:
    """同步回测：真实引擎 + 真实 universe/pred 数据（Redis 缓存 10min）。"""
    key = _run_cache_key(req)
    cached = await RedisClient.get(key)
    if cached:
        data = orjson.loads(cached)
        data["from_cache"] = True
        return ok(data)
    data = await asyncio.to_thread(_run, req)
    data["from_cache"] = False
    logger.info(f"[backtest] {req.start}~{req.end} top{req.top_k} done")
    await RedisClient.set(key, orjson.dumps(data), ex=600)
    return ok(data)


# ================= 策略回测页：多策略 + 可选参数寻优（vnpy 式） =================
class StrategyBacktestRequest(BaseModel):
    strategy_name: str = Field("趋势跟踪策略 v1.0", max_length=64)
    strategy_type: str = Field(
        "ma_cross", max_length=32,
        description="策略类型：ma_cross（原引擎）/ donchian / rsi_reversion（vnpy 式框架）")
    start: str = Field(..., pattern=r"^\d{4}-\d{2}-\d{2}$")
    end: str = Field(..., pattern=r"^\d{4}-\d{2}-\d{2}$")
    init_cash: float = Field(1_000_000, gt=0)
    commission_rate: float = Field(0.0003, ge=0.0001, le=0.01)
    slippage_bps: float = Field(5.0, ge=0, le=100, description="滑点（bps）：买加卖减")
    short_ma: int = Field(5, ge=2, le=60)
    long_ma: int = Field(20, ge=5, le=250)
    trailing_stop_pct: float = Field(3.0, ge=0.5, le=50)
    symbols: list[str] = Field(..., min_length=1, max_length=10)
    # ---- 可选参数寻优（vnpy OptimizeSetting）：给出即先搜索再用最优参数回测 ----
    optimize_params: dict[str, list[float]] | None = Field(
        None, max_length=6,
        description="参数网格，如 {\"short_ma\": [5, 10, 20]}；ma_cross/donchian/rsi 参数名")
    optimize_method: str = Field("grid", pattern=r"^(grid|ga|optuna)$")
    # ---- walk-forward 折外验证（§4.4 防过拟合）：给出即按折滚动 IS 寻优 → OOS 验证 ----
    walk_forward: bool = Field(False, description="true=walk-forward 折外验证模式")
    wf_folds: int = Field(3, ge=2, le=8, description="walk-forward 折数")
    # Task 7（整改 A-P1-8）：ma_cross 默认走框架引擎（broker 全套 A 股闸门）；
    # use_legacy_engine=true 临时回退旧引擎（无停牌/涨跌停/T+1 闸门，结果偏乐观）
    use_legacy_engine: bool = Field(False, description="ma_cross 使用旧引擎（仅对照用）")


def _load_strategy_bars(symbols: list[str], start: str, end: str) -> dict[str, "pl.DataFrame"]:
    """加载标的 QFQ 日线（缺 QFQ 回退 raw）。"""
    from ...data.parquet_store import read_symbol_dataset

    out: dict[str, pl.DataFrame] = {}
    for raw_sym in symbols:
        sym = raw_sym.strip().upper()
        if "." not in sym and sym.isdigit():
            sym = f"{sym}.{'SH' if sym.startswith(('6', '9', '5')) else 'SZ'}"
        df = read_symbol_dataset("daily_bar_qfq", sym)
        source = "qfq"
        if df.is_empty():
            df = read_symbol_dataset("daily_bar", sym)
            source = "raw"
        if df.is_empty():
            raise AQPException(40010, f"标的 {sym} 无本地行情数据（数据中心未收录）")
        dcol = df.schema["date"]
        if dcol != pl.Date:
            df = df.with_columns(pl.col("date").cast(pl.Date))
        df = df.filter((pl.col("date") >= date.fromisoformat(start))
                       & (pl.col("date") <= date.fromisoformat(end)))
        if df.height < 30:
            raise AQPException(40011, f"标的 {sym} 在区间内数据不足（{df.height} 行，需 ≥30）")
        # P1-7：透传真实成交量/成交额（原始口径），供 broker 计算冲击成本
        # 与停牌闸门；旧数据集若缺列则只保留基础字段（由 strategy_base 降级处理）
        # Task 7（整改 A-P1-8）：按板块生成 qfq 域涨跌停列供 broker 闸门
        # （qfq 与 raw 的比值关系一致，闸门比率正确；ST 历史口径缺失沿用
        #   当前快照限制，新股不设限期未特判——保守方向）
        from ...data.universe import board_of

        board = board_of(sym.split(".")[0])
        pct = {"bse": 0.30, "chinext_star": 0.20}.get(board, 0.10)
        df = df.sort("date").with_columns([
            (pl.col("close").shift(1) * (1 + pct)).round(2).alias("limit_up"),
            (pl.col("close").shift(1) * (1 - pct)).round(2).alias("limit_down"),
        ])
        keep = [c for c in ("date", "open", "close", "volume", "amount",
                            "limit_up", "limit_down")
                if c in df.columns]
        out[sym] = df.select(keep)
    return out


def _strategy_range_check(bars: dict, start: str, end: str) -> None:
    """校验回测区间不超出本地数据区间。"""
    lo = min(df["date"].min() for df in bars.values())
    hi = max(df["date"].max() for df in bars.values())
    lo_s, hi_s = str(lo)[:10], str(hi)[:10]
    if start < lo_s or end > hi_s:
        raise AQPException(40012, f"回测区间超出本地数据范围（可用 {lo_s} ~ {hi_s}）")


# 各策略在请求模型上的可寻优参数名（vnpy OptimizeSetting 的参数白名单）
_STRATEGY_PARAM_KEYS: dict[str, set[str]] = {
    "ma_cross": {"short_ma", "long_ma", "trailing_stop_pct"},
    "donchian": {"entry_window", "exit_window"},
    "rsi_reversion": {"rsi_period", "oversold", "overbuy"},
}

# 框架策略的信号 action -> 前端买卖标记
_BUY_ACTIONS = {"golden", "breakout", "oversold_entry"}


def _run_single(
    strategy_type: str, bars_pd: dict, bench: pd.DataFrame,
    req: StrategyBacktestRequest, overrides: dict,
) -> dict:
    """跑单次策略回测（ma_cross 原引擎或 vnpy 式框架策略）。"""
    # 三个分支返回同构 dict，但内部结果类型不同（MaCrossResult / StrategyRunResult、
    # MaCrossStrategy / StrategyTemplate），显式声明避免分支间类型互相污染
    res: Any
    strat: Any
    if strategy_type == "ma_cross" and getattr(req, "use_legacy_engine", False):
        # 旧引擎（无停牌/涨跌停/T+1 闸门）——仅 use_legacy_engine=true 对照用
        from ...backtest.ma_cross import MaCrossParams, run_ma_cross
        params = MaCrossParams(
            short_ma=int(overrides.get("short_ma", req.short_ma)),
            long_ma=int(overrides.get("long_ma", req.long_ma)),
            trailing_stop_pct=float(overrides.get("trailing_stop_pct",
                                                  req.trailing_stop_pct)),
            commission_rate=req.commission_rate,
            slippage_bps=req.slippage_bps, init_cash=req.init_cash)
        res = run_ma_cross(bars_pd, bench, params)
        return {"sharpe": float(res.risk.get("sharpe", float("nan"))),
                "nav": res.nav_df["strategy_nav"].to_numpy(), "result": res,
                "engine": "legacy_no_gates",
                "params": {"short_ma": params.short_ma, "long_ma": params.long_ma,
                           "trailing_stop_pct": params.trailing_stop_pct}}
    if strategy_type == "ma_cross":
        # Task 7（整改 A-P1-8）：默认走框架版（与 donchian/rsi 共用 broker 闸门）
        from ...backtest.strategy_base import MaCrossStrategy, run_strategy

        strat = MaCrossStrategy(
            short_ma=int(overrides.get("short_ma", req.short_ma)),
            long_ma=int(overrides.get("long_ma", req.long_ma)),
            trailing_stop_pct=float(overrides.get("trailing_stop_pct",
                                                  req.trailing_stop_pct)))
        res = run_strategy(bars_pd, strat, benchmark=bench,
                           init_cash=req.init_cash,
                           commission_rate=req.commission_rate,
                           slippage_bps=req.slippage_bps)
        return {"sharpe": float(res.risk.get("sharpe", float("nan"))),
                "nav": res.nav_df["strategy_nav"].to_numpy(), "result": res,
                "engine": "framework(broker_gates)",
                "params": {"short_ma": strat.short_ma, "long_ma": strat.long_ma,
                           "trailing_stop_pct": strat.trailing_stop_pct}}
    from ...backtest.strategy_base import STRATEGIES, run_strategy
    cls = STRATEGIES[strategy_type]
    valid = _STRATEGY_PARAM_KEYS[strategy_type]
    strat = cls(**{k: v for k, v in overrides.items() if k in valid})
    res = run_strategy(bars_pd, strat, benchmark=bench,
                       init_cash=req.init_cash, commission_rate=req.commission_rate,
                       slippage_bps=req.slippage_bps)
    return {"sharpe": float(res.risk.get("sharpe", float("nan"))),
            "nav": res.nav_df["strategy_nav"].to_numpy(), "result": res,
            "params": dict(strat.params_used)}


def _run_walk_forward(req: "StrategyBacktestRequest", bars_pd: dict,
                      bench: "pd.DataFrame", grid: dict) -> dict:
    """walk-forward 折外验证（§4.4）：时间轴均切 folds+1 段，
    第 i 折 = IS[头..(i+1)段末] 寻优 → OOS[第 i+2 段] 折外评估。"""
    from ...backtest.param_search import walk_forward_search

    all_dates = sorted({d for df in bars_pd.values() for d in df["date"]})
    n = len(all_dates)
    seg = n // (req.wf_folds + 1)
    if seg < 20:
        raise AQPException(40017,
                           f"区间过短（{n} 个交易日），无法切 {req.wf_folds} 折"
                           "（每段需 ≥20 个交易日），请拉长区间或减少折数")
    windows = []
    for i in range(req.wf_folds):
        is_s, is_e = all_dates[0], all_dates[(i + 1) * seg - 1]
        oos_s = all_dates[(i + 1) * seg]
        oos_e = all_dates[min((i + 2) * seg - 1, n - 1)]
        windows.append((is_s, is_e, oos_s, oos_e))

    def evaluate_window(ws, we):
        sliced = {sym: df[(df["date"] >= ws) & (df["date"] <= we)]
                  for sym, df in bars_pd.items()}
        bench_s = bench[(bench["date"] >= ws) & (bench["date"] <= we)]

        def ev(params: dict) -> dict:
            out = _run_single(req.strategy_type, sliced, bench_s, req, params)
            return {"sharpe": out["sharpe"], "nav": out["nav"]}

        return ev

    wf = walk_forward_search(evaluate_window, windows, grid,
                             method=req.optimize_method, n_trials_per_fold=30)
    return {
        "method": "walk_forward",
        "search_method": req.optimize_method,
        "n_folds": len(wf.folds),
        "folds": [{
            "fold": f.fold,
            "is_window": [str(f.is_window[0])[:10], str(f.is_window[1])[:10]],
            "oos_window": [str(f.oos_window[0])[:10], str(f.oos_window[1])[:10]],
            "best_params": f.best_params,
            "is_sharpe": round(f.is_objective, 4),
            "oos_sharpe": round(f.oos_objective, 4),
            "n_trials": f.n_trials,
        } for f in wf.folds],
        "mean_is_sharpe": round(wf.mean_is_objective, 4),
        "mean_oos_sharpe": round(wf.mean_oos_objective, 4),
        "overfit_ratio": (round(wf.overfit_ratio, 3)
                          if wf.overfit_ratio == wf.overfit_ratio else None),
        "note": "OOS 为折外窗口真实表现（无搜索偏置）；overfit_ratio>1.5 "
                "提示寻优过拟合，最优参数不可直接采信",
    }


def _persist_opt_report(req: "StrategyBacktestRequest",
                        optimization: dict | None) -> None:
    """寻优报告落盘（§4.4：MODEL_ROOT/exp/backtest_opt/，时间戳命名；失败仅告警）。"""
    if not optimization:
        return
    try:
        import json as _json
        import time as _time

        out_dir = get_settings().MODEL_ROOT / "exp" / "backtest_opt"
        out_dir.mkdir(parents=True, exist_ok=True)
        name = (f"{_time.strftime('%Y%m%d_%H%M%S')}_{req.strategy_type}_"
                f"{optimization.get('method', 'na')}.json")
        payload = {"strategy": req.strategy_type, "symbols": req.symbols,
                   "start": req.start, "end": req.end,
                   "optimization": optimization}
        (out_dir / name).write_text(_json.dumps(payload, ensure_ascii=False, indent=1),
                                    encoding="utf-8")
        logger.info(f"[strategy-backtest] opt report saved: {out_dir / name}")
    except Exception as e:  # noqa: BLE001 落盘失败不影响回测响应
        logger.warning(f"[strategy-backtest] opt report persist failed: {e!r}")


def _run_strategy(req: StrategyBacktestRequest) -> dict:
    from ...backtest.ma_cross import run_ma_cross  # noqa: F401  (路径守卫/懒加载预热)
    from ...data.ingest.akshare_adapter import fetch_index_daily

    if req.start >= req.end:
        raise AQPException(40013, "开始时间必须早于结束时间")
    if req.strategy_type == "ma_cross" and req.short_ma >= req.long_ma:
        raise AQPException(40014, f"短均线周期（{req.short_ma}）必须小于长均线周期（{req.long_ma}）")
    if req.strategy_type not in _STRATEGY_PARAM_KEYS:
        raise AQPException(40015, f"未知策略类型 {req.strategy_type!r}，"
                                  f"可选 {sorted(_STRATEGY_PARAM_KEYS)}")

    bars_pl = _load_strategy_bars(req.symbols, req.start, req.end)
    _strategy_range_check(bars_pl, req.start, req.end)
    bars_pd = {sym: df.to_pandas() for sym, df in bars_pl.items()}

    # 基准：沪深300（新浪源，模块级缓存）
    try:
        bench_df = fetch_index_daily("sh000300")
        dcol = bench_df.schema["date"]
        if dcol != pl.Date:
            bench_df = bench_df.with_columns(pl.col("date").cast(pl.Date))
        bench_df = bench_df.filter(
            (pl.col("date") >= date.fromisoformat(req.start))
            & (pl.col("date") <= date.fromisoformat(req.end)))
        bench = bench_df.select(["date", "close"]).to_pandas()
    except Exception as e:  # noqa: BLE001 基准失败时回测仍可运行（基准置常数）
        logger.warning(f"[strategy-backtest] benchmark degraded: {e!r}")
        first_day = min(df["date"].min() for df in bars_pd.values())
        bench = pd.DataFrame({"date": [first_day], "close": [1.0]})

    # ---- 可选：参数寻优（vnpy OptimizeSetting）----
    optimization: dict | None = None
    base_overrides: dict = {}
    if req.optimize_params:
        from ...backtest.param_search import run_search
        bad = set(req.optimize_params) - _STRATEGY_PARAM_KEYS[req.strategy_type]
        if bad:
            raise AQPException(40016, f"策略 {req.strategy_type} 不支持寻优参数: {sorted(bad)}")
        grid = {k: [float(v) for v in vs] for k, vs in req.optimize_params.items()}

        def _evaluate(params: dict) -> dict:
            out = _run_single(req.strategy_type, bars_pd, bench, req, params)
            return {"sharpe": out["sharpe"], "nav": out["nav"]}

        if req.optimize_method == "optuna" and len(grid) == 0:
            raise AQPException(40017, "optuna 寻优需要给出 optimize_params 候选列表")
        try:
            if req.walk_forward:
                wf = _run_walk_forward(req, bars_pd, bench, grid)
                optimization = wf
                base_overrides = wf.get("folds", [{}])[-1].get("best_params", {})                     if wf.get("folds") else {}
            else:
                search = run_search(_evaluate, grid, method=req.optimize_method,
                                    optuna_trials=30)
                optimization = {
                    "method": search.method,
                    "n_trials": len(search.trials),
                    "best_params": search.best_params,
                    "best_sharpe": round(search.best_objective, 6),
                    "deflated_sharpe": (round(search.deflated_sharpe, 6)
                                        if search.deflated_sharpe == search.deflated_sharpe  # noqa: PLR0124
                                        else None),
                    "top5": search.top(5),
                }
                base_overrides = search.best_params
        except ValueError as e:
            raise AQPException(40017, str(e)) from e
        # 寻优报告落盘（§4.4：data/models/exp/backtest_opt/，幂等时间戳命名）
        _persist_opt_report(req, optimization)

    # ---- 用（可能寻优后的）参数跑最终回测 ----
    out = _run_single(req.strategy_type, bars_pd, bench, req, base_overrides)
    res = out["result"]
    nav_map = dict(zip(res.nav_df["date"], res.nav_df["strategy_nav"]))
    trades = [{**t, "date": str(t["date"])[:10]} for t in res.trades]
    trade_rows = [{
        "date": t["date"], "symbol": t["symbol"],
        "side": "买入" if t["side"] in ("buy", "golden") else "卖出",
        "price": t["price"], "qty": t["qty"], "fee": t["fee"], "pnl": t["pnl"],
    } for t in trades]
    signals = [{"date": str(s["date"])[:10],
                "action": "买入" if s["action"] in _BUY_ACTIONS else "卖出",
                "raw_action": s["action"], "symbol": s["symbol"],
                "nav": round(float(nav_map.get(s["date"], float("nan"))), 4)}
               for s in res.signals]
    curve = [{"date": str(d)[:10], "strategy": round(float(s), 4),
              "benchmark": (round(float(b), 4) if b == b else None)}
             for d, s, b in zip(res.nav_df["date"], res.nav_df["strategy_nav"],
                                res.nav_df["benchmark_nav"])]
    risk_out = {k: (round(float(v), 4) if v == v and isinstance(v, (int, float)) else None)  # noqa: PLR0124
                for k, v in res.risk.items()}
    # P1-7 流动性/摩擦成本披露：框架策略（donchian/rsi_reversion）按真实日成交额
    # 计算冲击成本，停牌/无数据日按 halted 拒绝成交；ma_cross 原引擎仅计
    # 滑点/佣金/印花税，未计冲击成本（如实标注，避免结果被解读为可执行净值）
    if req.strategy_type == "ma_cross" and req.use_legacy_engine:
        liquidity = {
            "source": "real_daily_bar", "impact_cost_included": False,
            "engine": "legacy_no_gates",
            "note": "ma_cross 旧引擎：计入滑点/佣金/印花税，未计冲击成本，"
                    "且无停牌/涨跌停/T+1 闸门（结果偏乐观，仅对照用）",
        }
    else:
        liquidity = {
            "source": "real_daily_bar", "impact_cost_included": True,
            "note": "成交量/成交额取自本地日线（原始口径）；停牌或无数据日按停牌拒绝成交，冲击成本按当日真实成交额参与率计算",
        }
    return {
        "strategy_name": req.strategy_name,
        "strategy_type": req.strategy_type,
        "strategy_engine": out.get("engine", "framework(broker_gates)"),
        "start": req.start, "end": req.end,
        "init_cash": req.init_cash, "commission_rate": req.commission_rate,
        "slippage_bps": req.slippage_bps,
        "short_ma": int(out["params"].get("short_ma", req.short_ma)),
        "long_ma": int(out["params"].get("long_ma", req.long_ma)),
        "trailing_stop_pct": float(out["params"].get("trailing_stop_pct",
                                                     req.trailing_stop_pct)),
        "params_used": out["params"],
        "symbols": list(bars_pl.keys()),
        "kpi": {
            "annual_strategy": risk_out.get("annual_strategy"),
            "annual_benchmark": risk_out.get("annual_benchmark"),
            "sharpe": risk_out.get("sharpe"),
            "max_drawdown": risk_out.get("max_drawdown"),
        },
        "nav_curve": curve,
        "signals": signals,
        "monthly_returns": res.monthly,
        "trades": trade_rows,
        "risk": risk_out,
        "liquidity": liquidity,
        "optimization": optimization,
    }


@router.post("/strategy-run", response_model=APIResponse[dict])
async def run_strategy_backtest_api(req: StrategyBacktestRequest,
                                    _user: dict = Depends(require_role("researcher")),
                                    _compute: None = Depends(compute_slot)) -> APIResponse[dict]:
    """策略回测（多策略 + 可选参数寻优；轻量同步路由；Redis 缓存 10min）。"""
    key = _strategy_cache_key(req)
    cached = await RedisClient.get(key)
    if cached:
        data = orjson.loads(cached)
        data["from_cache"] = True
        return ok(data)
    data = await asyncio.to_thread(_run_strategy, req)
    data["from_cache"] = False
    logger.info(f"[strategy-backtest] {req.start}~{req.end} done, trades={len(data['trades'])}")
    await RedisClient.set(key, orjson.dumps(data), ex=600)
    return ok(data)


# ================= 信号分析（qlib 式 IC 衰减 / 分层多空） =================
class SignalAnalysisRequest(BaseModel):
    model_config = __import__('pydantic').ConfigDict(protected_namespaces=())
    start: str = Field(..., pattern=r"^\d{4}-\d{2}-\d{2}$")
    end: str = Field(..., pattern=r"^\d{4}-\d{2}-\d{2}$")
    horizons: list[int] = Field([1, 5, 10, 20], min_length=1, max_length=8)
    n_quantiles: int = Field(5, ge=2, le=10)
    model_version: str = Field("latest")


def _run_signal_analysis(req: SignalAnalysisRequest) -> dict:
    from ...ml.signal_analysis import ic_decay_report, quantile_spread_report

    uni, sig, used_versions = _load_universe_and_signals(
        req.start, req.end, req.model_version)
    close_pd = uni.select(["date", "symbol", "close"]).to_pandas()
    sig_pd = sig.to_pandas()
    ic_df = ic_decay_report(sig_pd, close_pd, horizons=tuple(req.horizons))
    qs = quantile_spread_report(sig_pd, close_pd, horizon=max(req.horizons),
                                n_quantiles=req.n_quantiles)
    return {
        "start": req.start, "end": req.end,
        "model_version": req.model_version,
        "used_model_versions": used_versions,
        "universe_scope": {
            "dataset": "universe_daily_bt",
            "n_symbols": int(uni["symbol"].n_unique()),
            "note": "股票池=本地已下载数据集（含退市证券的历史 bar，直至其 delist_date）；"
                    "delist_date 之后的日期已从宇宙剔除，超期持仓按最后收盘×haircut 强平减记",
        },
        "n_symbols": int(close_pd["symbol"].nunique()),
        "n_days": int(close_pd["date"].nunique()),
        "ic_summary": ic_df.replace({np.nan: None}).to_dict("records"),
        "quantile_spread": qs,
    }


@router.post("/signal-analysis", response_model=APIResponse[dict])
async def signal_analysis_api(req: SignalAnalysisRequest,
                              _user: dict = Depends(require_role("researcher")),
                              _compute: None = Depends(compute_slot)) -> APIResponse[dict]:
    """信号分析：Rank IC 衰减（多 horizon）+ 分层多空价差（Redis 缓存 10min）。"""
    key = k_backtest(f"signal_{req.start}_{req.end}_{req.model_version}_"
                     f"{'-'.join(map(str, sorted(req.horizons)))}_{req.n_quantiles}")
    cached = await RedisClient.get(key)
    if cached:
        data = orjson.loads(cached)
        data["from_cache"] = True
        return ok(data)
    data = await asyncio.to_thread(_run_signal_analysis, req)
    data["from_cache"] = False
    logger.info(f"[signal-analysis] {req.start}~{req.end} done")
    await RedisClient.set(key, orjson.dumps(data), ex=600)
    return ok(data)
