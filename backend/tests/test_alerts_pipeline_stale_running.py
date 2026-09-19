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


@pytest.mark.parametrize("hours", [25, 30, 72])
def test_stale_running_older_than_24h_still_triggers(health_db, hours):
    """卡死超过 24h 的 RUNNING **仍然必须告警**（非终态行不受 24h 查询窗口约束）。

    修复前：SQL 的 ``COALESCE(finished_at, started_at) >= now-24h`` 会把这类行直接筛掉
    ⇒ 返回 ``[]``、永不告警。而本项目实证就有租赁过期 5 天的僵尸行——恰恰是最该告警的
    那一类残留。
    """
    _seed(health_db, status="RUNNING", started_at=_ts(timedelta(hours=-hours)),
          finished_at=None)

    out = alerts._check_data_health(_rule())

    entries = _pipeline_entries(out)
    assert len(entries) == 1, f"卡死 {hours}h 的 RUNNING 未告警: {out}"
    assert entries[0]["failed_jobs"] == 1


def test_stale_running_beyond_top5_is_not_squeezed_out(health_db):
    """陈旧 RUNNING 即使不在最近 5 行之内，也不能被 ``LIMIT 5`` 挤出。"""
    _seed(health_db, status="RUNNING", started_at=_ts(timedelta(hours=-40)),
          finished_at=None, trade_date="2026-08-01")
    for i in range(6):
        _seed(health_db, status="SUCCESS", started_at=_ts(timedelta(hours=-(i + 1))),
              finished_at=_ts(timedelta(hours=-(i + 1))),
              trade_date=f"2026-09-{10 + i}")

    out = alerts._check_data_health(_rule())

    entries = _pipeline_entries(out)
    assert len(entries) == 1, f"陈旧 RUNNING 被 LIMIT 5 挤出: {out}"
    assert "RUNNING" in entries[0]["last_error"], entries[0]


def test_stale_running_keeps_original_error_and_hint(health_db):
    """陈旧 RUNNING 自带 error_message 时，原错误与"进程退出残留"提示必须**都在**。

    修复前只显示原错误 ⇒ 最关键的根因线索（疑似进程退出）被吞掉。
    """
    _seed(health_db, status="RUNNING", started_at=_ts(timedelta(hours=-8)),
          finished_at=None, error_message="partial-err")

    out = alerts._check_data_health(_rule())

    entries = _pipeline_entries(out)
    assert len(entries) == 1
    last = entries[0]["last_error"]
    assert "partial-err" in last, last
    assert "进程退出残留" in last, last


def test_failed_with_null_error_message_renders_status(health_db):
    """FAILED 且 error_message 为 NULL ⇒ last_error 必须是状态名，绝不能是 "None"。

    覆盖漏洞：修复前后**都一样绿**（缺这条就没有用例走 ``else status`` 分支）。
    """
    _seed(health_db, status="FAILED", started_at=_ts(timedelta(hours=-1)),
          finished_at=_ts(timedelta(hours=-1)), error_message=None)

    out = alerts._check_data_health(_rule())

    entries = _pipeline_entries(out)
    assert len(entries) == 1
    assert entries[0]["last_error"] == "FAILED", entries[0]


@pytest.mark.parametrize("raw, expected_none", [
    (None, True), ("", True), ("not-a-date", True), ("GARBAGE-TS", True),
    ("2026-09-19 10:00:00", False),
    ("2026-09-19T10:00:00", False),
    ("2026-09-19 10:00:00.123456", False),
])
def test_parse_db_ts_contract(raw, expected_none) -> None:
    """锁定 ``_parse_db_ts`` 契约：不可解析 ⇒ None（fail-loud 告警而非静默跳过）。"""
    parsed = alerts._parse_db_ts(raw)
    assert (parsed is None) is expected_none
    if not expected_none:
        assert parsed.replace(microsecond=0).strftime("%Y-%m-%d %H:%M:%S") \
            == "2026-09-19 10:00:00"
