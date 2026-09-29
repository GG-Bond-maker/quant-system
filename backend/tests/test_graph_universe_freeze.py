"""P1-18：``g1_*`` 邻接 universe 冻结（asof 稳定性）——缺陷本体反证 + 修复验证。

实测机制（本文件即是证据，非推断）：``propagate`` 的 ``agg = num/den`` 用同一
行归一化权重 ⇒ 归一化按行约掉，决定 ``g1_`` 的是"哪些邻居当天有取值"。故：
- 新标的入池且同行业桶 ⇒ 其取值进入邻居均值（3.0 → 252.0），冻结节点集可挡；
- 冻结标的行情消失 ⇒ 邻居均值少一项（3.0 → 2.5），不可复原 ⇒ 必须拒绝静默重写。
"""
from __future__ import annotations

import json
from datetime import date

import numpy as np
import pandas as pd
import polars as pl
import pytest

from app.ml import graph

_EMPTY_EDGES = pl.DataFrame(schema={"src": pl.String, "dst": pl.String,
                                    "relation": pl.String, "weight": pl.Float64})
_D = pd.Timestamp("2026-09-18")
_VALS = {"A": 1.0, "B": 2.0, "C": 3.0, "D": 4.0, "X": 999.0}


def _edges_for(symbols: list[str]) -> pl.DataFrame:
    """同行业桶全连接边（X 也在桶内 ⇒ 才有"新标的污染邻居"的真实现场）。"""
    pairs = [(a, b) for i, a in enumerate(symbols) for b in symbols[i + 1:]]
    return pl.DataFrame({
        "src": [p[0] for p in pairs] + [p[1] for p in pairs],
        "dst": [p[1] for p in pairs] + [p[0] for p in pairs],
        "relation": ["peer:半导体"] * (2 * len(pairs)),
        "weight": [1.0] * (2 * len(pairs))})


@pytest.fixture
def bucket_edges(monkeypatch):
    def fake_load() -> pl.DataFrame:
        return _edges_for(["A", "B", "C", "D", "X"])

    monkeypatch.setattr(graph, "load_edges", fake_load)
    monkeypatch.setattr(graph, "industry_edges", lambda: _EMPTY_EDGES)
    return fake_load


def _a_g1(symbols: list[str], universe: list[str] | None) -> float:
    df = pd.DataFrame({"symbol": symbols, "date": [_D] * len(symbols),
                       "f0": [_VALS[s] for s in symbols]})
    idx, A = graph.build_adjacency(sorted(symbols), universe=universe)
    out = graph.propagate(df, A, idx, ["f0"], hops=1).set_index("symbol")
    return float(out.loc["A", "g1_f0"])


def test_defect_body_new_member_pollutes_neighbour_g1(bucket_edges):
    """缺陷本体：同一历史日，面板里多一只**同桶新标的**就改写老标的 g1_。"""
    base = _a_g1(["A", "B", "C", "D"], None)
    polluted = _a_g1(["A", "B", "C", "D", "X"], None)
    assert abs(base - 3.0) < 1e-9                       # (2+3+4)/3
    assert abs(polluted - 252.0) < 1e-9                 # (2+3+4+999)/4 —— 解析值
    assert polluted != base, "本体反证失败：未复现 P1-18"


def test_frozen_universe_blocks_new_member_pollution(bucket_edges):
    """修复：冻结节点集后，新标的的取值不入分子分母 ⇒ 老标的 g1_ 逐值不变。"""
    frozen = ["A", "B", "C", "D"]
    base = _a_g1(frozen, None)
    after = _a_g1(["A", "B", "C", "D", "X"], frozen)
    assert abs(after - base) < 1e-12
    # 新标的自身不是节点 ⇒ g1_ 为 NaN（不造数）
    df = pd.DataFrame({"symbol": frozen + ["X"], "date": [_D] * 5,
                       "f0": [_VALS[s] for s in frozen + ["X"]]})
    idx, A = graph.build_adjacency(sorted(frozen + ["X"]), universe=frozen)
    out = graph.propagate(df, A, idx, ["f0"], hops=1).set_index("symbol")
    assert bool(np.isnan(out.loc["X", "g1_f0"]))
    assert set(idx) == set(frozen)


