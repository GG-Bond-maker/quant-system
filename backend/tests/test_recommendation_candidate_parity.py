from __future__ import annotations

from datetime import date
from types import SimpleNamespace

import polars as pl
import pytest

from app.data import screening
from app.core.errors import AQPException


def test_recommendation_candidate_filter_excludes_untradable_symbols(tmp_path, monkeypatch) -> None:
    monkeypatch.setattr(screening, "get_settings", lambda: SimpleNamespace(DATA_ROOT=tmp_path))
    path = tmp_path / "universe_daily" / "symbol=__all__"
    path.mkdir(parents=True)
    pl.DataFrame({
        "date": [date(2026, 9, 10)] * 3,
        "symbol": ["A.SH", "B.SH", "C.SH"],
        "is_st": [False, True, False],
        "is_halted": [False, False, True],
        "board": ["main"] * 3,
    }).write_parquet(path / "year=2026.parquet")
    pred = pl.DataFrame({
        "date": [date(2026, 9, 10)] * 3,
        "symbol": ["A.SH", "B.SH", "C.SH"],
        "pred_score": [0.1, 0.9, 0.8],
    })

    out, pool_size = screening.filter_universe(pred, "2026-09-10", "all")

    assert out["symbol"].to_list() == ["A.SH"]
    assert pool_size == 1

    (path / "year=2026.parquet").unlink()
    with pytest.raises(AQPException):
        screening.filter_universe(pred, "2026-09-10", "all", require_universe=True)
