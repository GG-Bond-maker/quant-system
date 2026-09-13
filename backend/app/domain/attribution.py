"""业绩归因与容量模型（纯函数，无 IO）。

- Brinson-Fachler 行业归因：allocation / selection / interaction，
  行业与权重全部来自真实数据（universe 的 industry 列 + 真实窗口收益）；
- 风格归因（Barra-lite）：组合日收益对风格因子收益序列 OLS，
  风格因子收益 = 截面 zscore 加权的等权市场收益（真实 features 截面）；
- 策略容量：ADV 中位数 × 参与率上限 × 调仓周期折算（真实成交额推导）。
"""
from __future__ import annotations

import numpy as np
import pandas as pd


def brindon_attribution(
    portfolio_w: dict[str, float],
    benchmark_w: dict[str, float],
    returns: pd.Series,                 # symbol -> 窗口收益
    industry: pd.Series,                # symbol -> 行业
) -> dict:
    """单期 Brinson-Fachler：按行业分解超额收益。

    allocation_j = (wp_j − wb_j)(rb_j − R_b)
    selection_j  = wb_j (rp_j − rb_j)
    interaction_j= (wp_j − wb_j)(rp_j − rb_j)
    """
    idx = sorted(set(portfolio_w) | set(benchmark_w))
    wp = pd.Series(portfolio_w).reindex(idx).fillna(0.0)
    wb = pd.Series(benchmark_w).reindex(idx).fillna(0.0)
    r = returns.reindex(idx).dropna()
    wp, wb = wp.reindex(r.index), wb.reindex(r.index)
    ind = industry.reindex(r.index).fillna("未分类")

    rows: list[dict] = []
    total_alloc = total_sel = total_inter = 0.0
    rp_total = float((wp * r).sum())
    rb_total = float((wb * r).sum())
    for j in sorted(ind.unique()):
        m = (ind == j).to_numpy()
        wpj, wbj = float(wp[m].sum()), float(wb[m].sum())
        # 组内收益：权重归一后的加权收益；空组收益记 0
        rpj = float((wp[m] * r[m]).sum() / wpj) if wpj > 1e-12 else 0.0
        rbj = float((wb[m] * r[m]).sum() / wbj) if wbj > 1e-12 else 0.0
        alloc = (wpj - wbj) * (rbj - rb_total)
        sel = wbj * (rpj - rbj)
        inter = (wpj - wbj) * (rpj - rbj)
        total_alloc += alloc
        total_sel += sel
        total_inter += inter
        rows.append({
            "industry": str(j),
            "portfolio_weight": round(wpj, 4),
            "benchmark_weight": round(wbj, 4),
            "portfolio_return": round(rpj, 6),
            "benchmark_return": round(rbj, 6),
            "allocation": round(alloc, 6),
            "selection": round(sel, 6),
            "interaction": round(inter, 6),
            "total": round(alloc + sel + inter, 6),
        })
    return {
        "sectors": sorted(rows, key=lambda x: -abs(x["total"])),
        "summary": {
            "portfolio_return": round(rp_total, 6),
            "benchmark_return": round(rb_total, 6),
            "excess_return": round(rp_total - rb_total, 6),
            "allocation": round(total_alloc, 6),
            "selection": round(total_sel, 6),
            "interaction": round(total_inter, 6),
            # 残差 = 纯粹 Alpha（模型选股在行业中性之外的贡献）
            "residual_alpha": round((rp_total - rb_total)
                                    - (total_alloc + total_sel + total_inter), 6),
        },
    }


def style_factor_returns(
    z_wide: pd.DataFrame,            # date × symbol 的风格因子截面 zscore
    ret_wide: pd.DataFrame,          # date × symbol 日收益
) -> pd.Series:
    """风格因子日收益序列：每日截面 zscore 加权的等权市场收益。"""
    aligned = z_wide.reindex(columns=ret_wide.columns)
    return (aligned * ret_wide).sum(axis=1) / aligned.notna().sum(axis=1).clip(lower=1)


def style_regression_attribution(
    port_ret: pd.Series,             # 组合日收益
    style_rets: dict[str, pd.Series],
) -> dict:
    """OLS：rp − rf = Σ β_k · f_k + α（无 rf，短窗口近似）。

    返回各风格 β、其贡献（β × 因子日均收益）与残差 alpha（年化）。
    """
    df = pd.DataFrame({"rp": port_ret}).dropna()
    for k, s in style_rets.items():
        df[k] = s.reindex(df.index)
    df = df.dropna()
    if len(df) < 30:
        return {"n_days": int(len(df)), "betas": {}, "contributions": {},
                "alpha_annualized": None, "r_squared": None}
    X = df[[c for c in df.columns if c != "rp"]].to_numpy(dtype=np.float64)
    y = df["rp"].to_numpy(dtype=np.float64)
    Xc = np.column_stack([np.ones_like(y), X])
    beta, *_ = np.linalg.lstsq(Xc, y, rcond=None)
    resid = y - Xc @ beta
    ss_res = float((resid ** 2).sum())
    ss_tot = float(((y - y.mean()) ** 2).sum())
    r2 = 1 - ss_res / ss_tot if ss_tot > 1e-18 else None
    names = [c for c in df.columns if c != "rp"]
    betas = {n: round(float(b), 4) for n, b in zip(names, beta[1:])}
    contrib = {n: round(float(b) * float(df[n].mean()) * 252, 6)
               for n, b in zip(names, beta[1:])}
    return {
        "n_days": int(len(df)),
        "betas": betas,
        "contributions": contrib,
        "alpha_annualized": round(float(beta[0]) * 252, 6),
        "r_squared": round(r2, 4) if r2 is not None else None,
    }


def strategy_capacity(
    adv_median: float,               # 全市场/目标池 日均成交额中位数（真实）
    participation_cap: float = 0.01,
    holdings: int = 20,
    rebalance_per_year: int = 12,
    adv_ratio_in_pool: float = 0.02,
) -> dict:
    """策略容量估算（真实口径推导，公式透明）。

    单日可建仓资金 = ADV中位数 × 参与率上限 / 池内成交额占比近似
    容量 ≈ 单日可建仓资金 × 持仓数 ÷ 每次调仓换手比例
    """
    if adv_median <= 0:
        return {"aum_threshold": None}
    per_name_daily = adv_median * participation_cap
    single_rebalance = per_name_daily * holdings
    turnover_ratio = min(1.0, 12.0 / max(rebalance_per_year, 1))  # 月调仓≈全换手
    aum = single_rebalance / max(turnover_ratio, 0.05)
    return {
        "aum_threshold": round(aum, 0),
        "aum_yi": round(aum / 1e8, 2),
        "formula": ("ADV中位数 × 参与率上限 × 持仓数 ÷ 调仓换手比例；"
                    f"ADV中位数={adv_median:,.0f}元, 参与率={participation_cap:.0%}, "
                    f"持仓={holdings}, 年调仓={rebalance_per_year}次"),
    }
