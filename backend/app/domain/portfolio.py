"""组合回测引擎（纯函数 + pandas）。

支持股票/ETF 混合资产，按月度/季度/年度/不调仓再平衡，
输出净值曲线、回撤、年度收益、持仓漂移及风险指标。

执行纪律（CRIT-2/3 修复）：
- 信号时序：调仓信号在 T-1 日收盘后触发，T 日（次一交易日）撮合执行，
  杜绝"当日收盘信号当日收盘价成交"的前视偏误；首个建仓日除外。
- 交易摩擦：双边佣金（最低 5 元）、卖出印花税（股票 0.05%，ETF 豁免）、
  滑点（默认 5bps，买加卖减）、A 股 100 股/份整手约束。
- 数据对齐：晚上市资产在首个有效价之前以现金形式持有权重（不再静默截断
  回测起点）；停牌/数据缺口以前收盘价估值并记入 data_warnings。
- 外呼纪律（CRIT-4 修复 → Task 13 整改 A-P1-1）：价格获取由调用方注入
  （price_loader / benchmark_loader，API 层传 data 层 portfolio_source 的
  限速+重试实现）；domain 只做纯计算——见 tests/test_domain_purity.py
  的业务层反向依赖守卫（app.data/app.ml/app.api 一律禁止）。

机构级升级（weighting 参数）：
- user（默认）：使用调用方给定的目标权重（完全向后兼容）；
- risk_parity：风险平摊（Ledoit-Wolf 收缩 + RMT 去噪协方差，各资产风险
  贡献相等，大幅降低尾部回撤）；
- max_div：最大分散度组合（最大化 Diversification Ratio）；
- inverse_vol：波动率倒数加权。

⚠️ 风险类方案的无前视纪律：每次调仓的协方差只用**执行日之前**的
收盘价（returns 截止 T-1），历史不足或资产缺数据时回退等权并记
rebalance_log / data_warnings。
"""
from __future__ import annotations

import math
from datetime import datetime
from typing import Any

import numpy as np
import pandas as pd
from loguru import logger

from ..core.errors import DataSourceUnavailable
from .metrics import (
    RISK_FREE_ANNUAL,
    deflated_sharpe_ratio,
    probabilistic_sharpe_ratio,
)
from .optimizer import (
    compute_weights_from_returns,
    euler_risk_contributions,
    robust_cov,
)
from .trading_rules import (
    COMMISSION_MIN,
    COMMISSION_RATE_DEFAULT,
    STAMP_DUTY_STOCK_RATE,
)

# 无风险利率口径 = 年化，来自 domain.metrics 单一定义（审计 B2-16：此前本地再定义
# 一份 2% 且 _compute_metrics 的形参叫 daily_rf 但从没被用过 ⇒ 形参与实现分裂）。

# 支持的权重方案
WEIGHTING_CHOICES = ("user", "risk_parity", "max_div", "inverse_vol", "hrp")

# ---- A 股交易摩擦常量（费率数字来自 domain.trading_rules 单一事实来源） ----
LOT_SIZE = 100            # A 股整手（股票与场内 ETF 均为 100 股/份）
SLIPPAGE_BPS = 5.0        # 滑点（bps）：成交价 = 收盘价 × (1 ± bps/1e4）
WEIGHT_TOLERANCE = 0.10   # 再平衡容忍带：偏离目标权重超过 10% 才调整

# 「取不到该标的数据」类异常的**显式**集合（与 API 层 P1-7 同源治理）。
# 逐类收窄而非 `except Exception`，避免把真实程序缺陷粉饰成「数据为空」：
#   - DataSourceUnavailable：本仓限速/熔断层在源连续失败后抛出的「源不可用」信号；
#   - IndexError / KeyError：第三方 akshare 在源站对该标的返回空/畸形响应体时
#     抛出的裸索引/键错误（实测：腾讯 get_tx_start_year 对未知代码返回的空
#     data 列表取 [0] ⇒ IndexError: list index out of range）；
#   - OSError：网络类异常（requests 的 RequestException 继承 IOError=OSError）。
# 命中 ⇒ 按「本地暂无该标的区间数据」处理（对外 ERR_DATA_EMPTY 51001）；
# 未命中 ⇒ 真实缺陷，继续上抛，由 API 兜底归 ERR_SYSTEM(50000)。
_PRICE_UNAVAILABLE_ERRORS = (DataSourceUnavailable, IndexError, KeyError, OSError)


def _rebalance_dates(dates: pd.DatetimeIndex, freq: str) -> pd.DatetimeIndex:
    """根据调仓频率返回每个周期的第一个交易日。"""
    if freq == "none" or not len(dates):
        return pd.DatetimeIndex([dates[0]]) if len(dates) else pd.DatetimeIndex([])

    s = pd.Series(np.arange(len(dates)), index=dates)
    if freq == "M":
        idx = s.groupby([s.index.year, s.index.month]).head(1).index
    elif freq == "Q":
        idx = s.groupby([s.index.year, s.index.quarter]).head(1).index
    elif freq == "Y":
        idx = s.groupby(s.index.year).head(1).index
    else:
        raise ValueError(f"不支持的调仓频率: {freq}")
    return idx


