"""数据前置能力测试：截面镜像（MED-003）/ 文本情绪因子（FinLLM 前置）/
序列张量与 purged 切分（TFT 前置）/ 图邻接与传导因子（GNN 前置）。

torch 相关脚手架：可选依赖，未安装时相关用例自动 skip。
"""
from __future__ import annotations

import ast
import importlib
import sys
from datetime import date, timedelta
from pathlib import Path

import numpy as np
import pandas as pd
import polars as pl
import pytest

BACKEND_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(BACKEND_ROOT))

from app.data import cross_section, text_ingest  # noqa: E402
from app.ml import graph, sequence_dataset  # noqa: E402


# ---------------- 截面分区镜像 ----------------
@pytest.fixture
def data_env(tmp_path, monkeypatch):
    """独立 DATA_ROOT + settings 缓存刷新（teardown 恢复，不污染后续测试）。"""
    data = tmp_path / "parquet"
    data.mkdir(parents=True)
    monkeypatch.setenv("DATA_ROOT", str(data))
    from app.core.config import get_settings
    get_settings.cache_clear()
    yield data
    get_settings.cache_clear()  # 下一个 get_settings() 用恢复后的 env 重建


def _write_symbol_dataset(data: Path, dataset: str, symbols: list[str],
                          days: list[date]):
    # [2026-09-22 §8.2 第 17 项] 原先用 write_whole_symbol（写 all.snappy.parquet）：
    # 该布局**只有** rglob 型读取（cross_section 镜像）看得见，read_symbol_dataset /
    # read_symbol_year 只 glob year=*.parquet ⇒ 同一份夹具数据对生产读路径不可见。
    # 已改为生产写入器 write_year_batch，夹具从此与线上同布局。
    from app.data.parquet_store import write_year_batch

    for sym in symbols:
        rows = [{"date": d, "symbol": sym, "close": 10.0 + i, "volume": 100}
                for i, d in enumerate(days)]
        by_year: dict[int, list[dict]] = {}
        for r in rows:
            by_year.setdefault(r["date"].year, []).append(r)
        for year, yr_rows in by_year.items():
            write_year_batch(dataset, sym, year, pl.DataFrame(yr_rows))


def test_cross_section_mirror_roundtrip(data_env):
    """镜像读出 = 源按日期过滤；读取只触碰 1 个文件（截面 O(1)）。"""
    days = [date(2026, 8, 24) + timedelta(days=i) for i in range(5)]
    _write_symbol_dataset(data_env, "daily_bar", ["000001.SZ", "000002.SZ"], days)

    res = cross_section.build_mirror("daily_bar")
    assert res["built"] == 5 and res["dates"] == 5

    cs = cross_section.read_cross_section("daily_bar", days[2])
    assert cs.height == 2 and set(cs["symbol"]) == {"000001.SZ", "000002.SZ"}

    rng = cross_section.read_cross_range("daily_bar", days[1], days[3])
    assert rng.height == 6 and rng["date"].n_unique() == 3

    # 增量重跑：无新数据 → 全部跳过
    again = cross_section.build_mirror("daily_bar")
    assert again["built"] == 0 and again["skipped"] == 5

    # 源追加新日期 → 增量粒度是「源文件级」（000001.SZ 整文件 mtime 更新，
    # 该文件涉及的 6 个日期全部重建——保守正确，宁多读不漏更）
    _write_symbol_dataset(data_env, "daily_bar", ["000001.SZ"],
                          days + [days[-1] + timedelta(days=1)])
    inc = cross_section.build_mirror("daily_bar")
    assert inc["built"] == 6

    st = cross_section.mirror_status()["daily_bar"]
    assert st["mirror_dates"] == 6 and st["lag_days"] == 0


def test_mirror_rejects_unknown_dataset():
    with pytest.raises(ValueError):
        cross_section.build_mirror("predictions")


