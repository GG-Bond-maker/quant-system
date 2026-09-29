"""
风险度量（纯函数，numpy 向量化，无 IO）。

计算窗口口径（可在调用时覆盖）：
- 波动率 / 最大回撤 / 夏普 / Beta：近 `window`（默认 252）个交易日
- 波动率分位：把【近 20 日年化波动率】放到【过去 250 个交易日的
  20 日年化波动率序列】里做百分位排名，衡量"当前波动处于历史什么水平"

公式：
- 日收益      r_t = C_t / C_{t-1} - 1
- 年化波动率  std(r, ddof=1) * sqrt(252)
- 最大回撤    max(1 - nav / running_peak)，nav 由窗口内收盘价归一化得到
- 夏普        mean(r - rf/252) / std(r - rf/252, ddof=1) * sqrt(252)
- Beta        cov(r_s, r_b) / var(r_b)（按日期内连接对齐，样本不足返回 None）
"""
from __future__ import annotations

from typing import Any

import numpy as np
import numpy.typing as npt
import polars as pl

from .metrics import RISK_FREE_ANNUAL

TRADING_DAYS_PER_YEAR = 252


def _daily_returns(close: npt.NDArray[np.float64]) -> npt.NDArray[np.float64]:
    with np.errstate(divide="ignore", invalid="ignore"):
        return close[1:] / close[:-1] - 1.0


def _annual_vol(r: npt.NDArray[np.float64]) -> float | None:
    r = r[np.isfinite(r)]
    if r.size < 2:
        return None
    return float(np.std(r, ddof=1) * np.sqrt(TRADING_DAYS_PER_YEAR))


def _sharpe(r: npt.NDArray[np.float64], rf: float = 0.0) -> float | None:
    x = r[np.isfinite(r)] - rf / TRADING_DAYS_PER_YEAR
    if x.size < 2:
        return None
    std = float(np.std(x, ddof=1))
    if std < 1e-12:
        return None
    return float(np.mean(x) / std * np.sqrt(TRADING_DAYS_PER_YEAR))


def _max_drawdown(close: npt.NDArray[np.float64]) -> float | None:
    if close.size < 2:
        return None
    peak = np.maximum.accumulate(close)
    with np.errstate(divide="ignore", invalid="ignore"):
        dd = 1.0 - close / peak
    return float(np.max(dd)) if np.isfinite(dd).any() else None


def _beta(stock_r: npt.NDArray[np.float64], bench_r: npt.NDArray[np.float64]) -> float | None:
    mask = np.isfinite(stock_r) & np.isfinite(bench_r)
    s, b = stock_r[mask], bench_r[mask]
    if s.size < 20:  # 样本过少时 Beta 不稳定，宁缺勿滥
        return None
    var = float(np.var(b, ddof=1))
    if var < 1e-14:
        return None
    return float(np.cov(s, b, ddof=1)[0, 1] / var)


def _rolling_vol_percentile(
    returns: npt.NDArray[np.float64], short_window: int, lookback: int
) -> tuple[float | None, float | None]:
    """(当前短周期年化波动率, 其在历史同窗口序列中的百分位 0~100)。"""
    if returns.size < short_window:
        return None, None
    vals: list[float] = []
    lo = max(0, returns.size - lookback)
    for end in range(lo, returns.size + 1):
        seg = returns[max(0, end - short_window) : end]
        if seg.size < short_window:
            continue
        v = _annual_vol(seg)
        if v is not None:
            vals.append(v)
    if not vals:
        return None, None
    cur = vals[-1]
    arr = np.asarray(vals, dtype=np.float64)
    pct = float((arr[:-1] < cur).mean() * 100.0) if arr.size > 1 else 50.0
    return cur, round(pct, 2)


def risk_metrics(
    df: pl.DataFrame,
    benchmark: pl.DataFrame | None = None,
    window: int = 252,
    short_window: int = 20,
    percentile_lookback: int = 250,
    rf: float = RISK_FREE_ANNUAL,
    benchmark_symbol: str | None = None,
) -> dict[str, Any]:
    """计算个股风险度量。

    :param df:                含 date/close 的日线（升序）
    :param benchmark:         基准指数日线（含 date/close），None 时 Beta 为 null
    :param window:            波动率 / 回撤 / 夏普 / Beta 的回看交易日数
    :param short_window:      短周期波动率窗口（用于波动率分位）
    :param percentile_lookback: 波动率分位的历史比较长度
    :param rf:                **年化**无风险利率（默认与组合口径同源
                              ``metrics.RISK_FREE_ANNUAL``；审计 B2-16 前此处恒为 0，
                              与组合页的 2% 不可比）
    :param benchmark_symbol:  基准的**真实标识**（审计 B2-16：此前无论传入什么基准，
                              返回的 ``benchmark`` 字段都硬编码成"沪深300"）。
                              None 时该字段为 None —— 不伪造标签。
    :return: dict（annual_vol / max_drawdown / sharpe / beta / vol_short /
             vol_percentile / as_of / window / rf_annual / benchmark / note）
    """
    if df.is_empty() or "close" not in df.columns:
        raise ValueError("日线数据缺少 close 列")
    if window < 20:
        raise ValueError("window 必须 >= 20 否则指标无统计意义")

    d = df.tail(window + 1)
    close = np.nan_to_num(
        d["close"].cast(pl.Float64, strict=False).to_numpy(), nan=0.0
    )
    close = close[close > 0]
    if close.size < 20:
        raise ValueError(f"有效收盘价不足 20 个（实际 {close.size}）")

    r = _daily_returns(close)

    # ---- Beta：按日期内连接对齐基准 ----
    beta: float | None = None
    bench_name: str | None = None
    if benchmark is not None and not benchmark.is_empty() and "close" in benchmark.columns:
        try:
            joined = (
                d.select(["date", "close"]).with_columns(pl.col("date").cast(pl.Date))
                .join(
                    benchmark.select(["date", "close"])
                    .with_columns(pl.col("date").cast(pl.Date))
                    .rename({"close": "bench_close"}),
                    on="date", how="inner",
                )
                .sort("date")
            )
            if joined.height >= 21:
                beta = _beta(
                    _daily_returns(joined["close"].to_numpy()),
                    _daily_returns(joined["bench_close"].to_numpy()),
                )
                bench_name = benchmark_symbol
        except Exception:  # noqa: BLE001 基准对齐失败不影响其余指标
            beta = None

    vol_short, vol_pct = _rolling_vol_percentile(r, short_window, percentile_lookback)

    as_of = None
    if "date" in d.columns and d.height:
        as_of = str(d["date"][-1])

    return {
        "as_of": as_of,
        "window": int(close.size - 1),
        "annual_vol": _round(_annual_vol(r)),
        "max_drawdown": _round(_max_drawdown(close)),
        "sharpe": _round(_sharpe(r, rf=rf), 4),
        "beta": _round(beta, 4),
        "vol_short": _round(vol_short),
        "vol_percentile": vol_pct,
        "benchmark": bench_name,
        "rf_annual": rf,
        "note": f"近 {close.size - 1} 个交易日口径；波动率分位为近 {short_window} 日波动率在近 {percentile_lookback} 日中的百分位",
    }


def _round(v: float | None, digits: int = 6) -> float | None:
    return None if v is None or not np.isfinite(v) else round(float(v), digits)