def test_absent_universe_symbol_cannot_be_restored(bucket_edges):
    """边界（不可修的那半）：冻结标的行情真没了 ⇒ 邻居 g1_ 必变，只能拒绝而非假装稳定。"""
    frozen = ["A", "B", "C", "D"]
    base = _a_g1(frozen, frozen)
    lost = _a_g1(["A", "B", "C"], frozen)
    assert abs(base - 3.0) < 1e-9
    assert abs(lost - 2.5) < 1e-9, "取值消失后仍称不变即为造数"
    assert lost != base


def test_edges_digest_is_topology_and_weight_sensitive_but_label_insensitive(
        bucket_edges, monkeypatch):
    """边集指纹：拓扑/权重变化必须变，relation 标签变化不应变。"""
    base = graph.edges_digest()
    assert len(base) == 16
    orig = bucket_edges()          # 先取原始帧，避免 lambda 自引用递归
    monkeypatch.setattr(graph, "load_edges",
                        lambda: orig.with_columns(pl.lit("peer:别的行业").alias("relation")))
    assert graph.edges_digest() == base, "relation 只是标签，不应改变指纹"
    monkeypatch.setattr(graph, "load_edges",
                        lambda: orig.with_columns(pl.lit(2.0).alias("weight")))
    assert graph.edges_digest() != base, "权重变化会改变 A，必须纳入指纹"


def test_resolve_universe_seeds_once_and_reuses(tmp_path, bucket_edges):
    """首次冻结后复用同一节点集：不同面板成员不改变 universe 与指纹。"""
    d = tmp_path / "version=x"
    first = graph.resolve_universe(d, ["A", "B", "C", "D"])
    assert first["drift"]["seeded"] is True
    assert first["universe"] == ["A", "B", "C", "D"]
    snap = json.loads((d / graph.GRAPH_SNAPSHOT_NAME).read_text(encoding="utf-8"))
    assert snap["n_symbols"] == 4 and snap["edges_digest"] == first["drift"]["edges_digest"]
    second = graph.resolve_universe(d, ["A", "B", "C", "D", "X"])
    assert second["drift"]["seeded"] is False
    assert second["universe"] == first["universe"]
    assert second["snapshot"]["fingerprint"] == first["snapshot"]["fingerprint"]
    assert second["drift"]["n_new_symbols"] == 1
    assert second["drift"]["n_absent_symbols"] == 0
    third = graph.resolve_universe(d, ["A", "B", "C"])
    assert third["drift"]["n_absent_symbols"] == 1
    assert third["drift"]["absent_symbols"] == ["D"]


def test_edge_drift_refused_by_default_and_refrozen_on_optin(tmp_path, bucket_edges,
                                                            monkeypatch):
    """边集指纹变化：默认拒绝（不重冻结、不改写），显式 opt-in 才重冻结并留痕。"""
    d = tmp_path / "version=x"
    graph.resolve_universe(d, ["A", "B", "C", "D"])
    before = json.loads((d / graph.GRAPH_SNAPSHOT_NAME).read_text(encoding="utf-8"))

    monkeypatch.setattr(graph, "load_edges",
                        lambda: _edges_for(["A", "B", "C", "D", "X"])[:4])
    refused = graph.resolve_universe(d, ["A", "B", "C", "D"])
    assert refused["drift"]["changed"] is True
    assert refused["drift"]["refrozen"] is False
    assert "不可比" in refused["drift"]["reason"]
    after_refuse = json.loads((d / graph.GRAPH_SNAPSHOT_NAME).read_text(encoding="utf-8"))
    assert after_refuse == before, "被拒绝时不得改动快照（否则等于静默改口径）"

    allowed = graph.resolve_universe(d, ["A", "B", "C", "D"], allow_drift=True)
    assert allowed["drift"]["refrozen"] is True
    snap = json.loads((d / graph.GRAPH_SNAPSHOT_NAME).read_text(encoding="utf-8"))
    assert snap["refrozen_from"]["edges_digest"] == before["edges_digest"]
    assert snap["edges_digest"] == allowed["drift"]["edges_digest"] != before["edges_digest"]


