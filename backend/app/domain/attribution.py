"""业绩归因与容量模型（纯函数，无 IO）。

包含：
1. Brinson 行业归因：
   - brinson_attribution：经典单期 Brinson-Fachler（保留 brindon_attribution 兼容别名）；
   - multi_period_brinson_attribution：Menchero / Carino 连乘平滑多期 Brinson 复合归因，
     消除长窗口复利误差，提供时序累积归因分解走势；
2. 个股微观贡献度穿透：
   - stock_level_contributions：计算各持仓标的超额贡献 bps，产出 Top 5 赢家与拖累榜；
3. 持仓流动性短板与锁喉瓶颈分析：
   - portfolio_liquidity_bottleneck：基于持仓各标的 ADV 与参与率上限，测算变现天数（DtL）
     并精准定位组合容量锁喉标的（Chokepoint Asset）；
4. 风格因子归因与 Barra 风险结构分解：
   - style_regression_attribution：OLS 回归带 t 检验显著性，并输出主动跟踪误差方差的
     因子风险 vs 特异风险分解；
5. 策略容量与边际衰减曲面：
   - strategy_capacity：ADV 中位数与换手率容量基准；
   - capacity_decay_curve：Almgren-Chriss 平方根冲击衰减曲面与多变现周期帕累托前沿。
"""
from __future__ import annotations

from typing import Any

import numpy as np
import pandas as pd
from scipy import stats


# ==================== 1. Brinson 行业归因 ====================

