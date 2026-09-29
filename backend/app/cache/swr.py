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

from ..core.resilience import is_fatal_base_exception, log_contained
from .redis_client import RedisClient

# 后台重建任务引用集：防止 create_task 无引用被 GC 中途取消
_bg_tasks: set[asyncio.Task] = set()

# ---------------- 降级 / 空态缓存策略（集中定义，便于后续统一调整） ----------------
# 背景：一次外部源抖动若把「不可用」空态按正常 TTL 落地，该键会在整个 TTL 内
# 持续返回空态且不自愈（实测 TTL：screener/market/overview=300s，stocks/watchlist=60s，
# datacenter=120s；stock.north=21600s、stock.events=3600s 更甚）。因此：
#   - status == "unavailable" ：**不写缓存**，下一次请求立即重新构建（快速自愈）；
#   - status == "degraded"    ：用**很短的 TTL**（≤ DEGRADED_TTL_SECONDS）落地，
#                               且不写影子键（避免空态被 SWR 再次读出）；
#   - 无 status 字段 / 其它值  ：按调用方 ttl 正常落地（向后兼容）。
# 阈值集中在下方常量，调参只改这里。
UNAVAILABLE_STATUS = "unavailable"
DEGRADED_STATUS = "degraded"
DEGRADED_TTL_SECONDS = 15      # degraded 落地 TTL 上限（≤15s，尽快自愈）
DEGRADED_STALE_WINDOW = 0      # degraded 不留影子键（不供旧值）


def default_cacheable(data: Any) -> bool:
    """默认有效性谓词：``status == "unavailable"`` 视为不可缓存，其余可缓存。

    这是 ``write_cache`` / ``cached_or_build`` 的默认谓词——**无 status 字段的
    载荷一律视为有效**（历史调用方行为完全不变，向后兼容）。
    """
    if not isinstance(data, dict):
        return True
    return data.get("status") != UNAVAILABLE_STATUS


def _effective_ttl(data: Any, ttl: int, stale_window: int) -> tuple[int, int]:
    """按状态收敛生效 TTL：degraded 用短 TTL 且不写影子键。"""
    if isinstance(data, dict) and data.get("status") == DEGRADED_STATUS:
        return min(ttl, DEGRADED_TTL_SECONDS), DEGRADED_STALE_WINDOW
    return ttl, stale_window


async def write_cache(key: str, data: dict[str, Any], ttl: int,
                      stale_window: int = 0, *,
                      is_cacheable: Callable[[Any], bool] | None = None) -> None:
    """重算结果回写：主键 TTL=ttl；影子键 TTL=ttl+stale_window（SWR 供旧值）。

    Args:
        key: 缓存键。
        data: 待回写载荷（dict）。
        ttl: 主键秒数。
        stale_window: 影子键额外保留秒数。
        is_cacheable: 有效性谓词；默认 ``default_cacheable``（unavailable 不落地）。
            谓词返回 False 时**跳过整次回写**（不写主键，也不写影子键）。
    """
    predicate = is_cacheable or default_cacheable
    try:
        cacheable = predicate(data)
    except Exception as e:  # noqa: BLE001 谓词异常按「可缓存」处理，不阻断回写
        logger.warning(f"[swr] is_cacheable {key} raised: {e!r}（按可缓存处理）")
        cacheable = True
    if not cacheable:
        logger.info(f"[swr] skip caching non-cacheable payload: {key} "
                    f"(status={data.get('status') if isinstance(data, dict) else None!r})")
        return
    ttl, stale_window = _effective_ttl(data, ttl, stale_window)
    await RedisClient.set(key, orjson.dumps(data), ex=ttl,
                          stale_ex=ttl + stale_window if stale_window > 0 else None)


def _spawn_rebuild(
    key: str,
    build: Callable[[], Awaitable[dict[str, Any]]],
    ttl: int,
    stale_window: int,
    lock_ttl: int,
    after_build: Callable[[dict[str, Any]], Awaitable[None]] | None,
    is_cacheable: Callable[[Any], bool] | None = None,
) -> None:
    """后台重建：抢 rebuild:{key} 锁（默认 120s，覆盖 overview ~50s 量级的构建），
    未抢到说明已有并发重建在跑，直接放弃。"""

    async def _run() -> None:
        lock_key = f"rebuild:{key}"
        if not await RedisClient.try_lock(lock_key, ttl=lock_ttl):
            return
        try:
            data = await build()
            await write_cache(key, data, ttl, stale_window,
                              is_cacheable=is_cacheable)
            if after_build is not None:
                try:
                    await after_build(data)
                except Exception as e:  # noqa: BLE001 副作用失败不影响缓存回写成果
                    logger.warning(f"[swr] after_build {key} failed: {e!r}")
        except Exception as e:  # noqa: BLE001 后台重建失败只记日志，旧值仍在服务
            logger.warning(f"[swr] background rebuild {key} failed: {e!r}")
        except BaseException as exc:  # noqa: BLE001 panic 等非 Exception 兜底
            # [AQP panic 收口 D] 兜住并留痕；**不吞掉**下方 finally —— unlock 仍执行。
            if is_fatal_base_exception(exc):
                raise
            log_contained("swr_rebuild", exc)
        finally:
            await RedisClient.unlock(lock_key)

    t = asyncio.get_running_loop().create_task(_run())
    _bg_tasks.add(t)
    t.add_done_callback(_bg_tasks.discard)


