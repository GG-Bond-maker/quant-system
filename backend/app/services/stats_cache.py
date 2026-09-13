"""数据中心统计的进程级 TTL 缓存（自 api/v1/datacenter 下沉）。

为何独立成模块：sync_service 在同步任务写库后需要 invalidate_stats_cache()
强制下次统计重扫；若缓存原语留在 api 层，services→api 反向依赖会把
刚拆掉的环重新焊上。datacenter 路由层从本模块重导出，行为与键名不变。
"""
from __future__ import annotations

import threading
import time
from typing import Any, Callable

_cache: dict[str, tuple[float, Any]] = {}
_CACHE_LOCK = threading.Lock()


def cached(key: str, ttl: float, fn: Callable[[], Any]) -> Any:
    """带 TTL 的进程级缓存；并发下只算一次。"""
    now = time.monotonic()
    with _CACHE_LOCK:
        hit = _cache.get(key)
        if hit and now - hit[0] < ttl:
            return hit[1]
    val = fn()
    with _CACHE_LOCK:
        _cache[key] = (time.monotonic(), val)
    return val


# 兼容别名：datacenter 内部历史名（路由层重导出沿用）
_cached = cached


def invalidate_stats_cache() -> None:
    """同步任务写入数据后调用，强制下一次统计重扫。"""
    with _CACHE_LOCK:
        for k in [k for k in _cache if k != "logs"]:
            _cache.pop(k, None)
