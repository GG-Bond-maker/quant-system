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

from app.data.parquet_store import write_year_batch  # noqa: E402

SYM = "600099.SH"
D0 = date(2023, 1, 2)  # 300 个交易日起点


def _seed_bars(days: list[date]) -> None:
    """合成 hfq 日线（几何随机游走 + 正弦动量，保证因子非退化）。"""
    rng = np.random.default_rng(11)
    n = len(days)
    rets = 0.02 * np.sin(np.arange(n) / 7) + rng.normal(0, 0.012, n)
    close = 20.0 * np.exp(np.cumsum(rets))
    df = pd.DataFrame({
        "symbol": SYM, "code": "600099", "date": days,
        "open": close * 0.995, "high": close * 1.01, "low": close * 0.99,
        "close": close, "volume": rng.uniform(5e5, 2e6, n),
        "amount": close * rng.uniform(5e5, 2e6, n),
    })
    pdf = pl.from_pandas(df)
    for year in sorted({d.year for d in days}):
        write_year_batch("daily_bar_hfq", SYM, year,
                         pdf.filter(pl.col("date").dt.year() == year))


def _biz_days(n: int) -> list[date]:
    return [d.date() for d in pd.bdate_range(D0, periods=n)]


def _run_step() -> None:
    from app.orchestrator import STEP_FUNCTIONS

    STEP_FUNCTIONS["build_features"](date(2024, 5, 1), ["600099"])


def _load_features() -> pd.DataFrame:
    from app.core.config import get_settings
    from app.ml.features import FEATURE_VERSION

    out_root = get_settings().DATA_ROOT / "features" / f"version={FEATURE_VERSION}"
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
