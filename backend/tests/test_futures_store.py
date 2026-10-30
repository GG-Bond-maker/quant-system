from __future__ import annotations

import json
from pathlib import Path

import pandas as pd
import polars as pl

from app.data.futures_store import (
    FuturesCacheMeta,
    normalize_futures_frame,
    read_futures_manifest,
    write_futures_manifest,
)


def _meta(symbol: str = "CF") -> FuturesCacheMeta:
    return FuturesCacheMeta(
        symbol=symbol,
        source="sina_main",
        source_symbol=f"{symbol}0",
        fetched_at="2026-10-30T18:00:00+08:00",
        listing_date="2004-06-01",
        raw_source_first_date="2005-01-04",
        source_first_date="2005-01-04",
        source_last_date="2026-10-30",
        cache_first_date="2005-01-04",
        effective_start="2005-01-04",
        requested_start_date=None,
        rows=5308,
        tier="core",
        quality_status="ok",
        trade_enabled=True,
        issue_counts={},
        warning_counts={},
        checksum="sha256:abc",
    )


def test_normalize_futures_frame_maps_sina_columns() -> None:
    frame = pd.DataFrame(
        {
            "日期": ["2020-01-03", "2020-01-02", "2020-01-03"],
            "开盘价": ["101", "100", "102"],
            "最高价": [103, 102, 104],
            "最低价": [100, 99, 101],
            "收盘价": [102, 101, 103],
            "成交量": [1100, 1000, 1200],
            "持仓量": [2100, 2000, 2200],
            "无关列": ["x", "y", "z"],
        }
    )

    normalized = normalize_futures_frame(frame)

    assert normalized.columns == [
        "date",
        "open",
        "high",
        "low",
        "close",
        "volume",
        "open_interest",
    ]
    assert normalized["date"].to_list() == ["2020-01-02", "2020-01-03"]
    assert normalized["close"].to_list() == [101.0, 103.0]
    assert normalized.schema["open"] == pl.Float64
    assert normalized.schema["date"] == pl.String


def test_manifest_round_trip_is_atomic(tmp_path: Path) -> None:
    cache_dir = tmp_path / "cache"
    entries = {"CF": _meta(), "SR": _meta("SR")}

    path = write_futures_manifest(cache_dir, entries)
    loaded = read_futures_manifest(cache_dir)

    assert path == cache_dir / "manifest.json"
    assert loaded == entries
    assert list(cache_dir.glob("*.tmp")) == []
    raw = path.read_text(encoding="utf-8")
    assert raw == json.dumps(
        {symbol: meta.__dict__ for symbol, meta in sorted(entries.items())},
        ensure_ascii=False,
        indent=2,
        sort_keys=True,
    ) + "\n"


def test_manifest_tolerates_corrupt_json(tmp_path: Path) -> None:
    cache_dir = tmp_path / "cache"
    cache_dir.mkdir()
    (cache_dir / "manifest.json").write_text("{not-json", encoding="utf-8")

    assert read_futures_manifest(cache_dir) == {}


def test_normalize_rejects_missing_required_sina_columns() -> None:
    frame = pd.DataFrame({"日期": ["2020-01-02"], "收盘价": [100.0]})

    try:
        normalize_futures_frame(frame)
    except ValueError as exc:
        assert "missing required columns" in str(exc)
    else:
        raise AssertionError("normalize_futures_frame should reject incomplete input")
