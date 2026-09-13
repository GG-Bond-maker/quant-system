"""信号分析（参考 microsoft/qlib 的 Signal Analysis：IC / ICIR / 分层多空）。

回答两个机构级问题：
1. Alpha 衰减速度：信号对未来 1/5/10/20 日收益的 Rank IC 及其衰减曲线
   —— 决定调仓频率与持有期（IC 衰减快 => 高频调仓，慢 => 低频）；
2. 分层单调性与多空价差：按信号分位分组的远期收益是否单调，
   多空价差（Q1 vs Qg）是否显著（t 统计量）。

全部纯函数（pandas/numpy），无 IO；输入均为宽表 pivot（date × symbol）。
"""
from __future__ import annotations

import numpy as np
import pandas as pd

TRADING_DAYS = 252


def forward_return_pivot(close_pivot: pd.DataFrame, horizon: int) -> pd.DataFrame:
    """未来 N 日收益宽表：close[t+h]/close[t] - 1（date × symbol）。

    注意这是"标签"性质的数据（含未来信息），仅用于评估，不得进特征。
    """
    return close_pivot.shift(-horizon) / close_pivot - 1.0


def daily_rank_ic(signal_pivot: pd.DataFrame, fwd_pivot: pd.DataFrame) -> pd.Series:
    """逐日截面 Rank IC（Spearman）：两宽表按行做秩相关。

    corrwith(axis=1) 按行逐对丢弃 NaN，等价于每日截面 Spearman。
    """
    aligned_sig = signal_pivot.reindex(columns=fwd_pivot.columns)
    return aligned_sig.rank(axis=1).corrwith(fwd_pivot.rank(axis=1), axis=1).dropna()


def ic_decay_report(
    signal: pd.DataFrame,
    close: pd.DataFrame,
    horizons: tuple[int, ...] = (1, 5, 10, 20),
) -> pd.DataFrame:
    """IC 衰减报告：每个 horizon 的日度 Rank IC 统计。

    :param signal: DataFrame[date, symbol, pred_score]
    :param close:  DataFrame[date, symbol, close]
    :return: DataFrame，每行一个 horizon：
             [horizon, mean_ic, std_ic, icir, t_stat, positive_ratio, n_days]
             t_stat = mean/std × sqrt(n)（近似 Newey-West 简化口径，HAC 需另行校正）
    """
    close_pivot = close.pivot_table(index="date", columns="symbol",
                                    values="close", aggfunc="last")
    sig_pivot = signal.pivot_table(index="date", columns="symbol",
                                   values="pred_score", aggfunc="last")
    # 仅保留信号与价格同时可得的交集
    common_dates = close_pivot.index.intersection(sig_pivot.index)
    close_pivot = close_pivot.loc[common_dates]
    sig_pivot = sig_pivot.loc[common_dates]

    rows: list[dict] = []
    for h in horizons:
        if h < 1:
            raise ValueError(f"horizon 必须 >= 1，收到 {h}")
        fwd = forward_return_pivot(close_pivot, h)
        ics = daily_rank_ic(sig_pivot, fwd)
        if ics.empty:
            rows.append({"horizon": h, "mean_ic": np.nan, "std_ic": np.nan,
                         "icir": np.nan, "t_stat": np.nan,
                         "positive_ratio": np.nan, "n_days": 0})
            continue
        mean, std = float(ics.mean()), float(ics.std(ddof=1))
        n = int(ics.size)
        t_stat = (mean / std * np.sqrt(n)) if std > 1e-12 else np.nan
        rows.append({
            "horizon": h,
            "mean_ic": round(mean, 6),
            "std_ic": round(std, 6),
            "icir": round(mean / std, 4) if std > 1e-12 else np.nan,
            "t_stat": round(float(t_stat), 4) if np.isfinite(t_stat) else np.nan,
            "positive_ratio": round(float((ics > 0).mean()), 4),
            "n_days": n,
        })
    return pd.DataFrame(rows)


def quantile_spread_report(
    signal: pd.DataFrame,
    close: pd.DataFrame,
    horizon: int = 5,
    n_quantiles: int = 5,
) -> dict[str, object]:
    """分层多空报告：按信号分位分组的远期收益。

    :return: {horizon, n_quantiles, quantile_mean_ret (Q1..Qg 日均远期收益),
              long_short_daily (多空价差日度序列), ls_mean, ls_t_stat,
              ls_annualized, monotonic (分组收益是否单调)}
    """
    close_pivot = close.pivot_table(index="date", columns="symbol",
                                    values="close", aggfunc="last")
    sig_pivot = signal.pivot_table(index="date", columns="symbol",
                                   values="pred_score", aggfunc="last")
    common_dates = close_pivot.index.intersection(sig_pivot.index)
    close_pivot = close_pivot.loc[common_dates]
    sig_pivot = sig_pivot.loc[common_dates]

    fwd = forward_return_pivot(close_pivot, horizon)
    ranks = sig_pivot.rank(axis=1, pct=True)
    q = pd.DataFrame(np.ceil(ranks * n_quantiles).clip(1, n_quantiles),
                     index=ranks.index, columns=ranks.columns)

    q_mean: dict[int, float] = {}
    for k in range(1, n_quantiles + 1):
        mask = (q == k) & fwd.notna()
        vals = fwd[mask].to_numpy(dtype=np.float64)
        vals = vals[np.isfinite(vals)]
        q_mean[k] = float(vals.mean()) if vals.size else np.nan

    # 多空价差：每日 top 分位均值 - bottom 分位均值（等权）
    long_mask = (q == n_quantiles) & fwd.notna()
    short_mask = (q == 1) & fwd.notna()
    ls_daily = fwd[long_mask].mean(axis=1) - fwd[short_mask].mean(axis=1)
    ls_daily = ls_daily.dropna()

    if len(ls_daily) >= 2:
        mean_ls = float(ls_daily.mean())
        std_ls = float(ls_daily.std(ddof=1))
        t = mean_ls / std_ls * np.sqrt(len(ls_daily)) if std_ls > 1e-12 else np.nan
        ann = mean_ls * TRADING_DAYS
    else:
        mean_ls, t, ann = np.nan, np.nan, np.nan

    q_vals = [q_mean[k] for k in range(1, n_quantiles + 1) if np.isfinite(q_mean[k])]
    monotonic = all(q_vals[i] <= q_vals[i + 1] for i in range(len(q_vals) - 1)) \
        if len(q_vals) == n_quantiles else False

    return {
        "horizon": horizon,
        "n_quantiles": n_quantiles,
        "quantile_mean_ret": {f"Q{k}": round(v, 6)
                              for k, v in q_mean.items()},
        "ls_mean_daily": round(mean_ls, 6) if np.isfinite(mean_ls) else None,
        "ls_t_stat": round(float(t), 4) if np.isfinite(t) else None,
        "ls_annualized": round(ann, 6) if np.isfinite(ann) else None,
        "monotonic": monotonic,
        "n_days": int(len(ls_daily)),
    }
