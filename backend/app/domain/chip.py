"""
筹码分布（纯函数，numpy 向量化，无 IO）。

模型：日频换手衰减近似（业界通用做法，非 Level-2 真实盘口成本）。
    1. 第 i 日（由新到旧）新增筹码权重
           w_i = t_i * Π_{k>i}(1 - t_k)
       其中 t_i 为当日换手率（小数，0.01 表示 1%）。
       该式满足 Σw_i + Π(1 - t_k) = 1（望远镜恒等式），
       剩余的 Π(1 - t_k) 是比回看窗口更老的"基底筹码"，并入最老一根。
    2. 当日新增筹码在 [low_i, high_i] 上按【三角形分布】摊布，
       峰值位于当日成交均价 VWAP = amount / volume
       （假设成交在均价附近最密集，向两端线性衰减）。
    3. 在价格网格上累加得到筹码密度，归一化后计算：
       平均成本 / 获利盘 / 套牢盘 / 90% 区间 / 筹码集中度。

⚠️ 换手率口径：Parquet 的 turnover 列为小数（0.004319 = 0.4319%），
   由 AKShare 东财日线接口提供；缺失时按 volume / MA60(volume) × 基准换手率估算。
"""
from __future__ import annotations

from typing import Any

import numpy as np
import polars as pl

TRADING_DAYS_PER_YEAR = 252

#: 换手率缺失时的估算基准（A 股日均换手率经验值 ~1.2%）
FALLBACK_TURNOVER = 0.012
#: 估算换手率的上限，防止极端放量日把历史筹码一次性清空
MAX_TURNOVER = 0.5


def _turnover_series(df: pl.DataFrame) -> np.ndarray:
    """取换手率（小数）；缺失/异常时按量能相对倍数估算。"""
    if "turnover" in df.columns:
        t = df["turnover"].cast(pl.Float64, strict=False).fill_null(0.0).to_numpy()
        # 东财 turnover 为百分数（4.319 表示 4.319%）还是小数？
        # 数据落库时 _standardize_daily 直接透传，实测为小数（0.004319）；
        # 兼容两种口径：均值 > 1 判定为百分数。
        finite = t[np.isfinite(t) & (t > 0)]
        if finite.size and float(np.mean(finite)) > 1.0:
            t = t / 100.0
        t = np.nan_to_num(t, nan=0.0, posinf=0.0, neginf=0.0)
        if np.any(t > 0):
            return np.clip(t, 0.0, MAX_TURNOVER)

    v = df["volume"].cast(pl.Float64, strict=False).fill_null(0.0).to_numpy()
    v = np.nan_to_num(v, nan=0.0)
    n = v.size
    win = min(60, max(1, n))
    base = np.array(
        [v[max(0, i - win + 1) : i + 1].mean() for i in range(n)], dtype=np.float64
    )
    with np.errstate(divide="ignore", invalid="ignore"):
        t = np.where(base > 0, v / base, 0.0) * FALLBACK_TURNOVER
    return np.clip(np.nan_to_num(t), 0.0, MAX_TURNOVER)


