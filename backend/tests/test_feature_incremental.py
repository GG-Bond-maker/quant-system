"""Task 11（整改计划 A-P1-5b）：特征管线增量构建。

背景：step_build_features docstring 称"增量"，实为每日全历史读+算+写
（5000 只 × 多年不可扩展）。
方案：hfq 因子具备 asof 稳定性（test_feature_asof 已守卫）——已落库特征
保留，仅对"最新日期 - 400 交易日预热窗"之后的原始数据重算，合并原子写。
正确性由本测试的**等值守卫**兜底：两段式投喂的增量结果必须与一次性全量
重算逐值一致，不一致即抛错并回退全量。
"""
from __future__ import annotations

import sys
from datetime import date
from pathlib import Path

BACKEND_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(BACKEND_ROOT))

import numpy as np  # noqa: E402
import pandas as pd  # noqa: E402
import polars as pl  # noqa: E402
import pytest  # noqa: E402

from app.data.parquet_store import write_year_batch  # noqa: E402
from app.ml.features import FEATURE_VERSION  # noqa: E402

@pytest.fixture(autouse=True)
def _isolated_feature_policy(tmp_path, monkeypatch):
    from app.core.config import get_settings
    monkeypatch.setattr(get_settings(), "FEATURE_ALLOW_GRAPH_DRIFT", False)
    monkeypatch.setattr(get_settings(), "DATA_ROOT", tmp_path / "feature-data")


SYM = "600099.SH"
SYM2 = "600088.SH"  # 缺陷 B 前沿日补齐用例的第二只标的
SYM3 = "600077.SH"  # 边界加固 stale 用例：新增标的（补位使前沿日行数不降）
D0 = date(2023, 1, 2)  # 300 个交易日起点


def _seed_bars(days: list[date]) -> None:
    """合成 hfq 日线（几何随机游走 + 正弦动量，保证因子非退化）。"""
    _seed_bars_symbol(SYM, days, seed=11)


def _seed_bars_symbol(sym: str, days: list[date], seed: int) -> None:
    """同 :func:`_seed_bars`，但可指定 symbol 与随机种子。"""
    rng = np.random.default_rng(seed)
    n = len(days)
    rets = 0.02 * np.sin(np.arange(n) / 7) + rng.normal(0, 0.012, n)
    close = 20.0 * np.exp(np.cumsum(rets))
    df = pd.DataFrame({
        "symbol": sym, "code": sym.split(".")[0], "date": days,
        "open": close * 0.995, "high": close * 1.01, "low": close * 0.99,
        "close": close, "volume": rng.uniform(5e5, 2e6, n),
        "amount": close * rng.uniform(5e5, 2e6, n),
    })
    pdf = pl.from_pandas(df)
    for year in sorted({d.year for d in days}):
        write_year_batch("daily_bar_hfq", sym, year,
                         pdf.filter(pl.col("date").dt.year() == year))


def _biz_days(n: int) -> list[date]:
    return [d.date() for d in pd.bdate_range(D0, periods=n)]


def _run_step() -> str:
    from app.orchestrator import STEP_FUNCTIONS

    return STEP_FUNCTIONS["build_features"](date(2024, 5, 1), ["600099"])


def _load_features(version: str = FEATURE_VERSION) -> pd.DataFrame:
    from app.core.config import get_settings

    out_root = get_settings().DATA_ROOT / "features" / f"version={version}"
    parts = sorted(out_root.glob("year=*.parquet"))
    assert parts, "特征分区不存在"
    df = pl.concat([pl.read_parquet(p) for p in parts], how="diagonal_relaxed")
    return df.to_pandas().sort_values(["symbol", "date"]).reset_index(drop=True)


def _assert_equal(a: pd.DataFrame, b: pd.DataFrame, upto: date | None = None) -> None:
    if upto is not None:
        a = a[a["date"] <= np.datetime64(upto)]
        b = b[b["date"] <= np.datetime64(upto)]
    assert len(a) == len(b), f"行数不一致 {len(a)} != {len(b)}"
    cols = [c for c in a.columns if c not in ("symbol", "date")]
    va = a[cols].to_numpy(dtype="float64")
    vb = b[cols].to_numpy(dtype="float64")
    assert np.allclose(va, vb, rtol=1e-9, atol=1e-12, equal_nan=True), \
        "增量结果与全量重算逐值不一致"


