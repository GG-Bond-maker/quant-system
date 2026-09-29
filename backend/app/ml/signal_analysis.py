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


def overlapping_t_stat(mean: float, std: float, n: int, horizon: int) -> float:
    """重叠样本下的 t 统计量（审计 P1-25，2026-09-21）。

    ⚠️ **原实现的数值口径错误**：h 日远期收益在**相邻日期之间重叠 h−1 天**
    （例如 h=5 时，t 与 t+1 的标签共享 4 天），因此 `n` 个观测**不是** n 个
    独立样本，`mean/std × sqrt(n)` 会把 t 值**放大 ≈√h 倍**
    （h=20 时约 4.5 倍 ⇒ 一个平庸信号也能"显著"）。

    此处按「每 h 天算一个独立块」的保守口径校正：`n_eff = n / h`。
    等价于把重叠序列降采样为非重叠样本后的 t 值；严格做法是 Newey-West
    （lag = h−1）HAC，本函数是其保守近似，且 h=1 时与朴素公式**完全一致**
    （故 1 日口径不受影响）。

    :param mean: 观测均值（h 日期收益/IC 的均值）
    :param std: 观测标准差（ddof=1）
    :param n: 观测数（= 日期数，**含重叠**）
    :param horizon: 远期窗口 h（>=1）
    :return: 校正后的 t 统计量；样本不足或 std≈0 时返回 nan
    """
    if horizon < 1:
        raise ValueError(f"horizon 必须 >= 1，收到 {horizon}")
    if n < 2 or not np.isfinite(mean) or not np.isfinite(std) or std <= 1e-12:
        return float("nan")
    n_eff = n / horizon
    if n_eff < 2:
        return float("nan")
    return float(mean / std * np.sqrt(n_eff))