def test_orchestrator_refuses_first_freeze_over_existing_history(tmp_path, monkeypatch,
                                                                bucket_edges):
    """过渡期守卫：已有历史特征但尚无快照 ⇒ 首次冻结必须显式授权。

    否则"部署这个修复"本身就成了另一次静默改写（旧口径=每次按当时面板建图 ⇒
    首次冻结会一次性改掉全部历史 g1_）。拒绝时**不得落快照**（不留下状态）。
    """
    from app import orchestrator
    from app.core.config import get_settings

    data = tmp_path / "parquet"
    vdir = data / "features" / "version=alpha_basic_v2g"
    vdir.mkdir(parents=True)
    pl.DataFrame({"symbol": ["A"], "date": [date(2026, 1, 5)],
                  "f": [1.0]}).write_parquet(vdir / "year=2026.parquet")
    # 仓库惯例：改**已缓存 Settings 对象**（勿用 setenv+cache_clear：teardown 只还原
    # 环境变量，lru_cache 仍指向 tmp_path ⇒ 污染同批次后续用例）。
    monkeypatch.setattr(get_settings(), "DATA_ROOT", data)
    monkeypatch.delenv("FEATURE_ALLOW_GRAPH_DRIFT", raising=False)
    with pytest.raises(ValueError) as ei:
        orchestrator.step_build_features(date(2026, 9, 18), [])
    assert "首次冻结会改变全部历史 g1_" in str(ei.value)
    assert not (vdir / graph.GRAPH_SNAPSHOT_NAME).exists(), "被拒绝时不得落快照"
    assert not (vdir / graph.GRAPH_LINEAGE_NAME).exists()
    # 显式授权后才允许在历史之上冻结：补出行情让重建本身能跑完
    _seed_bars(data, "600000.SH", [date(2026, 1, d) for d in (5, 6, 7, 8, 9)])
    monkeypatch.setenv("FEATURE_ALLOW_GRAPH_DRIFT", "1")
    orchestrator.step_build_features(date(2026, 9, 18), [])
    assert (vdir / graph.GRAPH_SNAPSHOT_NAME).exists()
    assert (vdir / graph.GRAPH_LINEAGE_NAME).exists()


def _seed_bars(data, sym: str, days: list) -> None:
    """最小 hfq 行情（OHLCV+amount）：让 `_full()` 能在合成 DATA_ROOT 上跑完。"""
    from app.data.parquet_store import write_year_batch

    rng = np.random.default_rng(3)
    n = len(days)
    close = 20.0 * np.exp(np.cumsum(rng.normal(0, 0.01, n)))
    pdf = pl.from_pandas(pd.DataFrame({
        "symbol": sym, "date": days, "open": close * 0.995, "high": close * 1.01,
        "low": close * 0.99, "close": close, "volume": rng.uniform(5e5, 2e6, n),
        "amount": close * rng.uniform(5e5, 2e6, n)}))
    for year in sorted({d.year for d in days}):
        write_year_batch("daily_bar_hfq", sym, year,
                         pdf.filter(pl.col("date").dt.year() == year))


def test_write_graph_lineage_records_fingerprint(tmp_path, bucket_edges):
    """血缘落盘：这批特征用的哪套图、有无漂移，事后可追。"""
    d = tmp_path / "version=x"
    res = graph.resolve_universe(d, ["A", "B", "C", "D"])
    # 面板后来多了 1 只未入图标的：血缘须如实记下"面板有 1 只不在 universe 内"
    res = graph.resolve_universe(d, ["A", "B", "C", "D", "X"])
    lin = graph.write_graph_lineage(d, res, n_panel=5)
    on_disk = json.loads((d / graph.GRAPH_LINEAGE_NAME).read_text(encoding="utf-8"))
    assert on_disk == lin
    assert lin["fingerprint"] == res["snapshot"]["fingerprint"]
    assert lin["n_nodes"] == 4 and lin["n_panel_symbols"] == 5
    assert lin["n_panel_symbols_not_in_universe"] == 1
    assert lin["graph_drift_detected"] is False and lin["graph_seeded"] is False


# ---------------- orchestrator 接线：拒绝静默改写 + 不落盘 ----------------

def _prep_features_env(tmp_path, monkeypatch, edges_digest: str) -> "object":
    """独立 DATA_ROOT + 预置一份"指纹已过期"的快照（模拟行业回填/隔离导致的口径变化）。"""
    from app.core.config import get_settings

    data = tmp_path / "parquet"
    (data / "features" / "version=alpha_basic_v2g").mkdir(parents=True)
    monkeypatch.setattr(get_settings(), "DATA_ROOT", data)
    snap = {"version": 1, "created_at": "2026-09-01T00:00:00+00:00",
            "n_symbols": 2, "hops": 1, "edges_digest": edges_digest,
            "fingerprint": "deadbeefdeadbeef", "symbols": ["A", "B"]}
    (data / "features" / "version=alpha_basic_v2g" / graph.GRAPH_SNAPSHOT_NAME).write_text(
        json.dumps(snap), encoding="utf-8")
    return data


