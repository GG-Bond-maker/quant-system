"""
绩效评估（纯函数，numpy 向量化，无 IO）。

输入净值序列（list[float] / np.ndarray），输出指标 dict 或标量。公式：
- 年化收益：(last / first) ** (252 / N) - 1
- 年化波动：std(daily_ret, ddof=1) * sqrt(252)
- 夏普：    mean(daily_ret - rf/252) / std(daily_ret - rf/252, ddof=1) * sqrt(252)
- 最大回撤：max(1 - nav / running_peak)，返回 (mdd, peak_idx, trough_idx)
- 胜率：    日收益 > 0 的比例
- 盈亏比：  mean(正收益) / |mean(负收益)|（任一为空返回 0）
"""
from __future__ import annotations

from typing import Any

import math

import numpy as np
import numpy.typing as npt

TRADING_DAYS_PER_YEAR = 252


def _as_arr(x: Any) -> npt.NDArray[np.float64]:
    """校验并转换净值序列：长度 >= 2 且全部 > 0。"""
    arr = np.asarray(list(x), dtype=np.float64)
    if arr.ndim != 1 or arr.size < 2:
        raise ValueError("净值序列长度必须 >= 2")
    if np.any(arr <= 0):
        raise ValueError("净值必须 > 0")
    return arr


def _daily_returns(arr: npt.NDArray[np.float64]) -> npt.NDArray[np.float64]:
    return np.diff(arr) / arr[:-1]


def total_return(arr: Any) -> float:
    """区间累计收益率：last / first - 1。"""
    a = _as_arr(arr)
    return float(a[-1] / a[0] - 1.0)


def annual_return(arr: Any, trading_days_per_year: int = TRADING_DAYS_PER_YEAR) -> float:
    """年化收益率。"""
    a = _as_arr(arr)
    n = len(a) - 1
    if n <= 0:
        return 0.0
    return float((a[-1] / a[0]) ** (trading_days_per_year / n) - 1.0)


def annual_volatility(
    arr: Any, trading_days_per_year: int = TRADING_DAYS_PER_YEAR
) -> float:
    """年化波动率。"""
    r = _daily_returns(_as_arr(arr))
    if r.size < 2:
        return 0.0
    return float(np.std(r, ddof=1) * np.sqrt(trading_days_per_year))


def sharpe_ratio(
    arr: Any,
    rf: float = 0.0,
    trading_days_per_year: int = TRADING_DAYS_PER_YEAR,
) -> float:
    """夏普比率（rf 为年化无风险利率）。"""
    a = _as_arr(arr)
    r = _daily_returns(a) - rf / trading_days_per_year
    if r.size < 2:
        return 0.0
    std = np.std(r, ddof=1)
    if std < 1e-12:
        return 0.0
    return float(np.mean(r) / std * np.sqrt(trading_days_per_year))


def max_drawdown(arr: Any) -> tuple[float, int, int]:
    """最大回撤，返回 (mdd_ratio, peak_idx, trough_idx)。

    peak_idx / trough_idx 为净值序列中的峰值 / 谷值位置（0-based）。
    """
    a = _as_arr(arr)
    peak = np.maximum.accumulate(a)
    dd = 1 - a / peak
    trough = int(np.argmax(dd))
    peak_idx = int(np.argmax(a[: trough + 1]))
    return float(dd[trough]), peak_idx, trough


def win_rate(arr: Any) -> float:
    """日度胜率：正收益日 / 全部交易日。"""
    r = _daily_returns(_as_arr(arr))
    if r.size == 0:
        return 0.0
    return float(np.mean(r > 0))


def profit_loss_ratio(arr: Any) -> float:
    """盈亏比：平均正收益 / |平均负收益|（无盈利日或无亏损日返回 0）。"""
    r = _daily_returns(_as_arr(arr))
    pos, neg = r[r > 0], r[r < 0]
    if pos.size == 0 or neg.size == 0:
        return 0.0
    return float(np.mean(pos) / abs(np.mean(neg)))


def annual_turnover(
    turnovers_per_day: Any, trading_days_per_year: int = TRADING_DAYS_PER_YEAR
) -> float:
    """年化换手率：日均换手 × 252。"""
    t = np.asarray(list(turnovers_per_day), dtype=np.float64)
    if t.size == 0:
        return 0.0
    return float(np.mean(t) * trading_days_per_year)


# ==================== 防过拟合指标（Bailey & López de Prado） ====================
# PSR（Probabilistic Sharpe Ratio）：给定偏度/峰度修正后的 Sharpe 分布，
#       P(SR_true > SR_benchmark)；
# DSR（Deflated Sharpe Ratio）：把基准 SR* 设为"尝试 N 次策略中最好一次
#       在零假设下的期望最大值"，即考虑多重试验（multiple testing）惩罚后
#       的 PSR —— 回测尝试的参数组合越多（n_trials 越大），DSR 越低。

def _norm_cdf(x: float) -> float:
    """标准正态 CDF（math.erf，无 scipy 依赖）。"""
    return 0.5 * (1.0 + math.erf(x / math.sqrt(2.0)))


