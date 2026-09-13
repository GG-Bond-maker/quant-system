"""Task 2（整改计划）：回测信号按 model_version 隔离。

背景：predictions 目录曾混存 4 代模型信号（含一次评分口径突变：普通回归
→ 横截面去均值），/run 全量加载不按版本过滤，跨代分数被引擎全局排序
混排（docs/audit/2026-09-05-量化专项审查报告.md P0-2）。
"""
from __future__ import annotations

import sys
from datetime import date
from pathlib import Path

BACKEND_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(BACKEND_ROOT))

import polars as pl  # noqa: E402
import pytest  # noqa: E402

from app.core.errors import AQPException  # noqa: E402

V_OLD = "lgbm_v1_20260101_aaa"
V_NEW = "lgbm_v1_20260901_bbb"  # 时间戳命名 -> 字典序 = 时间序


class _FakeSettings:
    def __init__(self, root: Path) -> None:
        self.DATA_ROOT = root


@pytest.fixture()
def seeded_preds(tmp_path: Path) -> Path:
    """同一日期两个版本不同分数（跨版本不可比），universe 种子最小化。"""
    uni_dir = tmp_path / "universe_daily_bt" / "symbol=__all__"
    uni_dir.mkdir(parents=True, exist_ok=True)
    pl.DataFrame({
        "date": [date(2025, 8, 8), date(2025, 8, 11)],
        "symbol": ["600001.SH", "600001.SH"],
    }).write_parquet(uni_dir / "year=2025.parquet")

    pred_dir = tmp_path / "predictions"
    pred_dir.mkdir(parents=True, exist_ok=True)
    pl.DataFrame({
        "date": [date(2025, 8, 8)] * 2,
        "symbol": ["600001.SH", "600001.SH"],
        "pred_score": [0.1, 0.9],
        "model_version": [V_OLD, V_NEW],
    }).write_parquet(pred_dir / "date=20250808.parquet")
    pl.DataFrame({
        "date": [date(2025, 8, 11)],
        "symbol": ["600001.SH"],
        "pred_score": [0.5],
        "model_version": [V_OLD],
    }).write_parquet(pred_dir / "date=20250811.parquet")
    return tmp_path


def _load(root: Path, start: str, end: str, version: str):
    from app.api.v1.backtest import _load_universe_and_signals

    import app.api.v1.backtest as bt_mod

    orig = bt_mod.get_settings
    bt_mod.get_settings = lambda: _FakeSettings(root)
    try:
        return _load_universe_and_signals(start, end, version)
    finally:
        bt_mod.get_settings = orig


def test_filter_by_model_version(seeded_preds):
    uni, sig, used = _load(seeded_preds, "2025-08-01", "2025-08-30", V_OLD)
    assert sig["model_version"].unique().to_list() == [V_OLD]
    assert sig["pred_score"].to_list() == [0.1, 0.5]  # V_NEW 的 0.9 被排除
    assert used == [V_OLD]


def test_latest_picks_dominant_coverage(seeded_preds):
    """latest = 窗口内信号日覆盖最广的版本（V_OLD 2 天 > V_NEW 1 天）。"""
    _, sig, used = _load(seeded_preds, "2025-08-01", "2025-08-30", "latest")
    assert used == [V_OLD]
    assert sig["pred_score"].to_list() == [0.1, 0.5]  # V_NEW 的 0.9 被排除


def test_latest_tie_breaks_to_max_version(tmp_path):
    """覆盖日并列时取字典序最大（时间戳命名下即最新）。"""
    pred_dir = tmp_path / "predictions"
    pred_dir.mkdir(parents=True, exist_ok=True)
    uni_dir = tmp_path / "universe_daily_bt" / "symbol=__all__"
    uni_dir.mkdir(parents=True, exist_ok=True)
    pl.DataFrame({"date": [date(2025, 8, 8)], "symbol": ["600001.SH"]}
                 ).write_parquet(uni_dir / "year=2025.parquet")
    for v in (V_OLD, V_NEW):
        pl.DataFrame({
            "date": [date(2025, 8, 8)], "symbol": ["600001.SH"],
            "pred_score": [0.1 if v == V_OLD else 0.9], "model_version": [v],
        }).write_parquet(pred_dir / f"date=20250808-{v}.parquet")
    _, sig, used = _load(tmp_path, "2025-08-01", "2025-08-30", "latest")
    assert used == [V_NEW]


def test_unknown_version_raises(seeded_preds):
    with pytest.raises(AQPException):
        _load(seeded_preds, "2025-08-01", "2025-08-30", "lgbm_v1_no_such")