def test_incremental_equals_full_rebuild(tmp_path, monkeypatch):
    """两段式（先 200 日建库 → 再补 300 日增量跑）与一次性全量重算逐值一致。"""
    # ---- 一次性全量（基准）----
    _seed_bars(_biz_days(300))
    monkeypatch.setattr("app.data.parquet_store.read_all_symbols",
                        lambda ds: [SYM] if ds == "daily_bar_hfq" else [])
    monkeypatch.setenv("FEATURE_INCREMENTAL", "0")  # 强制全量
    _run_step()
    full = _load_features()

    # ---- 增量：先 200 日全量建库，再补齐 300 日数据增量跑 ----
    from app.core.config import get_settings as gs
    import shutil

    feat_dir = gs().DATA_ROOT / "features"
    shutil.rmtree(feat_dir, ignore_errors=True)
    # 同时清空行情，避免 300 日种子的 2024 年分区污染"200 日基线"状态
    shutil.rmtree(gs().DATA_ROOT / "daily_bar_hfq", ignore_errors=True)
    _seed_bars(_biz_days(200))
    monkeypatch.setenv("FEATURE_INCREMENTAL", "0")
    _run_step()
    seeded = _load_features()
    assert seeded["date"].max() == np.datetime64(_biz_days(200)[-1])

    _seed_bars(_biz_days(300))  # 数据补齐（write_year_batch 整年覆盖语义）
    monkeypatch.setenv("FEATURE_INCREMENTAL", "1")
    _run_step()
    inc = _load_features()

    # 已落库段必须原样保留；新增段与全量重算逐值一致
    boundary = _biz_days(200)[-1]
    old_part_full = full[full["date"] <= np.datetime64(boundary)]
    old_part_inc = inc[inc["date"] <= np.datetime64(boundary)]
    _assert_equal(old_part_inc, old_part_full)
    _assert_equal(inc[inc["date"] > np.datetime64(boundary)],
                  full[full["date"] > np.datetime64(boundary)])
    assert inc["date"].max() == full["date"].max()


def test_incremental_fills_partially_written_frontier_day(tmp_path, monkeypatch):
    """缺陷 B（P0）：前沿日（d_last）被部分写入后，增量重跑必须补齐该日行。

    复现：先仅投喂标的 A 的完整历史并全量建特征 —— 前沿日只有 A 一行；随后补入
    标的 B「仅在前沿日」的行情，再跑增量。旧实现用 ``date > d_last`` 严格过滤，
    前沿日新增的 symbol 永远进不去（该日永久停在 1 行）；修复后前沿日按
    (date, symbol) 补齐式合并，行数增至 2，且 ``date < d_last`` 段逐值不变。

    注：B 只在前沿日有行情，故不会触及历史守卫段（``guard_start <= date < d_last``）
    —— 这正好把「前沿日增行」与「历史段一致性」两件事解耦。
    """
    from app.core.config import get_settings as gs
    import shutil

    # 隔离：清空共享临时 DATA_ROOT 下的 features / 行情，避免跨用例污染
    shutil.rmtree(gs().DATA_ROOT / "features", ignore_errors=True)
    shutil.rmtree(gs().DATA_ROOT / "daily_bar_hfq", ignore_errors=True)

    days = _biz_days(260)
    frontier = days[-1]

    # 1) 仅 A：全量建库 → 前沿日仅 1 行
    _seed_bars_symbol(SYM, days, seed=11)
    monkeypatch.setattr("app.data.parquet_store.read_all_symbols",
                        lambda ds: [SYM] if ds == "daily_bar_hfq" else [])
    monkeypatch.setenv("FEATURE_INCREMENTAL", "0")
    _run_step()
    base = _load_features()
    assert base["date"].max() == np.datetime64(frontier)
    assert int((base["date"] == np.datetime64(frontier)).sum()) == 1

    # 2) 补入 B（仅前沿日有行情）→ 增量重跑
    _seed_bars_symbol(SYM2, [frontier], seed=7)
    monkeypatch.setattr("app.data.parquet_store.read_all_symbols",
                        lambda ds: [SYM, SYM2] if ds == "daily_bar_hfq" else [])
    monkeypatch.setenv("FEATURE_INCREMENTAL", "1")
    detail = _run_step()
    inc = _load_features()

    # 必须走增量合并（而非回退全量）：旧实现在此会把前沿日新 symbol 丢弃
    assert "incremental" in detail and "filled=" in detail, detail
    # 前沿日被补齐到 2 行
    assert int((inc["date"] == np.datetime64(frontier)).sum()) == 2, "前沿日未补齐"
    # date < d_last 段逐值不变（历史 asof 稳定）
    _assert_equal(inc[inc["date"] < np.datetime64(frontier)],
                  base[base["date"] < np.datetime64(frontier)])


