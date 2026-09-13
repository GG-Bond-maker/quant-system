"""产业链图谱数据前置（3-2 GNN 前置：邻接构建 + 邻居聚合传导因子）。

数据来源（均可能为空——模块在无关系数据时透明降级，绝不造边）：
1. 显式关系表：DATA_ROOT/relations/edges.parquet
   列：src, dst, relation(supply/customer/parent/peer...), weight(0~1)
   —— 供应链/股权/竞争关系数据到位后由抓取管线写入；
2. 行业边：SQLite instrument 表 industry 列（当前 86% 为空——扩池抓数后
   自动变稠），同行业互连为 peer 边。

输出：
- ``build_adjacency(symbols)`` → (符号→下标映射, 行归一化邻接矩阵 A)；
- ``propagate(features_df, A, cols, hops)`` → 追加 ``g{hops}_{col}`` 列
  （邻居聚合特征 = A·X，即"上下游因子均值"的传导信号，可与本股因子
  一起进入 LGBM/TFT，作为产业链滞后续传播的最低成本近似）；
- torch GCN 训练器见 gnn_models.py（可选依赖，未装 torch 时不可用）。
"""
from __future__ import annotations

import numpy as np
import pandas as pd
import polars as pl
from loguru import logger

from ..core.config import get_settings

EDGES_DATASET = "relations"
# 单一行业桶的规模上限（超过则视为分类脏桶，不生成同业全连接边）
MAX_PEER_BUCKET = 150


def load_edges() -> pl.DataFrame:
    """显式关系边（无文件返回空表）。"""
    p = get_settings().DATA_ROOT / EDGES_DATASET / "edges.parquet"
    if not p.exists():
        return pl.DataFrame(schema={"src": pl.String, "dst": pl.String,
                                    "relation": pl.String, "weight": pl.Float64})
    return pl.read_parquet(p)


def industry_edges() -> pl.DataFrame:
    """同行业 peer 边（industry 为空的标的不产生边）。"""
    from ..trading.paper import sync_session_factory  # 延迟导入避免环

    Session = sync_session_factory()
    try:
        with Session() as sess:
            rows = sess.execute(sa_text(
                "SELECT symbol, industry FROM instrument "
                "WHERE industry IS NOT NULL AND industry != ''")).all()
    except Exception as e:  # noqa: BLE001 表未建/库缺失时透明降级
        logger.warning(f"[graph] 读取 instrument 行业失败: {type(e).__name__}")
        return pl.DataFrame(schema={"src": pl.String, "dst": pl.String,
                                    "relation": pl.String,
                                    "weight": pl.Float64})
    by_ind: dict[str, list[str]] = {}
    for sym, ind in rows:
        by_ind.setdefault(str(ind), []).append(str(sym))
    triples: list[tuple[str, str, str, float]] = []
    skipped = 0
    for ind, syms in by_ind.items():
        # 同行业是全连接：桶内 n 个标的产生 n(n-1)/2 条边。个别脏桶（如全部
        # 归入 "-"）在 5000 只池下能炸出上千万条边、把邻接矩阵灌成稠密图，
        # 这种"边"也不再表达任何产业链信息——直接跳过并告警。
        if len(syms) > MAX_PEER_BUCKET:
            skipped += 1
            continue
        for i, a in enumerate(syms):
            for b in syms[i + 1:]:
                triples.append((a, b, f"peer:{ind}", 1.0))
    if skipped:
        logger.warning(f"[graph] {skipped} 个行业桶超过 {MAX_PEER_BUCKET} 只，"
                       f"已跳过同业边（疑似分类脏桶）")
    if not triples:
        return pl.DataFrame(schema={"src": pl.String, "dst": pl.String,
                                    "relation": pl.String,
                                    "weight": pl.Float64})
    return pl.DataFrame(triples, schema=["src", "dst", "relation", "weight"],
                         orient="row")


def sa_text(q: str):
    import sqlalchemy as sa
    return sa.text(q)


def build_adjacency(symbols: list[str]) -> tuple[dict[str, int], np.ndarray]:
    """符号 → 行归一化邻接矩阵（无向：src/dst 双向注入；无任何边时 A=0）。

    行归一化（D^-1·A）使聚合 = 邻居均值，天然抗节点度差异。
    """
    edges = pl.concat([load_edges(), industry_edges()], how="diagonal_relaxed")
    idx = {s: i for i, s in enumerate(symbols)}
    A = np.zeros((len(symbols), len(symbols)), dtype=np.float64)
    n_edges = 0
    for src, dst, w in zip(edges["src"].to_list(), edges["dst"].to_list(),
                           edges["weight"].to_list()):
        i, j = idx.get(src), idx.get(dst)
        if i is None or j is None or i == j:
            continue
        A[i, j] += float(w or 1.0)
        A[j, i] += float(w or 1.0)
        n_edges += 1
    row_sum = A.sum(axis=1, keepdims=True)
    row_sum[row_sum == 0] = 1.0
    return idx, A / row_sum


