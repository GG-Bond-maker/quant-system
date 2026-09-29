"""项1 回归：``sync_service._record_sync_job`` 首插时 ``data_jobs.updated_at`` 与
``created_at`` 的**时区必须一致**。

缺陷：INSERT 列清单**漏 ``updated_at``** ⇒ 该列走 DB 默认值 ``CURRENT_TIMESTAMP``
（**UTC**），而 ``created_at`` 显式写 ``datetime('now','localtime')`` ⇒ 首次插入两列差
8 小时（真库实测 id=11：created_at 18:34:32 vs updated_at 10:34:32）。

判别式说明（务必保留）
----------------------
``|created_at - updated_at| <= 2s`` 这条断言**依赖本机 TZ≠UTC**（开发机 GMT+8）。
在 UTC 机器上 ``now`` 与 ``CURRENT_TIMESTAMP`` 本就相等，该断言不会失败、但也测不出
本缺陷（失去区分力）——故本用例应在 TZ≠UTC 环境运行。

隔离：``_record_sync_job`` 读 ``get_settings().SQLITE_PATH``，这里 monkeypatch 到用例
私有 ``tmp_path``，避免污染会话级共享库。
"""
from __future__ import annotations

import sqlite3
from datetime import datetime
from types import SimpleNamespace

import pytest

from app.services import sync_service

_DATA_JOBS_DDL = """
CREATE TABLE data_jobs (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    job_type TEXT NOT NULL,
    trade_date TEXT NOT NULL,
    status TEXT NOT NULL,
    current_step TEXT,
    started_at TEXT,
    finished_at TEXT,
    error_message TEXT,
    traceback TEXT,
    duration_ms INTEGER NOT NULL,
    created_at TEXT DEFAULT (CURRENT_TIMESTAMP) NOT NULL,
    updated_at TEXT DEFAULT (CURRENT_TIMESTAMP) NOT NULL,
    UNIQUE (job_type, trade_date)
)
"""


@pytest.fixture
def job_db(tmp_path, monkeypatch):
    """私有 tmp 库（含 data_jobs 表），并把 ``sync_service.get_settings`` 指向它。"""
    db_path = tmp_path / "app.db"
    with sqlite3.connect(str(db_path)) as conn:
        conn.execute(_DATA_JOBS_DDL)
        conn.commit()
    monkeypatch.setattr(sync_service, "get_settings",
                        lambda: SimpleNamespace(SQLITE_PATH=db_path))
    return db_path


def _row(db_path) -> tuple[str, str]:
    with sqlite3.connect(str(db_path)) as conn:
        r = conn.execute(
            "SELECT created_at, updated_at FROM data_jobs").fetchone()
    assert r is not None, "data_jobs 未写入"
    return r[0], r[1]


def test_record_sync_job_created_and_updated_same_tz(job_db):
    """首插后两列时区一致（|created_at-updated_at| <= 2s）。"""
    sync_service._record_sync_job("incremental", "SUCCESS", 1234)

    created_at, updated_at = _row(job_db)
    c = datetime.fromisoformat(created_at)
    u = datetime.fromisoformat(updated_at)
    assert abs((c - u).total_seconds()) <= 2, (
        f"created_at/updated_at 时区不一致（相差 {c - u}）：{created_at!r} vs {updated_at!r}")


def test_record_sync_job_updated_at_is_local_now(job_db):
    """updated_at 必须逼近本机 now（≤1h），而非被 UTC 默认值拉偏 8h。"""
    sync_service._record_sync_job("incremental", "SUCCESS", 1234)

    _created_at, updated_at = _row(job_db)
    u = datetime.fromisoformat(updated_at)
    assert abs((datetime.now() - u).total_seconds()) <= 3600, (
        f"updated_at 距本机 now 超过 1h（疑似仍走 UTC 默认值）：{updated_at!r}")


def test_record_sync_job_upsert_still_updates_updated_at(job_db):
    """同日同模式二次执行（upsert 分支）仍刷新 updated_at，且不报错。"""
    sync_service._record_sync_job("incremental", "SUCCESS", 10)
    sync_service._record_sync_job("incremental", "FAILED", 20)

    with sqlite3.connect(str(job_db)) as conn:
        rows = conn.execute(
            "SELECT status, duration_ms, created_at, updated_at FROM data_jobs").fetchall()
    assert len(rows) == 1, f"upsert 应保持单行: {rows}"
    status, duration_ms, created_at, updated_at = rows[0]
    assert status == "FAILED" and duration_ms == 20
    # updated_at 仍与本机 now 一致（未被 UTC 默认值污染）
    assert abs((datetime.now() - datetime.fromisoformat(updated_at)).total_seconds()) <= 3600
    assert abs((datetime.fromisoformat(created_at)
                - datetime.fromisoformat(updated_at)).total_seconds()) <= 3600