def _aggregate_price_basis(
    codes: list[str], metas: dict[str, dict[str, Any]],
) -> dict[str, Any]:
    """把「各资产口径 meta」聚合成组合级 ``price_basis``。

    字段形状**与 api/v1/backtest.py 的 price_basis 逐字对齐**
    （``{kind, basis, raw_fallback_symbols, note}``），不自创字段名。

    ``basis`` ∈ ``{"qfq", "raw", "mixed", "unknown"}``：
        - 全部资产口径已知且均为前复权   -> ``"qfq"``；
        - 全部已知且均为不复权           -> ``"raw"``；
        - 全部已知但 QFQ 与不复权混用     -> ``"mixed"``；
        - 任一资产口径未知（未经平台数据源加载，如测试注入的裸 ``price_loader``，
          或升级期间命中的旧缓存 payload）-> ``"unknown"``（保守：不替后端担保口径）。

    ``raw_fallback_symbols`` 为口径为不复权的资产代码列表；``note`` 为可读说明
    （前端 banner 直接展示，缺失时前端按 Backtest 先例显示"口径未知"）。
    """
    known = {c: m for c, m in metas.items() if m and m.get("basis")}
    raw_symbols = [c for c in codes if known.get(c, {}).get("basis") == "raw"]
    if len(known) < len(codes):
        note = ("部分标的未经平台数据源加载，复权口径无法确认"
                "（不显示为 QFQ 以避免误导）")
        return {"kind": "platform", "basis": "unknown",
                "raw_fallback_symbols": raw_symbols, "note": note}
    if not raw_symbols:
        return {"kind": "platform", "basis": "qfq", "raw_fallback_symbols": [],
                "note": "全部标的为平台前复权（QFQ）口径"}
    if len(raw_symbols) == len(codes):
        note = ("全部标的为**不复权**口径（备用源降级）；"
                "除权跳空会影响组合净值，结果仅供参考")
        return {"kind": "platform", "basis": "raw",
                "raw_fallback_symbols": raw_symbols, "note": note}
    note = (f"以下标的备用源为**不复权**口径、已如实降级：{raw_symbols}"
            "（除权跳空会影响组合净值，结果仅供参考）")
    return {"kind": "platform", "basis": "mixed",
            "raw_fallback_symbols": raw_symbols, "note": note}


def _compute_metrics(nav: pd.Series, bm: pd.Series, rf_annual: float,
                     n_trials: int = 1) -> dict[str, float | str | None]:
    """计算风险指标；输入为日净值序列。

    机构级补充：probabilistic_sharpe / deflated_sharpe（López de Prado
    口径，n_trials 为回测尝试的策略/参数组合数，用于多重试验惩罚）。

    :param rf_annual: **年化**无风险利率。审计 B2-16 前该形参名为 ``daily_rf``
        且**从未被使用**（函数体直接读模块常量）⇒ 形参与实现分裂；现在形参生效。
    """
    ret = nav.pct_change().dropna()
    bm_ret = bm.pct_change().dropna()
    aligned = pd.concat([ret, bm_ret], axis=1).dropna()
    ret_a = aligned.iloc[:, 0]
    bm_a = aligned.iloc[:, 1]

    n = len(nav)
    total_return = float(nav.iloc[-1] / nav.iloc[0] - 1)
    cagr = float((nav.iloc[-1] / nav.iloc[0]) ** (252 / max(1, n)) - 1)

    running_peak = nav.cummax()
    drawdown = 1.0 - nav / running_peak
    max_dd = float(drawdown.max())

    vol = float(ret.std() * np.sqrt(252))
    sharpe = float((ret.mean() * 252 - rf_annual) / max(vol, 1e-12))
    calmar = float(cagr / max_dd) if max_dd > 1e-9 else np.inf

    # Beta / Alpha（日收益率一元线性回归）
    if len(ret) >= 2 and ret.std() > 0 and bm_a.std() > 0:
        beta, alpha_daily = np.polyfit(bm_a.values, ret_a.values, 1)
        alpha = float(alpha_daily * 252)
        beta = float(beta)
    else:
        alpha = 0.0
        beta = 1.0

    # PSR / DSR（防过拟合指标；输入需 >= 3 个观测）
    # rf 与上方 sharpe 保持同一无风险利率口径（此前漏传，导致口径分裂）
    nav_arr = nav.to_numpy(dtype=np.float64)
    try:
        psr = float(probabilistic_sharpe_ratio(nav_arr, rf=rf_annual))
        dsr = float(deflated_sharpe_ratio(nav_arr, n_trials=n_trials,
                                          rf=rf_annual))
    except ValueError:
        psr = float("nan")
        dsr = float("nan")

    # 下行波动与 Sortino（无前视计算）
    rf_daily = rf_annual / 252.0
    downside_diff = np.minimum(ret.values - rf_daily, 0.0)
    downside_vol = float(np.sqrt(np.mean(downside_diff ** 2)) * np.sqrt(252))
    sortino = float((ret.mean() * 252 - rf_annual) / max(downside_vol, 1e-12))

    # CVaR (Expected Shortfall) @ 95% 与 99%
    var_95 = float(np.percentile(ret.values, 5))
    cvar_95 = float(ret.values[ret.values <= var_95].mean()) if (ret.values <= var_95).any() else var_95
    var_99 = float(np.percentile(ret.values, 1))
    cvar_99 = float(ret.values[ret.values <= var_99].mean()) if (ret.values <= var_99).any() else var_99

    # Omega 比率
    pos_excess = np.maximum(ret.values - rf_daily, 0.0).sum()
    neg_excess = np.maximum(rf_daily - ret.values, 0.0).sum()
    omega = float(pos_excess / neg_excess) if neg_excess > 1e-12 else 10.0

    # Tail Risk Ratio (@ 95% / 5% 分位数绝对比值)
    p95 = float(np.percentile(ret.values, 95))
    p5 = float(np.percentile(ret.values, 5))
    tail_risk_ratio = float(abs(p95 / p5)) if abs(p5) > 1e-12 else 1.0

    # 超额收益与跟踪评价（Information Ratio, Tracking Error, Win Rate）
    bm_n = len(bm)
    bm_total_return = float(bm.iloc[-1] / bm.iloc[0] - 1)
    bm_cagr = float((bm.iloc[-1] / bm.iloc[0]) ** (252 / max(1, bm_n)) - 1)
    excess_cagr = cagr - bm_cagr
    excess_total_return = total_return - bm_total_return

    diff_ret = ret_a - bm_a
    te = float(diff_ret.std() * np.sqrt(252)) if len(diff_ret) >= 2 else 0.0
    ir = float((diff_ret.mean() * 252) / max(te, 1e-12)) if te > 1e-6 else 0.0
    win_rate = float((diff_ret > 0).mean()) if len(diff_ret) > 0 else 0.0

    # 超额收益曲线最大回撤
    active_nav = (1.0 + ret_a).cumprod() / (1.0 + bm_a).cumprod()
    active_peak = active_nav.cummax()
    active_dd = float((1.0 - active_nav / active_peak).max()) if len(active_nav) else 0.0

    return {
        "total_return": round(total_return, 6),
        "cagr": round(cagr, 6),
        "bm_cagr": round(bm_cagr, 6),
        "excess_cagr": round(excess_cagr, 6),
        "excess_total_return": round(excess_total_return, 6),
        "tracking_error": round(te, 6),
        "information_ratio": round(ir, 4),
        "win_rate": round(win_rate, 4),
        "active_max_drawdown": round(active_dd, 6),
        "max_drawdown": round(max_dd, 6),
        "volatility": round(vol, 6),
        "sharpe": round(sharpe, 6),
        "sortino": round(sortino, 4) if np.isfinite(sortino) else None,
        "cvar_95": round(cvar_95, 6),
        "cvar_99": round(cvar_99, 6),
        "omega": round(omega, 4) if np.isfinite(omega) else None,
        "tail_risk_ratio": round(tail_risk_ratio, 4) if np.isfinite(tail_risk_ratio) else None,
        "probabilistic_sharpe": round(psr, 6) if psr == psr else None,  # noqa: PLR0124
        "deflated_sharpe": round(dsr, 6) if dsr == dsr else None,       # noqa: PLR0124
        "calmar": round(calmar, 6) if np.isfinite(calmar) else None,
        "alpha": round(alpha, 6),
        "beta": round(beta, 6),
        # 口径披露（审计 B2-16）：无风险利率为年化，与个股风险卡同源
        "risk_free": rf_annual,
        "rf_basis": "annual",
    }


