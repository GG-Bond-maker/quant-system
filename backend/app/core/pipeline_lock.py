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
import sys
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
# 只有 argv 里出现这些启动器关键字，才采信其中的 --workers（否则会把第三方命令行
# 的 --workers 当成部署形态，见 _workers_from_argv 的 docstring）。
_LAUNCHER_KEYWORDS = ("uvicorn", "gunicorn")


def _workers_from_argv(argv: list[str] | None = None) -> int:
    """从命令行 argv 解析 worker 数；未声明返回 1。

    为什么需要这一步（QA 真机实测）：``WEB_CONCURRENCY`` 是 uvicorn 的**输入**
    而非输出——``uvicorn/config.py`` 只读它（``if workers is None and "WEB_CONCURRENCY"
    in os.environ``），``uvicorn/supervisors/`` 下 **0 处**回写 ⇒ 用
    ``uvicorn ... --workers 4`` 启动时，子进程 env 里三个变量**全为 None**，
    只靠 env 推断会得到 1（**漏掉本项目真正的部署形态**）。
    但 uvicorn 派生的 worker 子进程 **argv 保留了 ``--workers``**，故据此兜底。

     ⚠️ 必须**先校验启动器**（QA 实测反例）：``--workers`` 不是 uvicorn 独有的选项，
    pytest-parallel / celery 等第三方命令行同样带 ``--workers``。若不加校验，
    ``pytest --workers 2 -q`` 会被当成"部署了 2 个 worker" ⇒ 误告警。故：argv 里
    没有 ``uvicorn``/``gunicorn`` 任一启动器关键字时，**一律不采信**，返回 1。
    有启动器时才认 ``--workers N`` / ``--workers=N`` / gunicorn 的 ``-w N``
    （gunicorn 既不回写 ``WEB_CONCURRENCY``，又常只用短写 ``-w``，不认就会假阴性）。
    """
    args = [str(a) for a in (sys.argv if argv is None else list(argv))]
    if not any(lan in a.lower() for a in args for lan in _LAUNCHER_KEYWORDS):
        return 1                        # 不是 uvicorn/gunicorn 的命令行 ⇒ 不采信
    for i, tok in enumerate(args):
        if tok in ("--workers", "-w") and i + 1 < len(args):
            try:
                return int(args[i + 1])
            except ValueError:
                continue
        if tok.startswith("--workers="):
            try:
                return int(tok.split("=", 1)[1])
            except ValueError:
                continue
    return 1


def detect_worker_count(argv: list[str] | None = None) -> int:
    """推断当前部署的 worker 数；无法判定时按 1（单 worker）处理。

    Args:
        argv: 仅供测试注入（显式传入即**不读全局 sys.argv**，避免用例被 runner 的
              真实 argv 污染）；生产调用不带参 ⇒ 读 ``sys.argv``。

    取**环境变量推断值**与 **argv 推断值**的最大值：env 覆盖显式 export 的部署，
    argv 覆盖 ``uvicorn/gunicorn --workers N`` 的部署。
    """
    count = 1
    for key in _WORKER_COUNT_ENV_KEYS:
        raw = os.environ.get(key)
        if not raw:
            continue
        try:
            count = max(count, int(raw))
        except ValueError:
            continue
    return max(count, _workers_from_argv(argv))


def warn_if_multi_worker() -> int:
    """多 worker 部署时告警：进程级锁与启动时残留回收都要求单实例。"""
    count = detect_worker_count()
    if count > 1:
        logger.warning(
            f"[pipeline_lock] 检测到 {count} 个 worker；pipeline_slot 是**进程级** "
            "threading.Lock，多 worker 下互斥会静默失效；启动时的残留回收也会把其它 "
            "worker 在飞的合法任务翻成 failed。请改回 --workers 1 或改用 Redis 分布式锁。")
    return count
