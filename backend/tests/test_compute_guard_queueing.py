"""回归守卫：compute_guard 并发满额时**有界等待**而非立即拒绝。

覆盖：
- 并发超过上限时，第 N+1 个请求会等待空闲 slot 后成功（而非立即 40103）；
- 等待超过 ``COMPUTE_ACQUIRE_TIMEOUT_SECONDS`` 才抛 ERR_RATE_LIMITED；
- 获取路径不阻塞事件循环（acquire 在 to_thread 里，事件循环可持续调度）。
"""
from __future__ import annotations

import asyncio
import threading
from types import SimpleNamespace

import pytest

from app.core import compute_guard as cg
from app.core.errors import AQPException


def _settings(timeout: float) -> SimpleNamespace:
    return SimpleNamespace(COMPUTE_ACQUIRE_TIMEOUT_SECONDS=timeout, COMPUTE_CONCURRENCY=1)


async def test_waits_then_succeeds_when_slot_frees(monkeypatch):
    """占满唯一 slot 后，第二个请求等待（不立即失败），slot 释放后成功。"""
    monkeypatch.setattr(cg, "_slots", threading.BoundedSemaphore(1))
    monkeypatch.setattr(cg, "get_settings", lambda: _settings(2.0))
    sem = cg._slots
    assert sem.acquire(timeout=0.1) is True  # 手动占满唯一 slot

    order: list[str] = []

    async def _release_after_delay() -> None:
        await asyncio.sleep(0.2)
        sem.release()  # 释放后，等待中的 compute_slot 才能进入

    async def _use_slot() -> None:
        async for _ in cg.compute_slot():
            order.append("acquired")

    await asyncio.gather(_release_after_delay(), _use_slot())
    assert order == ["acquired"], "应在等待到空闲 slot 后成功，而不是立即失败"


async def test_times_out_after_bounded_wait(monkeypatch):
    """等待超过上限才抛 ERR_RATE_LIMITED（真正超时）。"""
    monkeypatch.setattr(cg, "_slots", threading.BoundedSemaphore(1))
    monkeypatch.setattr(cg, "get_settings", lambda: _settings(0.15))
    sem = cg._slots
    assert sem.acquire(timeout=0.1) is True

    with pytest.raises(AQPException) as exc_info:
        async for _ in cg.compute_slot():
            pass
    assert exc_info.value.code == 40103
    sem.release()


async def test_event_loop_not_blocked_while_waiting(monkeypatch):
    """等待期间事件循环仍可调度其它协程（证明 acquire 未在 loop 内阻塞）。"""
    monkeypatch.setattr(cg, "_slots", threading.BoundedSemaphore(1))
    monkeypatch.setattr(cg, "get_settings", lambda: _settings(1.0))
    sem = cg._slots
    assert sem.acquire(timeout=0.1) is True

    ticks = 0

    async def _ticker() -> None:
        nonlocal ticks
        for _ in range(5):
            await asyncio.sleep(0.02)
            ticks += 1

    async def _waiter() -> None:
        async for _ in cg.compute_slot():
            pass

    loop = asyncio.get_running_loop()
    release_at = loop.time() + 0.1
    loop.call_later(0.1, sem.release)  # 稍后释放，让 waiter 成功
    await asyncio.gather(_ticker(), _waiter())
    assert ticks >= 3, f"等待期间事件循环应继续运行，ticks={ticks}"
    assert loop.time() >= release_at
