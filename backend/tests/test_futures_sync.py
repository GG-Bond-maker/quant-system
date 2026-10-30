from __future__ import annotations

from datetime import date
from pathlib import Path

import pandas as pd
import polars as pl
import pytest

from app.data import futures_sync
from app.data.futures_store import (
    futures_trade_status,
    read_futures_manifest,
)
from app.data.futures_sync import sync_futures_symbol, sync_futures_universe
from scripts.sync_futures_history import main as sync_main


def _short_raw() -> pd.DataFrame:
    return pd.DataFrame(
        {
            "日期": ["2005-01-04", "2005-01-05"],
            "开盘价": [100.0, 101.0],
            "最高价": [102.0, 103.0],
            "最低价": [99.0, 100.0],
            "收盘价": [101.0, 102.0],
            "成交量": [1000, 1100],
            "持仓量": [2000, 2100],
        }
    )


def _recent_raw(rows: int = 260) -> pd.DataFrame:
    dates = pd.bdate_range(end=pd.Timestamp.now().normalize(), periods=rows)
    closes = [100.0 + (index % 12) * 0.5 for index in range(rows)]
    return pd.DataFrame(
        {
            "日期": dates.strftime("%Y-%m-%d"),
            "开盘价": closes,
            "最高价": [value + 1.0 for value in closes],
            "最低价": [value - 1.0 for value in closes],
            "收盘价": closes,
            "成交量": [20_000] * rows,
            "持仓量": [30_000] * rows,
        }
    )


def test_sync_tracks_source_first_and_actual_cache_start(tmp_path: Path) -> None:
    cache = tmp_path / "cache"

    first = sync_futures_symbol("CF", cache_dir=cache, fetch_fn=lambda _: _short_raw())
    meta = read_futures_manifest(cache)["CF"]

    assert first.wrote_cache is True
    assert meta.source_first_date == "2005-01-04"
    assert meta.cache_first_date == "2005-01-04"
    assert meta.effective_start == "2005-01-04"
    assert meta.quality_status == "failed"
    assert meta.trade_enabled is False

    second = sync_futures_symbol(
        "CF",
        cache_dir=cache,
        requested_start_date=date(2005, 1, 5),
        force=True,
        fetch_fn=lambda _: _short_raw(),
    )
    meta = read_futures_manifest(cache)["CF"]

    assert second.wrote_cache is True
    assert meta.source_first_date == "2005-01-04"
    assert meta.cache_first_date == "2005-01-05"
    assert meta.requested_start_date == "2005-01-05"
    assert meta.effective_start == "2005-01-05"


def test_recent_core_data_is_quality_ok_and_tradable(tmp_path: Path) -> None:
    cache = tmp_path / "cache"

    sync_futures_symbol("CF", cache_dir=cache, fetch_fn=lambda _: _recent_raw())

    meta = read_futures_manifest(cache)["CF"]
    assert meta.rows == 260
    assert meta.quality_status == "ok"
    assert meta.trade_enabled is True
    assert futures_trade_status("CF", cache) == (True, "ok")
    assert futures_trade_status("PB", cache) == (False, "品种属于扩展池，默认未启用")
    assert futures_trade_status("ZZZZ", cache) == (False, "未知期货品种")


def test_core_data_with_quarantined_bad_ohlc_is_quality_ok(tmp_path: Path) -> None:
    cache = tmp_path / "cache"
    raw = _recent_raw()
    raw.loc[0, "最高价"] = 90.0

    sync_futures_symbol("CF", cache_dir=cache, fetch_fn=lambda _: raw)

    meta = read_futures_manifest(cache)["CF"]
    assert meta.rows == 259
    assert meta.issue_counts == {"invalid_ohlc": 1}
    assert meta.quality_status == "ok"
    assert meta.trade_enabled is True


def test_extended_symbol_never_enables_trading(tmp_path: Path) -> None:
    cache = tmp_path / "cache"

    result = sync_futures_symbol("PB", cache_dir=cache, fetch_fn=lambda _: _recent_raw())
    meta = read_futures_manifest(cache)["PB"]

    assert result.wrote_cache is True
    assert meta.quality_status == "warning"
    assert meta.trade_enabled is False
    assert futures_trade_status("PB", cache) == (False, "品种属于扩展池，默认未启用")


def test_fetch_failure_preserves_cache_and_manifest(
    tmp_path: Path,
    monkeypatch,
) -> None:
    cache = tmp_path / "cache"
    sync_futures_symbol("CF", cache_dir=cache, fetch_fn=lambda _: _recent_raw())
    target = cache / "CF.parquet"
    old_parquet = target.read_bytes()
    old_manifest = (cache / "manifest.json").read_bytes()
    attempts = 0

    def fail(_: str) -> pd.DataFrame:
        nonlocal attempts
        attempts += 1
        raise OSError("simulated upstream failure")

    monkeypatch.setattr(futures_sync.time, "sleep", lambda _: None)
    result = sync_futures_symbol("CF", cache_dir=cache, force=True, fetch_fn=fail)

    assert attempts == 3
    assert result.success is False
    assert result.wrote_cache is False
    assert target.read_bytes() == old_parquet
    assert (cache / "manifest.json").read_bytes() == old_manifest


def test_invalid_frame_preserves_existing_cache(tmp_path: Path, monkeypatch) -> None:
    cache = tmp_path / "cache"
    sync_futures_symbol("CF", cache_dir=cache, fetch_fn=lambda _: _recent_raw())
    target = cache / "CF.parquet"
    old_parquet = target.read_bytes()
    old_manifest = (cache / "manifest.json").read_bytes()
    bad = pd.DataFrame({"日期": ["2026-10-30"], "收盘价": [100.0]})

    monkeypatch.setattr(futures_sync.time, "sleep", lambda _: None)
    result = sync_futures_symbol("CF", cache_dir=cache, force=True, fetch_fn=lambda _: bad)

    assert result.success is False
    assert result.wrote_cache is False
    assert target.read_bytes() == old_parquet
    assert (cache / "manifest.json").read_bytes() == old_manifest


def test_parallel_sync_keeps_all_manifest_entries(tmp_path: Path) -> None:
    cache = tmp_path / "cache"

    results = sync_futures_universe(
        ["CF", "SR"],
        cache_dir=cache,
        max_workers=2,
        fetch_fn=lambda _: _recent_raw(),
    )
    manifest = read_futures_manifest(cache)

    assert [result.symbol for result in results] == ["CF", "SR"]
    assert set(manifest) == {"CF", "SR"}
    assert pl.read_parquet(cache / "CF.parquet").height == 260
    assert pl.read_parquet(cache / "SR.parquet").height == 260


def test_sync_dry_run_does_not_write(tmp_path: Path) -> None:
    cache = tmp_path / "cache"

    result = sync_futures_symbol(
        "CF",
        cache_dir=cache,
        fetch_fn=lambda _: _recent_raw(),
        apply=False,
    )

    assert result.wrote_cache is False
    assert not (cache / "CF.parquet").exists()
    assert not (cache / "manifest.json").exists()


def test_sync_cli_dry_run_requires_failed_core_quality_exit(
    tmp_path: Path,
) -> None:
    cache = tmp_path / "cache"

    code = sync_main(
        ["--symbols", "CF", "--cache-dir", str(cache)],
        fetch_fn=lambda _: _short_raw(),
    )

    assert code == 1
    assert not (cache / "CF.parquet").exists()


def test_sync_cli_requires_tier_or_symbols() -> None:
    with pytest.raises(SystemExit) as exc:
        sync_main([])

    assert exc.value.code == 2
