"""组合权重优化器（纯 numpy，纯函数，无 IO）。

对标机构级风控体系的四层能力：
1. 风险模型：Ledoit-Wolf 收缩协方差（解决 N 大 T 小的噪声/不可逆）+
   RMT 随机矩阵理论去噪（剔除 Marchenko-Pastur 噪声特征值）；
2. 风险平摊 / 风险预算（Risk Parity / Budgeting）：各资产风险贡献相等，
   循环坐标下降法（Griveau-Billion et al. 2013）求解，确定性收敛；
3. 最大分散度（Maximum Diversification）：最大化 Diversification Ratio；
4. 带约束 MVO：单资产上限 + 换手惩罚（L2）+ 简单x投影梯度求解。

所有权重输出：非负、sum=1、满足单资产上限（若可行）。
输入 returns 形状 (T, N)（按日期 × 资产），只允许用 T 日及之前的数据构造。
"""
from __future__ import annotations

import numpy as np

TRADING_DAYS = 252


# ==================== 风险模型：协方差估计 ====================
def sample_cov(returns: np.ndarray) -> np.ndarray:
    """样本协方差（日频，ddof=1）。输入 (T, N)。"""
    R = np.asarray(returns, dtype=np.float64)
    if R.ndim != 2 or R.shape[0] < 2:
        raise ValueError(f"returns 需 (T>=2, N)，收到 {R.shape}")
    return np.cov(R, rowvar=False, ddof=1)


def ledoit_wolf_cov(returns: np.ndarray) -> tuple[np.ndarray, float]:
    """Ledoit-Wolf 收缩协方差估计（target = 均值方差 × 单位阵）。

    Σ = δ·μ·I + (1-δ)·S，其中 μ = trace(S)/N，
    δ = min(b², d²)/d²（ Ledoit & Wolf 2004 "A well-conditioned estimator
    for large-dimensional covariance matrices" 的标准实现，与 sklearn
    LedoitWolf 同口径，纯 numpy 复刻以保持 domain 层零重依赖）。

    :return: (收缩后协方差, 收缩强度 δ ∈ [0, 1])
    """
    X = np.asarray(returns, dtype=np.float64)
    T, N = X.shape
    if T < 2:
        raise ValueError("样本数 T 必须 >= 2")
    S = sample_cov(X)
    mu = float(np.trace(S)) / N
    # d² = ||S - μI||_F² / N
    d2 = float(np.sum((S - mu * np.eye(N)) ** 2)) / N
    # b̄² = (1/T²) Σ_t ||x_t x_t' - S||_F² / N（x 已去均值）
    Xc = X - X.mean(axis=0)
    b2bar = 0.0
    for t in range(T):
        xt = Xc[t][:, None]
        b2bar += float(np.sum((xt @ xt.T - S) ** 2)) / N
    b2bar /= T * T
    if d2 <= 1e-18:            # 常数资产：无离散度可估
        return S.copy(), 1.0
    delta = float(min(b2bar, d2) / d2)
    delta = float(np.clip(delta, 0.0, 1.0))
    shrunk = delta * mu * np.eye(N) + (1.0 - delta) * S
    return shrunk, delta