def propagate(features: pd.DataFrame, A: np.ndarray, idx: dict[str, int],
              cols: list[str], hops: int = 1) -> pd.DataFrame:
    """邻居聚合传导因子：g{h}_{col} = (A^h · x)_symbol（同日截面传播）。

    features: pd.DataFrame(symbol, date, <cols>) → 原表追加 g{h}_<col> 列。
    交易日内逐日聚合（邻接静态假设——供应链关系月度变化对日频因子可忽略）。

    实现要点（防回归）：
    - **无关系数据也补 NaN 列**而非返回原表：下游按列名取 g{h}_<col> 时不
      应因"有没有关系数据"而在 KeyError 与正常之间摇摆；NaN 明确表达
      "无覆盖"，符合不造数原则。
    - 邻域聚合走**分块矩阵乘**而非逐日 Python 循环：把面板转成 (N, C×F)
      后一次 BLAS 完成 (N,N)@(N,C×F)，5000 只 ×1131 日 ×20 因子才可行
      （逐日三重循环在该规模下是千万级 Python 迭代）。
    - 分母按**有值邻居**归一（缺失邻居不稀释信号）；无任何有值邻居 → NaN。
    """
    cols = list(cols)
    if not cols or features.empty:
        return features.assign(**{f"g{hops}_{c}": np.nan for c in cols})
    if A.sum() == 0:
        # 无关系数据：透明降级为全 NaN 列（列名稳定存在，值明确表示无覆盖）
        return features.assign(**{f"g{hops}_{c}": np.nan for c in cols})

    out = features.copy()
    dts = pd.to_datetime(out["date"])
    dates = np.sort(dts.unique())
    An = A if hops == 1 else np.linalg.matrix_power(A, hops)

    code = out["symbol"].map(idx)
    valid = code.notna().to_numpy()
    si = np.where(valid, code.to_numpy(dtype=np.float64), 0).astype(np.int64)
    di = dts.map({d: i for i, d in enumerate(dates)}).to_numpy(dtype=np.int64)

    n_node, n_feat = len(idx), len(cols)
    vals = out[cols].to_numpy(dtype=np.float64)
    gvals = np.full((len(out), n_feat), np.nan)
    # 分块上限：约 2e7 个元素的临时矩阵（≈160MB @float64），按节点×因子自适应
    chunk = max(1, int(2e7 // max(1, n_node * n_feat)))

    for s in range(0, len(dates), chunk):
        e = min(s + chunk, len(dates))
        C = e - s
        P = np.full((C, n_node, n_feat), np.nan)
        sel = (di >= s) & (di < e) & valid
        P[di[sel] - s, si[sel]] = vals[sel]
        H = np.isfinite(P)
        # (N,N) @ (N, C*F) → (N, C*F)，再还原成 (C, N, F)
        flat = np.where(H, P, 0.0).transpose(1, 0, 2).reshape(n_node, C * n_feat)
        num = (An @ flat).reshape(n_node, C, n_feat).transpose(1, 0, 2)
        hflat = H.astype(np.float64).transpose(1, 0, 2).reshape(n_node, C * n_feat)
        den = (An @ hflat).reshape(n_node, C, n_feat).transpose(1, 0, 2)
        agg = np.where(den > 1e-12, num / np.where(den > 1e-12, den, 1.0), np.nan)
        gvals[sel] = agg[di[sel] - s, si[sel]]

    for f, col in enumerate(cols):
        out[f"g{hops}_{col}"] = gvals[:, f]
    return out


def relations_status() -> dict:
    """关系数据状态（GNN 可训练性判据）。"""
    edges = load_edges()
    ind = industry_edges()
    return {
        "explicit_edges": edges.height,
        "industry_edges": ind.height,
        "edge_file": str(get_settings().DATA_ROOT / EDGES_DATASET / "edges.parquet"),
        "note": ("关系数据就绪" if edges.height or ind.height
                 else "尚无关系数据：等待 relations/edges.parquet 或 instrument.industry 补齐"),
    }
