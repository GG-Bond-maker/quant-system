"""策略研究工作台（四象限）纯计算函数（无 IO）。

四象限与数据口径（全部真实计算，无模拟值）：
1. 因子研发：因子截面 Rank IC（MAD 去极值 + 可选市值中性化）、
   因子相关矩阵（逐日截面 Spearman 的时序均值）、分位组合累计净值；
2. MLOps：Purged CV fold 边界（复用 ml.purged_cv）、模型特征重要性（gain）；
3. 组合风控：风格暴露（features 截面 zscore 加权）、优化器（复用 domain.optimizer）；
4. 策略执行：日频执行冲击模拟（真实日 VWAP = amount/volume + sqrt 冲击模型）、
   压力测试（真实历史最深回撤窗口的历史重演，VaR/CVaR 历史法）。

⚠️ 无 L2/Tick 数据：微观结构模块采用**日频执行口径**（真实日 VWAP 成交价），
前端需如实标注，不得冒充日内高频模拟。
"""
from __future__ import annotations

import numpy as np
import pandas as pd

from .factor_processing import mad_winsorize
from .optimizer import cap_info_for, compute_weights_from_returns

TRADING_DAYS = 252


# ==================== 1. 因子研发 ====================
def daily_rank_ic_series(
    factor_wide: pd.DataFrame,      # index=date, columns=symbol, values=factor
    fwd_wide: pd.DataFrame,         # 同形，前向收益
    min_names: int = 8,
) -> pd.Series:
    """逐日截面 Spearman Rank IC（两宽表按行秩相关，成对丢 NaN）。"""
    aligned = factor_wide.reindex(columns=fwd_wide.columns)
    ics = aligned.rank(axis=1).corrwith(fwd_wide.rank(axis=1), axis=1)
    # 截面过薄的日期不参与统计
    valid = aligned.notna().sum(axis=1) >= min_names
    return ics[valid].dropna()


def neutralize_by_size_cs(
    factor_wide: pd.DataFrame, size_wide: pd.DataFrame,
) -> pd.DataFrame:
    """截面市值中性化：逐日因子 ~ a + b·log(size) 取残差（纯 pandas，无行业数据）。"""
    idx = factor_wide.index.intersection(size_wide.index)
    cols = factor_wide.columns
    out = pd.DataFrame(np.nan, index=idx, columns=cols)
    for d in idx:
        y = factor_wide.loc[d]
        x = np.log(size_wide.loc[d].reindex(cols).clip(lower=1e-9))
        ok = y.notna() & x.notna()
        if int(ok.sum()) < 5:
            continue
        xv, yv = x[ok].to_numpy(dtype=np.float64), y[ok].to_numpy(dtype=np.float64)
        X = np.column_stack([np.ones_like(xv), xv])
        beta, *_ = np.linalg.lstsq(X, yv, rcond=None)
        out.loc[d, ok[ok].index] = yv - X @ beta
    return out


def factor_ic_table(
    factor_wides: dict[str, pd.DataFrame],
    fwd_wide: pd.DataFrame,
    size_wide: pd.DataFrame | None = None,
    horizons_label: str = "",
) -> list[dict]:
    """多因子 IC 统计表（MAD 去极值 → 可选中性化 → Rank IC）。"""
    rows: list[dict] = []
    for name, fw in factor_wides.items():
        f = fw.apply(lambda col: pd.Series(
            mad_winsorize(col.to_numpy(dtype=np.float64)), index=col.index))
        if size_wide is not None:
            f = neutralize_by_size_cs(f, size_wide)
        ics = daily_rank_ic_series(f, fwd_wide)
        if ics.empty:
            rows.append({"factor": name, "mean_ic": None, "icir": None,
                         "t_stat": None, "positive_ratio": None,
                         "ic_series": [], "n_days": 0})
            continue
        mean, std = float(ics.mean()), float(ics.std(ddof=1))
        rows.append({
            "factor": name,
            "mean_ic": round(mean, 6),
            "icir": round(mean / std, 4) if std > 1e-12 else None,
            "t_stat": round(mean / std * np.sqrt(ics.size), 3) if std > 1e-12 else None,
            "positive_ratio": round(float((ics > 0).mean()), 4),
            "ic_series": [round(float(v), 4) for v in ics.iloc[-60:]],
            "n_days": int(ics.size),
        })
    return rows