# ---------------- 公告文本管线 ----------------
def test_text_import_dedupe_and_factor(data_env):
    """导入幂等（hash 去重）+ 规则情绪打分 + 日频因子聚合。"""
    docs = [
        {"symbol": "000001", "date": "2026-08-28",
         "title": "公司中标大额订单", "content": "预计全年盈利大幅增长"},
        {"symbol": "000001.SZ", "date": "2026-08-28",
         "title": "公司中标大额订单", "content": "重复文档（同 symbol/date/title）"},
        {"symbol": "600519.SH", "date": "2026-08-28",
         "title": "被立案调查", "content": "涉嫌信息披露违规"},
        {"symbol": "000002.SZ", "date": "2026-08-28", "title": "", "content": ""},
    ]
    r1 = text_ingest.import_documents(docs)
    # doc1/doc2 同 (symbol,date,title) → 批内去重为 1 条；doc4 空标题+内容 → invalid
    assert r1["imported"] == 2 and r1["duplicates"] == 1 and r1["invalid"] == 1
    r2 = text_ingest.import_documents(docs)
    assert r2["imported"] == 0 and r2["duplicates"] == 3  # 1 批内 + 2 对库

    st = text_ingest.text_status()
    assert st["docs"] == 2 and st["doc_symbols"] == 2
    assert st["llm_enabled"] is False

    built = text_ingest.score_and_build_factor()
    assert built["ok"] and built["method"] == "rule_lexicon"
    assert built["rows"] == 2

    fac = pl.read_parquet(data_env / "text_features" / "version=sentiment_v1"
                          / "year=2026.parquet")
    row = fac.filter(pl.col("symbol") == "000001.SZ")
    assert row["sentiment"][0] > 0.5        # 利好文档 → 正分
    neg = fac.filter(pl.col("symbol") == "600519.SH")
    assert neg["sentiment"][0] < -0.5       # 利空文档 → 负分
    # surprise 列随因子一并落盘（业绩表述事件强度，独立于情绪）
    assert "surprise" in fac.columns
    st2 = text_ingest.text_status()
    assert "surprise_active_rows" in st2 and "非超预期概率" in st2["basis"]


def test_attach_text_features_t_plus_one(data_env):
    """T 日公告 T+1 起可用（allow_exact_matches=False，防未来函数）。"""
    text_ingest.import_documents([
        {"symbol": "000001.SZ", "date": "2026-08-20",
         "title": "重大利好", "content": "超预期增长"}])
    text_ingest.score_and_build_factor()

    df = pd.DataFrame({
        "symbol": ["000001.SZ"] * 3,
        "date": pd.to_datetime(["2026-08-20", "2026-08-21", "2026-08-25"]),
    })
    out = text_ingest.attach_text_features(df)
    assert np.isnan(out["sentiment"].iloc[0])   # 当日不可见
    assert out["sentiment"].iloc[1] > 0.5       # T+1 起可用
    assert out["sentiment"].iloc[2] > 0.5
    assert "surprise" in out.columns            # 与 sentiment 同口径挂接


def test_rule_surprise_direction_negation_and_isolation():
    """业绩表述事件强度：方向 / 否定词 / 与情绪相互独立。"""
    # 方向：正向超预期 > 0，负向不及预期 < 0
    assert text_ingest.rule_surprise("三季度业绩大超预期") > 0.8
    assert text_ingest.rule_surprise("全年业绩不及预期，下修盈利预测") < -0.8
    # 否定词取反："未超预期" 不是利好
    assert text_ingest.rule_surprise("业绩未超预期") < 0
    # 无业绩表述 → 中性 0
    assert text_ingest.rule_surprise("公司中标大额订单") == 0.0
    # 与 sentiment 相互独立：纯情绪词不影响 surprise，反之亦然
    assert text_ingest.rule_surprise("重大利好 涨停") == 0.0
    assert text_ingest.rule_sentiment("三季度业绩预增") > 0  # 预增在情绪词库中也有


def test_rule_sentiment_neutral_and_chunking():
    assert text_ingest.rule_sentiment("今天天气不错") == 0.0
    assert len(text_ingest.chunk_text("a" * 1200, max_chars=500, overlap=50)) == 3


# ---------------- 序列张量 + purged 切分（TFT 前置） ----------------
def _synthetic_features(n_days=120, symbols=("A", "B", "C")):
    rng = np.random.default_rng(3)
    idx = pd.date_range("2024-01-01", periods=n_days, freq="D")
    rows = []
    for sym in symbols:
        px = 10.0
        for d in idx:
            px *= 1 + rng.normal(0, 0.01)
            rows.append({"symbol": sym, "date": d, "ret_5": rng.normal(0, 0.01),
                         "vol_20": abs(rng.normal(0.02, 0.005)), "close": px})
    return pd.DataFrame(rows)


def test_build_sequences_shapes_and_nan_drop():
    feat = _synthetic_features()
    out = sequence_dataset.build_sequences(feat, lookback=10, horizon=5)
    assert out["X"].shape[0] == out["y"].shape[0] == len(out["dates"])
    assert out["X"].shape[1] == 10 and out["X"].shape[2] == 2
    assert out["X"].dtype == np.float32
    assert np.isfinite(out["X"]).all() and np.isfinite(out["y"]).all()
    # 样本数 = 每标的 (n_days - lookback + 1 - horizon)
    assert out["X"].shape[0] == 3 * (120 - 10 + 1 - 5)


