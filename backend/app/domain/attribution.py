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


def capacity_decay_curve(
    adv_median: float,
    participation_cap: float = 0.01,
    holdings: int = 20,
    rebalance_per_year: int = 12,
    gross_return: float = 0.20,
    gross_vol: float = 0.18,
    rf: float = 0.02,
    base_fee_bps: float = 15.0,
    sqrt_coef_bps: float = 30.0,
) -> dict:
    """基于平方根市场冲击定律测算 AUM 规模扩张对净收益与夏普的边际衰减曲面。

    理论模型 (Almgren-Chriss / Barra Impact Model):
    - 冲击模型: impact_bps = sqrt_coef_bps * sqrt((AUM / holdings) / adv_median)
    - 年化交易总成本: annual_cost = (base_fee_bps + impact_bps) * 1e-4 * 2 * rebalance_per_year
    - 净收益: net_return = gross_return - annual_cost
    - 净夏普: net_sharpe = (net_return - rf) / gross_vol
    - Alpha 绝对利润 (万元): alpha_profit_wan = max(0, net_return - rf) * AUM / 10000
    - 黄金最优 AUM: 绝对利润最大化点。
    """
    if adv_median <= 0:
        return {"has_data": False, "curve": [], "optimal_aum": None}

    # 构造从 50 万元到 5 亿元的对数网格 (20 个点)
    aum_grid = np.geomspace(5e5, 5e8, num=20)
    curve_points = []
    best_profit = -1.0
    optimal_aum = float(aum_grid[0])
    optimal_sharpe = 0.0
    breakeven_aum = None

    for aum in aum_grid:
        per_stock_order = aum / max(holdings, 1)
        part_ratio = per_stock_order / adv_median
        impact_bps = float(sqrt_coef_bps * np.sqrt(max(part_ratio, 0.0)))
        total_bps_per_turn = base_fee_bps + impact_bps
        # 双边买卖，年化成本
        annual_cost = float(total_bps_per_turn * 1e-4 * 2.0 * min(rebalance_per_year, 52))
        net_ret = float(gross_return - annual_cost)
        net_sharpe = float((net_ret - rf) / max(gross_vol, 1e-6))
        alpha_profit_wan = float(max(0.0, net_ret - rf) * aum / 1e4)

        if alpha_profit_wan > best_profit:
            best_profit = alpha_profit_wan
            optimal_aum = float(aum)
            optimal_sharpe = net_sharpe

        if breakeven_aum is None and net_sharpe <= 0:
            breakeven_aum = float(aum)

        curve_points.append({
            "aum_yi": round(float(aum / 1e8), 4),
            "aum_wan": round(float(aum / 1e4), 1),
            "participation_pct": round(float(part_ratio * 100), 3),
            "impact_bps": round(impact_bps, 2),
            "total_cost_pct": round(annual_cost * 100, 3),
            "net_return_pct": round(net_ret * 100, 2),
            "net_sharpe": round(net_sharpe, 3),
            "alpha_profit_wan": round(alpha_profit_wan, 2),
        })

    return {
        "has_data": True,
        "adv_median_yi": round(adv_median / 1e8, 3),
        "holdings": holdings,
        "rebalance_per_year": rebalance_per_year,
        "gross_return_pct": round(gross_return * 100, 2),
        "gross_vol_pct": round(gross_vol * 100, 2),
        "optimal_aum_yi": round(optimal_aum / 1e8, 2),
        "optimal_aum_wan": round(optimal_aum / 1e4, 1),
        "optimal_sharpe": round(optimal_sharpe, 3),
        "max_profit_wan": round(best_profit, 1),
        "breakeven_aum_yi": round(breakeven_aum / 1e8, 2) if breakeven_aum else None,
        "curve": curve_points,
    }

