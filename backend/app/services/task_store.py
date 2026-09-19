"""轻量持久化后台任务协议（SQLite）。

任务状态与进程内 worker 解耦：API 重启后仍可查询最终状态；lease 字段为后续
多 worker 抢占提供原子更新基础。当前同步 worker 仍是单机线程，迁移到独立
worker 时只需复用本模块的状态更新接口。
"""
from __future__ import annotations

import json
import sqlite3
from datetime import datetime, timedelta, timezone
from typing import Any
from uuid import uuid4

from ..core.config import get_settings


def _conn() -> sqlite3.Connection:
    conn = sqlite3.connect(get_settings().SQLITE_PATH, timeout=30)
    conn.execute("""
        CREATE TABLE IF NOT EXISTS background_tasks (
            task_id TEXT PRIMARY KEY,
            task_type TEXT NOT NULL,
            status TEXT NOT NULL,
            progress_json TEXT NOT NULL DEFAULT '{}',
            result_json TEXT,
            error_message TEXT,
            owner TEXT,
            lease_until TEXT,
            created_at TEXT NOT NULL,
            started_at TEXT,
            finished_at TEXT
        )
    """)
    return conn


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def create_task(task_type: str, *, owner: str = "api") -> str:
    task_id = uuid4().hex
    now = _now()
    with _conn() as conn:
        conn.execute(
            "INSERT INTO background_tasks "
            "(task_id, task_type, status, owner, created_at) VALUES (?, ?, 'queued', ?, ?)",
            (task_id, task_type, owner, now),
        )
    return task_id


def update_task(task_id: str, status: str, *, progress: dict[str, Any] | None = None,
               result: dict[str, Any] | None = None, error: str | None = None) -> None:
    fields = ["status = ?"]
    values: list[Any] = [status]
    if progress is not None:
        fields.append("progress_json = ?")
        values.append(json.dumps(progress, ensure_ascii=False))
    if result is not None:
        fields.append("result_json = ?")
        values.append(json.dumps(result, ensure_ascii=False))
    if error is not None:
        fields.append("error_message = ?")
        values.append(error[:1000])
    if status == "running":
        fields.append("started_at = COALESCE(started_at, ?)")
        values.append(_now())
    # cancel_requested is an intermediate state: the worker still has to
    # observe it and publish the final cancelled result.
    if status in {"succeeded", "failed", "cancelled"}:
        fields.append("finished_at = ?")
        values.append(_now())
    values.append(task_id)
    with _conn() as conn:
        conn.execute(f"UPDATE background_tasks SET {', '.join(fields)} WHERE task_id = ?", values)


def claim_task(task_id: str, owner: str, lease_seconds: int = 300) -> bool:
    """原子领取 queued/租约已过期任务，供多 worker 迁移时复用。"""
    now = _now()
    lease_until = (datetime.now(timezone.utc) + timedelta(seconds=lease_seconds)).isoformat(timespec="seconds")
    with _conn() as conn:
        cur = conn.execute(
            "UPDATE background_tasks SET status='running', owner=?, lease_until=?, started_at=? "
            "WHERE task_id=? AND ("
            "status='queued' OR "
            "(status='running' AND lease_until IS NOT NULL AND lease_until < ?)"
            ")",
            (owner, lease_until, now, task_id, now),
        )
        return cur.rowcount == 1


def get_task(task_id: str) -> dict[str, Any] | None:
    with _conn() as conn:
        conn.row_factory = sqlite3.Row
        row = conn.execute("SELECT * FROM background_tasks WHERE task_id = ?", (task_id,)).fetchone()
    if row is None:
        return None
    out = dict(row)
    out["progress"] = json.loads(out.pop("progress_json") or "{}")
    raw = out.pop("result_json")
    out["result"] = json.loads(raw) if raw else None
    return out


def reap_stale_running_tasks(
        reason: str = "进程退出/租约过期（启动时回收）") -> list[str]:
    """启动时把**所有** ``status='running'`` 任务回收为 ``failed``（消除永不自愈的僵尸任务）。

    背景（生产证据，2026-09-19）
    ---------------------------
    ``data/sqlite/aqp.db`` 的 ``background_tasks`` 有一行 ``status=running``、
    ``lease_until`` 已过期 5 天、``finished_at`` 为 NULL —— 经 ``GET /sync/tasks/{id}``
    暴露给前端且**永不自愈**：``claim_task`` 只在**已知 task_id** 时被调用（新建任务
    路径），从不枚举旧行；``update_task(..., 'running')`` 又只写 ``started_at``、**不续租**。

    判据必须**无条件**（不看 lease），原因（写在此处以免后人"优化"成租约过滤）
    --------------------------------------------------------------------
    * 用 ``lease_until < now`` 过滤会**漏**：claim 之后**立刻被杀**的行，其 lease
      仍在未来（默认 300s）⇒ 永远不被任何扫描命中 —— 正是"永不自愈"的成因。
    * 用 ``lease_until < now`` 过滤还会**误**：``update_task(..., 'running')`` **不续租**，
      而真实增量同步实测跑了约 2 小时 ⇒ 健康的在跑任务 lease 早就过期，会被**误杀**。
    * 正确判据是**调用时机**：**只在启动阶段调用**。此刻本进程**不可能**拥有在飞任务
      （任务行只由本进程的 API 路径创建），故"无条件回收"安全且幂等。

    ⚠️ **禁止**把它做成定时 / 运行期 reaper：在没有"续租"机制之前，运行期无条件回收
    会**误杀**健康的长同步任务。本函数只应在 ``main.lifespan`` 启动分支调用一次。

    Args:
        reason: 写入 ``error_message`` 的说明。

    Returns:
        被回收的 ``task_id`` 列表；无 ``running`` 行时返回 ``[]``（幂等）。
    """
    now = _now()
    with _conn() as conn:
        rows = conn.execute(
            "SELECT task_id FROM background_tasks WHERE status = 'running'").fetchall()
        task_ids = [r[0] for r in rows]
        if not task_ids:
            return []
        conn.execute(
            "UPDATE background_tasks SET status='failed', finished_at=?, error_message=? "
            "WHERE status = 'running'",
            (now, reason[:1000]))
    return task_ids
