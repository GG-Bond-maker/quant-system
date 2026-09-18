"""watchlist/dashboard 与 stock panels 高频路径回归测试。"""
from __future__ import annotations

import asyncio
import time

import orjson

from app.api.v1 import stock as stock_api
from app.api.v1 import watchlist as watchlist_api
from app.cache.keys import k_stock_block, k_watchlist_dashboard
from app.cache.redis_client import RedisClient


def test_watchlist_cache_key_isolates_user_date_and_symbols() -> None:
    base = k_watchlist_dashboard("alice", "20260914", "symbols-a")
    assert base != k_watchlist_dashboard("bob", "20260914", "symbols-a")
    assert base != k_watchlist_dashboard("alice", "20260915", "symbols-a")
    assert base != k_watchlist_dashboard("alice", "20260914", "symbols-b")


def test_stock_block_cache_key_contains_parameter_dimensions() -> None:
    base = k_stock_block("600519.SH", "events", "20260914", "limit3")
    assert base != k_stock_block("600519.SH", "events", "20260914", "limit10")
    assert k_stock_block("600519.SH", "chip", "20260914", "lookback120") != \
        k_stock_block("600519.SH", "chip", "20260914", "lookback500")
    assert k_stock_block("600519.SH", "risk", "20260914", "window252") != \
        k_stock_block("600519.SH", "risk", "20260914", "window1000")


def _bars(close: float) -> list[dict]:
    rows = []
    for index in range(80):
        price = close - (79 - index) * 0.01
        rows.append({
            "date": f"2026-06-{(index % 28) + 1:02d}",
            "open": price, "high": price * 1.01, "low": price * 0.99,
            "close": price, "volume": 1000.0, "amount": price * 1000.0,
        })
    return rows


def test_watchlist_dashboard_result_and_hot_cache(monkeypatch) -> None:
    cache: dict[str, bytes] = {}
    bar_calls = 0

    async def fake_get_stale(cls, key: str) -> tuple[bytes | None, bool]:
        return cache.get(key), False

    async def fake_set(cls, key: str, value: bytes, **kwargs) -> None:
        cache[key] = value

    def fake_bars(symbol: str):
        nonlocal bar_calls
        bar_calls += 1
        return _bars(10.0), "local-parquet"

    async def fake_flow(stocks: list[str]):
        return 1.25, "ok"

    monkeypatch.setattr(RedisClient, "get_stale", classmethod(fake_get_stale))
    monkeypatch.setattr(RedisClient, "set", classmethod(fake_set))
    monkeypatch.setattr(watchlist_api, "_bars_for", fake_bars)
    monkeypatch.setattr(
        watchlist_api, "_load_instrument_names",
        lambda: {"600519.SH": "贵州茅台", "000001.SZ": "平安银行"},
    )
    monkeypatch.setattr(
        watchlist_api, "_batch_valuation",
        lambda symbols: {
            symbol: {"name": None, "price": 10.0, "pct": 1.0, "pe": 10.0, "pb": 2.0}
            for symbol in symbols
        },
    )
    monkeypatch.setattr(watchlist_api, "_fund_flow_total", fake_flow)
    monkeypatch.setattr(
        watchlist_api, "today_trade_date_or_last",
        lambda: __import__("datetime").date(2026, 9, 14),
    )

    first = asyncio.run(watchlist_api.dashboard(
        symbols="600519.SH,000001.SZ", _user={"username": "alice"}))
    second = asyncio.run(watchlist_api.dashboard(
        symbols="600519.SH,000001.SZ", _user={"username": "alice"}))

    assert first.data["summary"]["count"] == 2
    assert first.data["summary"]["flow_total_yi"] == 1.25
    assert first.data["items"][0]["name"] == "贵州茅台"
    assert second.data["from_cache"] is True
    assert bar_calls == 2


def test_watchlist_timeout_returns_structured_unavailable(monkeypatch) -> None:
    async def fake_get_stale(cls, key: str) -> tuple[bytes | None, bool]:
        return None, False

    async def fake_set(cls, key: str, value: bytes, **kwargs) -> None:
        return None

    def slow_bars(symbol: str):
        time.sleep(0.2)
        return _bars(10.0), "slow"

    monkeypatch.setattr(RedisClient, "get_stale", classmethod(fake_get_stale))
    monkeypatch.setattr(RedisClient, "set", classmethod(fake_set))
    monkeypatch.setattr(watchlist_api, "_bars_for", slow_bars)
    monkeypatch.setattr(watchlist_api, "_WATCHLIST_BUDGET_SECONDS", 0.05)

    started = time.perf_counter()
    result = asyncio.run(watchlist_api.dashboard(
        symbols="600519.SH", _user={"username": "alice"}))
    elapsed = time.perf_counter() - started

    # asyncio.run 会在退出时等待已提交线程结束；服务常驻 loop 的响应预算仍为 50ms。
    assert elapsed < 0.4
    assert result.data["status"] == "degraded"
    assert result.data["summary"]["flow_status"] == "unavailable"
    assert result.data["items"][0]["status"] == "unavailable"
    assert result.data["items"][0]["close"] is None


def test_stock_panels_timeout_returns_stale_block(monkeypatch) -> None:
    stale_quote = {"status": "ok", "price": 10.5, "from_cache": False}

    async def fake_get_stale(cls, key: str) -> tuple[bytes | None, bool]:
        return None, False

    async def fake_get(cls, key: str) -> bytes | None:
        if ":quote:" in key and key.endswith(":swr"):
            return orjson.dumps(stale_quote)
        return None

    async def fake_set(cls, *args, **kwargs) -> None:
        return None

    def slow_quote(symbol: str) -> dict:
        time.sleep(0.2)
        return {"status": "ok", "price": 11.0}

    def quick(symbol: str, **kwargs) -> dict:
        return {"status": "ok"}

    monkeypatch.setattr(RedisClient, "get_stale", classmethod(fake_get_stale))
    monkeypatch.setattr(RedisClient, "get", classmethod(fake_get))
    monkeypatch.setattr(RedisClient, "set", classmethod(fake_set))
    monkeypatch.setattr(stock_api, "build_quote", slow_quote)
    for name in (
        "build_money_flow", "build_north", "build_fundamentals", "build_events",
        "build_holders", "build_chip", "build_risk",
    ):
        monkeypatch.setattr(stock_api, name, quick)
    monkeypatch.setattr(stock_api, "_PANEL_BLOCK_TIMEOUT", 0.05)
    monkeypatch.setattr(
        stock_api, "today_trade_date_or_last",
        lambda: __import__("datetime").date(2026, 9, 14),
    )

    result = asyncio.run(stock_api.stock_panels(
        symbol="600519.SH", event_limit=3, chip_lookback=120,
        risk_window=252, _user={"username": "alice"}))

    assert result.data["quote"]["status"] == "ok"
    assert result.data["quote"]["price"] == 10.5
    assert result.data["quote"]["from_cache"] is True
    assert result.data["quote"]["stale"] is True
    assert result.data["risk"]["status"] == "ok"
