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
