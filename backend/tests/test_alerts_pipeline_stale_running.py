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


def test_two_identical_stale_rows_count_separately(health_db):
    """两条**内容完全相同**的陈旧残留必须各计一次（去重按行 id，不按三元组）。

    QA 实测反例：按 (status, error_message, ts) 判重时，两条同样的 >24h 陈旧行只报 1。
    """
    for trade_date in ("2026-08-01", "2026-08-02"):
        _seed(health_db, status="RUNNING", started_at=_ts(timedelta(hours=-40)),
              finished_at=None, trade_date=trade_date)

    out = alerts._check_data_health(_rule())

    entries = _pipeline_entries(out)
    assert len(entries) == 1, f"陈旧 RUNNING 未告警: {out}"
    assert entries[0]["failed_jobs"] == 2, f"相同内容的行被去重吞掉: {entries}"


def test_supplement_caps_at_twenty(health_db):
    """补捞上限 20 必须有人守（QA 实测把 LIMIT 20 改成 1 时全套仍绿）。

    这里用 25 条 >24h 残留把这个上限**钉住**：改上限必须让本用例变红。
    """
    for i in range(25):
        _seed(health_db, status="RUNNING", started_at=_ts(timedelta(hours=-40)),
              finished_at=None, trade_date=f"2026-{1 + i // 28:02d}-{1 + i % 28:02d}")

    out = alerts._check_data_health(_rule())

    entries = _pipeline_entries(out)
    assert len(entries) == 1
    assert entries[0]["failed_jobs"] == 20, f"补捞上限变了却没人发现: {entries}"


def test_hint_survives_long_error_message(health_db):
    """长长的 error_message 之下，"进程退出残留"提示必须**仍在**（故 hint 前置）。

    QA 实测：hint 拼在后面时，sync 的真实 error_message（可达 500 字符，见
    sync_service.py:473）会把提示从 [:120] 的尾部整段挤掉 ⇒ 提示白写。
    """
    _seed(health_db, status="RUNNING", started_at=_ts(timedelta(hours=-8)),
          finished_at=None, error_message="E" * 200)

    out = alerts._check_data_health(_rule())

    entries = _pipeline_entries(out)
    assert len(entries) == 1
    last = entries[0]["last_error"]
    assert "进程退出残留" in last, f"长错误把提示挤掉了: {last!r}"


def test_pending_is_not_reported_as_failure(health_db):
    """PENDING = 排队中（尚未开始），不得被当成流水线故障。

    补捞 `finished_at IS NULL` 会把 PENDING 带进结果集（旧注释里"PENDING 进不了
    结果集"的说法在补捞引入后已失效），故需显式跳过。
    """
    _seed(health_db, status="PENDING", started_at=None, finished_at=None)

    out = alerts._check_data_health(_rule())

    assert _pipeline_entries(out) == [], f"PENDING 被误报成失败: {out}"


def test_running_with_null_ts_never_renders_none(health_db):
    """RUNNING 且 started_at 为 NULL ⇒ 告警文案里绝不能出现 "None"。"""
    _seed(health_db, status="RUNNING", started_at=None, finished_at=None)

    out = alerts._check_data_health(_rule())

    entries = _pipeline_entries(out)
    assert len(entries) == 1, f"无 started_at 的 RUNNING 未告警: {out}"
    assert "None" not in entries[0]["last_error"], entries[0]


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