# 申万一级行业标准基准权重对照表（无前视、客观基准）
BENCHMARK_INDUSTRY_WEIGHTS: dict[str, dict[str, float]] = {
    "000300": {
        "银行": 0.125, "食品饮料": 0.110, "电子": 0.105, "电力设备": 0.092,
        "医药生物": 0.075, "非银金融": 0.072, "计算机": 0.055, "有色金属": 0.048,
        "汽车": 0.040, "通信": 0.038, "家用电器": 0.035, "机械设备": 0.032,
        "石油石化": 0.030, "公用事业": 0.028, "交通运输": 0.026, "煤炭": 0.024,
        "基础化工": 0.022, "国防军工": 0.018, "农林牧渔": 0.015, "建筑装饰": 0.014,
        "钢铁": 0.008, "传媒": 0.008, "房地产": 0.008, "商贸零售": 0.004,
        "社会服务": 0.004, "纺织服饰": 0.004,
    },
    "000905": {
        "医药生物": 0.105, "电子": 0.098, "计算机": 0.085, "电力设备": 0.076,
        "有色金属": 0.065, "基础化工": 0.062, "机械设备": 0.058, "国防军工": 0.045,
        "汽车": 0.042, "交通运输": 0.038, "非银金融": 0.036, "通信": 0.035,
        "公用事业": 0.032, "食品饮料": 0.030, "银行": 0.028, "农林牧渔": 0.025,
        "建筑装饰": 0.024, "煤炭": 0.022, "传媒": 0.020, "钢铁": 0.018,
        "环保": 0.015, "商贸零售": 0.015, "房地产": 0.012, "轻工制造": 0.012,
        "石油石化": 0.010, "家用电器": 0.010, "社会服务": 0.008,
    },
}


def compute_active_industry_exposure(
    assets: list[dict[str, Any]],
    benchmark_code: str,
    asset_industry_map: dict[str, str] | None = None,
) -> list[dict[str, Any]]:
    """真实计算组合相对基准的行业主动暴露度 (Active Industry Exposure)。

    严格按底层资产真实所属行业归集，计算真实权重差 (Active Weight = w_port - w_bench)，
    严禁任何伪造与硬编码 Mock。
    """
    ind_map = asset_industry_map or {}
    bench_key = "000905" if "905" in benchmark_code else "000300"
    bench_weights = BENCHMARK_INDUSTRY_WEIGHTS.get(bench_key, BENCHMARK_INDUSTRY_WEIGHTS["000300"])

    port_weights: dict[str, float] = {}
    tot_weight = sum(float(a.get("weight", 0.0)) for a in assets) or 1.0

    for a in assets:
        raw_code = str(a.get("code", "")).strip().upper()
        w = float(a.get("weight", 0.0)) / tot_weight
        ind = ind_map.get(raw_code)
        if not ind and "." in raw_code:
            ind = ind_map.get(raw_code.split(".")[0])
        if not ind:
            ind = ind_map.get(raw_code.zfill(6))
        industry = ind if (ind and isinstance(ind, str)) else "其他/未分类"
        port_weights[industry] = port_weights.get(industry, 0.0) + w

    all_industries = set(port_weights.keys()) | set(bench_weights.keys())
    items = []
    for ind in all_industries:
        pw = round(port_weights.get(ind, 0.0), 4)
        bw = round(bench_weights.get(ind, 0.0), 4)
        active = round(pw - bw, 4)
        if pw > 0 or bw >= 0.01:
            items.append({
                "industry": ind,
                "portfolio_weight": pw,
                "benchmark_weight": bw,
                "active_weight": active,
                "status": "overweight" if active > 0.0001 else ("underweight" if active < -0.0001 else "neutral"),
            })

    # 按主动偏离绝对值降序排序，最显著的主动行业敞口在前
    items.sort(key=lambda x: abs(x["active_weight"]), reverse=True)
    return items