def test_incremental_preserves_stale_frontier_rows(tmp_path, monkeypatch):
    """边界加固：前沿日既有 symbol 本次未重算（该日 raw 缺失）不得丢行。

    复现：先对 {A, B} 全量建库 → 前沿日 2 行；随后 A 的前沿日 raw 缺失、同时
    新增 C，再跑增量。此时 frontier_new={B,C}（2 行）与
    frontier_old={A,B}（2 行）**行数相等** —— 长度守卫放行，但「head_old 截断 +
    tail_new」写法会把 A 的前沿日旧行**静默删除**（且因同时新增 C，+1 −1 净变化
    看不出来）。修复后按 (date, symbol) 严格并集合并：前沿日保留 A 旧行并新增 C
    → 3 行，mode 中 stale=1 / filled=1。

    同时守卫「A 的历史段（date < d_last）必须原样保留」——若实现改成按 symbol
    过滤整表，A 的全部历史特征会被整体抹掉（比丢 1 行严重得多）。
    """
    from app.core.config import get_settings as gs
    import shutil

    shutil.rmtree(gs().DATA_ROOT / "features", ignore_errors=True)
    shutil.rmtree(gs().DATA_ROOT / "daily_bar_hfq", ignore_errors=True)

    days = _biz_days(260)
    frontier = days[-1]

    # 1) {A, B} 全量建库 → 前沿日 2 行
    from app.data import parquet_store

    _seed_bars_symbol(SYM, days, seed=11)
    _seed_bars_symbol(SYM2, days, seed=7)
    monkeypatch.setattr("app.data.parquet_store.read_all_symbols",
                        lambda ds: [SYM, SYM2] if ds == "daily_bar_hfq" else [])
    monkeypatch.setenv("FEATURE_INCREMENTAL", "0")
    _run_step()
    base = _load_features()
    assert int((base["date"] == np.datetime64(frontier)).sum()) == 2
    assert ((base["symbol"] == SYM) & (base["date"] < np.datetime64(frontier))).sum() > 0

    # 2) A 的前沿日 raw 缺失，新增 C → 增量重跑。
    #    A 仍在 read_all_symbols / 冻结 universe 中，因此不是图漂移，专门验证
    #    前沿日 stale 行的并集保留语义。
    _seed_bars_symbol(SYM3, days, seed=23)
    monkeypatch.setattr("app.data.parquet_store.read_all_symbols",
                        lambda ds: [SYM, SYM2, SYM3] if ds == "daily_bar_hfq" else [])
    real_read_symbol_dataset = parquet_store.read_symbol_dataset

    def _read_with_missing_frontier(dataset: str, symbol: str):
        frame = real_read_symbol_dataset(dataset, symbol)
        if dataset == "daily_bar_hfq" and symbol == SYM:
            return frame.filter(pl.col("date") < frontier)
        return frame

    monkeypatch.setattr(
        "app.data.parquet_store.read_symbol_dataset",
        _read_with_missing_frontier,
    )
    monkeypatch.setenv("FEATURE_INCREMENTAL", "1")
    detail = _run_step()
    inc = _load_features()

    # 必须走增量合并（而非异常回退全量），且如实披露 stale / filled
    assert "incremental" in detail, detail
    assert "stale=1" in detail and "filled=1" in detail, detail

    # 前沿日：A 旧行保留 + C 新增 = 3 行（截断式写法会退化为 2 行）
    assert int((inc["date"] == np.datetime64(frontier)).sum()) == 3, "前沿日旧行被丢弃"
    # A 的前沿日旧行仍在（未被静默删行）
    n_a_frontier = int(((inc["symbol"] == SYM)
                        & (inc["date"] == np.datetime64(frontier))).sum())
    assert n_a_frontier == 1

    # A 的历史段逐值原样保留（未被按 symbol 过滤整体抹除）
    a_inc = inc[(inc["symbol"] == SYM) & (inc["date"] < np.datetime64(frontier))]
    a_base = base[(base["symbol"] == SYM) & (base["date"] < np.datetime64(frontier))]
    assert len(a_inc) == len(a_base) > 0
    _assert_equal(a_inc.reset_index(drop=True), a_base.reset_index(drop=True))
    # 历史段整体（A + B）逐值不变
    _assert_equal(inc[inc["date"] < np.datetime64(frontier)],
                  base[base["date"] < np.datetime64(frontier)])


def test_feature_version_resolution_prefers_production_for_inference(monkeypatch):
    """构建可用显式版本预演，推理必须服从生产模型登记的特征版本。"""
    from app import orchestrator
    from app.core.config import get_settings
    from app.ml.features import FEATURE_VERSION, LEGACY_FEATURE_VERSION

    monkeypatch.setattr(get_settings(), "FEATURE_VERSION", FEATURE_VERSION)
    monkeypatch.setenv("FEATURE_VERSION", FEATURE_VERSION)
    monkeypatch.setattr(
        "app.ml.registry.get_production",
        lambda model_name="lgbm_v1": {
            "feature_version": LEGACY_FEATURE_VERSION,
        },
    )

    assert orchestrator._resolve_pipeline_feature_version(where="test_build") == FEATURE_VERSION
    assert orchestrator._resolve_pipeline_feature_version(
        where="test_infer",
        prefer_production=True,
    ) == LEGACY_FEATURE_VERSION


