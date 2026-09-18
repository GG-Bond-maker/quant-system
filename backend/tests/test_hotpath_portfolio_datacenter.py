"""portfolio/search 与 datacenter/overview 高频路径回归测试。"""
from __future__ import annotations

import asyncio
import json
import sqlite3
import time
from pathlib import Path
from types import SimpleNamespace

import orjson

from app.api.v1 import datacenter as datacenter_api
from app.api.v1 import portfolio as portfolio_api
from app.cache.keys import k_datacenter_overview, k_portfolio_search
from app.cache.redis_client import RedisClient


def _instrument_db(path: Path) -> None:
    with sqlite3.connect(path) as conn:
        conn.execute(
            "CREATE TABLE instrument ("
            "code TEXT, symbol TEXT, name TEXT, instrument_type TEXT)"
        )
        conn.executemany(
            "INSERT INTO instrument VALUES (?, ?, ?, ?)",
            [
                ("600519", "600519.SH", "贵州茅台", "stock"),
                ("510300", "510300.SH", "沪深300ETF", "etf"),
                ("000300", "000300.SH", "沪深300指数", "index"),
            ],
        )
        conn.commit()


def test_portfolio_search_uses_local_instrument_and_preserves_types(
    tmp_path: Path, monkeypatch,
) -> None:
    db_path = tmp_path / "aqp.db"
    _instrument_db(db_path)
    monkeypatch.setattr(
        portfolio_api, "get_settings", lambda: SimpleNamespace(SQLITE_PATH=db_path))

    stock = portfolio_api._search_assets("600519", 10)
    etf = portfolio_api._search_assets("300ETF", 10)
    wildcard = portfolio_api._search_assets("%", 10)

    assert stock == [{"code": "600519", "name": "贵州茅台", "type": "stock"}]
    assert etf == [{
        "code": "510300", "name": "沪深300ETF", "type": "etf",
        "tracking_index": None,
    }]
    assert wildcard == []


def test_portfolio_search_cache_key_isolates_user_date_query_and_limit() -> None:
    base = k_portfolio_search("alice", "20260914", "600519", 10)
    assert base != k_portfolio_search("bob", "20260914", "600519", 10)
    assert base != k_portfolio_search("alice", "20260915", "600519", 10)
    assert base != k_portfolio_search("alice", "20260914", "600519", 20)
    assert base != k_portfolio_search("alice", "20260914", "510300", 10)


def test_portfolio_search_hot_cache_skips_rebuild(monkeypatch) -> None:
    cache: dict[str, bytes] = {}
    calls = 0

    async def fake_get(cls, key: str) -> bytes | None:
        return cache.get(key)

    async def fake_set(cls, key: str, value: bytes, **kwargs) -> None:
        cache[key] = value

    def fake_search(query: str, limit: int) -> list[dict]:
        nonlocal calls
        calls += 1
        return [{"code": query, "name": "命中", "type": "stock"}][:limit]

    monkeypatch.setattr(RedisClient, "get", classmethod(fake_get))
    monkeypatch.setattr(RedisClient, "set", classmethod(fake_set))
    monkeypatch.setattr(portfolio_api, "_search_assets", fake_search)
    monkeypatch.setattr(
        portfolio_api, "today_trade_date_or_last",
        lambda: __import__("datetime").date(2026, 9, 14),
    )

    first = asyncio.run(portfolio_api.portfolio_search(
        q="600519", limit=10, _user={"username": "alice"}))
    second = asyncio.run(portfolio_api.portfolio_search(
        q="600519", limit=10, _user={"username": "alice"}))
    third = asyncio.run(portfolio_api.portfolio_search(
        q="600519", limit=20, _user={"username": "alice"}))

    assert first.data == second.data
    assert calls == 2
    assert third.data == first.data


def test_manifest_dataset_summary_aggregates_without_parquet_reads(tmp_path: Path) -> None:
    manifest = {
        "daily_bar": {
            "600519.SH": {"rows": 2, "first": "2026-09-11", "last": "2026-09-14"},
            "000001.SZ": {"rows": 3, "first": "2026-09-10", "last": "2026-09-15"},
        },
        "features": {"600519.SH": {"rows": 1, "first": "2026-09-14", "last": "2026-09-14"}},
    }
    (tmp_path / ".manifest.json").write_text(
        json.dumps(manifest), encoding="utf-8")

    result = datacenter_api._manifest_dataset_summary(tmp_path)

    assert result["daily_bar"] == {
        "symbols": 2, "rows": 5, "bytes": None,
        "start": "2026-09-10", "end": "2026-09-15",
    }
    assert result["features"]["rows"] == 1


def test_datacenter_cache_key_includes_root_and_revision() -> None:
    base = k_datacenter_overview("2026-09-14", "root-a", "rev-1")
    assert base != k_datacenter_overview("2026-09-14", "root-b", "rev-1")
    assert base != k_datacenter_overview("2026-09-14", "root-a", "rev-2")
    assert base != k_datacenter_overview("2026-09-15", "root-a", "rev-1")


def test_datacenter_overview_cold_and_hot_budget(tmp_path: Path, monkeypatch) -> None:
    db_path = tmp_path / "aqp.db"
    with sqlite3.connect(db_path) as conn:
        conn.execute("CREATE TABLE instrument (symbol TEXT)")
        conn.execute("INSERT INTO instrument VALUES ('600519.SH')")
        conn.execute(
            "CREATE TABLE data_jobs (status TEXT, error_message TEXT, "
            "finished_at TEXT, started_at TEXT, created_at TEXT)"
        )
        conn.execute(
            "INSERT INTO data_jobs VALUES "
            "('SUCCESS', NULL, '2026-09-14 16:00:00', NULL, '2026-09-14 15:00:00')"
        )
        conn.commit()
    manifest = {
        "daily_bar": {
            "600519.SH": {"rows": 2, "first": "2026-09-11", "last": "2026-09-14"}
        }
    }
    (tmp_path / ".manifest.json").write_text(
        json.dumps(manifest), encoding="utf-8")
    parquet = tmp_path / "daily_bar" / "symbol=600519.SH"
    parquet.mkdir(parents=True)
    (parquet / "year=2026.parquet").write_bytes(b"test")

    settings = SimpleNamespace(DATA_ROOT=tmp_path, SQLITE_PATH=db_path)
    monkeypatch.setattr(datacenter_api, "get_settings", lambda: settings)
    cache: dict[str, bytes] = {}

    async def fake_get_stale(cls, key: str) -> tuple[bytes | None, bool]:
        return cache.get(key), False

    async def fake_set(cls, key: str, value: bytes, **kwargs) -> None:
        cache[key] = value

    monkeypatch.setattr(RedisClient, "get_stale", classmethod(fake_get_stale))
    monkeypatch.setattr(RedisClient, "set", classmethod(fake_set))

    start = time.perf_counter()
    cold = asyncio.run(datacenter_api.overview(
        refresh=0, _user={"username": "alice"}))
    cold_elapsed = time.perf_counter() - start
    start = time.perf_counter()
    hot = asyncio.run(datacenter_api.overview(
        refresh=0, _user={"username": "alice"}))
    hot_elapsed = time.perf_counter() - start

    assert cold.data["covered_total"] == 1
    assert cold.data["covered_with_data"] == 1
    assert cold.data["storage_bytes"] >= 4
    assert hot.data["from_cache"] is True
    assert cold_elapsed < 3.0
    assert hot_elapsed < 0.5