def _norm_ppf(p: float) -> float:
    """标准正态分位函数（Acklam 有理逼近，|误差| < 1.15e-9，无 scipy 依赖）。"""
    if not (0.0 < p < 1.0):
        raise ValueError("p 必须在 (0, 1) 开区间")
    a = (-3.969683028665376e+01, 2.209460984245205e+02, -2.759285104469687e+02,
         1.383577518672690e+02, -3.066479806614716e+01, 2.506628277459239e+00)
    b = (-5.447609879822406e+01, 1.615858368580409e+02, -1.556989798598866e+02,
         6.680131188771972e+01, -1.328068155288572e+01)
    c = (-7.784894002430293e-03, -3.223964580411365e-01, -2.400758277161838e+00,
         -2.549732539343734e+00, 4.374664141464968e+00, 2.938163982698783e+00)
    d = (7.784695709041462e-03, 3.224671290700398e-01, 2.445134137142996e+00,
         3.754408661907416e+00)
    plow, phigh = 0.02425, 1.0 - 0.02425
    if p < plow:
        q = math.sqrt(-2.0 * math.log(p))
        return (((((c[0] * q + c[1]) * q + c[2]) * q + c[3]) * q + c[4]) * q + c[5]) / \
               ((((d[0] * q + d[1]) * q + d[2]) * q + d[3]) * q + 1.0)
    if p <= phigh:
        q = p - 0.5
        r = q * q
        return (((((a[0] * r + a[1]) * r + a[2]) * r + a[3]) * r + a[4]) * r + a[5]) * q / \
               (((((b[0] * r + b[1]) * r + b[2]) * r + b[3]) * r + b[4]) * r + 1.0)
    q = math.sqrt(-2.0 * math.log(1.0 - p))
    return -(((((c[0] * q + c[1]) * q + c[2]) * q + c[3]) * q + c[4]) * q + c[5]) / \
           ((((d[0] * q + d[1]) * q + d[2]) * q + d[3]) * q + 1.0)


def _sr_variance_factor(sr_daily: float, skew: float, kurt: float, n: int) -> float:
    """SR 估计量方差（按 de Prado 修正四阶矩公式，逐期口径）。"""
    var_factor = 1.0 - skew * sr_daily + (kurt - 1.0) / 4.0 * sr_daily ** 2
    return max(var_factor, 1e-12) / max(n - 1, 1)


def probabilistic_sharpe_ratio(
    nav: Any, sr_benchmark: float = 0.0, rf: float = 0.0
) -> float:
    """PSR ∈ (0,1)：P(真实 SR > sr_benchmark)，用偏度/峰度修正非正态性。"""
    r = _daily_returns(_as_arr(nav)) - rf / TRADING_DAYS_PER_YEAR
    if r.size < 3 or float(np.std(r, ddof=1)) < 1e-12:
        return float("nan")
    sr = float(np.mean(r) / np.std(r, ddof=1))
    m = float(np.mean(r))
    s = float(np.std(r, ddof=1))
    skew = float(np.mean(((r - m) / s) ** 3))
    kurt = float(np.mean(((r - m) / s) ** 4))   # 非超额峰度
    z = (sr - sr_benchmark) / math.sqrt(_sr_variance_factor(sr, skew, kurt, r.size))
    return _norm_cdf(z)


def deflated_sharpe_ratio(nav: Any, n_trials: int = 1, rf: float = 0.0) -> float:
    """DSR ∈ (0,1)：考虑 n_trials 次多重试验惩罚后的 PSR。

    零假设下的期望最大 SR（de Prado 2014, "The Deflated Sharpe Ratio"）：
        SR₀ = sqrt(V[SR]) × E[max of N iid normals]
        E[max] ≈ (1-γ)·Φ⁻¹(1-1/N) + γ·Φ⁻¹(1-1/(N·e))，γ 为 Euler 常数
    :param n_trials: 回测中尝试过的（近似独立的）策略/参数组合数
    """
    r = _daily_returns(_as_arr(nav)) - rf / TRADING_DAYS_PER_YEAR
    if r.size < 3 or float(np.std(r, ddof=1)) < 1e-12:
        return float("nan")
    n = int(max(n_trials, 1))
    sr = float(np.mean(r) / np.std(r, ddof=1))
    m = float(np.mean(r))
    s = float(np.std(r, ddof=1))
    skew = float(np.mean(((r - m) / s) ** 3))
    kurt = float(np.mean(((r - m) / s) ** 4))
    sr_std = math.sqrt(_sr_variance_factor(sr, skew, kurt, r.size))
    if n == 1:
        sr0 = 0.0
    else:
        euler_gamma = 0.5772156649015329
        e = math.e
        z1 = _norm_ppf(1.0 - 1.0 / n)
        z2 = _norm_ppf(1.0 - 1.0 / (n * e))
        sr0 = sr_std * ((1.0 - euler_gamma) * z1 + euler_gamma * z2)
    z = (sr - sr0) / sr_std
    return _norm_cdf(z)


def all_metrics(
    nav: Any,
    turnovers_per_day: Any | None = None,
    rf: float = 0.0,
    n_trials: int = 1,
) -> dict[str, float | int]:
    """一次性计算全部绩效指标，键名与前端/回测报告约定一致。

    n_trials > 1 时额外产出 deflated_sharpe（多重试验惩罚后的 PSR）。
    """
    a = _as_arr(nav)
    mdd, pk, tr = max_drawdown(a)
    return {
        "n_days": int(len(a) - 1),
        "total_return": total_return(a),
        "annual_return": annual_return(a),
        "annual_vol": annual_volatility(a),
        "sharpe": sharpe_ratio(a, rf=rf),
        "max_drawdown": mdd,
        "mdd_peak_day": pk,
        "mdd_trough_day": tr,
        "win_rate": win_rate(a),
        "profit_loss_ratio": profit_loss_ratio(a),
        "probabilistic_sharpe": probabilistic_sharpe_ratio(a, rf=rf),
        "deflated_sharpe": deflated_sharpe_ratio(a, n_trials=n_trials, rf=rf),
        "annual_turnover": (
            annual_turnover(turnovers_per_day) if turnovers_per_day is not None else float("nan")
        ),
    }