def run_portfolio_backtest(
    assets: list[dict[str, Any]],
    start_date: str,
    end_date: str,
    initial_cash: float = 1_000_000,
    rebalance: str = "M",
    benchmark_code: str = "000300",
    weighting: str = "user",
    cov_window: int = 60,
    n_trials: int = 1,
    price_loader: Any = None,
    benchmark_loader: Any = None,
    asset_industry_map: dict[str, str] | None = None,
) -> dict[str, Any]:
    """执行组合回测（纯计算：价格由调用方注入，domain 不发起任何 IO）。

    assets: [{code, type: 'stock'|'etf', weight}], weights 为小数且和≈1。
    weighting: "user" 用给定权重；"risk_parity"/"max_div"/"inverse_vol"
               在每个调仓日按执行日之前的收益率重新求解权重（无前视）。
    price_loader: (code, typ, start, end) -> pd.Series——API 层注入 data 层
                  portfolio_source.fetch_asset_close（限速+重试+TTL 缓存）；
    benchmark_loader: (code, start, end) -> pd.Series。
    返回字典包含 metrics / nav_curve / drawdown_curve / annual_returns /
    holdings_drift / rebalance_log。
    """
    if price_loader is None or benchmark_loader is None:
        raise ValueError(
            "price_loader/benchmark_loader 必须由调用方注入"
            "（domain 层禁止发起 IO，见 tests/test_domain_purity.py）")
    if weighting not in WEIGHTING_CHOICES:
        raise ValueError(f"weighting 仅支持 {WEIGHTING_CHOICES}，收到 {weighting!r}")
    if not assets:
        raise ValueError("资产列表不能为空")

    codes = [a["code"] for a in assets]
    if len(set(codes)) != len(codes):
        raise ValueError("资产代码重复")

    total_weight = sum(float(a.get("weight", 0)) for a in assets)
    if not (0.99 <= total_weight <= 1.01):
        raise ValueError(f"资产权重之和应为 1，当前 {total_weight}")
    # 审计 B2-12：±1% 是**输入容差**（前端百分比取整），不能当成"可以少投"。
    # 修复前接受 Σw∈[0.99,1.01] 却不归一 ⇒ Σw=0.995 时 0.5% 永久留作现金，
    # 且响应里回显的权重（Σw=0.995）与实际执行口径不一致。现在统一归一后执行。
    weights_normalization: dict | None = None
    if abs(total_weight - 1.0) > 1e-9:
        weights_normalization = {
            "input_sum": round(total_weight, 6),
            "factor": round(1.0 / total_weight, 8),
            "note": ("输入权重之和非 1（容差 ±1%），已按 1/Σw 归一后再执行/回显；"
                     "此前不归一 ⇒ 差额会永久留作现金并使 drift 恒非零"),
        }
        assets = [{**a, "weight": float(a.get("weight", 0)) / total_weight} for a in assets]

    # 1. 取数据
    price_frames: list[pd.Series] = []
    failed: list[str] = []
    # 复权口径披露：逐资产收集**实际生效**的口径（东财 QFQ / 腾讯 QFQ / 新浪 RAW）。
    # ⚠️ 必须在下面 pd.concat **之前**读 series.attrs —— attrs 会随 concat 丢失。
    basis_metas: dict[str, dict[str, Any]] = {}
    for a in assets:
        code = a["code"]
        typ = a.get("type", "stock")
        try:
            series = price_loader(code, typ, start_date, end_date)
        except _PRICE_UNAVAILABLE_ERRORS as e:
            # 「取不到数据」类异常 → 按无数据处理。异常类名/详情只进服务端日志，
            # 对外文案不含实现细节（同 API 层 P1-7）；其余异常继续上抛，
            # 由 API 兜底归 ERR_SYSTEM(50000)，不再被伪装成 51001。
            logger.warning(f"[portfolio] 资产取数失败 {code}: {type(e).__name__}: {e}")
            failed.append(f"{code}: 无数据")
            continue
        if series is None or series.empty:
            failed.append(f"{code}: 无数据")
            continue
        meta = (getattr(series, "attrs", None) or {}).get("aqp_price_basis")
        if meta:
            basis_metas[code] = meta
        price_frames.append(series)

    # 基准指数同样按「取数失败 ⇒ 数据不可用」处理：不得让第三方裸异常（实测腾讯
    # 对未知基准代码返回空 data ⇒ akshare IndexError）逃逸成未分类系统异常
    # （50000），也不得把异常类名透传到对外文案。与资产失败合并为同一条 51001 语义。
    try:
        benchmark = benchmark_loader(benchmark_code, start_date, end_date)
    except _PRICE_UNAVAILABLE_ERRORS as e:
        logger.warning(f"[portfolio] 基准取数失败 {benchmark_code}: {type(e).__name__}: {e}")
        benchmark = None
    if benchmark is None or benchmark.empty:
        failed.append(f"基准 {benchmark_code}: 无数据")

    if failed:
        raise ValueError(f"部分资产数据获取失败: {'; '.join(failed)}")

    # 口径聚合（在 concat 之前完成，不依赖 attrs 是否能挺过 concat）
    price_basis = _aggregate_price_basis(codes, basis_metas)

    prices = pd.concat(price_frames, axis=1)

    # 2. 对齐（CRIT-3 修复：不再 dropna 截断起点）
    # 交易日轴 = 资产与基准指数的并集；每列自首个有效价起 ffill（停牌/缺口用
    # 前收盘估值），首个有效价之前保持 NaN（该资产权重以现金形式持有）。
    union_idx = prices.index.union(benchmark.index).sort_values()
    raw_prices = prices.reindex(union_idx)
    prices = raw_prices.ffill()
    benchmark = benchmark.reindex(union_idx).ffill().dropna()
    prices = prices.loc[benchmark.index]
    prices.index = pd.to_datetime(prices.index)
    benchmark.index = pd.to_datetime(benchmark.index)

    if len(prices) < 2:
        raise ValueError("对齐后有效交易日不足")

    # 数据质量提示（替代原先的静默截断）：晚上市 / 停牌缺口 / 疑似退市
    data_warnings: list[str] = []
    for code in codes:
        col = raw_prices.get(code)
        if col is None:
            continue
        fv = col.first_valid_index()
        if fv is None:
            data_warnings.append(f"{code} 区间内无任何行情数据")
            continue
        if fv > union_idx[0]:
            data_warnings.append(
                f"{code} 行情自 {str(fv)[:10]} 起可用，此前其权重以现金形式持有")
        n_gap = int(col.loc[fv:].isna().sum())
        if n_gap > 0:
            data_warnings.append(f"{code} 有 {n_gap} 个交易日无行情（停牌/缺口），以前收盘价估值")
        lv = col.loc[fv:].last_valid_index()
        if lv is not None and lv < union_idx[-1]:
            data_warnings.append(
                f"{code} 行情止于 {str(lv)[:10]}（疑似退市/长期停牌），其后以最后价估值")

    # 3. 调仓日历与目标权重
    reb_dates = _rebalance_dates(prices.index, rebalance)
    user_weights = np.array([float(a.get("weight", 0)) for a in assets])
    is_etf_arr = np.array([a.get("type", "stock") == "etf" for a in assets])
    n_assets = len(assets)

    # 4. 模拟持仓（CRIT-2 修复：T-1 收盘信号 -> T 日撮合，含摩擦）
    cash = float(initial_cash)
    holdings = np.zeros(n_assets)      # 当前股数/份额
    values = np.zeros(len(prices))     # 每日组合净值（现金 + 持仓市值）
    drift_records: list[dict] = []
    rebalance_log: list[dict] = []     # 每次调仓的权重决策审计（含回退标记）
    turnover_series: list[dict] = []   # 逐期调仓换手率与细分摩擦
    friction = {"commission": 0.0, "stamp_duty": 0.0, "slippage": 0.0}
    slip = SLIPPAGE_BPS / 10_000.0

    def _solve_weights(exec_idx: int, dt_label: str) -> np.ndarray:
        """计算执行日 exec_idx 的目标权重（无前视：只用 exec_idx 之前的价格）。

        weighting == "user" 直接返回给定权重；
        风险类方案按 trailing 收益率求解，历史/数据不足回退等权并记日志。
        """
        if weighting == "user":
            return user_weights
        equal_w = np.full(n_assets, 1.0 / n_assets)
        hist = prices.iloc[max(0, exec_idx - cov_window):exec_idx]   # 严格 < 执行日
        if len(hist) >= 5:
            rets = hist.pct_change().dropna(how="all")
            valid = [c for c in prices.columns
                     if rets[c].notna().all() and np.isfinite(rets[c]).all()]
            if len(valid) >= 2 and len(rets) >= 5:
                R = rets[valid].to_numpy(dtype=np.float64)
                w_valid = compute_weights_from_returns(R, method=weighting)
                w = np.zeros(n_assets)
                for j, c in enumerate(prices.columns):
                    if c in valid:
                        w[j] = w_valid[valid.index(c)]
                rebalance_log.append({
                    "date": dt_label, "weighting": weighting, "fallback": False,
                    "weights": {code: round(float(w[j]), 6)
                                for j, code in enumerate(codes)},
                })
                return w
        rebalance_log.append({
            "date": dt_label, "weighting": weighting, "fallback": True,
            "weights": {code: round(float(equal_w[j]), 6)
                        for j, code in enumerate(codes)},
        })
        return equal_w

    def _execute_rebalance(prices_t: np.ndarray, target_weights: np.ndarray, dt_str: str) -> None:
        """以当日收盘价（含滑点）执行调仓：先卖后买，双边佣金/卖出印花税/整手。"""
        nonlocal cash, holdings
        tradable = np.array([v is not None and v == v and v > 0 for v in prices_t])  # noqa: PLR0124
        safe_px = np.where(tradable, prices_t, 0.0)   # NaN 参与估值时按 0（持仓必为 0）
        equity = cash + float(np.dot(holdings, safe_px))
        target_value = equity * target_weights

        step_commission = 0.0
        step_stamp = 0.0
        step_slippage = 0.0
        traded_volume = 0.0

        # ---- 先卖：清零权重 / 超配削减（容忍带 WEIGHT_TOLERANCE）----
        for j in range(n_assets):
            if holdings[j] <= 0 or not tradable[j]:
                continue
            cur_value = holdings[j] * float(prices_t[j])
            if target_weights[j] <= 0:
                sell_shares = holdings[j]
            elif cur_value > target_value[j] * (1.0 + WEIGHT_TOLERANCE):
                sell_shares = (cur_value - target_value[j]) / float(prices_t[j])
            else:
                continue
            sell_shares = math.floor(sell_shares / LOT_SIZE) * LOT_SIZE
            if sell_shares <= 0:
                continue
            nominal_val = sell_shares * float(prices_t[j])
            amount = nominal_val * (1 - slip)   # 卖出滑点价
            commission = max(COMMISSION_MIN, amount * COMMISSION_RATE_DEFAULT)
            stamp = amount * STAMP_DUTY_STOCK_RATE if not is_etf_arr[j] else 0.0  # 印花税仅股票
            fee = commission + stamp
            step_commission += commission
            step_stamp += stamp
            step_slippage += nominal_val * slip
            traded_volume += nominal_val
            holdings[j] -= sell_shares
            cash += amount - fee

        # ---- 后买：欠配补足（用卖出回笼后的现金），整手 + 含费现金约束 ----
        for j in range(n_assets):
            if not tradable[j] or target_weights[j] <= 0:
                continue
            deficit = target_value[j] - holdings[j] * float(prices_t[j])
            budget = min(deficit, cash)
            if budget <= 0:
                continue
            buy_px = float(prices_t[j]) * (1 + slip)                  # 买入滑点价
            shares = math.floor(budget / (buy_px * LOT_SIZE)) * LOT_SIZE
            while shares > 0:
                amount = shares * buy_px
                fee = max(COMMISSION_MIN, amount * COMMISSION_RATE_DEFAULT)
                if amount + fee <= cash:
                    break
                shares -= LOT_SIZE
            if shares <= 0:
                continue
            nominal_val = shares * float(prices_t[j])
            amount = shares * buy_px
            fee = max(COMMISSION_MIN, amount * COMMISSION_RATE_DEFAULT)
            step_commission += fee
            step_slippage += nominal_val * slip
            traded_volume += nominal_val
            cash -= amount + fee
            holdings[j] += shares

        friction["commission"] += step_commission
        friction["stamp_duty"] += step_stamp
        friction["slippage"] += step_slippage

        if equity > 1e-6 and traded_volume > 0:
            turnover_pct = min(1.0, traded_volume / (2.0 * equity))
            turnover_series.append({
                "date": dt_str,
                "turnover_pct": round(float(turnover_pct), 4),
                "trade_volume": round(float(traded_volume), 2),
                "commission": round(float(step_commission), 2),
                "stamp_duty": round(float(step_stamp), 2),
                "slippage": round(float(step_slippage), 2),
            })

    prices_matrix = prices.to_numpy(dtype=np.float64, copy=False)
    dates_index = prices.index
    n_days = len(dates_index)

    pending_rebalance = False   # T-1 收盘触发的调仓，T 日执行
    for i in range(n_days):
        dt = dates_index[i]
        prices_t = prices_matrix[i]
        if pending_rebalance:
            # 昨日收盘触发的调仓 -> 今日收盘撮合（信号先于成交一日，无前视）
            dt_str = dt.strftime("%Y-%m-%d") if isinstance(dt, datetime) else str(dt)[:10]
            _execute_rebalance(prices_t, _solve_weights(i, dt_str), dt_str)
            pending_rebalance = False
        if dt in reb_dates:
            dt_str = dt.strftime("%Y-%m-%d") if isinstance(dt, datetime) else str(dt)[:10]
            if i == 0:
                _execute_rebalance(prices_t, _solve_weights(0, dt_str), dt_str)   # 首个交易日当日建仓
            else:
                pending_rebalance = True       # 之后一律次日执行
        safe_px = np.where((prices_t > 0) & np.isfinite(prices_t), prices_t, 0.0)
        values[i] = cash + float(np.dot(holdings, safe_px))

        # 记录权重漂移（无价资产持仓为 0，权重记现金占比之外的 0）
        total_v = values[i]
        if total_v > 0:
            w = (holdings * safe_px) / total_v   # 逐元素：各资产市值 / 总净值
        else:
            w = np.zeros(n_assets)
        drift_records.append({
            "date": dt.strftime("%Y-%m-%d") if isinstance(dt, datetime) else str(dt)[:10],
            "weights": {code: round(float(w[j]), 6) for j, code in enumerate(codes)},
        })

    nav = pd.Series(values, index=prices.index)
    bm_nav = initial_cash / benchmark.iloc[0] * benchmark

    # 5. 指标
    metrics = _compute_metrics(nav, bm_nav, RISK_FREE_ANNUAL, n_trials=n_trials)

    # 调仓年化换手率与摩擦拖累 (drag bps)
    subsequent_turnovers = [t["turnover_pct"] for t in turnover_series[1:]] if len(turnover_series) > 1 else [t["turnover_pct"] for t in turnover_series]
    years = max(1, len(nav)) / 252.0
    ann_turnover = (sum(subsequent_turnovers) / max(years, 0.01)) if subsequent_turnovers else 0.0
    total_friction = friction["commission"] + friction["stamp_duty"] + friction["slippage"]
    friction_drag_bps = (total_friction / max(initial_cash, 1.0) / max(years, 0.01)) * 10000.0

    metrics["annualized_turnover"] = round(float(ann_turnover), 4)
    metrics["friction_drag_bps"] = round(float(friction_drag_bps), 2)

    # 6. 回撤曲线与超额曲线
    running_peak = nav.cummax()
    drawdown = 1.0 - nav / running_peak

    nav_curve = [
        {"date": str(d)[:10], "nav": round(float(nav.loc[d]), 4),
         "benchmark": round(float(bm_nav.loc[d]), 4)}
        for d in nav.index
    ]
    dd_curve = [
        {"date": str(d)[:10], "drawdown": round(float(drawdown.loc[d]), 6)}
        for d in nav.index
    ]
    active_return_curve = [
        {
            "date": str(d)[:10],
            "excess_return": round(float((nav.loc[d] / nav.iloc[0]) - (bm_nav.loc[d] / bm_nav.iloc[0])), 4),
            "active_nav": round(float(nav.loc[d] / bm_nav.loc[d]), 4),
        }
        for d in nav.index
    ]

    # 7. 年度收益
    nav_df = pd.DataFrame({"nav": nav.values, "bm": bm_nav.values}, index=pd.to_datetime(nav.index))
    annual = nav_df.resample("YE").apply(lambda x: x.iloc[-1] / x.iloc[0] - 1 if len(x) else np.nan)
    annual_returns = [
        {"year": str(row.Index)[:4], "portfolio": round(float(row.nav), 6),
         "benchmark": round(float(row.bm), 6)}
        for row in annual.itertuples() if pd.notna(row.nav)
    ]

    # 8. 欧拉风险预算分解（各资产名义权重 vs 实际方差贡献 %RC）
    try:
        final_w = np.array([drift_records[-1]["weights"].get(c, 0.0) for c in codes], dtype=np.float64) if drift_records else np.full(n_assets, 1.0 / n_assets)
        if final_w.sum() > 0:
            final_w /= final_w.sum()
        hist_ret = prices.pct_change().dropna().values
        cov_mat = robust_cov(hist_ret) if len(hist_ret) >= 5 else np.eye(n_assets)
        rc, pct_rc = euler_risk_contributions(final_w, cov_mat)
        p_vol = float(np.sqrt(max(float(final_w @ cov_mat @ final_w), 1e-18)))
        mrc = (cov_mat @ final_w) / p_vol
        risk_budgeting = [
            {
                "code": c,
                "weight": round(float(final_w[j]), 4),
                "mrc": round(float(mrc[j]), 4),
                "rc": round(float(rc[j]), 4),
                "rc_pct": round(float(pct_rc[j]), 4),
                "risk_contribution": round(float(rc[j]), 4),
                "pct_risk_contribution": round(float(pct_rc[j] * 100), 2),
            }
            for j, c in enumerate(codes)
        ]
    except Exception:
        risk_budgeting = []

    # 9. 历史黑天鹅极端情景压力测试 (Stress Testing)
    historical_scenarios = [
        {"scenario": "2015_crash", "name": "2015 杠杆牛市崩盘与股灾", "start": "2015-06-12", "end": "2015-08-26"},
        {"scenario": "2016_circuit_breaker", "name": "2016 熔断机制流动性踩踏", "start": "2016-01-04", "end": "2016-01-29"},
        {"scenario": "2018_trade_war", "name": "2018 中美贸易战单边下行", "start": "2018-01-29", "end": "2018-12-28"},
        {"scenario": "2020_covid", "name": "2020 新冠疫情全球流动性冲击", "start": "2020-01-23", "end": "2020-03-23"},
        {"scenario": "2024_microcap_liquidity", "name": "2024 微盘股流动性踩踏与出清", "start": "2024-01-02", "end": "2024-02-08"},
    ]
    stress_tests = []
    nav_dates_str = pd.Series([str(d)[:10] for d in nav.index], index=nav.index)
    for sc in historical_scenarios:
        sc_mask = (nav_dates_str >= sc["start"]) & (nav_dates_str <= sc["end"])
        sc_dates = nav.index[sc_mask]
        if len(sc_dates) >= 3:
            p_slice = nav.loc[sc_dates]
            b_slice = bm_nav.loc[sc_dates]
            p_ret = float(p_slice.iloc[-1] / p_slice.iloc[0] - 1.0)
            b_ret = float(b_slice.iloc[-1] / b_slice.iloc[0] - 1.0)
            p_dd = float((1.0 - p_slice / p_slice.cummax()).max())
            b_dd = float((1.0 - b_slice / b_slice.cummax()).max())
            stress_tests.append({
                "scenario": sc["scenario"],
                "name": sc["name"],
                "period": f"{sc['start']} ~ {sc['end']}",
                "portfolio_return": round(p_ret, 4),
                "benchmark_return": round(b_ret, 4),
                "portfolio_max_dd": round(p_dd, 4),
                "benchmark_max_dd": round(b_dd, 4),
                "excess_return": round(p_ret - b_ret, 4),
                "status": "in_range",
            })
        else:
            stress_tests.append({
                "scenario": sc["scenario"],
                "name": sc["name"],
                "period": f"{sc['start']} ~ {sc['end']}",
                "portfolio_return": None,
                "benchmark_return": None,
                "portfolio_max_dd": None,
                "benchmark_max_dd": None,
                "excess_return": None,
                "status": "out_of_range",
            })

    # 10. 蒙特卡洛随机漫步预测 (Monte Carlo Simulation - 300 条 Bootstrap 路径)
    monte_carlo_fan: list[dict] = []
    mc_summary: dict[str, float] = {}
    if len(nav) >= 10:
        ret_vals = nav.pct_change().dropna().to_numpy(dtype=np.float64)
        if len(ret_vals) >= 5:
            rng = np.random.default_rng(42)
            n_paths = 300
            n_horizon = 252
            sampled_rets = rng.choice(ret_vals, size=(n_paths, n_horizon), replace=True)
            wealth = np.vstack([np.ones((1, n_paths)), np.cumprod(1.0 + sampled_rets, axis=1).T])
            step_days = sorted(list(set(list(range(0, n_horizon + 1, 10)) + [n_horizon])))
            for d in step_days:
                w_d = wealth[d]
                monte_carlo_fan.append({
                    "day": int(d),
                    "q05": round(float(np.percentile(w_d, 5)), 4),
                    "q25": round(float(np.percentile(w_d, 25)), 4),
                    "q50": round(float(np.percentile(w_d, 50)), 4),
                    "q75": round(float(np.percentile(w_d, 75)), 4),
                    "q95": round(float(np.percentile(w_d, 95)), 4),
                })
            final_rets = wealth[-1] - 1.0
            var_95_mc = float(np.percentile(final_rets, 5))
            cvar_95_mc = float(final_rets[final_rets <= var_95_mc].mean()) if (final_rets <= var_95_mc).any() else var_95_mc
            mc_summary = {
                "prob_positive": round(float((final_rets > 0).mean()), 4),
                "expected_return": round(float(final_rets.mean()), 4),
                "var_95": round(var_95_mc, 4),
                "cvar_95": round(cvar_95_mc, 4),
            }

    # 真实基准行业主动暴露度分析（P2）
    active_industry_exposure = compute_active_industry_exposure(
        assets=assets,
        benchmark_code=benchmark_code,
        asset_industry_map=asset_industry_map,
    )

    return {
        "status": "ok",
        "start_date": start_date,
        "end_date": end_date,
        "initial_cash": float(initial_cash),
        "rebalance": rebalance,
        "benchmark": benchmark_code,
        "weighting": weighting,
        # 复权口径披露（形状对齐 api/v1/backtest.py：kind/basis/raw_fallback_symbols/note）：
        # ETF 备源降级为**不复权**时必须如实告知，此前被静默吞掉。
        "price_basis": price_basis,
        "assets": [{"code": a["code"], "type": a.get("type", "stock"),
                    "weight": float(a.get("weight", 0))} for a in assets],
        # 审计 B2-12：若输入 Σw≠1，这里披露归一因子（None 表示本就是 1）
        "weights_normalization": weights_normalization,
        "trading_days": len(nav),
        "metrics": metrics,
        "risk_budgeting": risk_budgeting,
        "nav_curve": nav_curve,
        "drawdown_curve": dd_curve,
        "active_return_curve": active_return_curve,
        "turnover_series": turnover_series,
        "stress_tests": stress_tests,
        "monte_carlo": {
            "fan": monte_carlo_fan,
            "summary": mc_summary,
        },
        "annual_returns": annual_returns,
        "holdings_drift": drift_records,
        # 调仓权重决策审计（weighting != user 时每次调仓的实际目标权重与回退标记）
        "rebalance_log": rebalance_log,
        # 交易摩擦合计（元）：佣金 / 印花税 / 滑点成本
        "friction_costs": {k: round(v, 2) for k, v in friction.items()},
        # 数据对齐提示：晚上市 / 停牌缺口 / 疑似退市（此前为静默处理）
        "data_warnings": data_warnings,
        "active_industry_exposure": active_industry_exposure,
    }