def test_step_infer_reads_production_version_and_refuses_fallback(
    tmp_path,
    monkeypatch,
):
    """生产模型要求 v1 时不得因 FEATURE_VERSION=v3g 而读取 v3g 特征。"""
    from app import orchestrator
    from app.core.config import get_settings
    from app.ml.features import FEATURE_VERSION, LEGACY_FEATURE_VERSION

    data_root = tmp_path / "feature-version-data"
    trade_date = date(2024, 6, 5)
    for version, value in (
        (LEGACY_FEATURE_VERSION, 1.0),
        (FEATURE_VERSION, 2.0),
    ):
        version_dir = data_root / "features" / f"version={version}"
        version_dir.mkdir(parents=True)
        pd.DataFrame({
            "symbol": [SYM],
            "date": [pd.Timestamp(trade_date)],
            "f0": [value],
        }).to_parquet(version_dir / f"year={trade_date.year}.parquet", index=False)

    monkeypatch.setattr(get_settings(), "DATA_ROOT", data_root)
    monkeypatch.setattr(get_settings(), "FEATURE_VERSION", FEATURE_VERSION)
    monkeypatch.setenv("FEATURE_VERSION", FEATURE_VERSION)
    monkeypatch.setattr(
        "app.ml.registry.get_production",
        lambda model_name="lgbm_v1": {
            "feature_version": LEGACY_FEATURE_VERSION,
        },
    )
    monkeypatch.setattr(
        "app.ml.infer.load_prod_model",
        lambda model_name="lgbm_v1": (object(), ["f0"], Path("lgbm_v1_test")),
    )
    captured: dict[str, float] = {}

    def _predict(day_df, booster, features, model_dir):
        del booster, features, model_dir
        captured["f0"] = float(day_df.iloc[0]["f0"])
        return day_df[["symbol", "date"]].copy()

    monkeypatch.setattr("app.ml.signal_contract.predict_panel", _predict)
    monkeypatch.setattr(
        "app.ml.signal_contract.write_predictions",
        lambda out, root: None,
    )

    detail = orchestrator.step_infer(trade_date, [SYM])

    assert "model=lgbm_v1_test" in detail
    assert captured["f0"] == 1.0, "推理读取了错误版本的特征目录"

    import shutil

    shutil.rmtree(
        data_root / "features" / f"version={LEGACY_FEATURE_VERSION}",
    )
    with pytest.raises(ValueError, match="拒绝回退"):
        orchestrator.step_infer(trade_date, [SYM])


def test_legacy_v1_build_uses_legacy_space_and_skips_graph(tmp_path, monkeypatch):
    """生产回滚到 v1 时不得用 v3g 的 45+ 列空间冒充 v1。"""
    from app.core.config import get_settings as gs
    from app.data.parquet_store import read_symbol_dataset
    from app.ml.features import LEGACY_FEATURE_VERSION, build_factors

    import shutil

    shutil.rmtree(gs().DATA_ROOT / "features", ignore_errors=True)
    shutil.rmtree(gs().DATA_ROOT / "daily_bar_hfq", ignore_errors=True)
    _seed_bars(_biz_days(300))
    monkeypatch.setattr(
        "app.data.parquet_store.read_all_symbols",
        lambda dataset: [SYM] if dataset == "daily_bar_hfq" else [],
    )
    monkeypatch.setattr(gs(), "FEATURE_VERSION", "")
    monkeypatch.delenv("FEATURE_VERSION", raising=False)
    monkeypatch.setenv("FEATURE_INCREMENTAL", "0")
    monkeypatch.setattr(
        "app.ml.registry.get_production",
        lambda model_name="lgbm_v1": {"feature_version": LEGACY_FEATURE_VERSION},
    )

    detail = _run_step()

    frame = _load_features(LEGACY_FEATURE_VERSION)
    expected = build_factors(read_symbol_dataset("daily_bar_hfq", SYM).to_pandas())
    assert list(frame.columns) == list(expected.columns)
    assert not any(column.startswith("g1_") for column in frame.columns)
    version_dir = gs().DATA_ROOT / "features" / f"version={LEGACY_FEATURE_VERSION}"
    assert not (version_dir / "universe_snapshot.json").exists()
    assert not (version_dir / "graph_lineage.json").exists()
    assert "full(cols=" in detail