def ic_decay_report(
    signal: pd.DataFrame,
    close: pd.DataFrame,
    horizons: tuple[int, ...] = (1, 5, 10, 20),
) -> pd.DataFrame:
    """IC 衰减报告：每个 horizon 的日度 Rank IC 统计。

    :param signal: DataFrame[date, symbol, pred_score]
    :param close:  DataFrame[date, symbol, close]
    :return: DataFrame，每行一个 horizon：
             [horizon, mean_ic, std_ic, icir, t_stat, positive_ratio,
              n_days, n_independent]

    ⚠️ t 口径（审计 P1-25 修正）：`t_stat` 现按**重叠样本校正**计算
    （`mean/std × sqrt(n/h)`，见 :func:`overlapping_t_stat`）；`n_days` 仍是
    原始日期数，另给 `n_independent`（= n/h）以便读者判断有效样本量。
    原实现直接用 `sqrt(n)`，h>1 时 t 值虚高 ≈√h 倍。
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
                         "positive_ratio": np.nan, "n_days": 0,
                         "n_independent": 0.0})
            continue
        mean, std = float(ics.mean()), float(ics.std(ddof=1))
        n = int(ics.size)
        t_stat = overlapping_t_stat(mean, std, n, h)
        rows.append({
            "horizon": h,
            "mean_ic": round(mean, 6),
            "std_ic": round(std, 6),
            "icir": round(mean / std, 4) if std > 1e-12 else np.nan,
            "t_stat": round(float(t_stat), 4) if np.isfinite(t_stat) else np.nan,
            "positive_ratio": round(float((ics > 0).mean()), 4),
            "n_days": n,
            # 有效独立样本量（审计 P1-25）：重叠窗口下 n 个日期只有 n/h 个独立块
            "n_independent": round(n / h, 1),
        })
    return pd.DataFrame(rows)


def quantile_spread_report(
    signal: pd.DataFrame,
    close: pd.DataFrame,
    horizon: int = 5,
    n_quantiles: int = 5,
) -> dict[str, object]:
    """分层多空报告：按信号分位分组的远期收益。

    :return: 实际返回的键（**审计 P1-25 修正：原 docstring 声称返回
             `long_short_daily` 与 `ls_mean`，二者都不存在，属文档失真**）：
             `horizon` / `n_quantiles` / `quantile_mean_ret`（Q1..Qg 的
             **h 日**远期收益均值）/ `long_short_nav`（多空净值序列，
             [{date, nav}]，前端画图用）/ `ls_mean_daily`（每日期多空价差
             均值，**单位是 h 日期收益，不是日收益**）/ `ls_t_stat`
             （重叠校正后）/ `ls_annualized` / `monotonic` / `n_days` /
             `n_independent` / `annualization_basis` / `t_stat_basis`

    ⚠️ 两处数值口径修正（审计 P1-25，2026-09-21）：
        ① **年化放大 h 倍**：`ls_daily` 每个元素是 **h 日**远期收益
           （`forward_return_pivot(..., h)`），而原实现 `ann = mean × 252`
           把 h 日期收益当**日**收益年化 ⇒ 放大 **h 倍**（调用方传
           `max(horizons)=20` ⇒ 20 倍）。现按 `252 / h` 年化。
        ② **t 值重叠高估 ≈√h**：h 日窗口在相邻日期重叠 h−1 天，原实现
           按 `sqrt(n)` 计算 ⇒ n=20 时 t 虚高约 4.5 倍。现用
           :func:`overlapping_t_stat`（n_eff = n/h）。
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
        t = overlapping_t_stat(mean_ls, std_ls, len(ls_daily), horizon)
        # 审计 P1-25：ls_daily 元素是 **h 日**远期收益 ⇒ 年化因子为 252/h
        # （原实现 × 252 把 h 日期收益当日期收益，放大 h 倍）。
        ann = mean_ls * (TRADING_DAYS / horizon)
    else:
        mean_ls, t, ann = np.nan, np.nan, np.nan

    q_vals = [q_mean[k] for k in range(1, n_quantiles + 1) if np.isfinite(q_mean[k])]
    monotonic = all(q_vals[i] <= q_vals[i + 1] for i in range(len(q_vals) - 1)) \
        if len(q_vals) == n_quantiles else False

    # 多空净值序列（审计 P1-25：前端读的 `long_short_nav` **此前根本不存在**
    # ⇒ 前端图表恒空、且 TS 类型也写错，静默拿到 undefined）。
    # 口径：按观测日复利 `(1 + 价差)`；因价差是 h 日期收益且窗口重叠，
    # 该曲线是「每 h 日滚动一次的多空累计表现」的近似，故随附 basis 披露。
    nav_series: list[dict] = []
    if not ls_daily.empty:
        nav = 1.0
        for d, v in ls_daily.items():
            fv = float(v)
            if not np.isfinite(fv):
                continue
            nav *= (1.0 + fv)
            nav_series.append({"date": str(d)[:10], "nav": round(nav, 6)})

    return {
        "horizon": horizon,
        "n_quantiles": n_quantiles,
        "quantile_mean_ret": {f"Q{k}": round(v, 6)
                              for k, v in q_mean.items()},
        "long_short_nav": nav_series,
        "ls_mean_daily": round(mean_ls, 6) if np.isfinite(mean_ls) else None,
        "ls_t_stat": round(float(t), 4) if np.isfinite(t) else None,
        "ls_annualized": round(ann, 6) if np.isfinite(ann) else None,
        "monotonic": monotonic,
        "n_days": int(len(ls_daily)),
        "n_independent": round(len(ls_daily) / horizon, 1),
        # 口径披露（契约第 6 条：派生指标必须披露计算依据）
        "annualization_basis": f"mean_h_day_spread * 252 / {horizon}",
        "t_stat_basis": f"overlap-adjusted n_eff=n/{horizon}",
        "nav_basis": (f"compounded (1+{horizon}-day long-short spread) "
                      f"per observation date; windows overlap"),
        "ls_mean_basis": f"mean of {horizon}-day forward spreads (NOT daily)",
    }
