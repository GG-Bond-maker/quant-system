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


def test_resume_all_completed_not_misjudged_as_noop(isolated_sync, monkeypatch):
    """边界加固：resume 且**全部**标的已完成 → 不得被判成 NOOP/FAILED。

    这是缺陷 D 的配套守卫回归。``_run_incremental``（同理 ``_run_repair`` /
    ``_run_rebuild``）在 resume 时若 ``pending`` 为空会直接 return、**不进循环**，
    旧实现下 ``_sync.done`` 停在 0 → worker 的 ``noop = total > 0 and done == 0``
    把一次正常的「全部已最新」误报成 FAILED(NOOP)，反过来污染 /overview 健康灯。

    既有用例覆盖不到该分支：``test_resume_retries_failed_skips_completed`` 用
    ``completed={A.SZ}`` + ``symbols=[A.SZ, B.SZ]`` → ``pending=[B.SZ]`` 非空。
    本用例让 ``completed`` ⊇ ``symbols``，使 pending 真正为空，走 worker 全链路
    断言终态仍为 SUCCESS。
    """
    recorded: dict = {}
    fetched: list[str] = []

    def fake_fetch(code, start, end, adjusts=("", "hfq"), fetcher=None):
        fetched.append(code)
        return 5, []

    monkeypatch.setattr(
        "app.data.ingest.tasks.fetch_and_write_daily_bars", fake_fetch)
    monkeypatch.setattr(
        ss, "_record_sync_job",
        lambda mode, status, dur, err=None: recorded.update(
            mode=mode, status=status, err=err))
    monkeypatch.setattr(ss, "_persist_sync_state", lambda *a, **k: None)

    symbols = ["A.SZ", "B.SZ"]
    isolated_sync.completed.update(symbols)  # 上次已全部完成 → pending 为空
    isolated_sync.total = len(symbols)      # 生产语义：total = 标的数 > 0
    isolated_sync.done = 0
    isolated_sync.rows_written = 0
    isolated_sync.error = None
    isolated_sync.cancel_event.clear()
    isolated_sync.task_id = None

    ss._sync_worker("incremental", symbols, resume=True)

    assert fetched == [], "全部已完成时不应再发起任何抓取"
    # 关键断言：done 推到 total（而非停在 0），故 noop 为 False、仍记 SUCCESS
    assert isolated_sync.done == len(symbols)
    assert recorded.get("status") == "SUCCESS", recorded


@pytest.mark.parametrize("mode,patched,entry", [
    ("repair", "app.data.ingest.tasks.fetch_and_write_daily_bars", "fetch"),
    ("rebuild", "app.data.repair.build_qfq_dataset", "build_qfq"),
])
def test_resume_all_completed_not_misjudged_as_noop_other_modes(
        mode, patched, entry, isolated_sync, monkeypatch):
    """同上，但覆盖 ``_run_repair`` / ``_run_rebuild`` 的同款分支。

    QA 复查发现：incremental 分支已有用例保护，但 repair(L291) / rebuild(L335)
    的 ``if not pending:`` grep 全 ``tests/`` 仍无用例触达 —— 三处是同一次加固
    一起加的，只保护一处等于留了两个未落的雷。本用例按 mode 参数化补齐。
    """
    recorded: dict = {}
    called: list[str] = []

    if entry == "fetch":
        def _stub(code, start, end, adjusts=("", "hfq"), fetcher=None):
            called.append(code)
            return 5, []
    else:
        def _stub(root, sym):
            called.append(sym)
            return 5

    monkeypatch.setattr(patched, _stub)
    monkeypatch.setattr(ss, "invalidate_stats_cache", lambda: None)
    monkeypatch.setattr(
        ss, "_record_sync_job",
        lambda m, status, dur, err=None: recorded.update(
            mode=m, status=status, err=err))
    monkeypatch.setattr(ss, "_persist_sync_state", lambda *a, **k: None)

    symbols = ["A.SZ", "B.SZ"]
    isolated_sync.completed.update(symbols)  # 上次已全部完成 → pending 为空
    isolated_sync.total = len(symbols)
    isolated_sync.done = 0
    isolated_sync.rows_written = 0
    isolated_sync.error = None
    isolated_sync.cancel_event.clear()
    isolated_sync.task_id = None

    ss._sync_worker(mode, symbols, resume=True)

    assert called == [], f"{mode}: 全部已完成时不应再发起任何处理"
    assert isolated_sync.done == len(symbols)
    assert recorded.get("status") == "SUCCESS", recorded


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


def _prep_worker(monkeypatch, sync, recorded: dict, runner) -> None:
    """公共准备：复位 worker 输入、隔离落库/持久化副作用、注入 runner。"""
    sync.error = None
    sync.cancel_event.clear()
    sync.task_id = None
    sync.rows_written = 0
    monkeypatch.setattr(
        ss, "_record_sync_job",
        lambda mode, status, dur, err=None: recorded.update(
            mode=mode, status=status, err=err))
    monkeypatch.setattr(ss, "_persist_sync_state", lambda *a, **k: None)
    monkeypatch.setattr(ss, "_run_incremental", runner)


def test_sync_worker_marks_noop_as_failed(isolated_sync, monkeypatch):
    """缺陷 D（P1）：total>0 但 done==0（循环一次都没跑）→ FAILED/NOOP，不得冒充 SUCCESS。

    复现：09-14 的 sync_incremental 以 1109ms 空跑记了 SUCCESS（2476 只待回补却
    一只都没处理），点亮了 /overview 健康灯、污染 AI 日报与 task-stats。修复后
    done 未推进即判 NOOP 并如实落 FAILED。
    """
    recorded: dict = {}
    # runner 什么都不做：done 不推进（模拟空跑）
    _prep_worker(monkeypatch, isolated_sync, recorded,
                 lambda symbols, resume=False: None)
    isolated_sync.total = 3
    isolated_sync.done = 0

    ss._sync_worker("incremental", ["A.SZ", "B.SZ", "C.SZ"], resume=False)

    assert recorded.get("status") == "FAILED", recorded
    assert "NOOP" in (recorded.get("err") or ""), recorded


def test_sync_worker_keeps_success_when_work_done(isolated_sync, monkeypatch):
    """对照：处理了标的（done>0）仍记 SUCCESS，不误伤「全部已最新」既有语义。"""
    recorded: dict = {}

    def fake_runner(symbols, resume=False):
        with ss._sync.lock:
            ss._sync.done = len(symbols)
            ss._sync.rows_written = 5

    _prep_worker(monkeypatch, isolated_sync, recorded, fake_runner)
    isolated_sync.total = 2
    isolated_sync.done = 0

    ss._sync_worker("incremental", ["A.SZ", "B.SZ"], resume=False)

    assert recorded.get("status") == "SUCCESS", recorded