def test_purged_time_split_gap_discipline():
    dates = np.array(pd.date_range("2024-01-01", periods=100, freq="D"))
    sp = sequence_dataset.purged_time_split(dates, holdout_days=30, gap_days=5)
    train_last = dates[sp["train"]].max()
    valid_first = dates[sp["valid"]].min()
    # gap：train 末与 valid 起点之间至少隔 gap_days 个交易日
    order = {d: i for i, d in enumerate(np.unique(dates))}
    assert order[valid_first] - order[train_last] >= 5


# ---------------- 图邻接与传导因子（GNN 前置） ----------------
def test_graph_propagation(data_env):
    """显式边 → 邻居聚合因子 = 邻接行归一化后的邻居均值；无邻居保持 NaN。"""
    edges = pl.DataFrame({"src": ["A", "B"], "dst": ["B", "C"],
                          "relation": ["supply", "supply"],
                          "weight": [1.0, 1.0]})
    rel_dir = data_env / "relations"
    rel_dir.mkdir(parents=True, exist_ok=True)
    edges.write_parquet(rel_dir / "edges.parquet")

    d = pd.Timestamp("2026-08-28")
    feat = pd.DataFrame({
        "symbol": ["A", "B", "C", "D"], "date": [d, d, d, d],
        "ret_5": [1.0, 2.0, np.nan, 5.0]})
    idx, A = graph.build_adjacency(["A", "B", "C", "D"])
    # A/B 互连、B/C 互连 → A 行邻居={B}, B 行邻居={A,C}, C 行邻居={B}；D 孤立
    assert abs(A[idx["A"], idx["B"]] - 1.0) < 1e-9

    out = graph.propagate(feat, A, idx, ["ret_5"], hops=1)
    g = out["g1_ret_5"]
    assert abs(g.iloc[0] - 2.0) < 1e-9        # A 的邻居 B=2.0
    assert abs(g.iloc[1] - 1.0) < 1e-9        # B 的邻居均值（A=1.0；C 缺失不计入分母）
    assert abs(g.iloc[2] - 2.0) < 1e-9        # C 的邻居 B=2.0
    assert np.isnan(g.iloc[3])                # D 孤立且自身不聚合 → NaN（不造数）

    st = graph.relations_status()
    assert st["explicit_edges"] == 2


def test_propagate_without_edges_adds_nan_columns(data_env):
    """无关系边也必须补出 g{h}_<col> NaN 列——此前静默返回原表，下游按列名
    取值时在 KeyError 与正常之间摇摆（"透明降级"名不副实）。"""
    A = np.zeros((2, 2))
    feat = pd.DataFrame({"symbol": ["A", "B"],
                         "date": pd.to_datetime(["2026-08-28"] * 2),
                         "ret_5": [1.0, 2.0]})
    out = graph.propagate(feat, A, {"A": 0, "B": 1}, ["ret_5"], hops=1)
    assert "g1_ret_5" in out.columns
    assert out["g1_ret_5"].isna().all()          # 无覆盖表达为 NaN，不造数


def test_propagate_missing_neighbor_not_diluted():
    """缺失邻居不进分母；单值邻居聚合结果 = 该值本身。"""
    A = np.array([[0.0, 1.0], [1.0, 0.0]])       # 行归一化后 A==原样
    feat = pd.DataFrame({"symbol": ["A", "B"],
                         "date": pd.to_datetime(["2026-08-28"] * 2),
                         "v": [np.nan, 7.0]})
    out = graph.propagate(feat, A, {"A": 0, "B": 1}, ["v"], hops=1)
    assert not np.isnan(out["g1_v"].iloc[0])      # np.isnan 返回 np.bool_，勿用 is False
    assert abs(out["g1_v"].iloc[0] - 7.0) < 1e-9  # A 的唯一有值邻居是 B
    assert np.isnan(out["g1_v"].iloc[1])          # B 的邻居 A 无值 → NaN


def test_industry_edges_skip_dirty_bucket(data_env, monkeypatch):
    """超大行业桶（疑似脏分类）不生成同业全连接边，防 O(n²) 边数爆炸。"""
    class FakeSession:
        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

        def execute(self, q):
            class R:
                def all(self):
                    return [(f"{600000 + i:06d}.SH", "-") for i in range(200)]
            return R()

    import app.trading.paper as paper
    monkeypatch.setattr(paper, "sync_session_factory", lambda: FakeSession)
    assert graph.industry_edges().height == 0


