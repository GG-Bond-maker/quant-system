"""公开计算接口的进程内资源闸门。"""
from __future__ import annotations

import threading
from collections.abc import AsyncIterator

from .config import get_settings
from .errors import AQPException, ERR_RATE_LIMITED

_slots = threading.BoundedSemaphore(get_settings().COMPUTE_CONCURRENCY)


async def compute_slot() -> AsyncIterator[None]:
    """限制同步计算线程数量，避免重复提交耗尽 API 进程资源。"""
    if not _slots.acquire(blocking=False):
        raise AQPException(ERR_RATE_LIMITED, "计算资源繁忙，请稍后重试", {"retryable": True})
    try:
        yield
    finally:
        _slots.release()
