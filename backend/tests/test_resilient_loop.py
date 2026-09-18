"""panic 收口 D 段回归：后台长驻循环的 ``BaseException`` 韧性兜底
（``app/core/resilience.py`` + 7 个站点）。

为什么需要 D 段
--------------
B 段 ``core/panic_guard.py`` 是 **ASGI 中间件**，只覆盖 HTTP 请求路径。本项目的
长驻后台循环（``asyncio.create_task`` 协程 / daemon 线程）**不经 ASGI 中间件栈**
⇒ B 段一个都兜不到；而这些循环只写 ``except Exception``，polars 在
``dtype == pl.Null`` 列上做单列 ``sort`` 抛的 ``pyo3_runtime.PanicException``
（``BaseException`` 子类）会被**全部漏接** ⇒ 协程/线程**永久停摆、且无任何日志**。

两条硬边界（本文件的核心断言）
----------------------------
1. ``asyncio.CancelledError`` / ``KeyboardInterrupt`` / ``SystemExit`` /
   ``GeneratorExit`` **必须放行**（不被兜住）—— CancelledError 是 lifespan 优雅
   关闭的机制，兜住会导致进程关不掉；
2. ``finally`` **语义不变** —— ``_sync_worker``（复位 ``_sync.running`` / 终态落库）
   与 ``swr._run``（``unlock``）的 finally 必须仍然执行。

⚠️ 日志落盘的坑（B 段已踩，此处必须复现并防住）
--------------------------------------------
本仓 ``core/logging.py`` 三个 sink 全部 ``enqueue=True``（L46/L59/L72）⇒ 每条 record
要 **pickle** 才能投递，而 ``PanicException`` **不可 pickle** ⇒ 若用
``logger.opt(exception=True)``，**整条日志连同堆栈一起被丢弃**。故
``resilience.log_contained`` 必须先把堆栈 format 成**字符串**再记。本文件用
``enqueue=True`` 的临时 sink 复现生产约束，并**读日志文件内容**断言
``[resilient-loop]`` 与 ``PanicException`` 真的落盘（不只断言调用了 logger）。

隔离：不联网；日志文件在 tmp_path；指标用 ``generate_latest()`` 读真实值。
"""
from __future__ import annotations

import asyncio
import contextlib
import sys
import time
from pathlib import Path
from types import SimpleNamespace

import polars as pl
import pytest
from loguru import logger
from prometheus_client import generate_latest

BACKEND_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(BACKEND_ROOT))

from app.api.v1 import alerts as alerts_mod  # noqa: E402
from app.cache import swr as swr_mod  # noqa: E402
from app.core.resilience import (LOG_PREFIX, format_exception_stack,  # noqa: E402
                                 is_fatal_base_exception, log_contained)
from app.jobs import evening_routine as evening_mod  # noqa: E402
from app.services import sync_service as sync_mod  # noqa: E402


# ------------------------------------------------------------------ 测试工具件
def _real_panic() -> BaseException:
    """制造一个**真实**的 polars Rust panic（不用自定义类冒充）。

    多列帧 + ``dtype=pl.Null`` 的 ``pred_score`` 上做**单列** sort ⇒ PanicException
    （单列帧会走 fast-path 不 panic，故这里必须是多列帧）。
    """
    try:
        pl.DataFrame({
            "symbol": ["600000.SH", "600001.SH"],
            "pred_score": pl.Series([None, None], dtype=pl.Null),
        }).sort("pred_score")
    except BaseException as exc:  # noqa: BLE001 PanicException 是 BaseException
        assert not isinstance(exc, Exception), "必须是不可捕获的 PanicException"
        return exc
    raise AssertionError("未触发 panic（polars 行为可能已变化）")


def _loop_metric(loop_name: str) -> float:
    """读 ``aqp_loop_panic_contained_total{loop="..."}`` 的**真实**当前值。"""
    needle = f'aqp_loop_panic_contained_total{{loop="{loop_name}"}}'
    for line in generate_latest().decode("utf-8").splitlines():
        if line.startswith(needle):
            return float(line.rsplit(" ", 1)[-1])
    return 0.0


@pytest.fixture
def log_capture(tmp_path):
    """临时 loguru sink（**enqueue=True**，复刻生产 sink 的 pickle 约束）。

    Returns:
        ``(log_path, read)``：``read()`` 先 ``logger.complete()`` 刷队列再读文件文本。
    """
    log_file = tmp_path / "resilience.log"
    sink_id = logger.add(str(log_file), level="DEBUG", format="{message}",
                         enqueue=True, encoding="utf-8")

    def _read() -> str:
        try:
            logger.complete()
        except Exception:  # noqa: BLE001 老版本 loguru 无 complete()
            pass
        return log_file.read_text(encoding="utf-8", errors="replace")

    try:
        yield log_file, _read
    finally:
        # setup_logging() 若被调用会 logger.remove() 清掉全部 sink ⇒ id 可能已失效
        with contextlib.suppress(ValueError):
            logger.remove(sink_id)


