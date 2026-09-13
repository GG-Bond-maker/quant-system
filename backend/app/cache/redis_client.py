"""
Redis 客户端封装，带三层防护（AQP）：

1. 总开关 REDIS_ENABLED=False -> 直接降级内存缓存；
2. 单次操作超时（<= REDIS_TIMEOUT 秒）-> 本次降级内存；
3. 连续失败 FAIL_THRESHOLD 次 -> 熔断 OPEN_SECONDS 秒，期间全部走内存。

API 保持简单：get / set / delete / exists / ping。
写操作永远双写进程内 LRU，保证降级期间仍可读。

Sprint1 扩展（路线图 L1-2 / §3.2）：
- set(..., stale_ex=...) 额外写影子键（key:swr），主键过期后影子键仍在，
  供 stale-while-revalidate「过期先回旧值」；
- get_stale(key) 按 主键 -> 影子键 顺序读取，返回 (value, is_stale)；
- try_lock / unlock：SET NX EX 防抖锁（refresh 合并、后台重建合并），
  Redis 不可用时用进程内时间戳锁兜底（防抖语义降级但成立）。
"""
from __future__ import annotations

import asyncio
import threading
import time
from typing import Any

from loguru import logger
from redis.asyncio import ConnectionPool, Redis

from ..core.config import get_settings
from .memory import lru_del, lru_get, lru_set

# ---------------- 熔断器状态（进程级单例） ----------------
_circuit_breaker: dict[str, float] = {
    "fail_count": 0,
    "open_until": 0.0,  # unix 时间戳；< now 表示熔断关闭
}
FAIL_THRESHOLD = 5    # 连续失败次数阈值
OPEN_SECONDS = 60     # 熔断持续时间（秒）

# SWR 影子键后缀：主键 aqp:* 过期后，aqp:*:swr 再保留 stale_window 秒
STALE_SUFFIX = ":swr"

# 进程内防抖锁兜底（Redis 不可用时）：key -> 过期时间戳
_local_locks: dict[str, float] = {}
_LOCAL_LOCKS_GUARD = threading.Lock()


