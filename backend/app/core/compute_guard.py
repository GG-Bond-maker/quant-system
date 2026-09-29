"""公开计算接口的进程内资源闸门。"""
from __future__ import annotations

import asyncio
import threading
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from .config import get_settings
from .errors import AQPException, ERR_RATE_LIMITED

_slots = threading.BoundedSemaphore(get_settings().COMPUTE_CONCURRENCY)


async def _acquire(timeout: float) -> bool:
    """在线程池里阻塞等待一个 slot；超时返回 ``False``（不抛异常）。

    ⚠️ ``threading.Semaphore.acquire`` 是**阻塞**调用，**绝不能**在事件循环线程里
    直接执行（会卡住整个 loop）；必须经 ``asyncio.to_thread`` 挪到线程池。
    """
    # sem.acquire(timeout=...) 默认 blocking=True：在 timeout 秒内等到则返回 True，
    # 超时返回 False（不抛异常），因此可直接 await 线程池结果。
    return await asyncio.to_thread(_slots.acquire, True, timeout)


@asynccontextmanager
async def compute_slot_ctx(*, timeout: float | None = None) -> AsyncIterator[None]:
    """:func:`compute_slot` 的**参数化**变体：可自定义等待时长。

    与 :func:`compute_slot` 共用同一个模块级 ``_slots``，因此两者**互相排队**
    （同一道闸门）。``timeout`` 为 ``None`` 时读
    ``COMPUTE_ACQUIRE_TIMEOUT_SECONDS``（与 :func:`compute_slot` 一致）。

    并发满额且等待超时 ⇒ 抛 ``ERR_RATE_LIMITED``（与 :func:`compute_slot` 同款
    code/message/context）。获取/释放语义与 :func:`compute_slot` 完全一致。
    """
    if timeout is None:
        timeout = get_settings().COMPUTE_ACQUIRE_TIMEOUT_SECONDS
    acquired = await _acquire(timeout)
    if not acquired:
        raise AQPException(ERR_RATE_LIMITED, "计算资源繁忙，请稍后重试", {"retryable": True})
    try:
        yield
    finally:
        _slots.release()


@asynccontextmanager
async def compute_slot_optional(*, timeout: float | None = None) -> AsyncIterator[bool]:
    """**可选** slot：拿不到就 ``yield False``（跳过本轮），而不是抛错。

    供后台路径（启动/周期预热等）使用：这些场景"这一轮先跳过"比"抛错"更合适。
    ``timeout`` 为 ``None`` 时读 ``COMPUTE_ACQUIRE_TIMEOUT_SECONDS``。

    - 超时：``yield False`` 后直接返回，**不**释放（本就没拿到）；
    - 成功：``yield True``，``finally`` 释放 slot。
    """
    if timeout is None:
        timeout = get_settings().COMPUTE_ACQUIRE_TIMEOUT_SECONDS
    acquired = await _acquire(timeout)
    if not acquired:
        yield False
        return
    try:
        yield True
    finally:
        _slots.release()


async def compute_slot() -> AsyncIterator[None]:
    """限制同步计算线程数量，避免重复提交耗尽 API 进程资源。

    并发满额时**有界等待**空闲 slot（默认 10s，见 ``COMPUTE_ACQUIRE_TIMEOUT_SECONDS``），
    而不是一到上限就立刻拒绝——前端 ``ComputeQueue`` 已把请求串行化，后端若立即
    抛 ``ERR_RATE_LIMITED`` 会让「前端已排好队」的用户仍被直接拒。只有真正等待
    超时才抛错。

    ⚠️ 这是 ``async def`` 依赖生成器：**禁止**在事件循环里直接阻塞
    ``threading.Semaphore.acquire``（会卡住整个 loop）。用 ``asyncio.to_thread``
    把阻塞等待挪到线程池；``acquire(timeout=...)`` 在超时返回 ``False`` 而非抛错。
    """
    async with compute_slot_ctx():
        yield