def test_orchestrator_refuses_edge_drift_without_writing(tmp_path, monkeypatch,
                                                         bucket_edges):
    """端到端：边集指纹变化 ⇒ step_build_features 抛错且**不落任何年分区/血缘**。"""
    from app import orchestrator

    d = _prep_features_env(tmp_path, monkeypatch, "0000000000000000")
    monkeypatch.delenv("FEATURE_ALLOW_GRAPH_DRIFT", raising=False)
    with pytest.raises(ValueError) as ei:
        orchestrator.step_build_features(pd.Timestamp("2026-09-18").date(), [])
    assert "邻接边集指纹变化" in str(ei.value)
    vdir = d / "features" / "version=alpha_basic_v2g"
    assert not list(vdir.glob("year=*.parquet")), "拒绝路径不得写特征分区"
    assert not (vdir / graph.GRAPH_LINEAGE_NAME).exists(), "拒绝路径不得写血缘"
    assert json.loads((vdir / graph.GRAPH_SNAPSHOT_NAME).read_text(encoding="utf-8"))[
        "edges_digest"] == "0000000000000000", "被拒绝时快照不得被改写"


def test_orchestrator_refuses_absent_universe_symbol_without_writing(
        tmp_path, monkeypatch, bucket_edges):
    """端到端：冻结标的行情消失（隔离）⇒ 拒绝静默改写历史，且不落盘。"""
    from app import orchestrator

    d = _prep_features_env(tmp_path, monkeypatch, graph.edges_digest())
    monkeypatch.delenv("FEATURE_ALLOW_GRAPH_DRIFT", raising=False)
    # 面板为空（read_all_symbols 会把被隔离标的的空目录跳过）⇒ A、B 都算"无行情"
    with pytest.raises(ValueError) as ei:
        orchestrator.step_build_features(pd.Timestamp("2026-09-18").date(), [])
    msg = str(ei.value)
    assert "本次无行情" in msg and "无法复现" in msg
    vdir = d / "features" / "version=alpha_basic_v2g"
    assert not list(vdir.glob("year=*.parquet"))


def test_orchestrator_is_wired_to_frozen_universe_and_serve_path_matches():
    """接线锁：构建与实时推理两条路径都必须传冻结 universe（train/serve 同源）。"""
    from pathlib import Path

    root = Path(__file__).resolve().parents[1]
    orch = (root / "app" / "orchestrator.py").read_text(encoding="utf-8")
    assert orch.count("apply_propagate(build_factors(raw)") == 2, "两处构建路径都需接线"
    assert orch.count("apply_propagate(build_factors(raw),\n                              universe=None") == 0
    assert orch.count("universe=universe") == 2, "_full 与增量路径都要传冻结集"
    assert "resolve_universe(out_root, symbols, allow_drift=allow_drift)" in orch
    pred = (root / "app" / "ml" / "predict.py").read_text(encoding="utf-8")
    assert "load_universe_snapshot(" in pred, "实时回退必须读冻结快照（train/serve 同源）"
    assert "universe=(snap or {}).get(\"symbols\")" in pred
    scr = (root / "scripts" / "build_features.py").read_text(encoding="utf-8")
    assert "universe=resolved[\"universe\"]" in scr, "离线重建工具同样必须接线"


def test_no_residual_unfrozen_callers():
    """反向锁：除测试与文档外，app/ 内不得再有未传 universe 的 apply_propagate 调用。

    用 AST 找真实 Call 节点（文档字符串里的示例调用不算调用点）。
    """
    import ast
    from pathlib import Path

    root = Path(__file__).resolve().parents[1] / "app"
    bad = []
    for p in sorted(root.rglob("*.py")):
        tree = ast.parse(p.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if not isinstance(node, ast.Call):
                continue
            fn = node.func
            name = fn.attr if isinstance(fn, ast.Attribute) else getattr(fn, "id", "")
            if name != "apply_propagate":
                continue
            if any(kw.arg == "universe" for kw in node.keywords):
                continue
            bad.append(f"{p.relative_to(root.parent)}:{node.lineno}")
    assert not bad, f"仍有未冻结 universe 的调用点：{bad}"