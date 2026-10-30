"""Futures cache writes must respect isolated storage and atomic replacement."""
from __future__ import annotations

from dataclasses import replace
from datetime import date

import pandas as pd
import polars as pl

from app.core.config import get_settings
from app.data import futures, futures_sync, parquet_store
from app.data.futures_store import read_futures_manifest, write_futures_manifest


def _raw_frame(*, close: float, trade_date: str | None = None) -> pd.DataFrame:
    day = trade_date or date.today().isoformat()
    return pd.DataFrame(
        {
            "日期": [day],
            "开盘价": [close - 0.5],
            "最高价": [close + 0.5],
            "最低价": [close - 1.0],
            "收盘价": [close],
            "成交量": [20_000.0],
            "持仓量": [30_000.0],
        }
    )


def test_futures_cache_uses_configured_data_root(monkeypatch, tmp_path):
    configured = tmp_path / "isolated"
    monkeypatch.setattr(get_settings(), "DATA_ROOT", configured)
    cache_dir = configured / "futures_cache"
    futures_sync.sync_futures_symbol(
        "IF",
        cache_dir=cache_dir,
        fetch_fn=lambda _: _raw_frame(close=10.5),
    )

    bars = futures.get_futures_kline("IF", days=1)

    target = cache_dir / "IF.parquet"
    assert target.exists()
    assert pl.read_parquet(target)["close"].to_list() == [10.5]
    assert bars[0]["close"] == 10.5
    assert not (tmp_path / "data" / "futures_cache").exists()


def test_futures_cache_failed_replace_preserves_previous_snapshot(
    monkeypatch,
    tmp_path,
):
    monkeypatch.setattr(get_settings(), "DATA_ROOT", tmp_path)
    cache_dir = tmp_path / "futures_cache"
    futures_sync.sync_futures_symbol(
        "IF",
        cache_dir=cache_dir,
        fetch_fn=lambda _: _raw_frame(close=1.5, trade_date="2020-01-02"),
    )
    target = cache_dir / "IF.parquet"
    old_bytes = target.read_bytes()
    old_meta = read_futures_manifest(cache_dir)["IF"]
    write_futures_manifest(
        cache_dir,
        {"IF": replace(old_meta, fetched_at="2020-01-01T00:00:00+08:00")},
    )

    def fail_replace(*args):
        raise OSError("simulated interrupted cache replacement")

    monkeypatch.setattr(parquet_store, "_fsync_and_replace", fail_replace)
    bars = futures.get_futures_kline("IF", days=1)

    assert target.read_bytes() == old_bytes
    assert bars[0]["close"] == 1.5
    assert {path.name for path in target.parent.iterdir()} == {
        "IF.parquet",
        "manifest.json",
    }
