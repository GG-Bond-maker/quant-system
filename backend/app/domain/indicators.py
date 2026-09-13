"""
K 线技术指标计算（纯函数，Polars 驱动，无 IO）。

供 /stock/{symbol}/kline 接口在复权后价格上叠加：
- MA(5/10/20/60/120/250)：简单移动平均；
- MACD(12,26,9)：DIF/DEA/BAR（原始量纲，供副图展示）；
- RSI(14)：Wilder 平滑；
- BOLL(20,2)：中轨/上轨/下轨。

与 ml/features.py 的区别：本模块输出【原始量纲】用于可视化，
features.py 输出【归一化因子】用于模型训练。
"""
from __future__ import annotations

import polars as pl

DEFAULT_MA_WINDOWS = (5, 10, 20, 60, 120, 250)


def add_ma(
    df: pl.DataFrame,
    windows: tuple[int, ...] = DEFAULT_MA_WINDOWS,
    col: str = "close",
) -> pl.DataFrame:
    """叠加简单移动平均列 ma_{w}（窗口不足处为 null）。"""
    return df.with_columns([
        pl.col(col).rolling_mean(window_size=w).alias(f"ma_{w}")
        for w in windows
    ])


def add_macd(
    df: pl.DataFrame,
    col: str = "close",
    fast: int = 12,
    slow: int = 26,
    signal: int = 9,
) -> pl.DataFrame:
    """叠加 MACD 三列：macd_dif / macd_dea / macd_bar（BAR = 2*(DIF-DEA)）。"""
    dif = pl.col(col).ewm_mean(span=fast, adjust=False) - pl.col(col).ewm_mean(
        span=slow, adjust=False
    )
    dea = dif.ewm_mean(span=signal, adjust=False)
    bar = 2.0 * (dif - dea)
    return df.with_columns([
        dif.alias("macd_dif"),
        dea.alias("macd_dea"),
        bar.alias("macd_bar"),
    ])


def add_rsi(df: pl.DataFrame, period: int = 14, col: str = "close") -> pl.DataFrame:
    """叠加 RSI 列（Wilder 平滑，0~100）。"""
    delta = pl.col(col).diff()
    gain = pl.when(delta > 0).then(delta).otherwise(0.0)
    loss = pl.when(delta < 0).then(-delta).otherwise(0.0)
    avg_gain = gain.ewm_mean(alpha=1.0 / period, adjust=False)
    avg_loss = loss.ewm_mean(alpha=1.0 / period, adjust=False)
    rsi = 100.0 - 100.0 / (1.0 + avg_gain / (avg_loss + 1e-12))
    return df.with_columns(rsi.alias(f"rsi_{period}"))


def add_boll(
    df: pl.DataFrame,
    n: int = 20,
    k: float = 2.0,
    col: str = "close",
) -> pl.DataFrame:
    """叠加布林带三列：boll_mid / boll_up / boll_low。"""
    mid = pl.col(col).rolling_mean(window_size=n)
    std = pl.col(col).rolling_std(window_size=n)
    return df.with_columns([
        mid.alias("boll_mid"),
        (mid + k * std).alias("boll_up"),
        (mid - k * std).alias("boll_low"),
    ])


def enrich_kline(
    df: pl.DataFrame,
    ma_windows: tuple[int, ...] = DEFAULT_MA_WINDOWS,
) -> pl.DataFrame:
    """K 线一站式指标增强：MA + MACD + RSI + BOLL。"""
    out = add_ma(df, windows=ma_windows)
    out = add_macd(out)
    out = add_rsi(out)
    out = add_boll(out)
    return out
