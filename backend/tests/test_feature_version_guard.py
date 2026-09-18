"""版本化 features 读取的 P0 防污染回归测试。"""
from __future__ import annotations

from datetime import date
from types import SimpleNamespace

import polars as pl
import pytest

from app.core.errors import AQPException
from app.data import features as feature_data


def _write_feature(root, version: str, rows: dict[str, list[object]]) -> None:
    """写入测试用年度 features 分区。"""
    directory = root / "features" / f"version={version}"
    directory.mkdir(parents=True, exist_ok=True)
    pl.DataFrame(rows).write_parquet(directory / "year=2026.parquet")


def test_auto_selects_one_latest_version_without_cross_version_concat(monkeypatch, tmp_path):
    """自动版本选择只能读取一套分区，不能把 v1/v2 合并。"""
    _write_feature(tmp_path, "v1", {
        "date": [date(2026, 1, 2)], "symbol": ["000001.SZ"], "factor": [1.0],
    })
    _write_feature(tmp_path, "v2", {
        "date": [date(2026, 1, 2)], "symbol": ["000001.SZ"], "factor": [2.0],
    })
    v2_file = tmp_path / "features" / "version=v2" / "year=2026.parquet"
    v2_file.touch()
    monkeypatch.setattr(
        feature_data,
        "get_settings",
        lambda: SimpleNamespace(DATA_ROOT=tmp_path, FEATURE_VERSION=""),
    )

    version, frame = feature_data.read_feature_frame()

    assert version == "v2"
    assert frame.height == 1
    assert frame["factor"].to_list() == [2.0]


def test_duplicate_feature_keys_fail_fast_with_version_and_count(monkeypatch, tmp_path):
    """同一版本内重复 (date, symbol) 也必须拒绝而非由 pivot 静默吞掉。"""
    _write_feature(tmp_path, "v2", {
        "date": [date(2026, 1, 2), date(2026, 1, 2)],
        "symbol": ["000001.SZ", "000001.SZ"],
        "factor": [1.0, 2.0],
    })
    monkeypatch.setattr(
        feature_data,
        "get_settings",
        lambda: SimpleNamespace(DATA_ROOT=tmp_path, FEATURE_VERSION="v2"),
    )

    with pytest.raises(AQPException, match=r"版本 v2.*1 个重复"):
        feature_data.read_feature_frame()