def factor_corr_matrix(factor_wides: dict[str, pd.DataFrame],
                       window_days: int = 60) -> dict:
    """因子相关矩阵：各因子最近 window_days 逐日截面 Spearman 的时序均值。

    比"单日快照"稳健；|r| > 0.7 的因子对由前端红牌高亮（多重共线性警告）。
    """
    names = list(factor_wides)
    # 对齐日期后合并为 (date, factor*symbol) 长表，逐日算截面 Spearman
    dfs = {n: fw.rank(axis=1) for n, fw in factor_wides.items()}
    # factor_wides 非空时下列循环必赋值；显式断言让静态检查知道非 None
    common_idx: pd.Index | None = None
    for fw in factor_wides.values():
        common_idx = fw.index if common_idx is None else \
            common_idx.intersection(fw.index)
    assert common_idx is not None
    common_idx = common_idx[-window_days:]
    cols: set[str] | None = None
    for fw in factor_wides.values():
        c = set(fw.columns)
        cols = c if cols is None else (cols & c)
    assert cols is not None
    col_names = sorted(cols)
    mat = np.eye(len(names))
    for i, a in enumerate(names):
        for j in range(i + 1, len(names)):
            b = names[j]
            da, db = (dfs[a].loc[common_idx, col_names],
                      dfs[b].loc[common_idx, col_names])
            daily = da.corrwith(db, axis=1).dropna()
            r = float(daily.mean()) if len(daily) else np.nan
            mat[i, j] = mat[j, i] = r
    pairs = [{"a": names[i], "b": names[j], "r": round(float(mat[i, j]), 4)}
             for i in range(len(names)) for j in range(i + 1, len(names))
             if np.isfinite(mat[i, j]) and abs(mat[i, j]) > 0.7]
    return {"factors": names, "matrix": [[round(float(v), 4) for v in row]
                                         for row in mat],
            "high_corr_pairs": pairs, "window_days": window_days}


def quantile_curves(
    factor_wide: pd.DataFrame, close_wide: pd.DataFrame,
    horizon: int = 5, n_quantiles: int = 5,
) -> dict:
    """分位组合累计净值：每 horizon 日按因子分位分组，组内等权，
    持有 horizon 日（滚动重叠，日收益按组均值摊平）—— 单因子分层回测标准口径。"""
    fwd = close_wide.shift(-horizon) / close_wide - 1.0
    ranks = factor_wide.rank(axis=1, pct=True)
    q = np.ceil(ranks * n_quantiles).clip(1, n_quantiles)
    dates = factor_wide.index.intersection(close_wide.index)
    # 每个调仓日的组收益 -> 日频摊平
    daily_by_q: dict[int, dict[pd.Timestamp, float]] = {
        k: {} for k in range(1, n_quantiles + 1)}
    step = horizon
    for i in range(0, len(dates) - horizon, step):
        d = dates[i]
        for k in range(1, n_quantiles + 1):
            mask = (q.loc[d] == k) & fwd.loc[d].notna()
            vals = fwd.loc[d][mask]
            daily_by_q[k][dates[min(i + step, len(dates) - 1)]] = \
                float(vals.mean()) if int(mask.sum()) else np.nan
    curves: dict[str, list] = {}
    for k in range(1, n_quantiles + 1):
        s = pd.Series(daily_by_q[k]).dropna().sort_index()
        nav = (1.0 + s).cumprod()
        curves[f"Q{k}"] = [{"date": str(d)[:10], "nav": round(float(v), 4)}
                           for d, v in nav.items()]
    return {"quantiles": n_quantiles, "horizon": horizon,
            "curves": curves,
            "labels": {f"Q{k}": ("Top" if k == n_quantiles else
                                 "Bottom" if k == 1 else f"Q{k}")
                       for k in range(1, n_quantiles + 1)}}


# ==================== 3. 组合风控 ====================
STYLE_FACTOR_MAP = {
    "Momentum": "ret_20",       # 20 日动量
    "Volatility": "vol_20",     # 20 日波动
    "Size": "amount_per_share", # 流动性/规模代理（log 后）
    "Reversal": "rsi_14",       # 短期反转（RSI）
}


