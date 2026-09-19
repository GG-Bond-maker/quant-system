"""后台任务残留回收：``task_store.reap_stale_running_tasks`` 的启动期兜底。

与 ``tests/test_monitor_retrain_resilience.py::test_reclaim_*`` 的分工
--------------------------------------------------------------------
上一轮修的是 **ML 重训** 的陈旧 running（KV ``app_state.retrain``）；本文件断言的是
**同步后台任务**（``background_tasks`` 表）的同一条成因、另一张表：

进程在同步任务执行期间退出 ⇒ 该任务的 daemon 线程被杀 ⇒ 没有任何 ``finally`` 执行 ⇒
行永久停在 ``status='running'``。而 ``GET /sync/tasks/{id}`` 会把它如实读给前端，**永不
自愈**：``claim_task`` 只在**已知 task_id** 时调用（新建路径），从不枚举旧行；
``task_store.update_task(..., 'running')`` 又只写 ``started_at``、**不续租**。

生产证据（2026-09-19，``data/sqlite/aqp.db``）
--------------------------------------------
``background_tasks`` 有一行 ``status='running'``、``lease_until`` 已过期约 5 天、
``finished_at`` 为 NULL。修复 = 启动阶段**无条件**回收所有 ``running`` → ``failed``。

⚠️ 为何判据必须**无条件**（本文件用 T2 钉死，防"优化"成租约过滤）
------------------------------------------------------------------
* 用 ``lease_until < now`` 过滤会**漏**：claim 后**立刻被杀**的行 lease 仍在未来，
  永远不被命中 —— 正是"永不自愈"的成因（T2）。
* 用 ``lease_until < now`` 过滤还会**误**：``update_task`` 不续租，健康长同步实测约 2h
  ⇒ lease 早过期会被误杀。故正确判据是**调用时机**（仅启动期），而非租约（见函数 docstring）。

⚠️ 隔离铁律
-----------
``task_store._conn()`` 在**调用时**读 ``get_settings().SQLITE_PATH``。故这里 monkeypatch
``app.services.task_store.get_settings`` 指向用例私有 ``tmp_path/tasks.db``；若沿用会话级
共享库，用例插入/改写的任务行会泄漏进后续用例。
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from app.core.config import get_settings as _real_get_settings
from app.services import task_store


# ------------------------------------------------------------------ 测试工具件
class _SettingsProxy:
    """把真实 settings 的少数字段换成用例私有值，其余字段透传（版本无关）。"""

    def __init__(self, base, **overrides) -> None:  # noqa: ANN001
        self._base = base
        self.__dict__.update(overrides)

    def __getattr__(self, name):  # noqa: ANN001 仅在正常查找失败时触发
        return getattr(self._base, name)


@pytest.fixture
def task_env(tmp_path, monkeypatch) -> Path:
    """隔离 ``task_store`` 的真实读写：SQLite 落到用例私有 ``tmp_path/tasks.db``。

    Returns:
        私有 SQLite 路径。
    """
    db_path = tmp_path / "tasks.db"
    monkeypatch.setattr(
        task_store, "get_settings",
        lambda: _SettingsProxy(_real_get_settings(), SQLITE_PATH=db_path))
    # 建表（commit）：后续公共读写均可直接落地
    with task_store._conn():
        pass
    return db_path


def _set_lease(task_id: str, lease_until: str) -> None:
    """直接改写 ``lease_until``（复刻不同场景：已过期 / 仍在未来）。"""
    with task_store._conn() as conn:
        conn.execute(
            "UPDATE background_tasks SET lease_until=? WHERE task_id=?",
            (lease_until, task_id))


def _iso(delta: timedelta) -> str:
    return (datetime.now(timezone.utc) + delta).isoformat(timespec="seconds")


# ================================================== 1) 真实僵尸行：无条件回收
def test_reap_reclaims_zombie_running_row(task_env):
    """T1（最重要）：复刻生产残留行（running + 租约已过期 5 天 + finished_at NULL）
    ⇒ 回收后 ``get_task(id)`` 经**公共读路径**可见 ``failed`` + 终态字段。

    反证：把实现改成空操作 ``return []``（m2）⇒ 本用例在 status 断言处变红。
    """
    tid = task_store.create_task("sync")
    task_store.update_task(tid, "running")          # started_at 写入，finished_at 仍 NULL
    _set_lease(tid, _iso(timedelta(days=-5)))       # 租约已过期 5 天（生产实况）

    before = task_store.get_task(tid)
    assert before is not None and before["status"] == "running"
    assert before["finished_at"] is None

    reclaimed = task_store.reap_stale_running_tasks()
    assert tid in reclaimed, f"僵尸行未被回收: {reclaimed}"

    row = task_store.get_task(tid)
    assert row is not None
    assert row["status"] == "failed", f"僵尸行仍非终态: {row}"
    assert row["finished_at"], f"finished_at 未落库: {row}"
    assert "进程退出" in row["error_message"], f"回收原因未落库: {row}"
    # 诊断价值：原 started_at 应保留
    assert row["started_at"], "原 started_at 应保留"


# ================================================== 2) 未来租约：也必须回收（钉死"无条件"）
def test_reap_reaps_running_with_future_lease(task_env):
    """T2：``lease_until`` 仍在未来的 running 行**也必须**被回收。

    这是"无条件 vs 租约过滤"的判别用例：claim 之后立刻被杀的行，其租约仍在未来
    （默认 300s）⇒ 任何"只看 lease"的实现都会**漏掉**它 ⇒ 本用例变红（m3）。
    """
    tid = task_store.create_task("sync")
    task_store.update_task(tid, "running")
    _set_lease(tid, _iso(timedelta(hours=1)))       # 租约仍在未来（claim-then-killed）

    reclaimed = task_store.reap_stale_running_tasks()
    assert tid in reclaimed, f"未来租约的 running 行被漏掉（实现退化成租约过滤？）: {reclaimed}"
    assert task_store.get_task(tid)["status"] == "failed"


# ================================================== 3) 非 running：一行都不能动
@pytest.mark.parametrize("status", ["queued", "succeeded", "failed", "cancelled"])
def test_reap_leaves_non_running_untouched(task_env, status):
    """T3：``queued`` / ``succeeded`` / ``failed`` / ``cancelled`` 全字段快照不变。"""
    tid = task_store.create_task("sync")
    task_store.update_task(tid, status)
    snapshot = task_store.get_task(tid)

    task_store.reap_stale_running_tasks()           # 无 running 行 ⇒ 幂等 no-op

    assert task_store.get_task(tid) == snapshot, "非 running 行不应被改写"


# ================================================== 3b) 空表：幂等返回 []
def test_reap_on_empty_table_is_noop(task_env):
    """T3b：无任何行 ⇒ 返回 ``[]``（不无中生有地写记录）。"""
    assert task_store.reap_stale_running_tasks() == []


# ================================================== 4) lifespan 接线可证伪
@pytest.mark.asyncio
async def test_lifespan_invokes_reap_stale_running_tasks(monkeypatch):
    """T4（接线可证伪）：证明 ``main.lifespan`` **真的调用**了 ``reap_stale_running_tasks``。

    本仓多次出现"函数写了但没接上"的静默下线，故必须证明接线。这里跑**真实 lifespan**
    上下文（而非 inspect 源码文本），把重依赖全部 monkeypatch 成 no-op，并把
    ``app.services.task_store.reap_stale_running_tasks`` 换成记录器断言其被调用。

    反证：删掉 ``main.py`` 里那一段 try/except（m1）⇒ 记录器为空 ⇒ 本用例变红。
    """
    called: list = []
    monkeypatch.setattr("app.services.task_store.reap_stale_running_tasks",
                        lambda *a, **k: called.append((a, k)))
    # 与本文档无关的另一半回收：置 no-op（其接线由 test_monitor_retrain_resilience 覆盖）
    monkeypatch.setattr("app.ml.monitor.reclaim_stale_retrain",
                        lambda *a, **k: None)

    async def _noop(*_a, **_k):
        return None

    monkeypatch.setattr("app.main.init_database", _noop)
    monkeypatch.setattr("app.main.refresh_calendar_cache", _noop)
    monkeypatch.setattr("app.main.setup_logging", lambda *a, **k: None)
    monkeypatch.setattr("app.core.events.bind_loop", lambda *a, **k: None)
    monkeypatch.setattr("app.services.sync_service.restore_sync_state",
                        lambda *a, **k: None)
    monkeypatch.setattr("app.services.sync_service.auto_sync_scheduler", _noop)
    monkeypatch.setattr("app.api.v1.alerts.alert_scheduler", _noop)
    monkeypatch.setattr("app.jobs.evening_routine.evening_routine_scheduler", _noop)
    monkeypatch.setattr("app.jobs.evening_routine.startup_catchup", _noop)

    from fastapi import FastAPI

    from app.main import lifespan

    async with lifespan(FastAPI()):
        pass

    assert called, "lifespan 未调用 reap_stale_running_tasks（函数写了但没接上）"
