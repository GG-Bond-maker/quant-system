"""市场概览与 ETF 性能回归测试（完全离线）。

覆盖本次修复的关键约束：
- 市场实时块的独立 IO 必须受限并行；
- ETF 表现结果按参数缓存，第二次请求不重复读取 K 线；
- 单 ETF 详情的慢/缺失源必须在块级预算内快速降级，而非长时间悬挂。
"""
from __future__ import annotations

import asyncio
import sys
import time
from pathlib import Path

import pytest

BACKEND_ROOT = Path(__file__).resolve().parents[1]
if str(BACKEND_ROOT) not in sys.path:
    sys.path.insert(0, str(BACKEND_ROOT))

from app.api.v1 import etf as etf_api  # noqa: E402
from app.api.v1 import market as market_api  # noqa: E402
from app.cache.memory import lru_clear  # noqa: E402
from app.cache import redis_client as redis_client_mod  # noqa: E402
from app.data import etf as etf_data  # noqa: E402


@pytest.fixture(autouse=True)
def offline_cache(monkeypatch: pytest.MonkeyPatch) -> None:
    """强制走 LRU 缓存，保证测试无 Redis / 外网依赖。"""
    lru_clear()
    monkeypatch.setattr(redis_client_mod.RedisClient, "_ensure", staticmethod(lambda: None))
    yield
    lru_clear()


def test_market_rt_builds_independent_blocks_concurrently(monkeypatch: pytest.MonkeyPatch) -> None:
    """三个实时子块各 80ms 时，总耗时应接近一个块而不是三块串行。"""
    def delayed(status: str = "ok") -> dict:
        time.sleep(0.08)
        return {"status": status}

    monkeypatch.setattr(market_api, "_build_indices", delayed)
    monkeypatch.setattr(market_api, "_build_money_flow", delayed)
    monkeypatch.setattr(market_api, "_build_heat", delayed)
    monkeypatch.setattr(market_api, "_build_anomalies", lambda _heat: delayed("degraded"))

    started = time.monotonic()
    result = market_api._build_rt(__import__("datetime").date.today())
    elapsed = time.monotonic() - started

    assert elapsed < 0.20, f"实时块疑似退化为串行：{elapsed:.3f}s"
    assert result["indices"]["status"] == "ok"
    assert result["anomalies"]["status"] == "degraded"
    assert result["data_freshness"]["status"] == "degraded"


async def test_etf_performance_uses_parameter_cache(monkeypatch: pytest.MonkeyPatch) -> None:
    """相同参数第二次调用必须命中 SWR/LRU，且不重复读取 K 线。"""
    calls: list[tuple[str, str]] = []
    catalog = [{
        "code": "510300", "name": "沪深300ETF", "country": "cn",
        "price": 4.0, "pct": 0.1, "amount": 1.0, "size_yi": 1.0,
    }]
    bars = [
        {"date": "2026-09-10", "open": 4.0, "high": 4.1, "low": 3.9,
         "close": 4.0, "volume": 100.0},
        {"date": "2026-09-11", "open": 4.0, "high": 4.2, "low": 4.0,
         "close": 4.1, "volume": 100.0},
    ]
    monkeypatch.setattr(etf_data, "build_catalog", lambda: catalog)

    def load_kline(market: str, code: str, limit: int = 800) -> list[dict]:
        calls.append((market, code))
        return bars

    monkeypatch.setattr(etf_data, "fetch_kline", load_kline)
    first = await etf_api.etf_performance("510300", "pct", "1m", {})
    second = await etf_api.etf_performance("510300", "pct", "1m", {})

    assert first.data["series"][0]["status"] == "ok"
    assert second.data["from_cache"] is True
    assert calls == [("sh", "510300")]


async def test_etf_detail_slow_source_fast_degrades(monkeypatch: pytest.MonkeyPatch) -> None:
    """详情块数据源卡住时不等待网络默认重试，返回结构化 unavailable。"""
    catalog = [{
        "code": "510300", "name": "沪深300ETF", "country": "cn",
        "price": 4.0, "pct": 0.1, "amount": 1.0, "size_yi": 1.0,
        "tracking_index": "沪深300", "manager": None, "inception": None,
    }]
    monkeypatch.setattr(etf_data, "build_catalog", lambda: catalog)
    monkeypatch.setattr(etf_api, "_ETF_DETAIL_BLOCK_BUDGET_SECONDS", 0.02)

    def slow_kline(*_args, **_kwargs) -> list[dict]:
        time.sleep(0.20)
        return []

    monkeypatch.setattr(etf_data, "fetch_kline", slow_kline)
    monkeypatch.setattr(etf_data, "fetch_etf_fee", lambda _code: {"management": None, "custody": None})
    monkeypatch.setattr(etf_data, "fetch_etf_holdings", lambda _code: {"items": []})
    monkeypatch.setattr(etf_data, "fetch_etf_industry", lambda _code: {"items": []})
    monkeypatch.setattr(etf_data, "fetch_etf_valuation_proxy", lambda _code: None)
    monkeypatch.setattr(etf_data, "fetch_etf_flow_history", lambda _code, days=60: [])
    monkeypatch.setattr(etf_data, "fetch_etf_news", lambda _code, limit=12: [])

    started = time.monotonic()
    response = await etf_api.etf_detail("510300", "day", {})
    elapsed = time.monotonic() - started

    assert elapsed < 0.12, f"详情页未按块级预算降级：{elapsed:.3f}s"
    assert response.data["status"] == "degraded"
    assert response.data["blocks"]["kline"]["status"] == "unavailable"
