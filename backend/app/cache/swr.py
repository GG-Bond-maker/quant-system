"""stale-while-revalidate + refresh=1 统一封装（路线图 L1-2 / §3.2，Sprint1）。

语义（验收口径）：
1. 主键命中 -> 直接返回（from_cache=True）；
2. 主键过期但影子键（stale 窗口内）仍命中 -> 回旧值并标 stale=True，
   同时后台重建回写；rebuild:{key} 锁保证并发下只重建一次；
3. 全部未命中 -> 同步重建（与原缓存 miss 路径相同）；
4. refresh=1 -> 抢 refresh:{key} 锁（TTL 5s）防抖：抢到则同步重算回写并标
   from_cache="refreshed"；未抢到说明 5s 内已有人刷新，回当前缓存值并标
   refreshed_recently=True。

红线：refresh 只绕过缓存**读**，不绕过限速（_throttle）与质量门禁——
重建路径与普通 miss 完全相同，限速在数据层生效。
"""
from __future__ import annotations

import asyncio
from typing import Any, Awaitable, Callable

import orjson
from loguru import logger

from .redis_client import RedisClient

# 后台重建任务引用集：防止 create_task 无引用被 GC 中途取消
_bg_tasks: set[asyncio.Task] = set()


async def write_cache(key: str, data: dict[str, Any], ttl: int,
                      stale_window: int = 0) -> None:
    """重算结果回写：主键 TTL=ttl；影子键 TTL=ttl+stale_window（SWR 供旧值）。"""
    await RedisClient.set(key, orjson.dumps(data), ex=ttl,
                          stale_ex=ttl + stale_window if stale_window > 0 else None)


def _spawn_rebuild(
    key: str,
    build: Callable[[], Awaitable[dict[str, Any]]],
    ttl: int,
    stale_window: int,
    lock_ttl: int,
    after_build: Callable[[dict[str, Any]], Awaitable[None]] | None,
) -> None:
    """后台重建：抢 rebuild:{key} 锁（默认 120s，覆盖 overview ~50s 量级的构建），
    未抢到说明已有并发重建在跑，直接放弃。"""

    async def _run() -> None:
        lock_key = f"rebuild:{key}"
        if not await RedisClient.try_lock(lock_key, ttl=lock_ttl):
            return
        try:
            data = await build()
            await write_cache(key, data, ttl, stale_window)
            if after_build is not None:
                try:
                    await after_build(data)
                except Exception as e:  # noqa: BLE001 副作用失败不影响缓存回写成果
                    logger.warning(f"[swr] after_build {key} failed: {e!r}")
        except Exception as e:  # noqa: BLE001 后台重建失败只记日志，旧值仍在服务
            logger.warning(f"[swr] background rebuild {key} failed: {e!r}")
        finally:
            await RedisClient.unlock(lock_key)

    t = asyncio.get_running_loop().create_task(_run())
    _bg_tasks.add(t)
    t.add_done_callback(_bg_tasks.discard)


async def cached_or_build(
    key: str,
    build: Callable[[], Awaitable[dict[str, Any]]],
    *,
    ttl: int = 300,
    stale_window: int = 1800,
    refresh: int = 0,
    rebuild_lock_ttl: int = 120,
    on_metrics: Callable[[str], None] | None = None,
    after_build: Callable[[dict[str, Any]], Awaitable[None]] | None = None,
) -> dict[str, Any]:
    """读缓存 -> stale 回旧值 -> refresh 防抖 -> 同步重建 的统一入口。

    Args:
        key: 缓存键（keys.py 集中生成）。
        build: 重算协程（网络阻塞应已在内部 to_thread）。
        ttl: 主键缓存秒数。
        stale_window: 软过期后的旧值保留窗口秒数（硬过期阈值 = ttl + stale_window）。
        refresh: 端点的 refresh 查询参数（0/1）。
        rebuild_lock_ttl: 后台重建锁秒数（需覆盖最坏重建耗时）。
        on_metrics: 命中观测回调，入参 "hit"（含 stale）/ "miss"（同步重建）。
        after_build: 重建成功后的副作用（如 FeatureRun 落库）；失败只记日志。

    Returns:
        已标注 from_cache / stale / refreshed_recently 的响应数据。
    """

    def _metrics(kind: str) -> None:
        if on_metrics is not None:
            try:
                on_metrics(kind)
            except Exception:  # noqa: BLE001 埋点失败不影响主流程
                pass

    # ---- 正常读：主键命中直接回；影子键命中回旧值 + 后台重建 ----
    if not refresh:
        val, stale = await RedisClient.get_stale(key)
        if val is not None:
            data = orjson.loads(val)
            data["from_cache"] = True
            if stale:
                data["stale"] = True
                _spawn_rebuild(key, build, ttl, stale_window,
                               rebuild_lock_ttl, after_build)
            _metrics("hit")
            return data

    # ---- refresh=1：防抖锁，5s 内重复刷新合并为一次重算 ----
    if refresh:
        if not await RedisClient.try_lock(f"refresh:{key}", ttl=5):
            val, stale = await RedisClient.get_stale(key)
            if val is not None:
                data = orjson.loads(val)
                data["from_cache"] = True
                data["refreshed_recently"] = True
                if stale:
                    data["stale"] = True
                return data
            # 锁被占但缓存全空（极端并发）：退化为普通重建，不返回空响应

    # ---- 同步重建（缓存 miss / refresh 抢到锁 / refresh 且无缓存可回） ----
    _metrics("miss")
    data = await build()
    await write_cache(key, data, ttl, stale_window)
    if after_build is not None:
        try:
            await after_build(data)
        except Exception as e:  # noqa: BLE001 副作用失败不影响响应
            logger.warning(f"[swr] after_build {key} failed: {e!r}")
    data["from_cache"] = "refreshed" if refresh else False
    return data