async def _resilient_loop(n_rounds: int, raiser, loop_name: str = "test_loop"):
    """7 个站点的**最小同构模型**：既有 ``except Exception`` 之后追加 ``except BaseException``。

    Args:
        n_rounds: 轮数（到点 break，避免无限循环）。
        raiser: ``f(i)``，第 i 轮要抛的异常（或 None 表示正常轮）。
        loop_name: 指标标签。

    Returns:
        每轮的轨迹：正常轮记 ``i``，被兜住记 ``("contained", i)``，
        走既有 Exception 分支记 ``("exception", i)``。
    """
    trace: list = []
    i = 0
    while True:
        if i >= n_rounds:
            break
        try:
            await asyncio.sleep(0)
            if raiser is not None:
                raiser(i)
            trace.append(i)
        except Exception:  # noqa: BLE001 既有分支（语义不变）
            trace.append(("exception", i))
        except BaseException as exc:  # noqa: BLE001 新增兜底
            if is_fatal_base_exception(exc):
                raise
            log_contained(loop_name, exc)
            trace.append(("contained", i))
        i += 1
    return trace


# ================================================== 1) 共享工具的三条语义
def test_is_fatal_base_exception_matrix():
    """必须放行的四类 = True；真实 panic 与普通 Exception = False（可兜）。"""
    for exc in (KeyboardInterrupt(), SystemExit(), GeneratorExit(),
                asyncio.CancelledError()):
        assert is_fatal_base_exception(exc) is True, type(exc).__name__
    assert is_fatal_base_exception(_real_panic()) is False
    assert is_fatal_base_exception(ValueError("x")) is False


def test_format_exception_stack_carries_traceback():
    """堆栈必须先 format 成**字符串**（enqueue=True 下 opt(exception=True) 会丢整条日志）。"""
    stack = format_exception_stack(_real_panic())
    assert isinstance(stack, str)
    assert "Traceback" in stack
    assert "PanicException" in stack
    assert "arg_sort" in stack or "not supported" in stack


def test_log_contained_lands_on_disk_and_increments_metric(log_capture):
    """``log_contained`` 必须**真的落盘**（不是只调用了 logger）+ 指标 +1。"""
    _log_file, read = log_capture
    before = _loop_metric("unit_probe")

    log_contained("unit_probe", _real_panic())

    text = read()
    assert LOG_PREFIX in text, f"日志未落盘（缺 {LOG_PREFIX}）：{text[:400]!r}"
    assert "PanicException" in text, "堆栈未随 message 落盘（opt(exception=True) 的坑）"
    assert "unit_probe" in text
    assert _loop_metric("unit_probe") - before == 1.0


# ================================================== 2) 模型 while True：兜住 / 放行
@pytest.mark.asyncio
async def test_resilient_loop_survives_real_panic(log_capture):
    """真实 panic 落在某一轮 ⇒ 循环**不终止**，后续轮次照常执行 + 指标 +1 + 日志落盘。"""
    _log_file, read = log_capture
    before = _loop_metric("model_panic")

    def _raiser(i: int) -> None:
        if i == 1:
            raise _real_panic()

    trace = await _resilient_loop(3, _raiser, loop_name="model_panic")

    # 第 1 轮被兜住，第 0/2 轮正常 ⇒ 循环确实活下来了
    assert trace == [0, ("contained", 1), 2], trace
    assert _loop_metric("model_panic") - before == 1.0
    assert LOG_PREFIX in read()


@pytest.mark.asyncio
async def test_resilient_loop_propagates_cancelled_error():
    """``CancelledError`` 必须**放行**（不被兜住）—— 优雅关闭机制，兜住会关不掉。"""
    before = _loop_metric("model_cancel")

    def _raiser(i: int) -> None:
        raise asyncio.CancelledError()

    with pytest.raises(asyncio.CancelledError):
        await _resilient_loop(3, _raiser, loop_name="model_cancel")
    assert _loop_metric("model_cancel") - before == 0.0


