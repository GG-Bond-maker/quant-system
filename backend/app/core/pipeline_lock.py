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
# 只有 argv 的**启动器位置**出现这些关键字，才采信其中的 --workers（否则会把第三方
# 命令行的 --workers 当成部署形态，见 _workers_from_argv 的 docstring）。
# 覆盖常见 ASGI/WSGI 服务器；hypercorn / granian 虽非本项目所用，但同属真实部署形态。
_LAUNCHER_KEYWORDS = ("uvicorn", "gunicorn", "hypercorn", "granian")


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
    ``pytest --workers 2 -q`` 会被当成"部署了 2 个 worker" ⇒ 误告警。

    ⚠️ 启动器只能看**启动器位置**（QA 第八轮实测的绕过，D-A）：全量扫描 argv 时，
    ``pytest --workers 2 tests/test_uvicorn_smoke.py``、``pytest -k uvicorn --workers 2``、
    ``mytool --workers 2 --log /var/log/uvicorn.log`` 都会因为"某个参数里提到了
    uvicorn"而重新打开大门。故只看 ① ``argv[0]`` 的文件名，或 ② ``python -m <模块>``
    的模块位（且 ``argv[0]`` 本身得是 python，避免与 pytest 的 ``-m`` 标记表达式混淆）。

    有启动器时才认 ``--workers N`` / ``--workers=N`` / ``-w N`` / ``-wN`` 连写
    （gunicorn 既不回写 ``WEB_CONCURRENCY``，又常只用短写 ``-w``，不认会假阴性）。
    重复声明取**最后一个有效值**（与 click / argparse 的 last-wins 一致）；
    非法值跳过而不放弃整条扫描。

    ⚠️ 已记录的边界（QA 实测，均非阻断、方向多为无害的多报）：
    * ``-wN`` 连写形态**无"选项取值"保护**：``gunicorn app:app --chdir -w9
      --workers 4`` 里 ``-w9`` 是 ``--chdir`` 的取值，会被当作 workers=9。已核查
      uvicorn/gunicorn/hypercorn 无其它 ``-w`` 前缀单破折号选项、granian 的
      ``--workers-*`` 是双破折号，故现实风险低；加白名单属过度设计。
    * workers 写在 **gunicorn 配置文件**（``-c gunicorn.conf.py``）或
      ``GUNICORN_CMD_ARGS`` 环境变量里时，argv 与三个 env 键都看不到 ⇒ 判 1。
      本项目不用 gunicorn，如将来启用需补 ``GUNICORN_CMD_ARGS`` 解析。
    """
    args = [str(a) for a in (sys.argv if argv is None else list(argv))]
    if not _looks_like_server_launch(args):
        return 1                        # 不是 ASGI/WSGI 服务器的启动命令行 ⇒ 不采信
    found: int | None = None
    for i, tok in enumerate(args):
        if tok in ("--workers", "-w") and i + 1 < len(args):
            try:
                found = int(args[i + 1])
                continue
            except ValueError:
                continue
        if tok.startswith("--workers="):
            try:
                found = int(tok.split("=", 1)[1])
                continue
            except ValueError:
                continue
        if tok.startswith("-w") and len(tok) > 2:      # -w4 / -w=4 连写形态
            try:
                found = int(tok[2:].lstrip("="))
                continue
            except ValueError:
                continue
    # 不立即 return：重复声明时按 last-wins（与 click/argparse 一致），
    # 且非法值只跳过该次、不放弃整条扫描。
    return found if found is not None and found >= 1 else 1


def _looks_like_server_launch(args: list[str]) -> bool:
    """argv 是否像一个 ASGI/WSGI 服务器的启动命令（只看**启动器位置**）。

    ① ``argv[0]`` 的文件名含启动器关键字（兼容绝对路径 / ``.exe`` / 大写）；
    ② 或 ``argv[0]`` 是 python，且**首个** ``-m`` 之后的模块名含关键字。

    ⚠️ 只取**首个** ``-m``：``python -m pytest -m uvicorn --workers 2`` 里第二个
    ``-m`` 是 pytest 的**标记表达式**，取到它就会把跑测试误判成 uvicorn 部署。
    反过来也不能设 ``args[:4]`` 之类的魔法窗口——``python -X dev -m uvicorn``
    的前导 flag 个数不确定，窗口一卡就假阴性（本项目真实部署是
    ``.venv/Scripts/python.exe -m uvicorn ...``，故这条必须有确定语义）。
    """
    if not args:
        return False
    head = os.path.basename(str(args[0])).lower()
    if any(kw in head for kw in _LAUNCHER_KEYWORDS):
        return True
    if "python" not in head:
        return False                     # argv[0] 不是 python ⇒ -m 不是"运行模块"
    try:
        idx = args.index("-m", 1)        # 首个 -m，位置不限
    except ValueError:
        return False                     # python 直接跑脚本，无 -m
    if idx + 1 >= len(args):
        return False                     # python ... -m（缺模块名）
    return any(kw in str(args[idx + 1]).lower() for kw in _LAUNCHER_KEYWORDS)


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
