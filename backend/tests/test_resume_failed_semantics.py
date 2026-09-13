"""Task 9（整改计划 A-P1-4）：datacenter 断点续传失败语义。

背景：增量同步把"三口径全败"的 symbol 也记入 completed 并持久化，
resume 永久跳过 → 数据缺口静默固化（审核报告 P1-4）。
修复：completed（有数据/无数据但非故障）与 failed（抓取故障，可重试）
分离；resume 只跳过 completed。
"""
from __future__ import annotations

import sys
from datetime import date, timedelta
from pathlib import Path

BACKEND_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(BACKEND_ROOT))

import pytest  # noqa: E402

# Task 16：同步状态机下沉 app/services/sync_service，测试随之指向服务模块
import app.services.sync_service as ss  # noqa: E402


@pytest.fixture()
def isolated_sync(monkeypatch):
    """复位同步状态并隔离网络/日历/DB 依赖。"""
    ss._sync.completed.clear()
    ss._sync.failed.clear()
    ss._sync.done = 0
    ss._sync.total = 0
    ss._sync.logs.clear()

    monkeypatch.setattr(ss, "_refresh_trade_calendar", lambda: None)
    # 60 个自然日的伪日历（今天-1 为目标交易日）；用真实 CalendarData 类型
    from app.domain.calendar import build_calendar

    days = [date.today() - timedelta(days=i) for i in range(1, 61)]
    monkeypatch.setattr(ss, "get_calendar", lambda: build_calendar(days))
    monkeypatch.setattr(ss, "_symbol_last_date", lambda sym, year: None)
    yield ss._sync


def test_all_source_failure_not_marked_completed(isolated_sync, monkeypatch):
    """三口径全败（0 行 + 全部口径抛错）→ 进 failed，不进 completed。"""
    calls: list[str] = []

    def fake_fetch(code, start, end, adjusts=("", "hfq"), fetcher=None):
        calls.extend(adjusts)
        return 0, list(adjusts)

    monkeypatch.setattr(ss, "fetch_and_write_daily_bars", fake_fetch,
                        raising=False)
    monkeypatch.setattr(
        "app.data.ingest.tasks.fetch_and_write_daily_bars", fake_fetch)
    isolated_sync.total = 1
    ss._run_incremental(["600001.SH"], resume=False)
    assert "600001.SH" in isolated_sync.failed
    assert "600001.SH" not in isolated_sync.completed
    assert calls == ["", "hfq"]


def test_no_data_marked_completed(isolated_sync, monkeypatch):
    """全口径空数据（停牌/退市，无异常）→ completed（resume 不空转重试）。"""

    def fake_fetch(code, start, end, adjusts=("", "hfq"), fetcher=None):
        return 0, []

    monkeypatch.setattr(
        "app.data.ingest.tasks.fetch_and_write_daily_bars", fake_fetch)
    isolated_sync.total = 1
    ss._run_incremental(["600001.SH"], resume=False)
    assert "600001.SH" in isolated_sync.completed
    assert "600001.SH" not in isolated_sync.failed


def test_success_marked_completed(isolated_sync, monkeypatch):
    """正常写入（n>0 无失败口径）→ completed。"""

    def fake_fetch(code, start, end, adjusts=("", "hfq"), fetcher=None):
        return 2, []

    monkeypatch.setattr(
        "app.data.ingest.tasks.fetch_and_write_daily_bars", fake_fetch)
    isolated_sync.total = 1
    ss._run_incremental(["600001.SH"], resume=False)
    assert "600001.SH" in isolated_sync.completed
    assert "600001.SH" not in isolated_sync.failed


def test_resume_retries_failed_skips_completed(isolated_sync, monkeypatch):
    """resume：completed 跳过、failed 重试。"""
    retried: list[str] = []
    isolated_sync.completed.add("A.SZ")

    def fake_fetch(code, start, end, adjusts=("", "hfq"), fetcher=None):
        retried.append(code)
        return 0, []

    monkeypatch.setattr(
        "app.data.ingest.tasks.fetch_and_write_daily_bars", fake_fetch)
    monkeypatch.setattr(ss, "_symbol_last_date", lambda sym, year: None)
    isolated_sync.total = 2
    ss._run_incremental(["A.SZ", "B.SZ"], resume=True)
    assert retried == ["B"]  # 只重试 failed/未完成的 B；A 被跳过


def test_partial_failure_goes_to_failed(isolated_sync, monkeypatch):
    """部分口径成功（raw 写入、hfq 抛错）→ failed（下次 resume 补齐 hfq）。"""

    def fake_fetch(code, start, end, adjusts=("", "hfq"), fetcher=None):
        return 3, ["hfq"]

    monkeypatch.setattr(
        "app.data.ingest.tasks.fetch_and_write_daily_bars", fake_fetch)
    isolated_sync.total = 1
    ss._run_incremental(["600001.SH"], resume=False)
    assert "600001.SH" in isolated_sync.failed
    assert "600001.SH" not in isolated_sync.completed
