from __future__ import annotations

from pathlib import Path

import polars as pl
import pytest

from app.core.config import get_settings
from app.core.errors import ERR_DATA_EMPTY, ERR_NOT_FOUND, AQPException
from app.data import futures_sync
from app.data.futures import get_futures_kline
from app.data.futures_store import (
    FuturesCacheMeta,
    parquet_checksum,
    write_futures_manifest,
)
from app.data.futures_sync import FuturesSyncResult


def _write_cache(
    root: Path,
    *,
    symbol: str = "CF",
    dates: list[str] | None = None,
    fetched_at: str = "2026-10-30T18:00:00+08:00",
) -> Path:
    dates = dates or [
        "2005-01-04",
        "2005-01-05",
        "2005-01-06",
        "2026-10-30",
    ]
    closes = [100.0 + index for index in range(len(dates))]
    frame = pl.DataFrame(
        {
            "date": dates,
            "open": closes,
            "high": [value + 1.0 for value in closes],
            "low": [value - 1.0 for value in closes],
            "close": closes,
            "volume": [20_000] * len(dates),
            "open_interest": [30_000] * len(dates),
        }
    )
    cache_dir = root / "futures_cache"
    cache_dir.mkdir(parents=True, exist_ok=True)
    path = cache_dir / f"{symbol}.parquet"
    frame.write_parquet(path)
    write_futures_manifest(
        cache_dir,
        {
            symbol: FuturesCacheMeta(
                symbol=symbol,
                source="sina_main",
                source_symbol=f"{symbol}0",
                fetched_at=fetched_at,
                listing_date="2004-06-01",
                raw_source_first_date=dates[0],
                source_first_date=dates[0],
                source_last_date=dates[-1],
                cache_first_date=dates[0],
                effective_start=dates[0],
                requested_start_date=None,
                rows=len(dates),
                tier="core",
                quality_status="ok",
                trade_enabled=True,
                issue_counts={},
                warning_counts={},
                checksum=parquet_checksum(path),
            )
        },
    )
    return path


def _failed_sync(symbol: str, **_: object) -> FuturesSyncResult:
    return FuturesSyncResult(
        symbol=symbol,
        success=False,
        wrote_cache=False,
        quality_status="failed",
        rows_before=0,
        rows_after=0,
        message="upstream unavailable",
    )


def test_unknown_futures_symbol_does_not_fall_back_to_if(
    monkeypatch,
    tmp_path: Path,
) -> None:
    monkeypatch.setattr(get_settings(), "DATA_ROOT", tmp_path)

    with pytest.raises(AQPException) as exc:
        get_futures_kline("ZZZZ")

    assert exc.value.code == ERR_NOT_FOUND


def test_empty_cache_and_failed_fetch_never_returns_synthetic(
    monkeypatch,
    tmp_path: Path,
) -> None:
    monkeypatch.setattr(get_settings(), "DATA_ROOT", tmp_path)
    monkeypatch.setattr(futures_sync, "sync_futures_symbol", _failed_sync)

    with pytest.raises(AQPException) as exc:
        get_futures_kline("CF")

    assert exc.value.code == ERR_DATA_EMPTY


def test_days_none_returns_full_source_history(
    monkeypatch,
    tmp_path: Path,
) -> None:
    monkeypatch.setattr(get_settings(), "DATA_ROOT", tmp_path)
    _write_cache(tmp_path)

    bars = get_futures_kline("CF", days=None)

    assert bars[0]["date"] == "2005-01-04"
    assert bars[-1]["date"] == "2026-10-30"


def test_explicit_dates_are_inclusive(
    monkeypatch,
    tmp_path: Path,
) -> None:
    monkeypatch.setattr(get_settings(), "DATA_ROOT", tmp_path)
    _write_cache(tmp_path)

    bars = get_futures_kline(
        "CF",
        days=None,
        start_date="2005-01-05",
        end_date="2005-01-06",
    )

    assert [bar["date"] for bar in bars] == ["2005-01-05", "2005-01-06"]


def test_stale_cache_is_returned_when_refresh_fails(
    monkeypatch,
    tmp_path: Path,
) -> None:
    monkeypatch.setattr(get_settings(), "DATA_ROOT", tmp_path)
    _write_cache(tmp_path, fetched_at="2020-01-01T00:00:00+08:00")
    monkeypatch.setattr(futures_sync, "sync_futures_symbol", _failed_sync)

    bars = get_futures_kline("CF", days=1)

    assert bars[-1]["date"] == "2026-10-30"


def test_continuous_bars_are_explicitly_heuristic(
    monkeypatch,
    tmp_path: Path,
) -> None:
    monkeypatch.setattr(get_settings(), "DATA_ROOT", tmp_path)
    _write_cache(tmp_path)

    bars = get_futures_kline("CF", days=None, continuous=True)

    assert bars
    assert all(bar["roll_detection"] == "heuristic" for bar in bars)
