"""项3 回归：单实例前提护栅。

- ``pipeline_slot`` 是**进程级** threading.Lock；启动时的残留回收（reclaim_stale_retrain /
  reap_stale_running_tasks）也假设单实例（本进程不可能有在飞任务）。多 worker 会让二者
  双双静默失效（回收还会误杀其它 worker 在飞的合法任务）。
- 故新增：``detect_worker_count``（从常见环境变量推断）+ ``warn_if_multi_worker``（多 worker
  告警），并在 ``main.lifespan`` 独立接线；同时断言 Dockerfile 确实声明了单 worker。
"""
from __future__ import annotations

from pathlib import Path

import pytest

from app.core import pipeline_lock


class _LogRecorder:
    """记录 logger.warning 的桩（warn_if_multi_worker 只调 warning）。"""

    def __init__(self) -> None:
        self.warnings: list[str] = []

    def warning(self, message) -> None:  # noqa: ANN001
        self.warnings.append(str(message))


def _dockerfile_cmd_line() -> str:
    dockerfile = Path(__file__).resolve().parents[1] / "Dockerfile"
    assert dockerfile.exists(), f"Dockerfile 不存在: {dockerfile}"
    for line in dockerfile.read_text(encoding="utf-8").splitlines():
        stripped = line.strip()
        if stripped.upper().startswith("CMD"):
            return stripped
    raise AssertionError("Dockerfile 未找到 CMD 行")


def test_dockerfile_declares_single_worker() -> None:
    """Dockerfile 的 CMD 若显式带 ``--workers``，其值必须为 1。

    若整行**无** ``--workers``，视为 uvicorn 默认（单 worker）→ 通过。
    """
    cmd = _dockerfile_cmd_line()
    tokens = (cmd.replace(",", " ").replace("[", " ").replace("]", " ")
              .split())
    if "--workers" not in tokens:
        # 无 --workers ⇒ uvicorn 默认 1 个 worker，满足单实例前提
        return
    idx = tokens.index("--workers")
    assert idx + 1 < len(tokens), f"CMD --workers 缺值: {cmd!r}"
    assert tokens[idx + 1].strip('"') == "1", f"Dockerfile 未声明单 worker: {cmd!r}"


def test_detect_worker_count_defaults_to_one(monkeypatch) -> None:
    """三个环境变量都缺席 ⇒ 1。"""
    for key in pipeline_lock._WORKER_COUNT_ENV_KEYS:
        monkeypatch.delenv(key, raising=False)
    assert pipeline_lock.detect_worker_count() == 1


def test_detect_worker_count_reads_env(monkeypatch) -> None:
    """WEB_CONCURRENCY=4 ⇒ 4。"""
    for key in pipeline_lock._WORKER_COUNT_ENV_KEYS:
        monkeypatch.delenv(key, raising=False)
    monkeypatch.setenv("WEB_CONCURRENCY", "4")
    assert pipeline_lock.detect_worker_count() == 4


def test_detect_worker_count_takes_max(monkeypatch) -> None:
    """多键并存取最大值；非法值忽略。"""
    for key in pipeline_lock._WORKER_COUNT_ENV_KEYS:
        monkeypatch.delenv(key, raising=False)
    monkeypatch.setenv("WEB_CONCURRENCY", "not-int")
    monkeypatch.setenv("UVICORN_WORKERS", "3")
    monkeypatch.setenv("GUNICORN_WORKERS", "2")
    assert pipeline_lock.detect_worker_count() == 3


def test_warn_if_multi_worker_logs_once(monkeypatch) -> None:
    """WEB_CONCURRENCY=4 ⇒ 返回 4，且恰好一条 warning。"""
    for key in pipeline_lock._WORKER_COUNT_ENV_KEYS:
        monkeypatch.delenv(key, raising=False)
    monkeypatch.setenv("WEB_CONCURRENCY", "4")
    recorder = _LogRecorder()
    monkeypatch.setattr(pipeline_lock, "logger", recorder)

    assert pipeline_lock.warn_if_multi_worker() == 4
    assert len(recorder.warnings) == 1, recorder.warnings
    assert "worker" in recorder.warnings[0]


def test_warn_if_multi_worker_silent_when_single(monkeypatch) -> None:
    """单 worker ⇒ 返回 1，且不告警。"""
    for key in pipeline_lock._WORKER_COUNT_ENV_KEYS:
        monkeypatch.delenv(key, raising=False)
    recorder = _LogRecorder()
    monkeypatch.setattr(pipeline_lock, "logger", recorder)

    assert pipeline_lock.warn_if_multi_worker() == 1
    assert recorder.warnings == []


@pytest.mark.asyncio
async def test_lifespan_invokes_multi_worker_guard(monkeypatch) -> None:
    """接线可证伪：``main.lifespan`` 真的调用 ``warn_if_multi_worker``。"""
    called: list = []
    monkeypatch.setattr("app.core.pipeline_lock.warn_if_multi_worker",
                        lambda *a, **k: called.append((a, k)) or 1)
    # 与本文档无关的其它启动副作用：全部 no-op
    monkeypatch.setattr("app.ml.monitor.reclaim_stale_retrain", lambda *a, **k: None)
    monkeypatch.setattr("app.services.task_store.reap_stale_running_tasks",
                        lambda *a, **k: [])

    async def _noop(*_a, **_k):
        return None

    monkeypatch.setattr("app.main.init_database", _noop)
    monkeypatch.setattr("app.main.refresh_calendar_cache", _noop)
    monkeypatch.setattr("app.main.setup_logging", lambda *a, **k: None)
    monkeypatch.setattr("app.core.events.bind_loop", lambda *a, **k: None)
    monkeypatch.setattr("app.services.sync_service.restore_sync_state", lambda *a, **k: None)
    monkeypatch.setattr("app.services.sync_service.auto_sync_scheduler", _noop)
    monkeypatch.setattr("app.api.v1.alerts.alert_scheduler", _noop)
    monkeypatch.setattr("app.jobs.evening_routine.evening_routine_scheduler", _noop)
    monkeypatch.setattr("app.jobs.evening_routine.startup_catchup", _noop)

    from fastapi import FastAPI

    from app.main import lifespan

    async with lifespan(FastAPI()):
        pass

    assert called, "lifespan 未调用 warn_if_multi_worker（护栅写了但没接上）"
