"""一次性运维脚本：失效市场概览日频块缓存（字段改名后必须清，否则前端拿到旧 schema）。

用法（必须先 cd backend）：
    ./.venv/Scripts/python.exe scripts/dev_purge_market_overview_cache.py

只删 `aqp:market:overview*` 命名空间，不触碰其它缓存。
"""
from __future__ import annotations

import asyncio
import sys
from pathlib import Path

BACKEND_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(BACKEND_ROOT))

from app.cache.redis_client import RedisClient  # noqa: E402


async def main() -> None:
    r = RedisClient._ensure()
    if r is None:
        print("[purge] Redis 不可用（熔断或未启动），跳过")
        return

    patterns = ["aqp:market:overview*", "aqp:market:overview_daily*"]
    seen: set[str] = set()
    for pat in patterns:
        keys = [k async for k in r.scan_iter(match=pat)]
        for k in keys:
            key = k.decode() if isinstance(k, bytes) else str(k)
            if key in seen:
                continue
            seen.add(key)
            ttl = await r.ttl(key)
            await r.delete(key)
            print(f"[purge] 已删除 {key} (原 TTL={ttl}s)")
    print(f"[purge] 完成，共删除 {len(seen)} 个 key")


if __name__ == "__main__":
    asyncio.run(main())
