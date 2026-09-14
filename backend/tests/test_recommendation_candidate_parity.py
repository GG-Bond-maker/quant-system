from __future__ import annotations

from datetime import date
from types import SimpleNamespace

import polars as pl
import pytest

from app.data import parquet_store, screening
from app.core.errors import AQPException


def test_recommendation_candidate_filter_excludes_untradable_symbols(tmp_path, monkeypatch) -> None:
    # P0 修复（2026-09-14）后 filter_universe 的分区路径经
    # ``parquet_store.path_for_year`` 解析，读的是 **parquet_store** 模块的
    # get_settings；screening 自身已不再直连 DATA_ROOT，故须同步注入 parquet_store，
    # 否则 join 会落到真实数据仓库（本用例的合成 A/B/C.SH 永不命中）。
    monkeypatch.setattr(screening, "get_settings", lambda: SimpleNamespace(DATA_ROOT=tmp_path))
    monkeypatch.setattr(parquet_store, "get_settings", lambda: SimpleNamespace(DATA_ROOT=tmp_path))
    path = tmp_path / "universe_daily" / "symbol=__all__"
    path.mkdir(parents=True)
    pl.DataFrame({
        "date": [date(2026, 9, 10)] * 3,
        "symbol": ["A.SH", "B.SH", "C.SH"],
        "is_st": [False, True, False],
        "is_halted": [False, False, True],
        "board": ["main"] * 3,
    }).write_parquet(path / "year=2026.snappy.parquet")
    pred = pl.DataFrame({
        "date": [date(2026, 9, 10)] * 3,
        "symbol": ["A.SH", "B.SH", "C.SH"],
        "pred_score": [0.1, 0.9, 0.8],
    })

    out, pool_size = screening.filter_universe(pred, "2026-09-10", "all")

    assert out["symbol"].to_list() == ["A.SH"]
    assert pool_size == 1

    (path / "year=2026.snappy.parquet").unlink()
    with pytest.raises(AQPException):
        screening.filter_universe(pred, "2026-09-10", "all", require_universe=True)