@pytest.mark.asyncio
@pytest.mark.parametrize("exc_cls", [KeyboardInterrupt, SystemExit])
async def test_resilient_loop_propagates_fatal_base_exceptions(exc_cls):
    """``KeyboardInterrupt`` / ``SystemExit`` 必须放行（不得被兜住后继续跑）。"""
    def _raiser(i: int) -> None:
        raise exc_cls()

    with pytest.raises(exc_cls):
        await _resilient_loop(3, _raiser, loop_name="model_fatal")


@pytest.mark.asyncio
async def test_resilient_loop_ordinary_exception_uses_existing_branch(log_capture):
    """语义没被抢：普通 ``Exception`` 仍走**既有** ``except Exception`` 分支
    （不进 log_contained、指标不变）。"""
    _log_file, read = log_capture
    before = _loop_metric("model_plain")

    def _raiser(i: int) -> None:
        if i == 1:
            raise ValueError("普通异常")

    trace = await _resilient_loop(3, _raiser, loop_name="model_plain")

    assert trace == [0, ("exception", 1), 2], trace
    assert _loop_metric("model_plain") - before == 0.0, "普通异常不应计入兜底指标"
    assert LOG_PREFIX not in read(), "普通异常不应走 resilience 兜底分支"


# ================================================== 3) 真实站点
@pytest.mark.asyncio
async def test_alert_scheduler_continues_after_panic_and_stops_on_cancel(log_capture):
    """站点 4（``alerts.alert_scheduler``）：panic 被兜住 ⇒ 进入下一轮；
    CancelledError **放行** ⇒ 由该协程外层 ``except CancelledError: pass`` 优雅退出
    （若被兜住则会无限循环，函数永不返回）。"""
    _log_file, read = log_capture
    state = {"runs": 0}

    async def _boom():
        state["runs"] += 1
        raise _real_panic()

    async def _sleep(_sec):
        if state["runs"] >= 2:
            raise asyncio.CancelledError()

    monkeypatch_free = pytest.MonkeyPatch()
    try:
        monkeypatch_free.setattr(alerts_mod, "evaluate_rules", _boom)
        monkeypatch_free.setattr(alerts_mod, "_is_market_open", lambda *_a, **_k: True)
        monkeypatch_free.setattr(alerts_mod.asyncio, "sleep", _sleep)
        await alerts_mod.alert_scheduler()   # 正常返回（CancelledError 被外层吞掉）
    finally:
        monkeypatch_free.undo()

    assert state["runs"] == 2, "panic 被兜住后必须进入第 2 轮，且取消后不再有第 3 轮"
    text = read()
    assert LOG_PREFIX in text and "alert_scheduler" in text
    assert _loop_metric("alert_scheduler") >= 1.0


@pytest.mark.asyncio
async def test_auto_sync_scheduler_continues_after_panic_then_propagates_cancel(log_capture):
    """站点 5（``sync_service.auto_sync_scheduler``）：panic 兜住继续；CancelledError 放行上抛。"""
    _log_file, read = log_capture
    state = {"runs": 0}

    def _boom():
        state["runs"] += 1
        raise _real_panic()

    async def _sleep(_sec):
        if state["runs"] >= 2:
            raise asyncio.CancelledError()

    mp = pytest.MonkeyPatch()
    try:
        mp.setattr(sync_mod, "_load_auto_sync", _boom)
        mp.setattr(sync_mod.asyncio, "sleep", _sleep)
        with pytest.raises(asyncio.CancelledError):
            await sync_mod.auto_sync_scheduler()
    finally:
        mp.undo()

    assert state["runs"] == 2
    text = read()
    assert LOG_PREFIX in text and "auto_sync" in text
    assert _loop_metric("auto_sync") >= 1.0


@pytest.mark.asyncio
async def test_evening_routine_scheduler_continues_after_panic_then_propagates_cancel(log_capture):
    """站点 1（``evening_routine.evening_routine_scheduler``）：同上。"""
    _log_file, read = log_capture
    state = {"runs": 0}

    async def _to_thread(_fn, *_a, **_k):
        state["runs"] += 1
        raise _real_panic()

    async def _sleep(_sec):
        if state["runs"] >= 2:
            raise asyncio.CancelledError()

    mp = pytest.MonkeyPatch()
    try:
        mp.setattr(evening_mod, "setup_logging", lambda *_a, **_k: None)
        mp.setattr(evening_mod, "get_settings",
                   lambda: SimpleNamespace(EVENING_ROUTINE_ENABLED=True,
                                           EVENING_ROUTINE_TIME="00:00"))
        mp.setattr(evening_mod, "_already_done_today", lambda: False)
        mp.setattr(evening_mod, "_mark", lambda *_a, **_k: None)
        mp.setattr(evening_mod.asyncio, "to_thread", _to_thread)
        mp.setattr(evening_mod.asyncio, "sleep", _sleep)
        with pytest.raises(asyncio.CancelledError):
            await evening_mod.evening_routine_scheduler()
    finally:
        mp.undo()

    assert state["runs"] == 2
    text = read()
    assert LOG_PREFIX in text and "evening_routine" in text
    assert _loop_metric("evening_routine") >= 1.0


