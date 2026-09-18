"""回归守卫：monitor 特征版本单版本读取（防 v1/v2g 混算）。

覆盖：
- ``_signature('features')`` 只统计**当前有效单一版本**的 parquet；
- ``_features_frame`` 只读取该版本的行（不把两个版本 concat 混算）；
- 显式 ``FEATURE_VERSION`` 覆盖自动选择。
"""
from __future__ import annotations

from datetime import date
from types import SimpleNamespace

import polars as pl
import pytest

from app.data import features as feature_data
from app.ml import monitor


def _write_feature(root, version: str, rows: dict[str, list[object]]) -> None:
    directory = root / "features" / f"version={version}"
    directory.mkdir(parents=True, exist_ok=True)
    pl.DataFrame(rows).write_parquet(directory / "year=2024.parquet")


@pytest.fixture(autouse=True)
def _clear_feature_cache():
    monitor._FEATURES_CACHE.clear()
    yield
    monitor._FEATURES_CACHE.clear()


def test_signature_scopes_to_single_latest_version(monkeypatch, tmp_path):
    """两个版本并存时，签名与读取都只落在写入更新的那一个版本。"""
    _write_feature(tmp_path, "alpha_basic_v1",
                   {"date": [date(2024, 1, 2)], "symbol": ["A.SH"], "f": [1.0]})
    _write_feature(tmp_path, "alpha_basic_v2g",
                   {"date": [date(2024, 1, 3)], "symbol": ["B.SH"], "f": [2.0]})
    monkeypatch.setattr(
        monitor, "get_settings",
        lambda: SimpleNamespace(DATA_ROOT=tmp_path, FEATURE_VERSION=""))

    sig = monitor._signature("features")
    assert sig is not None, "features 签名不应为 None"
    files = sig[1]
    assert len(files) == 1, f"应只统计单一版本文件，实际 {len(files)} 个: {files}"
    assert all("version=alpha_basic_v2g" in str(f) for f in files), files

    df = monitor._features_frame(days=10)
    assert df["f"].to_list() == [2.0], "只应读到 v2g 的行，不得混入 v1"
    assert df["symbol"].to_list() == ["B.SH"]


def test_explicit_feature_version_overrides_auto(monkeypatch, tmp_path):
    """显式 FEATURE_VERSION 时固定读取该版本，即便它不是最新。"""
    _write_feature(tmp_path, "alpha_basic_v1",
                   {"date": [date(2024, 1, 2)], "symbol": ["A.SH"], "f": [1.0]})
    _write_feature(tmp_path, "alpha_basic_v2g",
                   {"date": [date(2024, 1, 3)], "symbol": ["B.SH"], "f": [2.0]})
    monkeypatch.setattr(
        monitor, "get_settings",
        lambda: SimpleNamespace(DATA_ROOT=tmp_path, FEATURE_VERSION="alpha_basic_v1"))

    df = monitor._features_frame(days=10)
    assert df["f"].to_list() == [1.0]
    assert df["symbol"].to_list() == ["A.SH"]


# ---------------- 防漂移：monitor 与 data.features 版本解析一致性（parity） ----------------
# monitor._resolve_feature_dir 就地复刻了 data.features.resolve_feature_version 的
# 单版本解析语义（因后者 settings 源不可注入 + 严格的 cast(pl.Date) 与 monitor 的
# Utf8 容错冲突，无法直接复用）。本区块用同一 fixture 断言两者**解析结果必须一致**，
# 一旦任一侧口径漂移（例如改选版本规则），这里立即失败。
def test_resolve_parity_auto_select(monkeypatch, tmp_path):
    """自动选择：monitor 与 data.features 必须解析出同一版本目录。"""
    _write_feature(tmp_path, "alpha_basic_v1",
                   {"date": [date(2024, 1, 2)], "symbol": ["A.SH"], "f": [1.0]})
    _write_feature(tmp_path, "alpha_basic_v2g",
                   {"date": [date(2024, 1, 3)], "symbol": ["B.SH"], "f": [2.0]})

    # 两者 settings 源不同源：分别 patch，指向同一 (DATA_ROOT, FEATURE_VERSION) 做对比。
    stub = lambda: SimpleNamespace(DATA_ROOT=tmp_path, FEATURE_VERSION="")  # noqa: E731
    monkeypatch.setattr(feature_data, "get_settings", stub)
    monkeypatch.setattr(monitor, "get_settings", stub)
    expected = feature_data.resolve_feature_version()

    resolved = monitor._resolve_feature_dir(tmp_path / "features")
    assert resolved is not None
    name, version_dir = resolved
    assert name == expected, f"monitor 解析 {name!r} != features.resolve {expected!r}"
    assert version_dir == tmp_path / "features" / f"version={expected}"


def test_resolve_parity_explicit_version(monkeypatch, tmp_path):
    """显式 FEATURE_VERSION 下两者同样必须一致（口径不可两套实现分叉）。"""
    _write_feature(tmp_path, "alpha_basic_v1",
                   {"date": [date(2024, 1, 2)], "symbol": ["A.SH"], "f": [1.0]})
    _write_feature(tmp_path, "alpha_basic_v2g",
                   {"date": [date(2024, 1, 3)], "symbol": ["B.SH"], "f": [2.0]})

    stub = lambda: SimpleNamespace(  # noqa: E731
        DATA_ROOT=tmp_path, FEATURE_VERSION="alpha_basic_v1")
    monkeypatch.setattr(feature_data, "get_settings", stub)
    monkeypatch.setattr(monitor, "get_settings", stub)
    expected = feature_data.resolve_feature_version()
    assert expected == "alpha_basic_v1"

    resolved = monitor._resolve_feature_dir(tmp_path / "features")
    assert resolved is not None and resolved[0] == expected
