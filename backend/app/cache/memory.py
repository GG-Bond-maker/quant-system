"""
进程内 LRU 缓存（AQP）。

- 主要作为 Redis 的降级后备（Redis 不可用 / 超时 / 熔断期间兜底）；
- 也用于读频繁、写一次的极小对象（如交易日历数组）；
- 每条记录以 (expire_at, value) 元组存储，读取时惰性判断过期。
"""
from __future__ import annotations

import threading
import time

from cachetools import LRUCache

# 容量 10w 条，足够覆盖 5k 股票 * 20 个热点 key
_CACHE: LRUCache[str, tuple[float, object]] = LRUCache(maxsize=100_000)
_LOCK = threading.RLock()


def lru_get(key: str) -> object | None:
    """读取缓存；过期条目惰性删除并返回 None。"""
    with _LOCK:
        item = _CACHE.get(key)
    if item is None:
        return None
    expire_at, value = item
    if time.time() > expire_at:
        lru_del(key)
        return None
    return value


def lru_set(key: str, value: object, ttl: int = 3600) -> None:
    """写入缓存（近似 TTL：保存过期时间戳，读取时自行判断，不主动清理）。"""
    with _LOCK:
        _CACHE[key] = (time.time() + ttl, value)


def lru_del(key: str) -> None:
    """删除单条缓存。"""
    with _LOCK:
        _CACHE.pop(key, None)


def lru_ttl(key: str) -> int:
    """返回条目剩余生存秒数，语义与 Redis ``TTL`` 对齐。

    - ``-2``：键不存在（或已过期，惰性删除）
    - ``-1``：存在但无过期 —— 本 LRU 的每条记录都由 :func:`lru_set` 带 TTL 写入，
      不存在"无过期"条目，故永不返回 -1
    - ``>=0``：剩余秒数

    供 :meth:`RedisClient.ttl` 在 Redis 不可用/异常降级时读取，保证与
    :meth:`RedisClient.get`（降级时同样读 LRU）口径一致——不会出现
    「get() 有值、ttl() 却说不存在」的自相矛盾。
    """
    with _LOCK:
        item = _CACHE.get(key)
    if item is None:
        return -2
    expire_at, _value = item
    remaining = expire_at - time.time()
    if remaining <= 0:
        lru_del(key)
        return -2
    return int(remaining)


def lru_take(key: str) -> object | None:
    """原子地读取并删除缓存条目（一次性凭据等场景）。"""
    with _LOCK:
        item = _CACHE.pop(key, None)
    if item is None:
        return None
    expire_at, value = item
    if time.time() > expire_at:
        return None
    return value


def lru_clear() -> None:
    """清空全部缓存。"""
    with _LOCK:
        _CACHE.clear()
