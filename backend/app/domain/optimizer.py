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


def apply_weight_cap(w: np.ndarray, cap: float, max_iter: int = 100) -> np.ndarray:
    """简单x上限约束：迭代 clip+重分配（cap*len(w) >= 1 时必可行）。"""
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


# ==================== 统一分发入口 ====================
WEIGHTING_SCHEMES = ("user", "equal", "score_weighted", "risk_parity",
                     "max_div", "inverse_vol", "mvo")


def compute_weights_from_returns(
    returns: np.ndarray,
    method: str = "risk_parity",
    expected_returns: np.ndarray | None = None,
    weight_cap: float = 0.0,
    prev_weights: np.ndarray | None = None,
    turnover_penalty: float = 0.0,
) -> np.ndarray:
    """按方案计算权重（输入严格 T 日及之前的收益率矩阵）。

    :param method: risk_parity / max_div / inverse_vol / mvo / equal
    :raises ValueError: 未知方案或 returns 形状非法
    """
    R = np.asarray(returns, dtype=np.float64)
    if method == "equal":
        return np.full(R.shape[1], 1.0 / R.shape[1])
    if method == "inverse_vol":
        w = inverse_vol_weights(R)
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