@pytest.mark.asyncio
async def test_startup_catchup_contains_panic(log_capture):
    """站点 2（``evening_routine.startup_catchup``）：一次性后台任务，panic 兜住后正常返回
    （否则异常滞留 Task，只在 GC 时留一条无排障价值的告警）。"""
    _log_file, read = log_capture
    before = _loop_metric("startup_catchup")

    async def _to_thread(_fn, *_a, **_k):
        raise _real_panic()

    mp = pytest.MonkeyPatch()
    try:
        mp.setattr("app.ml.monitor.get_health_snapshot", lambda: None)
        mp.setattr(evening_mod.asyncio, "to_thread", _to_thread)
        await evening_mod.startup_catchup()   # 不得抛出
    finally:
        mp.undo()

    assert _loop_metric("startup_catchup") - before == 1.0
    assert LOG_PREFIX in read()


def test_sync_worker_contains_panic_resets_state_and_marks_error(log_capture):
    """站点 6（``sync_service._sync_worker``，daemon 线程）：
    panic 兜住 ⇒ ①``finally`` 仍执行（``_sync.running`` 复位）②**必须**置
    ``_sync.error`` ⇒ 终态记 **FAILED**（只兜不置错会被记成 SUCCESS = 数据造假）。
    """
    _log_file, read = log_capture
    jobs: list = []

    def _boom(_symbols, resume=False):
        raise _real_panic()

    mp = pytest.MonkeyPatch()
    try:
        mp.setattr("app.core.pipeline_lock.pipeline_slot", lambda *_a, **_k: contextlib.nullcontext())
        mp.setattr(sync_mod, "_run_incremental", _boom)
        mp.setattr(sync_mod, "_persist_sync_state", lambda *_a, **_k: None)
        mp.setattr(sync_mod, "_record_sync_job",
                   lambda mode, status, dur, err=None: jobs.append((mode, status, err)))

        st = sync_mod._sync
        with st.lock:
            st.running = True
            st.error = None
            st.mode = "incremental"
            st.task_id = None
            st.done = 1
            st.total = 1
            st.rows_written = 0
            st.cancel_event.clear()
            st.completed.clear()
            st.started_at = time.monotonic()

        sync_mod._sync_worker("incremental", [], False)   # 不得抛出
    finally:
        mp.undo()

    # ① finally 仍然执行
    assert sync_mod._sync.running is False, "finally 未执行 ⇒ 同步状态永久卡在 running"
    # ② 终态必须是 FAILED（不是 SUCCESS）
    assert sync_mod._sync.error is not None
    assert sync_mod._sync.error.startswith("PanicException")
    assert jobs and jobs[0][1] == "FAILED", f"panic 被记成了非 FAILED: {jobs}"
    assert LOG_PREFIX in read() and "sync_worker" in read()


@pytest.mark.asyncio
async def test_swr_rebuild_contains_panic_and_still_unlocks(log_capture):
    """站点 7（``cache/swr._spawn_rebuild._run``）：panic 兜住 ⇒ ``finally`` 的
    ``RedisClient.unlock`` **仍然执行**（锁不泄漏）。"""
    _log_file, read = log_capture
    before = _loop_metric("swr_rebuild")
    unlocked: list = []

    async def _boom():
        raise _real_panic()

    async def _unlock(key):
        unlocked.append(key)

    async def _try_lock(*_a, **_k):
        return True

    mp = pytest.MonkeyPatch()
    try:
        mp.setattr(swr_mod.RedisClient, "try_lock", _try_lock)
        mp.setattr(swr_mod.RedisClient, "unlock", _unlock)
        before_tasks = set(swr_mod._bg_tasks)
        swr_mod._spawn_rebuild("probe:key", _boom, 300, 1800, 120, None, None)
        new_tasks = list(set(swr_mod._bg_tasks) - before_tasks)
        assert len(new_tasks) == 1, "后台重建任务未生成"
        await new_tasks[0]   # 任务正常结束（异常已被兜住，不会以 panic 收尾）
    finally:
        mp.undo()

    assert unlocked == ["rebuild:probe:key"], f"finally 的 unlock 未执行: {unlocked}"
    assert _loop_metric("swr_rebuild") - before == 1.0
    assert LOG_PREFIX in read()
