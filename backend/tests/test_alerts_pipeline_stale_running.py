"""项2 回归：pipeline 健康检查必须把**陈旧 RUNNING**（进程退出残留）计入失败告警。

镜像缺陷：修复前 ``failed = [r for r in row if r[0] not in ("SUCCESS","RUNNING")]``
⇒ 卡死的 RUNNING 行**永远不会告警**（与"RUNNING 当绿灯"方向相反的另一面）。
修复后：RUNNING 仅在**超过** ``PIPELINE_STALE_RUNNING_HOURS``（=6h）仍未落终态时计入；
新鲜 RUNNING 不得误报；SUCCESS 永不计。

隔离：``_check_data_health`` 读 ``get_settings().SQLITE_PATH``，这里 monkeypatch 到
用例私有 ``tmp_path``。
"""
from __future__ import annotations

import json
import sqlite3
from datetime import datetime, timedelta
from types import SimpleNamespace

import pytest

from app.api.v1 import alerts

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


def _rule(metric: str = "pipeline"):
    return SimpleNamespace(params_json=json.dumps({"metric": metric}))


def _ts(delta: timedelta) -> str:
    return (datetime.now() + delta).strftime("%Y-%m-%d %H:%M:%S")


def _seed(db_path, *, status, started_at=None, finished_at=None,
          error_message=None, job_type="daily_pipeline", trade_date="2026-09-19") -> None:
    with sqlite3.connect(str(db_path)) as conn:
        conn.execute(
            "INSERT INTO data_jobs (job_type, trade_date, status, started_at, "
            "finished_at, error_message, duration_ms) VALUES (?,?,?,?,?,?,?)",
            (job_type, trade_date, status, started_at, finished_at,
             error_message, 1000))
        conn.commit()


@pytest.fixture
def health_db(tmp_path, monkeypatch):
    """私有 tmp 库（含 data_jobs 表）+ 把 alerts.get_settings 指向它。"""
    db_path = tmp_path / "app.db"
    with sqlite3.connect(str(db_path)) as conn:
        conn.execute(_DATA_JOBS_DDL)
        conn.commit()
    monkeypatch.setattr(
        alerts, "get_settings",
        lambda: SimpleNamespace(SQLITE_PATH=db_path, DATA_ROOT=tmp_path))
    return db_path


def _pipeline_entries(out: list[dict]) -> list[dict]:
    return [d for d in out if d.get("metric") == "pipeline"]


def test_stale_running_triggers_failure_alert(health_db):
    """陈旧 RUNNING（started_at=now-8h，无 finished_at）⇒ 触发 1 条，且含 "RUNNING"。"""
    _seed(health_db, status="RUNNING", started_at=_ts(timedelta(hours=-8)),
          finished_at=None, error_message=None)

    out = alerts._check_data_health(_rule())

    entries = _pipeline_entries(out)
    assert len(entries) == 1, f"陈旧 RUNNING 未告警: {out}"
    assert entries[0]["failed_jobs"] == 1
    assert "RUNNING" in entries[0]["last_error"], entries[0]
    assert "None" not in entries[0]["last_error"], "不得渲染出字符串 None"


def test_fresh_running_does_not_trigger(health_db):
    """新鲜 RUNNING（now-10min）⇒ 确实还在跑，不得误报。"""
    _seed(health_db, status="RUNNING", started_at=_ts(timedelta(minutes=-10)),
          finished_at=None)

    out = alerts._check_data_health(_rule())

    assert _pipeline_entries(out) == [], f"新鲜 RUNNING 被误报: {out}"


def test_failed_job_triggers_with_real_error(health_db):
    """FAILED（有 error_message）⇒ 触发，last_error 用真实错误文本。"""
    _seed(health_db, status="FAILED", started_at=_ts(timedelta(hours=-1)),
          finished_at=_ts(timedelta(hours=-1)), error_message="boom-connect")

    out = alerts._check_data_health(_rule())

    entries = _pipeline_entries(out)
    assert len(entries) == 1, f"FAILED 未告警: {out}"
    assert "boom-connect" in entries[0]["last_error"], entries[0]


def test_success_never_triggers(health_db):
    """SUCCESS ⇒ 永不告警。"""
    _seed(health_db, status="SUCCESS", started_at=_ts(timedelta(hours=-1)),
          finished_at=_ts(timedelta(hours=-1)))

    out = alerts._check_data_health(_rule())

    assert _pipeline_entries(out) == [], f"SUCCESS 被误报: {out}"


def test_stale_running_threshold_is_monkeypatchable(health_db, monkeypatch):
    """阈值读的是**函数体内**的模块级常量（便于调参/测试）：把窗口放大到 100h 后，
    now-8h 的 RUNNING 变成"新鲜" ⇒ 不再告警。"""
    monkeypatch.setattr(alerts, "PIPELINE_STALE_RUNNING_HOURS", 100)
    _seed(health_db, status="RUNNING", started_at=_ts(timedelta(hours=-8)),
          finished_at=None)

    out = alerts._check_data_health(_rule())

    assert _pipeline_entries(out) == [], f"阈值放大后不应再告警: {out}"


def test_only_most_recent_five_rows_considered(health_db):
    """沿用原语义：只取最近 5 行（id DESC）。第 6 条（最旧）不入结果集。"""
    for i in range(6):
        _seed(health_db, status="FAILED", started_at=_ts(timedelta(hours=-(i + 1))),
              finished_at=_ts(timedelta(hours=-(i + 1))),
              error_message=f"err-{i}", trade_date=f"2026-09-{10 + i}")

    out = alerts._check_data_health(_rule())

    entries = _pipeline_entries(out)
    assert len(entries) == 1
    assert entries[0]["failed_jobs"] == 5, f"应只统计最近 5 行: {entries}"