def chip_distribution(
    df: pl.DataFrame,
    current_price: float | None = None,
    lookback: int = 120,
    bins: int = 200,
    curve_points: int = 60,
) -> dict[str, Any]:
    """由日线计算筹码分布指标。

    :param df:            含 date/high/low/close/volume/amount[/turnover] 的日线（升序）
    :param current_price: 现价；None 时取最后一根收盘价
    :param lookback:      回看交易日数
    :param bins:          价格网格数量
    :return: dict（avg_cost / profit_ratio / trapped_ratio / p5 / p95 /
             concentration / current_price / as_of / curve / note）
    """
    need = {"high", "low", "close", "volume", "amount"}
    if df.is_empty() or not need.issubset(set(df.columns)):
        raise ValueError(f"日线数据缺少必要列: {need}")
    if lookback < 2 or bins < 10:
        raise ValueError("lookback 必须 >= 2 且 bins 必须 >= 10")

    d = df.tail(lookback)
    n = d.height
    if n < 2:
        raise ValueError("有效日线不足 2 根，无法计算筹码分布")

    high = d["high"].cast(pl.Float64, strict=False).to_numpy()
    low = d["low"].cast(pl.Float64, strict=False).to_numpy()
    close = d["close"].cast(pl.Float64, strict=False).to_numpy()
    volume = d["volume"].cast(pl.Float64, strict=False).to_numpy()
    amount = d["amount"].cast(pl.Float64, strict=False).to_numpy()
    high = np.nan_to_num(high, nan=0.0)
    low = np.nan_to_num(low, nan=0.0)
    close = np.nan_to_num(close, nan=0.0)
    volume = np.nan_to_num(volume, nan=0.0)
    amount = np.nan_to_num(amount, nan=0.0)

    current = float(current_price if current_price else close[-1])
    if current <= 0:
        raise ValueError("现价必须 > 0")

    # ---- 每日成交均价（VWAP），异常时回退典型价 ----
    with np.errstate(divide="ignore", invalid="ignore"):
        vwap = np.where(volume > 0, amount / np.where(volume > 0, volume, 1.0), np.nan)
    typical = (high + low + close) / 3.0
    vwap = np.where(np.isfinite(vwap), vwap, typical)
    vwap = np.clip(vwap, low, high)

    # ---- 换手衰减权重（新 -> 旧） ----
    t = _turnover_series(d)
    keep = 1.0
    w = np.zeros(n, dtype=np.float64)
    for i in range(n - 1, -1, -1):
        w[i] = t[i] * keep
        keep *= 1.0 - t[i]
    w[0] += keep  # 早于回看窗口的基底筹码并入最老一根
    total = float(w.sum())
    if total <= 0:
        raise ValueError("换手率全为 0，无法构建筹码分布")
    w = w / total

    # ---- 价格网格 ----
    lo = float(np.min(low[low > 0])) if np.any(low > 0) else current * 0.8
    hi = float(np.max(high))
    if not np.isfinite(lo) or not np.isfinite(hi) or hi <= lo:
        lo, hi = current * 0.8, current * 1.2
    lo, hi = lo * 0.98, hi * 1.02
    if current < lo:
        lo = current * 0.98
    if current > hi:
        hi = current * 1.02

    edges = np.linspace(lo, hi, bins + 1)
    centers = (edges[:-1] + edges[1:]) / 2.0
    step = float(edges[1] - edges[0])
    chips = np.zeros(bins, dtype=np.float64)

    for i in range(n):
        l, h, m = low[i], high[i], vwap[i]
        if h <= l or w[i] <= 0:
            continue
        left = max(l, lo)
        right = min(h, hi)
        if right <= left:  # 当日区间落在网格外（极端跳空），压到最近格
            idx = int(np.argmin(np.abs(centers - m)))
            chips[idx] += w[i]
            continue
        sel = (centers >= left) & (centers <= right)
        if not sel.any():  # 当日振幅小于网格步长，全部落入最近格
            idx = int(np.argmin(np.abs(centers - m)))
            chips[idx] += w[i]
            continue
        p = centers[sel]
        # 三角形分布：峰值在 VWAP，向 low / high 线性衰减
        if m > l:
            up = (p - l) / (m - l)
        else:
            up = np.ones_like(p)
        if h > m:
            down = (h - p) / (h - m)
        else:
            down = np.ones_like(p)
        density = np.clip(np.minimum(up, down), 0.0, None)
        s = float(density.sum())
        if s <= 0:
            density = np.ones_like(p)
            s = float(density.sum())
        chips[sel] += w[i] * density / s

    total = float(chips.sum())
    if total <= 0:
        raise ValueError("筹码分布计算为空")
    chips = chips / total

    # ---- 指标 ----
    avg_cost = float(np.sum(centers * chips))
    profit_mask = centers <= current
    profit_ratio = float(chips[profit_mask].sum())
    cdf = np.cumsum(chips)
    p5 = float(centers[int(np.searchsorted(cdf, 0.05))]) if bins else lo
    p95 = float(centers[int(np.searchsorted(cdf, 0.95))]) if bins else hi
    mid = (p5 + p95) / 2.0
    concentration = float((p95 - p5) / mid) if mid > 0 else None

    # 展示用曲线（等距降采样到 curve_points 个柱）
    if bins > curve_points:
        grp = bins // curve_points
        used = grp * curve_points
        agg = chips[:used].reshape(curve_points, grp).sum(axis=1)
        ctr = centers[:used].reshape(curve_points, grp).mean(axis=1)
    else:
        agg, ctr = chips, centers
    peak = float(agg.max()) or 1.0
    curve = [
        {"price": round(float(p), 4), "pct": round(float(v / peak), 4)}
        for p, v in zip(ctr, agg)
    ]

    as_of = None
    if "date" in d.columns:
        as_of = str(d["date"][-1])

    return {
        "as_of": as_of,
        "current_price": round(current, 4),
        "avg_cost": round(avg_cost, 4),
        "profit_ratio": round(profit_ratio, 6),
        "trapped_ratio": round(1.0 - profit_ratio, 6),
        "p5": round(p5, 4),
        "p95": round(p95, 4),
        "concentration": round(concentration, 6) if concentration is not None else None,
        "lookback": n,
        "grid_step": round(step, 6),
        "curve": curve,
        "note": "日频换手衰减近似模型，非 Level-2 真实盘口筹码，仅作研究参考",
    }