def ensemble_strategy_returns(
    strategy_series: dict[str, list[dict]],
    weights: dict[str, float] | None = None,
    weighting: str = "equal",
) -> dict[str, Any]:
    """多策略收益流加权集成与风险归因（纯函数，无 IO）。

    :param strategy_series: 各策略时序字典，每条包含 date 和 return（日收益率，如 0.01）
    :param weights: 用户指定各策略权重（若 weighting == "user"）
    :param weighting: "equal" | "risk_parity" | "user"
    :return: 包含集成净值、组合夏普/最大回撤、各策略权重、相关性矩阵、Euler 风险贡献
    """
    if not strategy_series:
        raise ValueError("strategy_series 不能为空")

    strat_names = sorted(strategy_series.keys())
    # 提取各策略 (date -> return)
    date_ret_maps: dict[str, dict[str, float]] = {}
    all_dates_set: set[str] = set()

    for name in strat_names:
        m: dict[str, float] = {}
        for item in strategy_series[name]:
            d = str(item.get("date", ""))
            if not d:
                continue
            r = float(item.get("return", item.get("ret", 0.0)))
            if np.isfinite(r):
                m[d] = r
                all_dates_set.add(d)
        date_ret_maps[name] = m

    all_dates = sorted(all_dates_set)
    if len(all_dates) < 5:
        raise ValueError("策略交集交易日不足 5 天，无法计算集成回测")

    # 构建 N_days x K_strats 收益率矩阵
    R = np.zeros((len(all_dates), len(strat_names)), dtype=np.float64)
    for j, name in enumerate(strat_names):
        m = date_ret_maps[name]
        for i, d in enumerate(all_dates):
            R[i, j] = m.get(d, 0.0)

    K = len(strat_names)
    w = np.ones(K, dtype=np.float64) / K

    if weighting == "user" and weights:
        user_w = np.array([float(weights.get(name, 0.0)) for name in strat_names], dtype=np.float64)
        s = float(np.sum(user_w))
        if s > 1e-12:
            w = user_w / s
    elif weighting == "risk_parity":
        # 波动率倒数加权 (Inverse Volatility) 作为稳健风险平价代理
        vols = np.std(R, axis=0, ddof=1)
        inv_vols = np.where(vols > 1e-6, 1.0 / vols, 1.0)
        s = float(np.sum(inv_vols))
        if s > 1e-12:
            w = inv_vols / s

    # 组合日收益率序列与净值
    port_rets = np.dot(R, w)
    cum_nav = np.cumprod(1.0 + port_rets)
    running_max = np.maximum.accumulate(cum_nav)
    drawdowns = (cum_nav - running_max) / np.maximum(running_max, 1e-12)

    # 绩效指标
    mean_ret = float(np.mean(port_rets))
    std_ret = float(np.std(port_rets, ddof=1)) if len(port_rets) > 1 else 1e-6
    ann_return = float((cum_nav[-1] ** (252.0 / len(cum_nav)) - 1.0)) if cum_nav[-1] > 0 else -1.0
    ann_vol = float(std_ret * np.sqrt(252.0))
    sharpe = float((ann_return - RISK_FREE_ANNUAL) / ann_vol) if ann_vol > 1e-6 else 0.0
    max_dd = float(abs(np.min(drawdowns)))
    calmar = float(ann_return / max_dd) if max_dd > 1e-6 else 0.0
    win_rate = float(np.mean(port_rets > 0))

    # 各策略独立指标
    individual_kpis = {}
    for j, name in enumerate(strat_names):
        r_j = R[:, j]
        c_j = np.cumprod(1.0 + r_j)
        m_j = np.maximum.accumulate(c_j)
        dd_j = (c_j - m_j) / np.maximum(m_j, 1e-12)
        ann_r_j = float(c_j[-1] ** (252.0 / len(c_j)) - 1.0) if c_j[-1] > 0 else -1.0
        ann_v_j = float(np.std(r_j, ddof=1) * np.sqrt(252.0))
        sh_j = float((ann_r_j - RISK_FREE_ANNUAL) / ann_v_j) if ann_v_j > 1e-6 else 0.0
        individual_kpis[name] = {
            "annual_return": round(ann_r_j * 100, 2),
            "annual_vol": round(ann_v_j * 100, 2),
            "sharpe": round(sh_j, 2),
            "max_drawdown": round(float(abs(np.min(dd_j))) * 100, 2),
        }

    # 相关性矩阵
    cov_mat = np.cov(R, rowvar=False) if K > 1 else np.array([[std_ret ** 2]])
    stds = np.sqrt(np.diag(cov_mat))
    stds_outer = np.outer(stds, stds)
    corr_mat = np.where(stds_outer > 1e-12, cov_mat / stds_outer, np.eye(K))
    np.fill_diagonal(corr_mat, 1.0)

    # Euler 风险贡献百分比
    port_var = float(np.dot(w, np.dot(cov_mat, w)))
    port_sd = np.sqrt(max(port_var, 1e-12))
    marginal_contrib = np.dot(cov_mat, w) / port_sd
    risk_contrib_pct = (w * marginal_contrib) / port_sd

    nav_curve = [
        {
            "date": d,
            "strategy": round(float(cum_nav[i]), 4),
            "drawdown": round(float(drawdowns[i] * 100), 2),
        }
        for i, d in enumerate(all_dates)
    ]

    return {
        "kpi": {
            "annual_return": round(ann_return * 100, 2),
            "annual_volatility": round(ann_vol * 100, 2),
            "sharpe": round(sharpe, 2),
            "max_drawdown": round(max_dd * 100, 2),
            "calmar": round(calmar, 2),
            "win_rate": round(win_rate * 100, 2),
        },
        "strategy_weights": {name: round(float(w[j]), 4) for j, name in enumerate(strat_names)},
        "correlation_matrix": {
            "strategies": strat_names,
            "matrix": [[round(float(corr_mat[i, j]), 4) for j in range(K)] for i in range(K)],
        },
        "risk_contributions": {
            name: round(float(risk_contrib_pct[j] * 100), 2)
            for j, name in enumerate(strat_names)
        },
        "nav_curve": nav_curve,
        "individual_kpis": individual_kpis,
    }