def _cov2corr(cov: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
    """协方差 -> 相关矩阵 + 各资产波动率。"""
    vol = np.sqrt(np.diag(cov))
    safe = np.where(vol < 1e-15, 1e-15, vol)
    corr = cov / np.outer(safe, safe)
    np.fill_diagonal(corr, 1.0)
    return np.clip(corr, -1.0, 1.0), vol


def rmt_denoise(cov: np.ndarray, n_obs: int) -> np.ndarray:
    """随机矩阵理论（RMT）去噪：剔除噪声特征值，保留真实风险因子。

    步骤（López de Prado《ML for Asset Managers》口径）：
    1. 协方差 -> 相关矩阵；
    2. Marchenko-Pastur 上界 λ₊ = (1 + sqrt(N/T))²（单位方差下）；
    3. 低于 λ₊ 的特征值（噪声带）替换为它们的均值（保留总迹）；
    4. 重构相关矩阵，再还原为协方差。

    :param n_obs: 估计该协方差使用的样本数 T（决定 MP 上界）
    """
    C = np.asarray(cov, dtype=np.float64)
    N = C.shape[0]
    if n_obs <= N:
        return C.copy()          # T <= N：MP 边界无意义，返回原矩阵
    corr, vol = _cov2corr(C)
    eigvals, eigvecs = np.linalg.eigh(corr)
    mp_edge = (1.0 + np.sqrt(N / n_obs)) ** 2
    noise = eigvals < mp_edge
    if not noise.any():
        return C.copy()
    # 噪声特征值等值化为均值（迹不变 => 总方差不变）
    eigvals_denoised = eigvals.copy()
    eigvals_denoised[noise] = eigvals[noise].mean()
    corr_dn = eigvecs @ np.diag(eigvals_denoised) @ eigvecs.T
    corr_dn, _ = _cov2corr((corr_dn + corr_dn.T) / 2.0)   # 数值对称 + 对角归一
    return corr_dn * np.outer(vol, vol)


def robust_cov(returns: np.ndarray, denoise: bool = True) -> np.ndarray:
    """Ledoit-Wolf 收缩 + RMT 去噪的组合风险模型（机构标准两层净化）。"""
    R = np.asarray(returns, dtype=np.float64)
    shrunk, _ = ledoit_wolf_cov(R)
    return rmt_denoise(shrunk, R.shape[0]) if denoise else shrunk


# ==================== 权重求解 ====================
def _normalize(w: np.ndarray) -> np.ndarray:
    s = w.sum()
    if s <= 0 or not np.isfinite(s):
        return np.full(w.shape, 1.0 / w.size)
    return w / s


def cap_feasibility(n_assets: int, cap: float) -> dict:
    """单资产权重上限的**可行性**判定（审计 P1-13）。

    ``Σw = 1`` 与 ``wᵢ ≤ cap`` 同时成立的必要条件是 ``n·cap ≥ 1``。否则**最多**
    只能投出 ``n·cap``（N=10、cap=5% ⇒ 只能投 **50%**，其余为永久现金）。
    修复前该情形静默返回半仓权重，API 仍报 ``status=ok``、``fallback=false``
    ⇒ 用户把"一半现金的回测"读成正常结果。

    Returns:
        ``{cap, n_assets, feasible, max_invested_ratio, min_feasible_cap, binding}``。
        ``min_feasible_cap = 1/n`` 是能满仓的最小上限；``binding`` 表示上限是否真的生效。
    """
    n = int(max(n_assets, 0))
    min_cap = (1.0 / n) if n else 0.0
    if cap <= 0 or cap >= 1.0:
        return {"cap": float(cap), "n_assets": n, "feasible": True,
                "max_invested_ratio": 1.0, "min_feasible_cap": min_cap, "binding": False}
    return {"cap": float(cap), "n_assets": n,
            "feasible": cap * n >= 1.0 - 1e-12,
            "max_invested_ratio": min(1.0, cap * n),
            "min_feasible_cap": min_cap,
            "binding": cap < 1.0 - 1e-12}


def apply_weight_cap(w: np.ndarray, cap: float, max_iter: int = 100) -> np.ndarray:
    """单资产上限约束：迭代 clip+重分配（``cap*len(w) >= 1`` 时必可行）。

    ⚠️ ``cap*len(w) < 1`` 时**无解**：本函数按原行为返回"每个都不超过 cap"的权重，
    但此时 ``Σw = n*cap < 1``（其余是现金）。调用方**必须**用 :func:`cap_info_for`
    把可行性披露出去 —— 静默把"半仓"当正常结果是审计 P1-13 的缺陷本体。
    """
    if cap <= 0 or cap >= 1.0:
        return w
    out = np.clip(np.asarray(w, dtype=np.float64), 0.0, None)
    out = _normalize(out)
    for _ in range(max_iter):
        over = out > cap
        if not over.any():
            break
        excess = float((out[over] - cap).sum())
        out[over] = cap
        free = ~over
        room = cap - out[free]
        total_room = float(room.sum())
        if total_room <= 1e-15 or excess <= 1e-15:
            break
        add = np.minimum(room, excess * room / total_room)
        out[free] += add
        excess_left = excess - float(add.sum())
        if excess_left < 1e-12:
            break
    return out


def cap_info_for(w: np.ndarray, cap: float) -> dict:
    """对**实际返回的权重**做上限可行性/生效性披露（审计 P1-13）。

    与 :func:`cap_feasibility` 的区别：后者只看 ``(n, cap)``，本函数看**真实权重**
    ⇒ 能同时发现两类问题：

    * ``feasible=False``：``n·cap<1`` ⇒ ``Σw<1``，回测/lab 结果是"部分现金"；
    * ``cap_enforced=False``：上限根本没被执行（例如 `equal` 分支 1/n > cap，
      或某个方案内部投影与外部 cap 不一致）⇒ 声称的上限是**空头承诺**。
    """
    arr = np.asarray(w, dtype=np.float64).ravel()
    info = cap_feasibility(arr.size, cap)
    info["invested_ratio"] = round(float(np.nansum(arr)), 6)
    info["max_weight"] = round(float(np.nanmax(arr)), 6) if arr.size else None
    info["cap_enforced"] = bool(arr.size and info["max_weight"] <= cap + 1e-9)
    # 披露优先级：先报**实际发生了什么**（权重是否违反上限），再说"若要执行上限会
    # 付出什么代价"（n·cap<1 ⇒ 只能部分投资）。顺序颠倒会把"实际满仓且超限"说成
    # "只能投 80%"（equal 方案实测就是这样，见 test_portfolio_constraints.py）。
    if cap <= 0:
        info["note"] = "未设单资产上限"
    elif not info["cap_enforced"]:
        info["note"] = (f"⚠️ 上限**未生效**：实际 max w={info['max_weight']} > cap={info['cap']}"
                        + ("；且该上限对 n={n} 不可行（n·cap<1）——若要强制生效，"
                           "最多只能投出 {r:.1%}，其余为现金，cap 需 ≥ {c:.4f} 才能满仓"
                           .format(n=info["n_assets"], r=info["max_invested_ratio"],
                                   c=info["min_feasible_cap"])
                           if not info["feasible"] else ""))
    elif not info["feasible"]:
        info["note"] = (
            f"⚠️ 上限不可行（n·cap={info['n_assets']}×{info['cap']}<1）⇒ 最多只能投出 "
            f"{info['max_invested_ratio']:.1%}，其余为现金；cap 需 ≥ "
            f"{info['min_feasible_cap']:.4f} 才能满仓（实际投出 {info['invested_ratio']:.1%}）")
    else:
        info["note"] = (f"上限生效（n={info['n_assets']}、cap={info['cap']}）；"
                        f"实际投出 {info['invested_ratio']:.1%}")
    return info


def risk_parity_weights(cov: np.ndarray, budgets: np.ndarray | None = None,
                        max_iter: int = 500, tol: float = 1e-10) -> np.ndarray:
    """风险预算 / 风险平摊权重（循环坐标下降 CCD，纯 numpy 确定性求解）。

    目标：min ½ w'Σw − Σᵢ bᵢ ln(wᵢ)，其 KKT 条件等价于
          wᵢ (Σw)ᵢ = bᵢ · (w'Σw)，即各资产风险贡献 RCᵢ ∝ 预算 bᵢ。
    默认 bᵢ = 1/N 即经典 Risk Parity（各资产对总风险贡献相等）。
    """
    C = np.asarray(cov, dtype=np.float64)
    N = C.shape[0]
    if N == 1:
        return np.array([1.0])
    b = np.full(N, 1.0 / N) if budgets is None else np.asarray(budgets, dtype=np.float64)
    b = b / b.sum()
    diag = np.diag(C).copy()
    diag[diag < 1e-18] = 1e-18
    w = np.full(N, 1.0 / N)
    for _ in range(max_iter):
        w_old = w.copy()
        for i in range(N):
            s_i = float(C[i] @ w) - diag[i] * w[i]   # 除 i 外其他资产贡献
            w[i] = (-s_i + np.sqrt(s_i * s_i + 4.0 * diag[i] * b[i])) / (2.0 * diag[i])
        if np.max(np.abs(w - w_old)) < tol:
            break
    return _normalize(w)


def risk_contributions(w: np.ndarray, cov: np.ndarray) -> np.ndarray:
    """各资产风险贡献 RCᵢ = wᵢ(Σw)ᵢ / (w'Σw)（和为 1）。"""
    w = np.asarray(w, dtype=np.float64)
    port_var = float(w @ cov @ w)
    if port_var <= 1e-18:
        return np.full(w.shape, 1.0 / w.size)
    return w * (cov @ w) / port_var


def _project_simplex(v: np.ndarray) -> np.ndarray:
    """欧氏范数下投影到概率单纯形（Duchi et al. 2008，O(n log n)）。"""
    u = np.sort(v)[::-1]
    css = np.cumsum(u) - 1.0
    idx = np.arange(1, v.size + 1)
    cond = u - css / idx > 0
    rho = idx[cond][-1]
    theta = css[cond][-1] / rho
    return np.maximum(v - theta, 0.0)


def max_diversification_weights(cov: np.ndarray, max_iter: int = 1000,
                                tol: float = 1e-10) -> np.ndarray:
    """最大分散度组合（Choueifaty & Coignard 2008）。

    最大化 DR(w) = w'σ / sqrt(w'Σw)。用投影梯度上升（步长自适应回缩），
    单纯x投影保证多头且 sum=1。等价于 tangency（Σ⁻¹σ）问题的多头约束版。
    """
    C = np.asarray(cov, dtype=np.float64)
    N = C.shape[0]
    if N == 1:
        return np.array([1.0])
    sigma = np.sqrt(np.maximum(np.diag(C), 1e-18))
    w = np.full(N, 1.0 / N)
    step = 0.1
    best_w, best_dr = w.copy(), -np.inf
    for it in range(max_iter):
        port_vol = float(np.sqrt(max(w @ C @ w, 1e-18)))
        grad = sigma / port_vol - (w @ C @ w / (port_vol ** 3)) * (C @ w)
        w_new = _project_simplex(w + step * grad)
        dr_new = float(w_new @ sigma) / float(np.sqrt(max(w_new @ C @ w_new, 1e-18)))
        if dr_new < float(w @ sigma) / port_vol - 1e-14:
            step *= 0.5                       # 步长过大回缩
            if step < 1e-12:
                break
            continue
        moved = float(np.max(np.abs(w_new - w)))
        w = w_new
        if dr_new > best_dr:
            best_dr, best_w = dr_new, w.copy()
        if moved < tol:
            break
    return best_w if np.isfinite(best_dr) else _normalize(np.ones(N))


def mean_variance_weights(expected_returns: np.ndarray, cov: np.ndarray,
                          risk_aversion: float = 8.0,
                          weight_cap: float = 0.0,
                          prev_weights: np.ndarray | None = None,
                          turnover_penalty: float = 0.0,
                          max_iter: int = 500, tol: float = 1e-10) -> np.ndarray:
    """带约束的均值-方差优化（投影梯度上升，多头 + 单纯x + 单资产上限）。

    max  μ'w − λ·w'Σw − γ‖w − w_prev‖²
    s.t.  Σw = 1, 0 ≤ w ≤ cap

    :param risk_aversion: λ（风险厌恶，越大越保守）
    :param weight_cap:    单资产权重上限（0 = 不限制）
    :param prev_weights:  上一期权重（换手惩罚的锚点）
    :param turnover_penalty: γ（L2 换手惩罚，抑制无谓调仓）
    """
    mu = np.asarray(expected_returns, dtype=np.float64)
    C = np.asarray(cov, dtype=np.float64)
    N = C.shape[0]
    if N == 1:
        return np.array([1.0])
    w_prev = (np.full(N, 1.0 / N) if prev_weights is None
              else np.asarray(prev_weights, dtype=np.float64))
    lam = max(risk_aversion, 1e-6)
    gam = max(turnover_penalty, 0.0)
    w = w_prev.copy()

    def _proj(v: np.ndarray) -> np.ndarray:
        p = _project_simplex(v)
        return apply_weight_cap(p, weight_cap) if weight_cap > 0 else p

    step = 1.0 / (2.0 * lam * float(np.trace(C)) / N + 2.0 * gam + 1e-6)
    for _ in range(max_iter):
        grad = mu - 2.0 * lam * (C @ w) - 2.0 * gam * (w - w_prev)
        w_new = _proj(w + step * grad)
        if float(np.max(np.abs(w_new - w))) < tol:
            w = w_new
            break
        w = w_new
    return w


def inverse_vol_weights(returns: np.ndarray) -> np.ndarray:
    """波动率倒数加权：wᵢ ∝ 1/σᵢ（低波动资产占更高权重）。"""
    R = np.asarray(returns, dtype=np.float64)
    vol = np.sqrt(np.maximum(np.diag(sample_cov(R)), 1e-18))
    inv = 1.0 / vol
    return _normalize(inv)


def hrp_weights(returns: np.ndarray) -> np.ndarray:
    """分层风险平价（Hierarchical Risk Parity, Marcos López de Prado 2016）。

    1. 计算相关距离矩阵 D_ij = sqrt(0.5 * (1 - rho_ij))；
    2. 树状层级聚类并准对角化重排（Quasi-Diagonalization）；
    3. 递归二分法（Recursive Bisection）分配方差倒数权重。
    完全不依赖协方差求逆，彻底避免病态矩阵导致的权重极化与震荡。
    """
    R = np.asarray(returns, dtype=np.float64)
    T, N = R.shape
    if N <= 1:
        return np.ones(N)

    cov = robust_cov(R)
    corr, _ = _cov2corr(cov)
    dist = np.sqrt(np.maximum(0.5 * (1.0 - corr), 0.0))
    np.fill_diagonal(dist, 0.0)

    try:
        from scipy.cluster.hierarchy import linkage, to_tree
        from scipy.spatial.distance import squareform

        condensed_dist = squareform(dist, checks=False)
        link = linkage(condensed_dist, method="single")
        root = to_tree(link)

        def _get_leaf_order(node: Any) -> list[int]:
            if node is None:
                return []
            if node.is_leaf():
                return [node.id]
            return _get_leaf_order(node.left) + _get_leaf_order(node.right)

        sort_ix = _get_leaf_order(root)
    except Exception:
        return inverse_vol_weights(R)

    # 递归二分分配权重
    w: dict[int, float] = {idx: 1.0 for idx in sort_ix}
    clusters = [sort_ix]

    while clusters:
        next_clusters = []
        for cluster in clusters:
            if len(cluster) <= 1:
                continue
            mid = len(cluster) // 2
            c1 = cluster[:mid]
            c2 = cluster[mid:]

            def _cluster_variance(c: list[int]) -> float:
                sub_cov = cov[np.ix_(c, c)]
                sub_var = np.diag(sub_cov)
                sub_inv_var = 1.0 / np.maximum(sub_var, 1e-12)
                sub_w = sub_inv_var / np.sum(sub_inv_var)
                return float(sub_w @ sub_cov @ sub_w)

            v1 = _cluster_variance(c1)
            v2 = _cluster_variance(c2)
            alpha = 1.0 - v1 / max(v1 + v2, 1e-12)

            for i in c1:
                w[i] *= alpha
            for i in c2:
                w[i] *= (1.0 - alpha)

            if len(c1) > 1:
                next_clusters.append(c1)
            if len(c2) > 1:
                next_clusters.append(c2)
        clusters = next_clusters

    weights = np.zeros(N)
    for idx, weight in w.items():
        weights[idx] = weight
    return _normalize(weights)


def euler_risk_contributions(weights: np.ndarray, cov: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
    """欧拉风险分解（Euler's Risk Decomposition）：

    返回 (绝对风险贡献 RC, 百分比风险贡献 %RC)。
    满足 sum(%RC) == 1.0，精准识别单一资产是否存在风险过度集中。
    """
    w = np.asarray(weights, dtype=np.float64)
    cov = np.asarray(cov, dtype=np.float64)
    p_var = float(w @ cov @ w)
    p_vol = float(np.sqrt(max(p_var, 1e-18)))
    mrc = (cov @ w) / p_vol
    rc = w * mrc
    pct_rc = rc / max(p_vol, 1e-18)
    return rc, pct_rc


# ==================== 统一分发入口 ====================
WEIGHTING_SCHEMES = ("user", "equal", "score_weighted", "risk_parity",
                     "max_div", "inverse_vol", "mvo", "hrp")


def compute_weights_from_returns(
    returns: np.ndarray,
    method: str = "risk_parity",
    expected_returns: np.ndarray | None = None,
    weight_cap: float = 0.0,
    prev_weights: np.ndarray | None = None,
    turnover_penalty: float = 0.0,
) -> np.ndarray:
    """按方案计算权重（输入严格 T 日及之前的收益率矩阵）。

    :param method: risk_parity / max_div / inverse_vol / hrp / mvo / equal
    :raises ValueError: 未知方案或 returns 形状非法
    """
    R = np.asarray(returns, dtype=np.float64)
    if method == "equal":
        return np.full(R.shape[1], 1.0 / R.shape[1])
    if method == "inverse_vol":
        w = inverse_vol_weights(R)
    elif method == "hrp":
        w = hrp_weights(R)
    elif method in ("risk_parity", "max_div", "mvo"):
        cov = robust_cov(R)
        if method == "risk_parity":
            w = risk_parity_weights(cov)
        elif method == "max_div":
            w = max_diversification_weights(cov)
        else:
            mu = (np.full(R.shape[1], 0.0) if expected_returns is None
                  else np.asarray(expected_returns, dtype=np.float64))
            w = mean_variance_weights(mu, cov, weight_cap=weight_cap,
                                      prev_weights=prev_weights,
                                      turnover_penalty=turnover_penalty)
    else:
        raise ValueError(f"未知 weighting 方案: {method!r}，可选 {WEIGHTING_SCHEMES}")
    return apply_weight_cap(w, weight_cap) if weight_cap > 0 else w


def diversification_ratio(w: np.ndarray, cov: np.ndarray) -> float:
    """分散化比率 DR = 加权平均波动 / 组合波动（越大越分散）。"""
    w = np.asarray(w, dtype=np.float64)
    sigma = np.sqrt(np.maximum(np.diag(cov), 1e-18))
    pv = float(np.sqrt(max(w @ cov @ w, 1e-18)))
    return float(w @ sigma) / pv


def compute_efficient_frontier(
    returns: np.ndarray,
    symbols: list[str],
    current_weights: dict[str, float] | None = None,
    n_points: int = 25,
    risk_free_rate: float = 0.02,
) -> dict:
    """计算马科维茨有效前沿（基于 Ledoit-Wolf 收缩协方差 + 历史年化期望）。

    量化投研价值：
    1. 测算风险-收益有效边界（Efficient Frontier）；
    2. 定位全局最小方差组合（Min Volatility）与最大夏普切线组合（Max Sharpe / Tangency）；
    3. 标定当前投资组合在边界上的偏离程度，指导仓位权重优化。
    """
    R = np.asarray(returns, dtype=np.float64)
    T, N = R.shape
    if T < 5 or N < 2:
        return {"error": "有效样本不足（需资产数>=2且交易日>=5），无法生成有效前沿", "frontier": []}

    cov = robust_cov(R) * TRADING_DAYS
    mu = np.mean(R, axis=0) * TRADING_DAYS

    # 各单资产点坐标 (vol, ret)
    assets = []
    for i, s in enumerate(symbols):
        a_vol = float(np.sqrt(max(cov[i, i], 1e-12)))
        a_ret = float(mu[i])
        a_sharpe = (a_ret - risk_free_rate) / a_vol if a_vol > 1e-12 else 0.0
        assets.append({
            "symbol": s,
            "vol": round(a_vol, 4),
            "ret": round(a_ret, 4),
            "sharpe": round(a_sharpe, 4),
        })

    # 扫描风险厌恶系数 lambda，覆盖激进（高收益）到极端保守（最小方差）
    lambdas = np.logspace(-1.5, 3.0, max(n_points, 15))
    pts = []
    best_sharpe = -1e9
    max_sharpe_pt = None
    min_vol_val = 1e9
    min_vol_pt = None

    for lam in lambdas:
        w = mean_variance_weights(mu, cov, risk_aversion=lam)
        p_vol = float(np.sqrt(max(float(w @ cov @ w), 1e-12)))
        p_ret = float(w @ mu)
        sharpe = (p_ret - risk_free_rate) / p_vol if p_vol > 1e-12 else 0.0
        pt = {
            "vol": round(p_vol, 4),
            "ret": round(p_ret, 4),
            "sharpe": round(sharpe, 4),
            "weights": {s: round(float(w[j]), 4) for j, s in enumerate(symbols)},
        }
        pts.append(pt)
        if sharpe > best_sharpe:
            best_sharpe = sharpe
            max_sharpe_pt = pt
        if p_vol < min_vol_val:
            min_vol_val = p_vol
            min_vol_pt = pt

    # 滤掉下方非有效分支（在同波动下保留更高收益），按波动率升序排列
    pts = sorted(pts, key=lambda x: x["vol"])
    frontier = []
    max_r = -1e9
    for p in pts:
        if p["ret"] >= max_r - 0.001:
            frontier.append(p)
            max_r = max(max_r, p["ret"])

    # 当前持仓组合在坐标系中的位置
    curr_pt = None
    if current_weights:
        w_curr = np.array([current_weights.get(s, 0.0) for s in symbols], dtype=np.float64)
        if w_curr.sum() > 1e-6:
            w_curr = w_curr / w_curr.sum()
            c_vol = float(np.sqrt(max(float(w_curr @ cov @ w_curr), 1e-12)))
            c_ret = float(w_curr @ mu)
            c_sharpe = (c_ret - risk_free_rate) / c_vol if c_vol > 1e-12 else 0.0
            curr_pt = {
                "vol": round(c_vol, 4),
                "ret": round(c_ret, 4),
                "sharpe": round(c_sharpe, 4),
                "weights": {s: round(float(w_curr[j]), 4) for j, s in enumerate(symbols)},
            }

    return {
        "frontier": frontier,
        "assets": assets,
        "min_vol": min_vol_pt,
        "max_sharpe": max_sharpe_pt,
        "current": curr_pt,
        "risk_free_rate": risk_free_rate,
    }