def test_propagate_matches_reference_implementation():
    """分块矩阵乘版与逐日循环参考实现数值一致（含缺失值/孤立节点）。"""
    rng = np.random.default_rng(2)
    syms = [f"S{i}" for i in range(10)]
    rows = [{"symbol": s, "date": d, "f1": rng.normal()}
            for d in pd.date_range("2026-01-01", periods=6)
            for s in syms]
    feat = pd.DataFrame(rows)
    feat.loc[feat.index[::11], "f1"] = np.nan

    A = np.zeros((10, 10))
    for i, j in [(0, 1), (1, 2), (3, 4), (0, 3)]:
        A[i, j] = A[j, i] = 1.0
    rs = A.sum(1, keepdims=True)
    rs[rs == 0] = 1.0
    A = A / rs
    idx = {s: i for i, s in enumerate(syms)}

    An = A
    ref = np.full(len(feat), np.nan)
    for d in pd.to_datetime(feat["date"]).unique():
        m = pd.to_datetime(feat["date"]) == d
        x = np.full(10, np.nan)
        for sym, v in zip(feat.loc[m, "symbol"], feat.loc[m, "f1"]):
            if sym in idx and np.isfinite(v):
                x[idx[sym]] = v
        den = An @ np.isfinite(x).astype(float)
        agg = np.where(den > 1e-12, An @ np.nan_to_num(x)
                       / np.where(den > 1e-12, den, 1.0), np.nan)
        ref[m.to_numpy()] = [agg[idx.get(s, -1)] if s in idx else np.nan
                             for s in feat.loc[m, "symbol"]]

    out = graph.propagate(feat, A, idx, ["f1"], hops=1)
    np.testing.assert_allclose(out["g1_f1"].to_numpy(), ref, equal_nan=True)


# ---------------- 脚本依赖健全性（train_tft / train_gnn） ----------------
@pytest.mark.parametrize("script", ["train_tft.py", "train_gnn.py"])
def test_cli_script_imports_resolvable(script):
    """脚本里每个 ``from X import Y`` 都必须真实可解析。

    train_gnn 曾在训练完成后才因 ``from torch_models import predict_gnn``
    触发 ImportError（该函数在 gnn_models）——40 轮训练白跑、候选未登记。
    这类错误只有在 torch 可用且真跑 CLI 时才会暴露，用静态+导入双检提前拦。
    """
    tree = ast.parse((BACKEND_ROOT / "scripts" / script).read_text(encoding="utf-8"))
    for node in ast.walk(tree):
        if not isinstance(node, ast.ImportFrom) or not node.module:
            continue
        m = importlib.import_module(node.module)
        for a in node.names:
            assert hasattr(m, a.name), \
                f"{script}: {node.module}.{a.name} 不存在（导入自错误模块？）"


# ---------------- torch 依赖用例（未装则 skip） ----------------
def test_gnn_train_nan_features_do_not_poison_weights(data_env):
    """特征含 NaN 必须被显式处理：任一 NaN 进入 A·X·W 会让权重全变 NaN，
    训练日志看似正常、模型实际是废的——曾静默发生且无告警。"""
    pytest.importorskip("torch")
    from app.ml.gnn_models import train_gnn
    from app.ml.torch_models import torch_ready

    if not torch_ready():
        pytest.skip("torch 未安装")
    D, N, F = 6, 4, 3
    rng = np.random.default_rng(0)
    X = rng.normal(size=(D, N, F)).astype(np.float32)
    X[:, 0, 0] = np.nan                       # 特征列含 NaN
    y = rng.normal(size=(D, N)).astype(np.float32)
    A = np.full((N, N), 0.25, dtype=np.float64)
    model, hist = train_gnn(X, y, A, epochs=2)
    for p in model.parameters():
        assert np.isfinite(p.detach().numpy()).all()
    assert np.isfinite(hist[0]["mse"])


def test_sequence_model_save_load_roundtrip(data_env):
    """存盘必须连同重建所需的结构参数——只存裸 state_dict 无法重建模型类。"""
    pytest.importorskip("torch")
    from app.ml.torch_models import (build_model, predict_sequence, torch_ready)

    if not torch_ready():
        pytest.skip("torch 未安装")
    from app.ml.torch_models import load_sequence_model, save_sequence_model

    X = np.random.default_rng(0).normal(size=(8, 5, 3)).astype(np.float32)
    model = build_model(n_features=3, lookback=5)
    p = data_env / "m.pt"
    save_sequence_model(model, p)
    re = load_sequence_model(p)
    np.testing.assert_allclose(
        predict_sequence(model, X), predict_sequence(re, X), rtol=1e-5)
