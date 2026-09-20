"""项3 回归：单实例前提护栅。

- ``pipeline_slot`` 是**进程级** threading.Lock；启动时的残留回收（reclaim_stale_retrain /
  reap_stale_running_tasks）也假设单实例（本进程不可能有在飞任务）。多 worker 会让二者
  双双静默失效（回收还会误杀其它 worker 在飞的合法任务）。
- 故新增：``detect_worker_count``（从常见环境变量推断）+ ``warn_if_multi_worker``（多 worker
  告警），并在 ``main.lifespan`` 独立接线；同时断言 Dockerfile 确实声明了单 worker。
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

from app.core import pipeline_lock


class _LogRecorder:
    """记录 logger.warning 的桩（warn_if_multi_worker 只调 warning）。"""

    def __init__(self) -> None:
        self.warnings: list[str] = []

    def warning(self, message) -> None:  # noqa: ANN001
        self.warnings.append(str(message))


def _dockerfile_cmd_lines() -> list[str]:
    """所有以 CMD 开头的行（注意：可能有多条，含 HEALTHCHECK 的 ``CMD curl ...``）。"""
    dockerfile = Path(__file__).resolve().parents[1] / "Dockerfile"
    assert dockerfile.exists(), f"Dockerfile 不存在: {dockerfile}"
    return [ln.strip() for ln in dockerfile.read_text(encoding="utf-8").splitlines()
            if ln.strip().upper().startswith("CMD")]


def _parse_workers_from_cmd(cmd_line: str) -> int | None:
    """解析一行 Dockerfile CMD 声明的 worker 数。无 uvicorn / 未声明 workers 返回 ``None``。

    Returns:
        int: 该 CMD 行显式声明的 ``--workers`` 值。
        None: 该行**不是** uvicorn 的启动行（如 HEALTHCHECK 的 ``CMD curl ...``），
              或虽启动 uvicorn 但未声明 ``--workers``（此时 uvicorn 默认单 worker）。

    ⚠️ 历史 bug（QA 变异实证：把 ``--workers 1`` 改成 ``4``，本套件仍 ``1 passed``）：
    ① 旧实现只取**第一个** CMD 行 ⇒ 命中的是排在前面的 HEALTHCHECK 那一行，它不含
    ``--workers`` ⇒ "找不到就跳过" ⇒ 断言**恒绿**；
    ② JSON 数组形式里 token 自带引号，``"--workers" in tokens`` 恒为 False（旧代码只在
    取下一个值时写了 ``strip('"')``，成员判断漏 strip）。
    故这里所有 token **一律先 strip 引号再比较**，并且"不是 uvicorn 启动行"必须显式
    返回 ``None`` 而不是跳过整条用例。
    """
    tokens = [t.strip('"').strip("'") for t in
              cmd_line.replace(",", " ").replace("[", " ").replace("]", " ").split()]
    if "uvicorn" not in tokens:
        return None                      # 不是 uvicorn 的启动行（如 HEALTHCHECK）
    if "--workers" not in tokens:
        return None                      # 未声明 ⇒ uvicorn 默认单 worker
    idx = tokens.index("--workers")
    assert idx + 1 < len(tokens), f"CMD 声明了 --workers 却没有值: {cmd_line!r}"
    value = tokens[idx + 1]
    assert value.isdigit(), f"CMD 的 --workers 值不是整数: {cmd_line!r}"
    return int(value)


@pytest.mark.parametrize("cmd, expected", [
    ('CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000", '
     '"--workers", "1"]', 1),
    ('CMD ["uvicorn", "app.main:app", "--workers", "4"]', 4),
    ('CMD uvicorn app.main:app --workers 4', 4),
    ('CMD curl -fsS http://127.0.0.1:8000/health/ready || exit 1', None),
    ('CMD ["uvicorn", "app.main:app"]', None),
])
def test_parse_workers_from_cmd(cmd, expected) -> None:
    """helper 自身的判别力：这条是静态门禁的**底座**。

    先前正是因为 helper 失真（抓错行 + 引号未 strip），真实文件那条用例才会
    "改坏了也照样绿"。这里把 helper 拉出来单测，保证它**真的能读出数字**。
    """
    assert _parse_workers_from_cmd(cmd) == expected


def test_dockerfile_declares_single_worker() -> None:
    """真实 Dockerfile：必须至少有一行启动 uvicorn 的 CMD，且声明的 ``--workers`` 必须为 1。

    **不允许静默跳过**：找不到 uvicorn CMD 行必须 FAIL（启动方式被改坏或改了运行时，
    单实例前提就无从谈起）。
    """
    cmds = _dockerfile_cmd_lines()
    assert cmds, "Dockerfile 里找不到任何 CMD 行"
    uvicorn_lines = [(c, _parse_workers_from_cmd(c)) for c in cmds if "uvicorn" in c]
    assert uvicorn_lines, (
        "Dockerfile 里没有启动 uvicorn 的 CMD 行，无法确认单实例前提: "
        f"{[c[:60] for c in cmds]}")
    declared = [n for _, n in uvicorn_lines if n is not None]
    assert declared, (
        "Dockerfile 的 uvicorn CMD 未声明 --workers，无从锁定单 worker（请显式写 1）: "
        f"{[c[:60] for c, _ in uvicorn_lines]}")
    assert set(declared) == {1}, f"Dockerfile 未声明单 worker: {uvicorn_lines}"


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


@pytest.mark.parametrize("argv, expected", [
    (["uvicorn", "app.main:app"], 1),                        # 未声明 ⇒ 单 worker
    (["uvicorn", "app.main:app", "--workers", "2"], 2),      # 空格写法
    (["uvicorn", "app.main:app", "--workers=3"], 3),         # 等号写法
    (["uvicorn", "app.main:app", "--workers", "x"], 1),      # 非法值 ⇒ 兜底 1
    (["uvicorn", "app.main:app", "--workers"], 1),           # 缺值 ⇒ 1
    # gunicorn 既不回写 WEB_CONCURRENCY，又常用短写 -w ⇒ 必须认，否则假阴性
    (["gunicorn", "app.main:app", "-w", "4"], 4),
    (["gunicorn", "app:app", "-w4"], 4),                     # 连写形态
    (["gunicorn", "app:app", "-w=4"], 4),                    # 连写带等号
    # ⚠️ D-C：这两种**真实部署形态**此前**零用例覆盖** —— QA 第八轮实测"把启动器校验
    # 收紧成只扫 argv[0]"能修好绕过、却同时把这两种打断，而套件**全程绿灯**。
    (["python", "-m", "uvicorn", "app.main:app", "--workers", "4"], 4),
    (["python", "-m", "gunicorn", "app:app", "-w", "4"], 4),
    (["/usr/local/bin/uvicorn", "app.main:app", "--workers", "4"], 4),   # 绝对路径
    (["C:\\Python\\Scripts\\uvicorn.exe", "app:app", "--workers", "4"], 4),
    # D-B：其它 ASGI 服务器同属真实部署形态，不该漏网
    (["hypercorn", "app.main:app", "--workers", "4"], 4),
    (["granian", "app:app", "--workers", "4"], 4),
    # 首个 -m 之前可以有任意多个前导 flag，不得设 args[:4] 这类魔法窗口
    (["python", "-X", "dev", "-m", "uvicorn", "app.main:app", "--workers", "4"], 4),
    (["python", "-u", "-m", "gunicorn", "app:app", "-w", "4"], 4),
    # ⚠️ 但**只能取首个 -m**：第二个 -m 是 pytest 的标记表达式，取到就误判成部署
    (["python", "-m", "pytest", "-m", "uvicorn", "--workers", "2"], 1),
    (["python", "-m", "uvicorn"], 1),                        # 无 --workers
    (["python", "-m"], 1),                                   # -m 后缺模块名
    (["python", "manage.py", "--workers", "4"], 1),          # 跑脚本，非 -m 部署
    # ⚠️ D-A：QA 实测的绕过形态——只在**非启动器位置**提到 uvicorn/gunicorn，
    # 不得重新打开大门（这是"全量扫描 argv"被证伪的直接反例）。
    (["pytest", "--workers", "2", "tests/test_uvicorn_smoke.py"], 1),
    (["pytest", "-k", "uvicorn", "--workers", "2"], 1),
    (["pytest", "-p", "gunicorn_plugin", "--workers", "2"], 1),
    (["mytool", "--workers", "2", "--log", "/var/log/uvicorn.log"], 1),
    # pytest 的 -m 是**标记表达式**，不是 python -m ⇒ 不得被误认成服务器启动
    (["pytest", "-m", "uvicorn", "--workers", "2"], 1),
    # ⚠️ QA 实测反例：第三方命令行同样带 --workers（pytest-parallel / celery 等）。
    # 没有 uvicorn/gunicorn 启动器关键字 ⇒ **不采信**，否则会误判成多 worker。
    (["pytest", "--workers", "2", "-q"], 1),
    (["python", "-m", "pytest", "--workers", "4", "tests/"], 1),
    (["celery", "-A", "app", "worker", "--workers", "3"], 1),
    ([], 1),
])
def test_workers_from_argv(argv, expected) -> None:
    """``WEB_CONCURRENCY`` 是 uvicorn 的**输入**不是输出，只能靠 argv 兜底。"""
    assert pipeline_lock._workers_from_argv(argv) == expected


def test_detect_worker_count_ignores_foreign_argv(monkeypatch) -> None:
    """端到端密封性：即使全局 sys.argv 含第三方 --workers，也必须判为单 worker。

    同时证明 `detect_worker_count(argv=...)` 这条**显式注入**通道可用（不读全局
    sys.argv ⇒ 用例不再被 runner 的真实 argv 污染）。
    """
    for key in pipeline_lock._WORKER_COUNT_ENV_KEYS:
        monkeypatch.delenv(key, raising=False)
    monkeypatch.setattr(sys, "argv", ["pytest", "--workers", "2", "-q"])
    assert pipeline_lock.detect_worker_count() == 1
    assert pipeline_lock.detect_worker_count(
        ["uvicorn", "app.main:app", "--workers", "2"]) == 2


def test_detect_worker_count_prefers_max_of_env_and_argv(monkeypatch) -> None:
    """env=1 而 argv=--workers 4 ⇒ **必须取 4**。

    这是 uvicorn ``--workers N`` 部署形态的唯一入口（QA 真机实证该形态下 env 全为
    None）；若这里取 min 或忽略 argv，将来有人把 Dockerfile 改成 ``--workers 4``
    依然不会告警。
    """
    for key in pipeline_lock._WORKER_COUNT_ENV_KEYS:
        monkeypatch.delenv(key, raising=False)
    monkeypatch.setenv("WEB_CONCURRENCY", "1")
    monkeypatch.setattr(sys, "argv", ["uvicorn", "app.main:app", "--workers", "4"])
    assert pipeline_lock.detect_worker_count() == 4
