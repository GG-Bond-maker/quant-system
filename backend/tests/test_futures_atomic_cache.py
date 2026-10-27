"""Futures cache writes must respect isolated storage and atomic replacement."""
import sys
import types
from datetime import date

import pandas as pd
import polars as pl

from app.core.config import get_settings
from app.data import futures, parquet_store


def _source(monkeypatch):
    frame = pd.DataFrame({"日期": [date.today().isoformat()], "开盘价": [10.0],
                          "最高价": [11.0], "最低价": [9.0], "收盘价": [10.5],
                          "成交量": [100.0], "持仓量": [200.0]})
    monkeypatch.setitem(sys.modules, "akshare", types.SimpleNamespace(
        futures_main_sina=lambda symbol: frame))


def test_futures_cache_uses_configured_data_root(monkeypatch, tmp_path):
    monkeypatch.chdir(tmp_path)
    configured = tmp_path / "isolated"
    monkeypatch.setattr(get_settings(), "DATA_ROOT", configured)
    _source(monkeypatch)
    bars = futures.get_futures_kline("IF", days=1)
    target = configured / "futures_cache" / "IF.parquet"
    assert target.exists()
    assert pl.read_parquet(target)["close"].to_list() == [10.5]
    assert bars[0]["close"] == 10.5
    assert not (tmp_path / "data" / "futures_cache").exists()


def test_futures_cache_failed_replace_preserves_previous_snapshot(monkeypatch, tmp_path):
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(get_settings(), "DATA_ROOT", tmp_path)
    _source(monkeypatch)
    target = tmp_path / "futures_cache" / "IF.parquet"
    target.parent.mkdir()
    old = pl.DataFrame({"date": ["2020-01-02"], "open": [1.0], "high": [2.0],
                        "low": [0.5], "close": [1.5], "volume": [10.0],
                        "open_interest": [20.0]})
    old.write_parquet(target)
    old_bytes = target.read_bytes()

    def fail_replace(*args):
        raise OSError("simulated interrupted cache replacement")

    monkeypatch.setattr(parquet_store, "_fsync_and_replace", fail_replace)
    bars = futures.get_futures_kline("IF", days=1)
    assert target.read_bytes() == old_bytes
    assert bars[0]["close"] == 1.5  # degraded read uses the intact prior snapshot
    assert sorted(target.parent.iterdir()) == [target]
