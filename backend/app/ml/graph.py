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

import hashlib
import json
from datetime import datetime, timezone
from pathlib import Path

import numpy as np
import pandas as pd
import polars as pl
from loguru import logger

from ..core.config import get_settings

EDGES_DATASET = "relations"
# 单一行业桶的规模上限（超过则视为分类脏桶，不生成同业全连接边）
MAX_PEER_BUCKET = 150
# 冻结邻接 universe 快照 / 血缘文件名（落在 features/version=<v>/ 下）
GRAPH_SNAPSHOT_NAME = "universe_snapshot.json"
GRAPH_LINEAGE_NAME = "graph_lineage.json"


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


def edges_digest() -> str:
    """邻接**边集**指纹（16 hex）：显式边 + 行业边，去重排序后哈希。

    纳入 (src, dst, weight) 三元组：行归一化后 A 只由节点集与这三者决定，
    relation 仅是标签故不计入（同拓扑换标签不应改变 g1_）。
    """
    frames = []
    for df in (load_edges(), industry_edges()):
        if df.height:
            cols = [c for c in ("src", "dst", "weight") if c in df.columns]
            frames.append(df.select(cols))
    if not frames:
        return "empty"
    all_e = pl.concat(frames, how="diagonal_relaxed")
    all_e = all_e.with_columns(pl.col("weight").fill_null(1.0).cast(pl.Float64))
    all_e = all_e.unique().sort(["src", "dst", "weight"])
    return hashlib.sha256(all_e.hash_rows().to_numpy().tobytes()).hexdigest()[:16]


def universe_digest(symbols: list[str], edges_dg: str, hops: int = 1) -> str:
    """邻接 universe 指纹（16 hex）：节点集 + 边集指纹 + 跳数。"""
    h = hashlib.sha256()
    h.update(f"hops={hops}|edges={edges_dg}|n={len(symbols)}|".encode())
    h.update("\x00".join(sorted(set(symbols))).encode())
    return h.hexdigest()[:16]


def load_universe_snapshot(feature_dir: Path) -> dict | None:
    """读取特征版本目录下的邻接 universe 快照（缺失/损坏返回 None 并告警）。"""
    p = Path(feature_dir) / GRAPH_SNAPSHOT_NAME
    if not p.exists():
        return None
    try:
        snap = json.loads(p.read_text(encoding="utf-8"))
    except Exception as e:  # noqa: BLE001 快照损坏不得让构建失败，按"未冻结"处理
        logger.warning(f"[graph] universe 快照不可读（{p}）：{type(e).__name__}: {e}")
        return None
    if not isinstance(snap, dict) or not snap.get("symbols"):
        logger.warning(f"[graph] universe 快照内容非法（{p}），按未冻结处理")
        return None
    return snap


def save_universe_snapshot(feature_dir: Path, symbols: list[str], hops: int = 1,
                           edges_dg: str | None = None,
                           prev: dict | None = None) -> dict:
    """原子写入邻接 universe 快照（首次冻结 / 显式重冻结）。"""
    dg = edges_dg if edges_dg is not None else edges_digest()
    nodes = sorted(set(symbols))
    snap = {
        "version": 1,
        "created_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "n_symbols": len(nodes),
        "hops": hops,
        "edges_digest": dg,
        "fingerprint": universe_digest(nodes, dg, hops),
        "symbols": nodes,
    }
    if prev:  # 重冻结：留痕，便于事后追"历史 g1_ 何时被改口径"
        snap["refrozen_from"] = {
            "fingerprint": prev.get("fingerprint"),
            "edges_digest": prev.get("edges_digest"),
            "at": prev.get("created_at"),
        }
    p = Path(feature_dir)
    p.mkdir(parents=True, exist_ok=True)
    out = p / GRAPH_SNAPSHOT_NAME
    tmp = out.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(snap, ensure_ascii=False, indent=1), encoding="utf-8")
    tmp.replace(out)
    return snap


