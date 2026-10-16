"""行业中性化（P2-7，纯函数）：因子 ~ β0 + β1·log(市值) + 行业哑元 → 残差。

每日截面独立回归（OLS，numpy lstsq）；NaN 行原样返回 NaN。
验收口径：中性化后各行业残差均值 ≈ 0（哑元满秩时理论上精确为 0）。
"""
from __future__ import annotations

import numpy as np


def neutralize_cross_section(
    factor: np.ndarray,
    log_mcap: np.ndarray,
    industry: np.ndarray,
    weights: np.ndarray | None = None,
) -> np.ndarray:
    """单日截面中性化。industry 为字符串数组；任一输入 NaN/None 的行返回 NaN。

    支持 WLS 加权（weights 如 sqrt(市值)），抑制微盘股异方差噪点。
    """
    factor = np.asarray(factor, dtype=np.float64)
    log_mcap = np.asarray(log_mcap, dtype=np.float64)
    ok = np.isfinite(factor) & np.isfinite(log_mcap) & np.array(
        [i is not None and isinstance(i, str) and i != "" for i in industry])
    if weights is not None:
        w_arr = np.asarray(weights, dtype=np.float64)
        ok = ok & np.isfinite(w_arr) & (w_arr > 0)
    out = np.full(factor.shape, np.nan)
    idx = np.where(ok)[0]
    if idx.size < 3:
        return out

    y = factor[idx]
    industries = sorted({industry[i] for i in idx})
    ind_idx = {name: k for k, name in enumerate(industries)}
    # 设计矩阵：截距 + log_mcap + (K-1) 个行业哑元（防共线，第一行业为基准）
    # ⚠️ 必须零初始化：np.ones 会让基准行业哑元残留 1，与截距完全共线，
    # 导致行业效应永远无法被剔除（真实 bug，由测试 test_industry_means_near_zero 守卫）
    X = np.zeros((idx.size, 2 + len(industries) - 1))
    X[:, 0] = 1.0
    X[:, 1] = log_mcap[idx]
    for r, i in enumerate(idx):
        k = ind_idx[industry[i]]
        if k > 0:
            X[r, 1 + k] = 1.0

    if weights is not None:
        w = np.asarray(weights, dtype=np.float64)[idx]
        sw = np.sqrt(w)
        # WLS: (W^1/2 X) beta = W^1/2 y
        X_star = X * sw[:, None]
        y_star = y * sw
        beta, *_ = np.linalg.lstsq(X_star, y_star, rcond=None)
    else:
        beta, *_ = np.linalg.lstsq(X, y, rcond=None)
    out[idx] = y - X @ beta
    return out


def apply_industry_neutral(
    df,  # polars/pandas DataFrame: date, factor, market_cap, industry_sw1
    factor_col: str = "factor",
    mcap_col: str = "market_cap",
    industry_col: str = "industry_sw1",
    date_col: str = "date",
    out_col: str = "factor_neutral",
    method: str = "ols",  # "ols" | "wls"
):
    """按日期分组的行业中性化（跨日独立，无滚动/无未来信息）。

    method="wls" 时采用 Barra 标准加权（权重 = sqrt(market_cap)）。
    """
    import polars as pl

    if isinstance(df, pl.DataFrame):
        pdf = df.to_pandas()
    else:
        pdf = df.copy()
    import numpy as np

    out = np.full(len(pdf), np.nan)
    for _, g in pdf.groupby(date_col):
        idx = g.index.to_numpy()
        mcap_vals = g[mcap_col].to_numpy(dtype=np.float64).clip(min=1e-9)
        weights = np.sqrt(mcap_vals) if method == "wls" else None
        res = neutralize_cross_section(
            g[factor_col].to_numpy(dtype=np.float64),
            np.log(mcap_vals),
            g[industry_col].to_numpy(dtype=object),
            weights=weights,
        )
        out[idx] = res
    if isinstance(df, pl.DataFrame):
        return df.with_columns(pl.Series(out_col, out))
    pdf[out_col] = out
    return pdf
