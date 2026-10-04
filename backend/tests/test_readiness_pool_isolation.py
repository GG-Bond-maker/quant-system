"""readiness 探针的**线程池隔离**回归（P-1，2026-10-04 上线前全检）。

## 缺陷

``/health/ready`` 的探测体（``main._probe_readiness_sync``）走
``asyncio.to_thread`` ⇒ 排在**全站默认池**（``min(32, cpu+4)``，本机 18 核 ⇒ 22 槽）
的队尾。默认池里同时挤着大量未迁移的重计算（datacenter / desk / research /
studio / screener / ops / alerts / quotes_hub），于是并发冷算一上来就把探针**饿死**。

实测（``qa-lead-poolexhaust.log`` / ``qa-lead-steady.log`` / ``qa-lead-anon-dos2.log``）::

    cpu_count=18  默认 executor max_workers=22
    [0] 基线       /health/ready -> 200      12.5ms
    [1] 22 路并发冷算
    [2] 压力中     /health/live  -> 200      10.4ms   （不走线程池，对照）
                  /health/ready -> EXC   12008.1ms ReadTimeout  ⚠️ 挂死

而 ``docker-compose.yml`` 的探针超时是 **5s** ⇒ 容器被判 unhealthy、被摘流量甚至
重启 —— 而平台契约"HTTP 恒 200"让监控完全看不见。

## 修好之后

探针走**专用 2 线程池**（``main._get_readiness_executor``），其排队长度与业务
重活彻底解耦。

覆盖：
1. 探针池与 event loop 默认池**不是同一个对象**（隔离的直接证据）；
2. 探针池容量固定为 2（足够廉价探测，且不会抢占业务 CPU）；
3. 默认池被占满时，探针池仍能立刻执行（不共享队列）；
4. 收尾后可重建（同一进程多次进出 lifespan 不会永久失效）。
"""
from __future__ import annotations

import asyncio
import concurrent.futures
import sys
import threading
import time
from pathlib import Path

BACKEND_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(BACKEND_ROOT))

from app.main import (  # noqa: E402
    _get_readiness_executor,
    _shutdown_readiness_executor,
)


def test_readiness_executor_is_separate_from_default_pool():
    """探针池必须是**独立对象**，不能复用 event loop 的默认池。

    这条是隔离的核心断言：只要它们是同一个对象，探针就仍会和业务重活排队，
    P-1 等于没修。
    """
    ex = _get_readiness_executor()
    try:
        async def _get_default():
            loop = asyncio.get_running_loop()
            # 触发默认 executor 的惰性创建（CPython 3.11 起 _default_executor 懒建）
            await loop.run_in_executor(None, lambda: None)
            return loop._default_executor  # noqa: SLF001

        default = asyncio.run(_get_default())
        assert default is not None, "未能取到默认 executor（测试前提失效）"
        assert ex is not default, "探针池与默认池是同一个对象 ⇒ 隔离失效"
    finally:
        _shutdown_readiness_executor()


def test_readiness_executor_capacity_is_two():
    """容量固定为 2：探测廉价，且刻意不抢占业务线程。"""
    ex = _get_readiness_executor()
    try:
        assert ex._max_workers == 2, f"探针池应为 2 线程，实际 {ex._max_workers}"  # noqa: SLF001
    finally:
        _shutdown_readiness_executor()


def test_probe_runs_while_default_pool_is_saturated():
    """默认池被 22 个长任务占满时，探针池仍能立刻执行（不共享队列）。

    复刻实测场景但不依赖时序运气：直接**占满**默认池的全部 worker
    （每个都阻塞在一个 Event 上），再提交探针。若探针排在默认池后面，
    它会一直等（本用例给 2s 硬上限）；隔离正确时它几乎立刻返回。
    """
    ex = _get_readiness_executor()
    release = threading.Event()
    default: concurrent.futures.ThreadPoolExecutor | None = None
    try:
        async def _saturate_and_probe():
            nonlocal default
            loop = asyncio.get_running_loop()
            await loop.run_in_executor(None, lambda: None)   # 确保默认池已建
            default = loop._default_executor  # noqa: SLF001
            workers = default._max_workers  # noqa: SLF001
            # 提交 workers 个**占满槽位**的任务；不 await，让它们挂住
            for _ in range(workers):
                loop.run_in_executor(None, release.wait, 5.0)
            await asyncio.sleep(0.15)  # 给它们一点时间真正占住槽位

            # 现在默认池已满；探针必须走自己的池
            t0 = time.perf_counter()
            got = await loop.run_in_executor(ex, lambda: "probe-ok")
            return got, time.perf_counter() - t0

        async def _bounded():
            # ⚠️ asyncio.wait_for 必须被 await（它本身是协程）；写成
            # asyncio.run(asyncio.wait_for(coro, ...)) 会抛
            # "cannot unpack non-iterable coroutine object"。
            return await asyncio.wait_for(_saturate_and_probe(), timeout=10)

        got, elapsed = asyncio.run(_bounded())
        assert got == "probe-ok"
        assert elapsed < 2.0, (
            f"默认池占满时探针耗时 {elapsed:.2f}s —— 说明它仍在默认池排队（隔离失效）")
    finally:
        release.set()                       # 放掉所有占位任务
        _shutdown_readiness_executor()


def test_executor_rebuildable_after_shutdown():
    """收尾后必须能重建：同一进程多次进出 lifespan（测试常见）不能永久失效。"""
    first = _get_readiness_executor()
    _shutdown_readiness_executor()
    second = _get_readiness_executor()
    try:
        assert second is not first, "shutdown 后应新建实例（否则再 submit 会 RuntimeError）"
        assert second.submit(lambda: 1).result(timeout=5) == 1
    finally:
        _shutdown_readiness_executor()
