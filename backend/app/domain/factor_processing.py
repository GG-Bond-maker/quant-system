"""因子预处理与纯化（机构级截面处理流水线，纯函数，无 IO）。

标准处理链（每个交易日截面独立执行，跨日无滚动 => 无未来信息）：
    原始因子
      -> MAD 去极值（median ± n_mad × 1.4826 × MAD，比 3-Sigma 稳健）
      -> 截面标准化（Z-Score 或 Rank/分位数变换，消除偏态）
      -> 行业/市值中性化（见 neutralize.py，OLS 残差）
      -> 对称正交化（Lowdin：消除因子间多重共线性，保留全部信息）

所有函数均为纯函数（numpy/pandas），符合 domain 层架构守卫
（tests/test_domain_purity.py 的 AST 扫描会自动纳入本文件）。
"""
from __future__ import annotations

import numpy as np
import pandas as pd

# MAD 与正态分布等价的比例系数：1 个 MAD ≈ 1.4826 个标准差
MAD_TO_STD = 1.4826


# ==================== 1) 单截面纯函数 ====================
def mad_winsorize(arr: np.ndarray, n_mad: float = 3.0) -> np.ndarray:
    """MAD 去极值：clip 到 [median - n_mad·1.4826·MAD, median + n_mad·1.4826·MAD]。

    NaN 原样保留（由上游决定剔除或填充）；MAD 为 0（常数列）时不做处理。
    """
    a = np.asarray(arr, dtype=np.float64).copy()
    finite = a[np.isfinite(a)]
    if finite.size == 0:
        return a
    median = float(np.median(finite))
    mad = float(np.median(np.abs(finite - median)))
    if mad <= 0:
        return a
    bound = n_mad * MAD_TO_STD * mad
    a[np.isfinite(a)] = np.clip(finite, median - bound, median + bound)
    return a


def cross_sectional_zscore(arr: np.ndarray) -> np.ndarray:
    """截面 Z-Score：去均值 / 标准差。std=0（常数列）返回全 0，NaN 保留。"""
    a = np.asarray(arr, dtype=np.float64).copy()
    finite = a[np.isfinite(a)]
    if finite.size < 2:
        return a
    std = float(np.std(finite, ddof=1))
    if std < 1e-12:
        a[np.isfinite(a)] = 0.0
        return a
    mean = float(np.mean(finite))
    a[np.isfinite(a)] = (finite - mean) / std
    return a


def rank_transform(arr: np.ndarray) -> np.ndarray:
    """Rank / 分位数变换：截面秩转为 [-0.5, 0.5] 均匀分布。

    消除因子偏态（比 Z-Score 更稳健），单调性保留（对 RankIC 无损）。
    并列取平均秩（pandas average 方式）；NaN 保留。
    """
    a = np.asarray(arr, dtype=np.float64)
    out = a.copy()
    mask = np.isfinite(a)
    n = int(mask.sum())
    if n < 2:
        return out
    ranks = pd.Series(a[mask]).rank(method="average").to_numpy(dtype=np.float64)
    out[mask] = (ranks - (n + 1) / 2.0) / n   # 值域 (-0.5, 0.5)，均值恒为 0
    return out


