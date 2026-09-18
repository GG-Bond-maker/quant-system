"""公开计算接口的进程内资源闸门。"""
from __future__ import annotations

import asyncio
import threading
from collections.abc import AsyncIterator

from .config import get_settings
from .errors import AQPException, ERR_RATE_LIMITED

_slots = threading.BoundedSemaphore(get_settings().COMPUTE_CONCURRENCY)


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
    timeout = get_settings().COMPUTE_ACQUIRE_TIMEOUT_SECONDS
    # sem.acquire(timeout=...) 默认 blocking=True：在 timeout 秒内等到则返回 True，
    # 超时返回 False（不抛异常），因此可直接 await 线程池结果。
    acquired = await asyncio.to_thread(_slots.acquire, True, timeout)
    if not acquired:
        raise AQPException(ERR_RATE_LIMITED, "计算资源繁忙，请稍后重试", {"retryable": True})
    try:
        yield
    finally:
        _slots.release()
