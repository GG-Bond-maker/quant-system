"""公开计算接口的进程内资源闸门。"""
from __future__ import annotations

import asyncio
import threading
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from .config import get_settings
from .errors import AQPException, ERR_RATE_LIMITED

# 信号量**惰性**创建（P-3 修复，2026-10-04 上线前全检）。
#
# 缺陷：原先是 ``_slots = threading.BoundedSemaphore(get_settings().COMPUTE_CONCURRENCY)``
# —— 在**导入期**取一次配置。于是
#   ① 改 ``.env`` / 环境变量后必须重建进程才生效（部署时极易误判"改了没用"）；
#   ② 测试若先导入了本模块，之后再 monkeypatch 配置也不生效。
# 叠加当时 ``COMPUTE_CONCURRENCY`` 被 ``le=2`` 硬顶，实际并发恒为 2，
# 而 ``compute_pool`` 是 6 —— 两套闸门语义不一致，运维按后者调优却对前者无效。
#
# 现在：首次真正用到时才建，之后复用（不重建 ⇒ 已持有的 slot 不会错乱）。
# ⚠️ ``_slots`` 保持为**模块属性**，测试可直接
# ``monkeypatch.setattr(cg, "_slots", sem)`` 注入；下面的判据是 ``_slots is None``，
# 故注入值会被原样使用（不会被惰性创建覆盖）。
_slots: threading.BoundedSemaphore | None = None
_slots_lock = threading.Lock()


def _get_slots() -> threading.BoundedSemaphore:
    """返回（必要时创建）重计算信号量。

    ``_slots is None`` 是唯一判据 —— 既覆盖"尚未创建"，也天然尊重测试注入的实例。
    """
    global _slots
    if _slots is not None:
        return _slots
    with _slots_lock:
        if _slots is None:
            _slots = threading.BoundedSemaphore(
                max(1, int(get_settings().COMPUTE_CONCURRENCY)))
        return _slots


def reset_compute_slots() -> None:
    """丢弃信号量，使下次使用时按当前配置重建（测试复位 / 配置热变更用）。

    ⚠️ 仅在**确认没有在途请求持有 slot** 时调用；否则重建会让旧持有者的
    ``release()`` 打到新信号量上，导致额度虚增。
    """
    global _slots
    with _slots_lock:
        _slots = None


async def _acquire(timeout: float) -> threading.BoundedSemaphore | None:
    """在线程池里阻塞等待一个 slot；超时返回 ``None``（不抛异常）。

    ⚠️ ``threading.Semaphore.acquire`` 是**阻塞**调用，**绝不能**在事件循环线程里
    直接执行（会卡住整个 loop）；必须经 ``asyncio.to_thread`` 挪到线程池。

    返回**拿到的那个信号量实例**（而不是布尔值）：释放时用它，避免"等待期间配置
    被重建"导致 release 打到另一个信号量上。
    """
    sem = _get_slots()
    acquired = await asyncio.to_thread(sem.acquire, True, timeout)
    return sem if acquired else None


@asynccontextmanager
async def compute_slot_ctx(*, timeout: float | None = None) -> AsyncIterator[None]:
    """:func:`compute_slot` 的**参数化**变体：可自定义等待时长。

    与 :func:`compute_slot` 共用同一个模块级信号量，因此两者**互相排队**
    （同一道闸门）。``timeout`` 为 ``None`` 时读
    ``COMPUTE_ACQUIRE_TIMEOUT_SECONDS``（与 :func:`compute_slot` 一致）。

    并发满额且等待超时 ⇒ 抛 ``ERR_RATE_LIMITED``（与 :func:`compute_slot` 同款
    code/message/context）。获取/释放语义与 :func:`compute_slot` 完全一致。
    """
    if timeout is None:
        timeout = get_settings().COMPUTE_ACQUIRE_TIMEOUT_SECONDS
    sem = await _acquire(timeout)
    if sem is None:
        raise AQPException(ERR_RATE_LIMITED, "计算资源繁忙，请稍后重试", {"retryable": True})
    try:
        yield
    finally:
        sem.release()


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
    sem = await _acquire(timeout)
    if sem is None:
        yield False
        return
    try:
        yield True
    finally:
        sem.release()


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