# ==================== 2) sklearn 风格的截面处理器 ====================
class CrossSectionalScaler:
    """按日期分组：MAD 去极值 -> 截面标准化（zscore 或 rank）。

    与 sklearn BaseEstimator 不同，本类**无拟合状态**：每个时间截面
    独立变换，天然 PIT 安全（任意截断日重算，历史截面值不变）。

    用法::

        scaler = CrossSectionalScaler(method="zscore", n_mad=3.0)
        scaled = scaler.transform(df)          # df 含 date 列 + 因子列
    """

    def __init__(self, method: str = "zscore", n_mad: float = 3.0,
                 exclude_cols: tuple[str, ...] = ("date", "symbol", "code")):
        if method not in ("zscore", "rank"):
            raise ValueError(f"method 仅支持 zscore/rank，收到 {method!r}")
        self.method = method
        self.n_mad = n_mad
        self.exclude_cols = tuple(exclude_cols)

    def transform(self, df: pd.DataFrame, feature_cols: list[str] | None = None) -> pd.DataFrame:
        """对每个 date 截面逐列做 MAD 去极值 + 标准化；返回副本（不改入参）。"""
        if df.empty:
            return df.copy()
        cols = [c for c in (feature_cols or
                            [c for c in df.columns if c not in self.exclude_cols])
                if c in df.columns]
        out = df.copy()
        for _, idx in out.groupby("date", sort=False).groups.items():
            pos = out.index.get_indexer(idx)
            for col in cols:
                v = out.iloc[pos][col].to_numpy(dtype=np.float64)
                v = mad_winsorize(v, self.n_mad)
                if self.method == "rank":
                    v = rank_transform(v)
                else:
                    v = cross_sectional_zscore(v)
                out.iloc[pos, out.columns.get_loc(col)] = v
        return out


# ==================== 3) 对称正交化（Lowdin / Symmetric Orthogonalization） ====================
def symmetric_orthogonalize(F: np.ndarray, tol: float = 1e-12) -> np.ndarray:
    """Lowdin 对称正交化：F_ortho = F (F'F)^{-1/2}。

    消除因子间多重共线性，同时**均摊**信息到各列（不像 Gram-Schmidt
    那样偏袒排序列）。SVD 实现：F = U S V' => F_ortho = U V'。

    性质（测试守卫）：
    - F_ortho' F_ortho = I（列正交单位化）
    - F_ortho 与 F 张成相同的列空间（到列空间上的投影不变）
    - rank 退化列（奇异值 < tol）自动丢弃零空间分量

    注意：正交化保证列向量内积为 0；若还需 Pearson 相关为 0，
    须先对截面去均值（见 orthogonalize_factor_panel）。

    :param F: (n_samples, n_factors) 截面因子矩阵，NaN 需上游处理
    """
    M = np.asarray(F, dtype=np.float64)
    if M.ndim != 2:
        raise ValueError(f"F 必须是 2 维矩阵，收到 {M.ndim} 维")
    if not np.all(np.isfinite(M)):
        raise ValueError("F 含 NaN/inf：请先剔除或填充（正交化对缺失敏感）")
    U, S, Vt = np.linalg.svd(M, full_matrices=False)
    keep = S > tol * max(S[0] if S.size else 1.0, 1.0)   # 数值秩
    Uk, Vtk = U[:, keep], Vt[keep, :]
    return Uk @ Vtk


def orthogonalize_factor_panel(
    df: pd.DataFrame,
    factor_cols: list[str],
    date_col: str = "date",
) -> pd.DataFrame:
    """按日期截面对因子面板做对称正交化。

    每个截面：剔除含 NaN 的行 -> 截面去均值 -> Lowdin 正交化 -> 写回
    （被剔除行保持 NaN）。去均值是必要的：正交化只保证列向量内积为 0，
    Pearson 相关还要求零均值。截面样本数 <= 因子数时跳过该截面
    （欠定无法正交），原值保留。
    """
    out = df.copy()
    out.loc[:, factor_cols] = np.nan
    for d, g in df.groupby(date_col, sort=False):
        sub = g[factor_cols].apply(pd.to_numeric, errors="coerce")
        ok = sub.notna().all(axis=1)
        M = sub.loc[ok].to_numpy(dtype=np.float64)
        if M.shape[0] <= len(factor_cols) or M.shape[0] < 2:
            out.loc[g.index, factor_cols] = sub
            continue
        M = M - M.mean(axis=0)          # 截面去均值（保证正交 == 零相关）
        ortho = symmetric_orthogonalize(M)
        # 列方向单位方差，保持因子间可比（正交化后方差不再为 1）
        std = np.std(ortho, axis=0, ddof=1)
        std[std < 1e-12] = 1.0
        out.loc[g.index[ok], factor_cols] = ortho / std
    return out