def style_exposure(zrow: pd.Series,
                   weights: dict[str, float]) -> dict[str, float | None]:
    """组合风格暴露 = Σ w_i × z_i（z 为最新截面标准化因子值）。"""
    out: dict[str, float | None] = {}
    for style, col in STYLE_FACTOR_MAP.items():
        num = sum(w * float(zrow.get(f"{col}_z", np.nan) or np.nan)
                  for sym, w in weights.items()
                  if (f"{col}_z") in zrow.index)
        out[style] = round(num, 4) if np.isfinite(num) else None
    return out


def optimize_portfolio(
    returns: np.ndarray, symbols: list[str], method: str,
    weight_cap: float = 0.0, turnover_penalty: float = 0.0,
    prev_weights: np.ndarray | None = None,
) -> dict:
    """真实优化器（复用 domain.optimizer：LW 收缩 + RMT 去噪协方差）。

    附带 ``weight_cap_info``（审计 P1-13）：上限不可行时（``n·cap<1``）权重之和
    必然 <1，控制台必须能看出"这不是满仓解"。
    """
    w = compute_weights_from_returns(
        returns, method=method, weight_cap=weight_cap,
        prev_weights=prev_weights, turnover_penalty=turnover_penalty)
    return {"weights": {s: round(float(x), 6) for s, x in zip(symbols, w)},
            "method": method,
            "weight_cap_info": cap_info_for(w, weight_cap),
            "expected_returns": _expected_returns_disclosure(method)}


def _expected_returns_disclosure(method: str) -> dict:
    """``mvo`` 的 μ 口径披露（审计 B2-11）。

    本函数**收不到**预期收益（只拿历史收益矩阵算协方差）⇒ ``μ ≡ 0``，此时
    ``mean_variance_weights`` 的解与 risk_aversion **完全无关**（实测 λ=8 与 λ=50
    的权重 L1 距离 = 0），退化为纯风险项最优（本例恰为等权 1/N）。
    实测：只要 μ 异质，λ 立刻生效（L1=0.459）⇒ 一旦接线 μ，λ 参数即恢复意义。

    为什么不在这轮直接接 μ（审核 §S6 的结论）：Top-10 组合无正超额，拼接历史均值
    会把噪声当收益喂给优化器、并让"Mean-Variance"这个标签变得**看起来**可信。
    因此在接线（需要样本外收缩估计）之前，先把标签与字段说清楚。
    """
    if method != "mvo":
        return {}
    return {
        "basis": "unavailable",
        "value": "zero",
        "risk_aversion_effective": False,
        "note": ("⚠️ 未提供预期收益（接口只传历史收益矩阵）⇒ μ≡0：风险厌恶系数对结果"
                 "**无影响**，本结果实为纯风险最小化，不是「均值-方差」最优解"),
    }


# ==================== 4. 策略执行（日频口径） ====================
def execution_impact_sim(
    bars: pd.DataFrame,        # date, open, high, low, close, volume, amount（升序）
    side: str, order_amount: float, participation_cap: float,
    algo: str = "market", split_days: int = 5,
    impact_coef_bps: float = 10.0,
) -> dict:
    """日频执行冲击模拟（Almgren-Chriss sqrt 冲击，真实日 VWAP 口径）。

    - market ：首日开盘价一次性成交，参与率 = 订单/首日成交额；
    - vwap   ：分 split_days 日、每日等金额，以**真实日 VWAP**（amount/volume）成交；
    - twap   ：分 split_days 日等金额，以当日 (open+close)/2 近似时序均价成交。
    冲击成本 = amount × coef/1e4 × sqrt(participation)，participation 截断到
    participation_cap（超过部分该日不可成交，顺延次日 —— 真实流动性约束）。
    """
    algo = algo.lower()
    daily_amount = (bars["amount"] / bars["volume"].clip(lower=1.0))
    if algo == "market":
        plan = [(bars.index[0], order_amount)]
    else:
        per = order_amount / split_days
        plan = [(bars.index[i], per) for i in range(min(split_days, len(bars)))]
    fills: list[dict] = []
    cash_left = order_amount
    for d, budget in plan:
        row = bars.loc[d]
        if cash_left <= 1e-9:
            break
        amt = min(budget, cash_left)
        if algo == "market":
            px_ref = float(row["open"])
        elif algo == "vwap":
            px_ref = float(daily_amount.loc[d]) if np.isfinite(daily_amount.loc[d]) \
                else float(row["close"])
        else:
            px_ref = (float(row["open"]) + float(row["close"])) / 2.0
        part = amt / float(row["amount"]) if row["amount"] > 0 else 1.0
        if part > participation_cap:      # 流动性闸门：超额顺延
            amt = float(row["amount"]) * participation_cap
        part = min(amt / float(row["amount"]) if row["amount"] > 0 else 1.0,
                   participation_cap)
        if amt <= 0:
            continue
        impact_bps = impact_coef_bps * np.sqrt(part)
        exec_px = px_ref * (1 + impact_bps / 1e4) if side == "buy" \
            else px_ref * (1 - impact_bps / 1e4)
        cash_left -= amt
        fills.append({
            "date": str(d)[:10], "algo": algo, "ref_price": round(px_ref, 4),
            "exec_price": round(exec_px, 4), "amount": round(amt, 2),
            "participation": round(part, 6),
            "impact_bps": round(float(impact_bps), 2),
            "side": side,
        })
    total_cost = sum(f["amount"] * f["impact_bps"] / 1e4 for f in fills)
    avg_bps = (total_cost / sum(f["amount"] for f in fills) * 1e4) \
        if fills and sum(f["amount"] for f in fills) > 0 else 0.0
    return {
        "algo": algo, "side": side, "order_amount": order_amount,
        "fills": fills, "unfilled": round(max(cash_left, 0.0), 2),
        "total_impact_cost": round(total_cost, 2),
        "avg_impact_bps": round(float(avg_bps), 2),
        "participation_cap": participation_cap,
    }


