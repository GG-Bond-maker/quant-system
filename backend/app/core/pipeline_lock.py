"""管道级互斥：sync / fetch / pipeline / mirror-rebuild / training 重任务统一互斥。

背景（2026-09-12 架构审查 C-01/C-02/C-03）：autoSync（15:45）、晚间例行
pipeline（17:30，含 rebuild_qfq / build_cs_mirror）、手动 ``POST /mirror/rebuild``
与训练任务都会写 ``daily_bar_qfq`` / ``cs/`` 镜像 / ``features``，但此前各用各的
锁（``_sync.lock`` / ``_job.lock`` / ``compute_slot``），互不相识。
``atomic_write_parquet`` 只保证单文件不写半截，**不保证两个写者之间的先后顺序**
——撞车时后写者会静默覆盖先写者，产出"看起来正常但错误"的数据。

设计：
- **进程级**互斥（当前部署为单 worker，见 Dockerfile ``--workers 1``；跨 worker
  部署需配合 Redis 分布式锁，尚未实现）。
- **非阻塞申请 + 直接拒绝**而非排队：长任务排队会让请求挂起数分钟，不如明确
  告知"X 正在执行"。冲突抛 :class:`PipelineBusy`，调用方现有 ``except Exception``
  兜底即可优雅降级（晚间例行记 warning 继续、API 端点转业务码）。
- 锁由**调用方**申请（sync 的 ``_sync_worker``、pipeline 的 ``run_pipeline``、
  mirror 的 ``/mirror/rebuild``、training 的 ``_worker``），粒度覆盖整个任务体。
"""
from __future__ import annotations

import os
import threading
import time
from contextlib import contextmanager
from typing import Iterator

from loguru import logger

_LOCK = threading.Lock()
_OWNER: str | None = None
_STARTED_MONO: float | None = None

TASKS = ("sync", "fetch", "pipeline", "mirror", "training")


class PipelineBusy(RuntimeError):
    """管道互斥冲突：另一个管道任务正在执行。

    Attributes:
        owner: 当前占用管道的任务名（sync / pipeline / mirror / training）。
    """

    def __init__(self, owner: str) -> None:
        self.owner = owner
        super().__init__(f"管道任务 [{owner}] 正在执行，已拒绝并发进入")


def current_pipeline_owner() -> str | None:
    """当前占用管道的任务名；空闲返回 ``None``。"""
    return _OWNER


def current_owner() -> str | None:
    """别名：同 :func:`current_pipeline_owner`。"""
    return _OWNER


@contextmanager
def pipeline_slot(task: str) -> Iterator[None]:
    """申请管道互斥（非阻塞）。

    Args:
        task: 任务名，见 ``TASKS``。

    Raises:
        ValueError: task 名非法（防拼写错误导致观测混乱）。
        PipelineBusy: 已有其他管道任务在执行。
    """
    global _OWNER, _STARTED_MONO
    if task not in TASKS:
        raise ValueError(f"未知管道任务名 {task!r}，应为 {TASKS} 之一")
    if not _LOCK.acquire(blocking=False):
        raise PipelineBusy(_OWNER or "unknown")
    _OWNER, _STARTED_MONO = task, time.monotonic()
    try:
        yield
    finally:
        _OWNER, _STARTED_MONO = None, None
        _LOCK.release()


_WORKER_COUNT_ENV_KEYS = ("WEB_CONCURRENCY", "UVICORN_WORKERS", "GUNICORN_WORKERS")


def detect_worker_count() -> int:
    """从常见部署环境变量推断 worker 数；无法判定时按 1（单 worker）处理。"""
    count = 1
    for key in _WORKER_COUNT_ENV_KEYS:
        raw = os.environ.get(key)
        if not raw:
            continue
        try:
            count = max(count, int(raw))
        except ValueError:
            continue
    return count


def warn_if_multi_worker() -> int:
    """多 worker 部署时告警：进程级锁与启动时残留回收都要求单实例。"""
    count = detect_worker_count()
    if count > 1:
        logger.warning(
            f"[pipeline_lock] 检测到 {count} 个 worker；pipeline_slot 是**进程级** "
            "threading.Lock，多 worker 下互斥会静默失效；启动时的残留回收也会把其它 "
            "worker 在飞的合法任务翻成 failed。请改回 --workers 1 或改用 Redis 分布式锁。")
    return count