# ---------------- single-flight：请求路径的冷启动惊群保护 ----------------
# 背景（2026-09-27，/ops/lineage 实测）：冷缓存下 N 路并发**各自**跑一次全量重建。
# 单请求冷扫 29.3s，4 路并发因磁盘争用劣化到 60.9~63.0s（> 前端 60s 预算 ⇒ 全部超时），
# 且重复 IO 是纯粹的浪费。此处让**同 key 只放行一次重建**，其余调用等待并共享结果。
# 与 ``_spawn_rebuild`` 分工互补：那里合并的是 stale 路径的**后台**重建，
# 这里合并的是全 miss / 冷启动时**请求路径**的同步重建。
_inflight: dict[str, asyncio.Task] = {}


async def _single_flight(
    key: str,
    build: Callable[[], Awaitable[dict[str, Any]]],
    ttl: int,
    stale_window: int,
    after_build: Callable[[dict[str, Any]], Awaitable[None]] | None,
    is_cacheable: Callable[[Any], bool] | None,
) -> dict[str, Any]:
    """同 key 并发只执行一次 ``build``，其余调用等待并共享同一结果。

    Why ``Task`` + ``shield``：
        - 重建放进独立 Task，生命周期**不绑定任何单个请求**：某等待方被取消
          （客户端断开）不会连坐取消重建，缓存照常回写；
        - 等待方 ``await asyncio.shield(task)``，取消等待方不波及共享任务；
        - 任务内部完成 ``write_cache`` / ``after_build``，因此即使发起方中途取消，
          缓存回写也不会丢（否则冷扫 30s 的成果会随一次断连被丢弃）；
        - 完成后按**身份**从表中摘除，避免摘掉后来者新建的任务；
        - 回调里消费一次 ``exception()``，避免无人 await 时
          "Task exception was never retrieved" 告警。

    Returns:
        每个调用方各自的**浅拷贝**——调用方会写入 from_cache/stale 标记，
        共享同一 dict 会让并发响应互相污染。
    """

    async def _run() -> dict[str, Any]:
        data = await build()
        await write_cache(key, data, ttl, stale_window, is_cacheable=is_cacheable)
        if after_build is not None:
            try:
                await after_build(data)
            except Exception as e:  # noqa: BLE001 副作用失败不影响缓存回写成果
                logger.warning(f"[swr] after_build {key} failed: {e!r}")
        return data

    task = _inflight.get(key)
    if task is None:
        # 无 await 插入：get 与 set 之间不会被事件循环切换，故「谁当 leader」是原子的。
        task = asyncio.get_running_loop().create_task(_run())
        _inflight[key] = task

        def _cleanup(t: asyncio.Task) -> None:
            if _inflight.get(key) is t:
                _inflight.pop(key, None)
            if not t.cancelled():
                t.exception()  # 消费异常，避免 never-retrieved 告警

        task.add_done_callback(_cleanup)
    return dict(await asyncio.shield(task))


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
    is_cacheable: Callable[[Any], bool] | None = None,
    background_build: Callable[[], Awaitable[dict[str, Any]]] | None = None,
    single_flight: bool = False,
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
        is_cacheable: 有效性谓词（默认 ``default_cacheable``：unavailable 空态不落地，
            degraded 用短 TTL）；缺省即套用内置降级策略，无 status 载荷行为不变。
        background_build: **仅供后台重建（stale-while-revalidate）路径**使用的
            重算协程。缺省 None = 与改动前行为完全一致（后台重建复用 ``build``）。
        single_flight: 请求路径的**冷启动惊群保护**（默认 False = 行为与改动前完全一致）。
            置 True 时，全 miss / 冷启动下同 key 的并发请求**只跑一次** ``build``，
            其余等待并共享结果（见 :func:`_single_flight`）。适用前提：``build`` 无
            请求级私有状态、同 key 结果对并发调用方一致。``_build_lineage`` 属此类。

    Why ``background_build``（2026-09-19）:
        请求路径与后台重建路径的**时延约束根本不同**。请求路径必须有严格预算
        —— 宁可返回降级载荷也不能把用户挂在慢源上；而**后台重建没有这个约束**
        —— 它正是唯一能把"好数据"重新灌回缓存的路径（``warm_overview_cache``
        之外），若继承请求预算，慢构建（market/overview 实测 23.5~113.5s）会
        100% 超时，后台重建每轮只写回 unavailable 降级载荷 ⇒ 缓存永不自愈，
        用户每次请求都打回冷路径。故两者必须可分离。

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
                # 后台重建用 background_build（若提供）；缺省时与改动前完全一致（复用 build）。
                _spawn_rebuild(key, background_build if background_build is not None else build,
                               ttl, stale_window,
                               rebuild_lock_ttl, after_build, is_cacheable)
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
    if single_flight:
        # 冷启动惊群：同 key 只放行一次重建，其余等待并共享结果；
        # 回写与 after_build 由共享任务内部完成，避免重复执行副作用。
        data = await _single_flight(key, build, ttl, stale_window,
                                    after_build, is_cacheable)
        data["from_cache"] = "refreshed" if refresh else False
        return data
    data = await build()
    await write_cache(key, data, ttl, stale_window, is_cacheable=is_cacheable)
    if after_build is not None:
        try:
            await after_build(data)
        except Exception as e:  # noqa: BLE001 副作用失败不影响响应
            logger.warning(f"[swr] after_build {key} failed: {e!r}")
    data["from_cache"] = "refreshed" if refresh else False
    return data