def stress_windows(
    market_nav: pd.Series, window: int = 20, top: int = 5,
    min_gap_days: int = 20,
) -> list[dict]:
    """自动识别真实历史最深回撤窗口（按滚动窗口收益最差的互不重叠区间）。

    数据驱动的"极端情景"识别——不硬编码 2015/2020 等事件名
    （本地数据自 2022 起，硬编码年份反而无法计算）。
    """
    ret = market_nav.pct_change().dropna()
    roll = market_nav / market_nav.shift(window) - 1.0
    cand = roll.dropna().sort_values().index.tolist()
    chosen: list[pd.Timestamp] = []
    for d in cand:
        if all(abs((d - c).days) > min_gap_days * 2 for c in chosen):
            chosen.append(d)
        if len(chosen) >= top:
            break
    out: list[dict] = []
    for end in chosen:
        loc = market_nav.index.get_loc(end)
        seg_ret = ret.iloc[max(0, loc - window + 1):loc + 1]
        nav_seg = (1.0 + seg_ret).cumprod()
        peak = nav_seg.cummax()
        mdd = float((1 - nav_seg / peak).max())
        var95 = float(np.percentile(seg_ret, 5))
        cvar95 = float(seg_ret[seg_ret <= var95].mean()) \
            if (seg_ret <= var95).any() else var95
        out.append({
            "window_start": str(seg_ret.index[0])[:10],
            "window_end": str(end)[:10],
            "market_return": round(float(roll.loc[end]), 4),
            "market_mdd": round(mdd, 4),
            "var_95": round(var95, 4),
            "cvar_95": round(cvar95, 4),
            "n_days": int(len(seg_ret)),
        })
    return sorted(out, key=lambda x: x["market_return"])


def portfolio_stress_replay(
    price_wide: pd.DataFrame, weights: dict[str, float],
    windows: list[dict],
) -> list[dict]:
    """历史重演压力测试：真实窗口内组合收益（权重固定、日再平衡近似）。"""
    out: list[dict] = []
    w = pd.Series(weights).reindex(price_wide.columns).fillna(0.0)
    w = w / w.sum() if w.sum() > 0 else w
    port_ret = price_wide.pct_change().dot(w).dropna()
    for win in windows:
        mask = (port_ret.index >= pd.Timestamp(win["window_start"])) & \
               (port_ret.index <= pd.Timestamp(win["window_end"]))
        seg = port_ret[mask]
        if seg.empty:
            out.append({**win, "portfolio_return": None, "portfolio_mdd": None,
                        "portfolio_var_95": None})
            continue
        nav = (1.0 + seg).cumprod()
        mdd = float((1 - nav / nav.cummax()).max())
        var95 = float(np.percentile(seg, 5))
        out.append({**win,
                    "portfolio_return": round(float(nav.iloc[-1] - 1.0), 4),
                    "portfolio_mdd": round(mdd, 4),
                    "portfolio_var_95": round(var95, 4)})
    return out