def resolve_universe(feature_dir: Path, symbols: list[str], hops: int = 1,
                     allow_drift: bool = False, write: bool = True) -> dict:
    """取（必要时冻结）邻接 universe 快照——``g1_*`` asof 稳定性的唯一入口。

    实测机制（探针 probe_p118*.py，非推断）：``propagate`` 的聚合是
    ``agg = num/den``，两者都用同一行归一化权重 ⇒ **归一化按行约掉**，真正决定
    ``g1_`` 的是"哪些邻居当天有取值"。因此面板成员变化通过两条路改写历史
    ``g1_``：
    1. **新标的入池且同行业桶** ⇒ 它的取值进入邻居均值（实测 A 的 ``g1_``
       3.0 → 252.0）；冻结节点集可挡住（非 universe 标的不是节点、其取值不入
       分子分母）；
    2. **已在 universe 的标的行情消失**（``read_all_symbols(skip_empty=True)``
       会跳过被隔离标的留下的空目录）⇒ 邻居均值少一项（实测 3.0 → 2.5），
       **无法复原**，只能拒绝静默改写历史并告警（``drift.n_absent_symbols``）。
    另有第三条：**边集指纹变化**（行业/关系数据回填）⇒ 默认同样拒绝。

    Args:
        feature_dir: 特征版本目录（快照落 ``universe_snapshot.json``）。
        symbols: 本次面板的标的集（仅用于**首次**冻结与漂移披露）。
        allow_drift: 边集指纹变化时是否允许重冻结（默认 False = 拒绝，调用方
            据此拒绝"静默改写历史"；显式 opt-in 才重冻结并留痕）。
        write: False ⇒ 只读（推理/服务路径不得写盘）。

    Returns:
        ``{"universe", "snapshot", "drift"}``；``drift.changed`` 为边集指纹变化，
        ``drift.refrozen`` 表示本次已按 allow_drift 重冻结，``drift.seeded`` 为首次冻结，
        ``drift.absent_symbols`` 为冻结集里本次无行情的标的（样本，前 10 只）。
    """
    snap = load_universe_snapshot(feature_dir)
    cur = edges_digest()
    drift: dict[str, object] = {"changed": False, "edges_digest": cur,
             "snapshot_edges_digest": (snap or {}).get("edges_digest"),
             "refrozen": False, "seeded": False,
             "n_new_symbols": 0, "n_absent_symbols": 0, "absent_symbols": [],
             "reason": ""}
    if snap is None:
        if not write:
            return {"universe": None, "snapshot": None, "drift": drift}
        snap = save_universe_snapshot(feature_dir, symbols, hops, edges_dg=cur)
        drift["seeded"] = True
        drift["reason"] = "首次冻结邻接 universe"
        logger.info(f"[graph] 邻接 universe 已冻结：n={snap['n_symbols']} "
                    f"edges={cur} fp={snap['fingerprint']}")
        return {"universe": snap["symbols"], "snapshot": snap, "drift": drift}

    nodes = snap["symbols"]
    panel = set(symbols)
    absent = sorted(set(nodes) - panel)
    drift["n_new_symbols"] = len(panel - set(nodes))
    drift["n_absent_symbols"] = len(absent)
    drift["absent_symbols"] = absent[:10]
    if drift["snapshot_edges_digest"] != cur:
        drift["changed"] = True
        if allow_drift and write:
            snap = save_universe_snapshot(feature_dir, nodes, hops, edges_dg=cur,
                                          prev=snap)
            drift["refrozen"] = True
            drift["reason"] = (f"边集指纹变化 {drift['snapshot_edges_digest']}→{cur}，"
                               f"已按 FEATURE_ALLOW_GRAPH_DRIFT 重冻结（历史 g1_ 口径改变）")
            logger.warning(f"[graph] {drift['reason']} fp={snap['fingerprint']}")
        else:
            drift["reason"] = (f"边集指纹变化 {drift['snapshot_edges_digest']}→{cur}，"
                               f"历史 g1_ 不可比")
    return {"universe": snap["symbols"], "snapshot": snap, "drift": drift}


def write_graph_lineage(feature_dir: Path, resolved: dict, n_panel: int) -> dict:
    """把邻接 universe 指纹写进特征版本目录血缘（供"这批特征用的哪套图"追溯）。"""
    snap = resolved.get("snapshot") or {}
    drift = resolved.get("drift") or {}
    lin = {
        "feature_scope": "g1_graph_universe",
        "fingerprint": snap.get("fingerprint"),
        "n_nodes": snap.get("n_symbols"),
        "hops": snap.get("hops"),
        "edges_digest": snap.get("edges_digest"),
        "n_panel_symbols": n_panel,
        "n_panel_symbols_not_in_universe": drift.get("n_new_symbols"),
        "universe_absent_from_panel": drift.get("n_absent_symbols"),
        "graph_drift_detected": bool(drift.get("changed")),
        "graph_refrozen": bool(drift.get("refrozen")),
        "graph_seeded": bool(drift.get("seeded")),
        "updated_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "note": ("g1_* 由冻结邻接 universe 计算：面板成员变化不再改写历史 g1_；"
                 "边集指纹变化被拒绝或显式重冻结后才改变历史口径"),
    }
    p = Path(feature_dir)
    p.mkdir(parents=True, exist_ok=True)
    (p / GRAPH_LINEAGE_NAME).write_text(
        json.dumps(lin, ensure_ascii=False, indent=1), encoding="utf-8")
    return lin


def build_adjacency(symbols: list[str],
                    universe: list[str] | None = None) -> tuple[dict[str, int], np.ndarray]:
    """符号 → 行归一化邻接矩阵（无向：src/dst 双向注入；无任何边时 A=0）。

    行归一化（D^-1·A）使聚合 = 邻居均值，天然抗节点度差异。

    ``universe``: 冻结的邻接**节点集**（None ⇒ 用 ``symbols``，即当前面板）。
    实测要点（勿误信"归一化分母"解释）：``propagate`` 里 ``agg = num/den`` 用同一
    行归一化权重、按行约掉，故节点集**不影响**权重；它的作用是**筛选取值**——
    面板里存在但不属于 ``universe`` 的标的（新入池且同行业桶）不是节点，其取值
    不进入分子分母，从而不污染老标的的 ``g1_*``（实测 3.0 vs 252.0）。
    """
    nodes = sorted(set(universe) if universe else {str(s) for s in symbols})
    edges = pl.concat([load_edges(), industry_edges()], how="diagonal_relaxed")
    idx = {s: i for i, s in enumerate(nodes)}
    A = np.zeros((len(nodes), len(nodes)), dtype=np.float64)
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
