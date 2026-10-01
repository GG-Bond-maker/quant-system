"""Investigator: cold-path timing probe for AQP endpoints (read-only + controlled cache purge).

Measures wall-clock of expensive endpoints on a dedicated backend (port 8021),
with the target Redis keys purged before each cold measurement.
"""
from __future__ import annotations

import json
import sys
import time
import urllib.request
from pathlib import Path

BACKEND_ROOT = Path(r"D:\Python_Project\Alpha Quant Platform\backend")
sys.path.insert(0, str(BACKEND_ROOT))

BASE = "http://127.0.0.1:8021"
REDIS_HOST = "127.0.0.1"
REDIS_PORT = 6379


def http_get(path: str, timeout: float = 180.0):
    url = BASE + path
    t0 = time.perf_counter()
    try:
        req = urllib.request.Request(url)
        with urllib.request.urlopen(req, timeout=timeout) as r:
            body = r.read()
            dt = time.perf_counter() - t0
            return r.status, dt, body
    except Exception as e:  # noqa: BLE001
        dt = time.perf_counter() - t0
        return f"ERR:{type(e).__name__}:{e}", dt, b""


def summarize(body: bytes, maxlen: int = 220) -> str:
    try:
        j = json.loads(body)
        data = j.get("data", {})
        bits = {}
        if isinstance(data, dict):
            for k in ("status", "from_cache", "stale", "trade_date", "as_of"):
                if k in data:
                    bits[k] = data[k]
            df = data.get("data_freshness")
            if isinstance(df, dict):
                bits["freshness"] = df.get("source") or df.get("status")
        return f"code={j.get('code')} {bits}"
    except Exception:
        return body[:maxlen].decode("utf-8", "replace")


def purge(patterns: list[str]) -> int:
    """Purge keys via the app's own RedisClient (uses .env config)."""
    import asyncio
    from app.cache.redis_client import RedisClient

    async def _run() -> int:
        r = RedisClient._ensure()
        if r is None:
            print("  [purge] Redis unavailable via RedisClient")
            return 0
        seen = set()
        for pat in patterns:
            async for k in r.scan_iter(match=pat, count=500):
                key = k.decode() if isinstance(k, bytes) else str(k)
                await r.delete(key)
                seen.add(key)
        return len(seen)

    return asyncio.run(_run())


ENDPOINTS = [
    # (label, path, redis patterns to purge for cold)
    ("daily", "/api/v1/market/overview/daily?recommend_k=50", ["aqp:market:overview_daily*"]),
    ("rt", "/api/v1/market/overview/rt", ["aqp:market:overview_rt*"]),
    ("overview_compat", "/api/v1/market/overview?recommend_k=50", ["aqp:market:overview:*"]),
    ("etf_overview", "/api/v1/etf/overview", ["aqp:etf:overview:*"]),
    ("etf_flow", "/api/v1/etf/flow?period=1d&limit=20", ["aqp:etf:*"]),
    ("watchlist", "/api/v1/watchlist/dashboard?symbols=600519.SH,000001.SZ,300750.SZ", ["aqp:watchlist*"]),
    ("screener", "/api/v1/screener?strategy=multifactor&top_k=50", ["aqp:screener*"]),
    ("stock_panels", "/api/v1/stock/600519.SH/panels", ["aqp:stock*"]),
    ("quotes", "/api/v1/market/quotes?symbols=600519.SH,000001.SZ,300750.SZ", ["aqp:market*"]),
    ("index_kline", "/api/v1/market/index/kline?symbol=000300.SH&period=day&limit=120", ["aqp:market*"]),
]


def main() -> None:
    only = sys.argv[1] if len(sys.argv) > 1 else None
    print("=== AQP endpoint timing probe ===")
    for label, path, pats in ENDPOINTS:
        if only and only != label:
            continue
        purged = purge(pats)
        status, dt, body = http_get(path)
        print(f"[{label}] cold purge={purged} | {status} | {dt:.2f}s | {summarize(body)}")
        # warm (immediate second call, should hit cache)
        status2, dt2, body2 = http_get(path)
        print(f"[{label}] warm              | {status2} | {dt2:.2f}s | {summarize(body2)}")
        sys.stdout.flush()


if __name__ == "__main__":
    main()