def brinson_attribution(
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
            "active_weight": round(wpj - wbj, 4),
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


# 向后兼容别名
brindon_attribution = brinson_attribution


def multi_period_brinson_attribution(
    port_rets_wide: pd.DataFrame,       # date × symbol 日收益率
    bench_rets_wide: pd.DataFrame,      # date × symbol 日收益率
    port_weights: dict[str, float],     # symbol -> 目标权重
    bench_weights: dict[str, float],    # symbol -> 目标权重
    industry: pd.Series,                # symbol -> 行业
) -> dict:
    """基于 Carino (1999) / Menchero (2000) 连乘平滑模型的多期 Brinson-Fachler 归因。

    解决长窗口几何复利断裂问题，保证各期效应连乘累加后**严格等于几何累计超额收益**（残差精确为 0）。
    同时输出每日时序累积归因分解走势（Excess, Allocation, Selection, Interaction）。
    """
    if port_rets_wide.empty or bench_rets_wide.empty:
        return {"sectors": [], "summary": {}, "timeseries": []}

    # 对齐日期
    common_dates = port_rets_wide.index.intersection(bench_rets_wide.index).sort_values()
    if len(common_dates) < 2:
        return {"sectors": [], "summary": {}, "timeseries": []}

    p_rets = port_rets_wide.loc[common_dates]
    b_rets = bench_rets_wide.loc[common_dates]

    # 标的与权重对齐与归一化
    p_w_series = pd.Series(port_weights).reindex(p_rets.columns).fillna(0.0)
    p_tot = p_w_series.sum()
    if p_tot > 0:
        p_w_series = p_w_series / p_tot

    b_w_series = pd.Series(bench_weights).reindex(b_rets.columns).fillna(0.0)
    b_tot = b_w_series.sum()
    if b_tot > 0:
        b_w_series = b_w_series / b_tot

    # 行业映射
    all_syms = sorted(set(p_rets.columns) | set(b_rets.columns))
    ind_series = industry.reindex(all_syms).fillna("未分类")
    unique_sectors = sorted(ind_series.unique())

    # 1. 计算每日组合与基准总收益
    daily_rp = p_rets.mul(p_w_series, axis=1).sum(axis=1)
    daily_rb = b_rets.mul(b_w_series, axis=1).sum(axis=1)

    # 2. 计算每日行业权重与行业组内收益
    n_days = len(common_dates)
    sector_alloc_daily = {s: np.zeros(n_days) for s in unique_sectors}
    sector_sel_daily = {s: np.zeros(n_days) for s in unique_sectors}
    sector_inter_daily = {s: np.zeros(n_days) for s in unique_sectors}
    sector_wp_mean = {s: 0.0 for s in unique_sectors}
    sector_wb_mean = {s: 0.0 for s in unique_sectors}

    for t_idx, d in enumerate(common_dates):
        rp_t = float(daily_rp.iloc[t_idx])
        rb_t = float(daily_rb.iloc[t_idx])
        row_p = p_rets.iloc[t_idx]
        row_b = b_rets.iloc[t_idx]

        for s in unique_sectors:
            syms_in_s = ind_series[ind_series == s].index
            p_syms = [sym for sym in syms_in_s if sym in p_rets.columns]
            b_syms = [sym for sym in syms_in_s if sym in b_rets.columns]

            wp_s = float(p_w_series.reindex(p_syms).sum())
            wb_s = float(b_w_series.reindex(b_syms).sum())

            rp_s = float((row_p.reindex(p_syms) * p_w_series.reindex(p_syms)).sum() / wp_s) if wp_s > 1e-12 else 0.0
            rb_s = float((row_b.reindex(b_syms) * b_w_series.reindex(b_syms)).sum() / wb_s) if wb_s > 1e-12 else 0.0

            alloc = (wp_s - wb_s) * (rb_s - rb_t)
            sel = wb_s * (rp_s - rb_s)
            inter = (wp_s - wb_s) * (rp_s - rb_s)

            sector_alloc_daily[s][t_idx] = alloc
            sector_sel_daily[s][t_idx] = sel
            sector_inter_daily[s][t_idx] = inter

            sector_wp_mean[s] += wp_s / n_days
            sector_wb_mean[s] += wb_s / n_days

    # 3. 几何累计收益与 Carino 连乘平滑系数
    cum_rp = float((1.0 + daily_rp).prod() - 1.0)
    cum_rb = float((1.0 + daily_rb).prod() - 1.0)
    cum_excess = cum_rp - cum_rb

    # Carino Linking Factor kt: kt = at * (cum_excess / diff_log)
    diff_log = np.log(1.0 + cum_rp) - np.log(1.0 + cum_rb)
    if abs(diff_log) < 1e-12 or abs(cum_excess) < 1e-12:
        k_scaler = 1.0
    else:
        k_scaler = cum_excess / diff_log

    k_t = np.zeros(n_days)
    for t_idx in range(n_days):
        rpt = float(daily_rp.iloc[t_idx])
        rbt = float(daily_rb.iloc[t_idx])
        diff_t = rpt - rbt
        if abs(diff_t) < 1e-12:
            at = 1.0 / (1.0 + rbt)
        else:
            at = (np.log(1.0 + rpt) - np.log(1.0 + rbt)) / diff_t
        k_t[t_idx] = at * k_scaler

    # 4. 汇总各行业多期平滑累积效应
    sector_rows = []
    tot_alloc = tot_sel = tot_inter = 0.0

    for s in unique_sectors:
        alloc_s = float((k_t * sector_alloc_daily[s]).sum())
        sel_s = float((k_t * sector_sel_daily[s]).sum())
        inter_s = float((k_t * sector_inter_daily[s]).sum())
        tot_s = alloc_s + sel_s + inter_s

        tot_alloc += alloc_s
        tot_sel += sel_s
        tot_inter += inter_s

        sector_rows.append({
            "industry": str(s),
            "portfolio_weight": round(sector_wp_mean[s], 4),
            "benchmark_weight": round(sector_wb_mean[s], 4),
            "active_weight": round(sector_wp_mean[s] - sector_wb_mean[s], 4),
            "allocation": round(alloc_s, 6),
            "selection": round(sel_s, 6),
            "interaction": round(inter_s, 6),
            "total": round(tot_s, 6),
        })

    # 5. 构建每日时序累积走势（供 P1 时序瀑布与面积图）
    timeseries = []
    alloc_cumsum = np.zeros(n_days)
    sel_cumsum = np.zeros(n_days)
    inter_cumsum = np.zeros(n_days)

    total_alloc_daily = np.sum([sector_alloc_daily[s] for s in unique_sectors], axis=0)
    total_sel_daily = np.sum([sector_sel_daily[s] for s in unique_sectors], axis=0)
    total_inter_daily = np.sum([sector_inter_daily[s] for s in unique_sectors], axis=0)

    cur_port_nav = 1.0
    cur_bench_nav = 1.0
    for t_idx, d in enumerate(common_dates):
        cur_port_nav *= (1.0 + daily_rp.iloc[t_idx])
        cur_bench_nav *= (1.0 + daily_rb.iloc[t_idx])
        alloc_cumsum[t_idx] = (alloc_cumsum[t_idx - 1] if t_idx > 0 else 0.0) + k_t[t_idx] * total_alloc_daily[t_idx]
        sel_cumsum[t_idx] = (sel_cumsum[t_idx - 1] if t_idx > 0 else 0.0) + k_t[t_idx] * total_sel_daily[t_idx]
        inter_cumsum[t_idx] = (inter_cumsum[t_idx - 1] if t_idx > 0 else 0.0) + k_t[t_idx] * total_inter_daily[t_idx]

        timeseries.append({
            "date": d.strftime("%Y-%m-%d") if hasattr(d, "strftime") else str(d)[:10],
            "portfolio_cum_ret": round(cur_port_nav - 1.0, 6),
            "benchmark_cum_ret": round(cur_bench_nav - 1.0, 6),
            "excess_cum_ret": round((cur_port_nav - 1.0) - (cur_bench_nav - 1.0), 6),
            "cum_allocation": round(alloc_cumsum[t_idx], 6),
            "cum_selection": round(sel_cumsum[t_idx], 6),
            "cum_interaction": round(inter_cumsum[t_idx], 6),
        })

    return {
        "sectors": sorted(sector_rows, key=lambda x: -abs(x["total"])),
        "summary": {
            "portfolio_return": round(cum_rp, 6),
            "benchmark_return": round(cum_rb, 6),
            "excess_return": round(cum_excess, 6),
            "allocation": round(tot_alloc, 6),
            "selection": round(tot_sel, 6),
            "interaction": round(tot_inter, 6),
            # Menchero 连乘平滑下残差理论上为 0
            "residual_alpha": round(cum_excess - (tot_alloc + tot_sel + tot_inter), 6),
        },
        "timeseries": timeseries,
    }


# ==================== 2. 个股微观贡献度穿透 ====================

def stock_level_contributions(
    port_rets_wide: pd.DataFrame,
    port_weights: dict[str, float],
    bench_cum_ret: float,
    industry: pd.Series,
    names: dict[str, str] | None = None,
) -> dict:
    """计算组合内各标的的微观盈亏与超额贡献度（bps）。

    产出 Top 5 赢家标的、Top 5 拖累标的以及完整持仓盈亏下钻明细。
    """
    if port_rets_wide.empty or not port_weights:
        return {"top_contributors": [], "bottom_drags": [], "all_holdings": []}

    names = names or {}
    holdings_list = []
    tot_weight = sum(port_weights.values()) or 1.0

    for sym, raw_w in port_weights.items():
        if sym not in port_rets_wide.columns:
            continue
        w = raw_w / tot_weight
        s_rets = port_rets_wide[sym].dropna()
        if s_rets.empty:
            continue
        cum_ret = float((1.0 + s_rets).prod() - 1.0)
        pnl_contrib = w * cum_ret
        excess_contrib = w * (cum_ret - bench_cum_ret)

        holdings_list.append({
            "symbol": sym,
            "name": names.get(sym, sym),
            "industry": str(industry.get(sym, "未分类")),
            "weight": round(w, 4),
            "cum_return": round(cum_ret, 6),
            "pnl_contrib_pct": round(pnl_contrib * 100, 3),
            "excess_contrib_bps": round(excess_contrib * 10000, 1),
        })

    holdings_sorted = sorted(holdings_list, key=lambda x: -x["excess_contrib_bps"])
    top_5 = holdings_sorted[:5]
    bottom_5 = sorted(holdings_list, key=lambda x: x["excess_contrib_bps"])[:5]

    return {
        "top_contributors": top_5,
        "bottom_drags": bottom_5,
        "all_holdings": holdings_sorted,
    }


# ==================== 3. 持仓流动性短板与锁喉瓶颈 ====================

def portfolio_liquidity_bottleneck(
    weights: dict[str, float],
    adv_map: dict[str, float],
    target_aum: float = 5e7,
    participation_cap: float = 0.10,
    liquidation_days_target: int = 3,
    names: dict[str, str] | None = None,
    industry: dict[str, str] | None = None,
) -> dict:
    """基于持仓异质性流动性数据，测算各持仓变现天数（DtL）与组合容量锁喉资产。

    - 变现天数 DtL_i = (w_i × AUM) / (participation_cap × ADV_i)
    - 标的容量上限 Capacity_i = (participation_cap × ADV_i × target_days) / w_i
    - 组合木桶短板 = min(Capacity_i)
    """
    if not weights or not adv_map:
        return {
            "has_data": False,
            "bottleneck_symbol": None,
            "bottleneck_name": None,
            "bottleneck_capacity_yi": None,
            "holdings": [],
        }

    names = names or {}
    industry = industry or {}
    tot_w = sum(weights.values()) or 1.0
    norm_w = {k: v / tot_w for k, v in weights.items() if v > 0}

    holdings_metrics = []
    min_capacity = float("inf")
    bottleneck_sym = None
    weighted_dtl = 0.0

    for sym, w in norm_w.items():
        adv = float(adv_map.get(sym, 0.0))
        pos_val = w * target_aum
        # 兜底避免除零
        safe_adv = max(adv, 1e4)
        daily_cap = safe_adv * participation_cap
        dtl = pos_val / daily_cap
        single_cap = (daily_cap * liquidation_days_target) / max(w, 1e-6)

        weighted_dtl += w * dtl

        if single_cap < min_capacity:
            min_capacity = single_cap
            bottleneck_sym = sym

        stress = "low"
        if dtl > liquidation_days_target:
            stress = "high"
        elif dtl > 1.0:
            stress = "medium"

        holdings_metrics.append({
            "symbol": sym,
            "name": names.get(sym, sym),
            "industry": str(industry.get(sym, "未分类")),
            "weight": round(w, 4),
            "position_val_wan": round(pos_val / 1e4, 1),
            "adv_wan": round(adv / 1e4, 1),
            "days_to_liquidate": round(dtl, 2),
            "capacity_limit_yi": round(single_cap / 1e8, 2),
            "stress_level": stress,
        })

    holdings_metrics.sort(key=lambda x: -x["days_to_liquidate"])

    return {
        "has_data": True,
        "target_aum_yi": round(target_aum / 1e8, 2),
        "participation_cap_pct": round(participation_cap * 100, 1),
        "liquidation_days_target": liquidation_days_target,
        "bottleneck_symbol": bottleneck_sym,
        "bottleneck_name": names.get(bottleneck_sym, bottleneck_sym) if bottleneck_sym else None,
        "bottleneck_capacity_yi": round(min_capacity / 1e8, 2) if min_capacity < float("inf") else None,
        "weighted_dtl": round(weighted_dtl, 2),
        "holdings_count": len(holdings_metrics),
        "holdings": holdings_metrics,
    }


# ==================== 4. Barra 风格归因与跟踪误差风险分解 ====================

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
    bench_ret: pd.Series | None = None,
) -> dict:
    """OLS 多因子风格回归，包含 t 检验显著性分析与主动跟踪误差风险结构分解。

    rp − rf = Σ β_k · f_k + α
    - 提供系数 β、标准误 SE、t 统计量、p 值及显著性标记；
    - 对主动方差进行 Barra 式分解：Total Active Risk = Factor Risk + Specific Risk。
    """
    df = pd.DataFrame({"rp": port_ret}).dropna()
    for k, s in style_rets.items():
        df[k] = s.reindex(df.index)
    if bench_ret is not None:
        df["rb"] = bench_ret.reindex(df.index)

    df = df.dropna()
    if len(df) < 30:
        return {
            "n_days": int(len(df)),
            "betas": {},
            "contributions": {},
            "alpha_annualized": None,
            "r_squared": None,
            "factor_stats": {},
            "risk_decomposition": None,
        }

    factor_names = [c for c in df.columns if c not in ("rp", "rb")]
    X = df[factor_names].to_numpy(dtype=np.float64)
    y = df["rp"].to_numpy(dtype=np.float64)
    n, p = X.shape
    Xc = np.column_stack([np.ones_like(y), X])

    beta, *_ = np.linalg.lstsq(Xc, y, rcond=None)
    resid = y - Xc @ beta

    ss_res = float((resid ** 2).sum())
    ss_tot = float(((y - y.mean()) ** 2).sum())
    r2 = 1.0 - ss_res / ss_tot if ss_tot > 1e-18 else None

    # 计算标准误、t 统计量与 p 值
    dof = max(n - p - 1, 1)
    s2 = ss_res / dof
    try:
        var_covar = np.linalg.inv(Xc.T @ Xc) * s2
        se = np.sqrt(np.diag(var_covar))
    except np.linalg.LinAlgError:
        se = np.ones(p + 1) * 1e-4

    t_stats = beta / np.clip(se, 1e-12, None)
    # 自由度为 dof 的双尾 t 分布 p-value
    p_values = [float(2.0 * (1.0 - stats.t.cdf(abs(t), df=dof))) for t in t_stats]

    def _sig_star(pv: float) -> str:
        if pv < 0.001:
            return "***"
        if pv < 0.01:
            return "**"
        if pv < 0.05:
            return "*"
        return "ns"

    factor_stats = {}
    for i, name in enumerate(factor_names):
        factor_stats[name] = {
            "beta": round(float(beta[i + 1]), 4),
            "std_error": round(float(se[i + 1]), 4),
            "t_stat": round(float(t_stats[i + 1]), 3),
            "p_value": round(float(p_values[i + 1]), 4),
            "significance": _sig_star(p_values[i + 1]),
            "annualized_contrib": round(float(beta[i + 1]) * float(df[name].mean()) * 252, 6),
        }

    alpha_se = float(se[0])
    alpha_t = float(t_stats[0])
    alpha_p = float(p_values[0])

    betas = {n: round(float(b), 4) for n, b in zip(factor_names, beta[1:])}
    contrib = {n: round(float(b) * float(df[n].mean()) * 252, 6) for n, b in zip(factor_names, beta[1:])}

    # Barra 跟踪误差方差分解（Factor Risk vs Specific Risk）
    risk_decomp = None
    if "rb" in df.columns:
        active_ret = df["rp"] - df["rb"]
        tot_active_var = float(active_ret.var(ddof=1) * 252)
        fitted_factor = (Xc[:, 1:] @ beta[1:])
        factor_var = float(np.var(fitted_factor, ddof=1) * 252)
        spec_var = float(np.var(resid, ddof=1) * 252)
        sum_var = factor_var + spec_var
        if sum_var > 1e-12:
            factor_risk_pct = round(factor_var / sum_var * 100, 1)
            spec_risk_pct = round(spec_var / sum_var * 100, 1)
        else:
            factor_risk_pct = 50.0
            spec_risk_pct = 50.0

        risk_decomp = {
            "tracking_error_pct": round(np.sqrt(max(tot_active_var, 0.0)) * 100, 2),
            "factor_risk_pct": factor_risk_pct,
            "specific_risk_pct": spec_risk_pct,
            "annual_factor_vol_pct": round(np.sqrt(max(factor_var, 0.0)) * 100, 2),
            "annual_specific_vol_pct": round(np.sqrt(max(spec_var, 0.0)) * 100, 2),
        }

    return {
        "n_days": int(len(df)),
        "betas": betas,
        "contributions": contrib,
        "alpha_annualized": round(float(beta[0]) * 252, 6),
        "alpha_t_stat": round(alpha_t, 3),
        "alpha_p_value": round(alpha_p, 4),
        "alpha_significance": _sig_star(alpha_p),
        "r_squared": round(r2, 4) if r2 is not None else None,
        "factor_stats": factor_stats,
        "risk_decomposition": risk_decomp,
    }


# ==================== 5. 策略容量模型与衰减曲面 ====================

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

    # 多变现周期帕累托前沿（1日、3日、5日分期建仓容量）
    horizon_frontiers = []
    for h_days in (1, 3, 5):
        h_cap_yi = round(float(adv_median * participation_cap * holdings * h_days) / 1e8, 2)
        horizon_frontiers.append({
            "horizon_days": h_days,
            "desc": f"{h_days} 日变现建仓",
            "capacity_yi": h_cap_yi,
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
        "horizon_frontiers": horizon_frontiers,
    }