class RedisClient:
    """静态门面：所有方法均为 classmethod，调用方无需持有实例。"""

    _pool: ConnectionPool | None = None
    _client: Redis | None = None
    # ⚠️ redis.asyncio 连接不可跨事件循环复用：记录创建时的 loop，
    # 检测到 loop 变化（测试多次 asyncio.run / 脚本场景）时重建客户端。
    _loop_ref: asyncio.AbstractEventLoop | None = None

    # ---------------- 内部 ----------------
    @classmethod
    def _ensure(cls) -> Redis | None:
        """获取可用 Redis 客户端；不可用时返回 None（调用方降级）。"""
        s = get_settings()
        if not s.REDIS_ENABLED:
            return None
        # 熔断开启期间直接拒绝
        if _circuit_breaker["open_until"] > time.time():
            return None
        try:
            current_loop = asyncio.get_running_loop()
        except RuntimeError:
            return None  # 无运行中事件循环（同步上下文）-> 直接走 LRU
        if cls._client is None or cls._loop_ref is not current_loop:
            cls._pool = ConnectionPool(
                host=s.REDIS_HOST,
                port=s.REDIS_PORT,
                db=s.REDIS_DB,
                password=s.REDIS_PASSWORD,
                socket_timeout=s.REDIS_TIMEOUT,
                socket_connect_timeout=s.REDIS_TIMEOUT,
                max_connections=32,
                retry_on_timeout=True,
            )
            cls._client = Redis(connection_pool=cls._pool)
            cls._loop_ref = current_loop
        return cls._client

    @classmethod
    def _mark_fail(cls) -> None:
        """记录一次失败；达到阈值则打开熔断。"""
        _circuit_breaker["fail_count"] += 1
        if _circuit_breaker["fail_count"] >= FAIL_THRESHOLD:
            _circuit_breaker["open_until"] = time.time() + OPEN_SECONDS
            logger.warning(f"Redis circuit breaker OPEN for {OPEN_SECONDS}s")
            _circuit_breaker["fail_count"] = 0

    @classmethod
    def _mark_ok(cls) -> None:
        """记录一次成功：清零失败计数。"""
        _circuit_breaker["fail_count"] = 0

    # ---------------- 对外 API ----------------
    @classmethod
    async def get(cls, key: str) -> bytes | None:
        """读取缓存；Redis 不可用时读进程内 LRU。"""
        r = cls._ensure()
        if r is None:
            v = lru_get(key)
            return v if isinstance(v, (bytes, type(None))) else None
        try:
            v = await asyncio.wait_for(r.get(key), timeout=get_settings().REDIS_TIMEOUT)
            cls._mark_ok()
            return v if isinstance(v, bytes) else None
        except Exception as e:
            cls._mark_fail()
            logger.trace(f"redis.get fallback {key}: {e!r}")
            v = lru_get(key)
            return v if isinstance(v, (bytes, type(None))) else None

    @classmethod
    async def set(cls, key: str, value: bytes, ex: int = 3600,
                  stale_ex: int | None = None) -> None:
        """写入缓存：永远双写内存 LRU（保证降级时可读），Redis 失败不抛错。

        stale_ex 提供时额外写影子键（key:swr），TTL = stale_ex：
        主键 ex 秒后过期，影子键保留到 stale_ex，供 get_stale 回旧值。
        """
        lru_set(key, value, ttl=ex)
        if stale_ex is not None and stale_ex > ex:
            lru_set(key + STALE_SUFFIX, value, ttl=stale_ex)
        r = cls._ensure()
        if r is None:
            return
        try:
            await asyncio.wait_for(r.set(key, value, ex=ex), timeout=get_settings().REDIS_TIMEOUT)
            if stale_ex is not None and stale_ex > ex:
                await asyncio.wait_for(
                    r.set(key + STALE_SUFFIX, value, ex=stale_ex),
                    timeout=get_settings().REDIS_TIMEOUT)
            cls._mark_ok()
        except Exception as e:
            cls._mark_fail()
            logger.trace(f"redis.set fallback {key}: {e!r}")

    @classmethod
    async def get_stale(cls, key: str) -> tuple[bytes | None, bool]:
        """读取缓存（含 SWR 影子键），返回 ``(value, is_stale)``。

        主键命中 -> (value, False)；仅影子键命中 -> (value, True)
        （调用方应回旧值并后台重建）；两者都未命中 -> (None, False)。
        """
        v = await cls.get(key)
        if v is not None:
            return v, False
        v = await cls.get(key + STALE_SUFFIX)
        if v is not None:
            return v, True
        return None, False

    @classmethod
    def _try_lock_local(cls, key: str, ttl: int) -> bool:
        """进程内锁兜底：时间戳判过期；顺手清理过期项防 dict 膨胀。"""
        now = time.time()
        with _LOCAL_LOCKS_GUARD:
            if _local_locks.get(key, 0) > now:
                return False
            _local_locks[key] = now + ttl
            if len(_local_locks) > 1024:
                for k in [k for k, exp in _local_locks.items() if exp <= now]:
                    _local_locks.pop(k, None)
            return True

    @classmethod
    async def try_lock(cls, key: str, ttl: int = 5) -> bool:
        """抢防抖锁（SET NX EX）：True=抢到（调用方执行重算），False=已被占用。

        用于 refresh=1 合并（5s 内重复刷新只重算一次）与后台重建合并。
        Redis 不可用/失败时退化为进程内锁（单实例部署下语义等价）。
        """
        r = cls._ensure()
        if r is not None:
            try:
                ok = await asyncio.wait_for(
                    r.set(key, b"1", nx=True, ex=ttl), timeout=get_settings().REDIS_TIMEOUT)
                cls._mark_ok()
                return bool(ok)
            except Exception as e:
                cls._mark_fail()
                logger.trace(f"redis.try_lock fallback {key}: {e!r}")
        return cls._try_lock_local(key, ttl)

    @classmethod
    async def unlock(cls, key: str) -> None:
        """释放 try_lock 抢到的锁（本地 + Redis 双清）。"""
        with _LOCAL_LOCKS_GUARD:
            _local_locks.pop(key, None)
        r = cls._ensure()
        if r is None:
            return
        try:
            await asyncio.wait_for(r.delete(key), timeout=get_settings().REDIS_TIMEOUT)
        except Exception as e:
            cls._mark_fail()
            logger.trace(f"redis.unlock fallback {key}: {e!r}")

    @classmethod
    async def delete(cls, key: str) -> None:
        """删除缓存：内存 + Redis 双删（含 SWR 影子键，避免删主键后旧值仍可回）。"""
        lru_del(key)
        lru_del(key + STALE_SUFFIX)
        r = cls._ensure()
        if r is None:
            return
        try:
            await asyncio.wait_for(
                r.delete(key, key + STALE_SUFFIX), timeout=get_settings().REDIS_TIMEOUT)
        except Exception as e:
            cls._mark_fail()
            logger.trace(f"redis.delete fallback {key}: {e!r}")

    @classmethod
    async def ping(cls) -> bool:
        """健康探测：Redis 可用返回 True；不可用（含降级）返回 False。"""
        r = cls._ensure()
        if r is None:
            return False
        try:
            return bool(await asyncio.wait_for(r.ping(), timeout=0.5))
        except Exception:
            cls._mark_fail()
            return False

    @classmethod
    def breaker_status(cls) -> dict[str, Any]:
        """暴露熔断器状态，供健康检查 / 运维观测。"""
        return {
            "fail_count": _circuit_breaker["fail_count"],
            "open": _circuit_breaker["open_until"] > time.time(),
            "open_until": _circuit_breaker["open_until"],
        }
