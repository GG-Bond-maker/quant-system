"""批 2 / 批 3 的**对抗性**守卫（QA2，非作者用例的复刻）。

为什么另起一份
--------------
作者 20 条用例全部是**单标的**场景（SYM 一只，第 1 轮失败 → 第 2 轮成功/仍失败）。
单标的场景有一个结构性盲区：**它抓不到「清太多」**。

    不变式要求：进 completed 时 `failed.discard(sym)`（只摘自己）。
    但把它写成 `failed.clear()`（清空全部）也能让作者的
    `test_*_second_round_success_removes_failed` / `test_*_still_failed_keeps_failed`
    单标的用例**全绿** —— 前者断言 "SYM ∉ failed"（clear 也满足），
    后者压根不走成功分支（clear 执行不到）。
    ⇒ 只守住"没清"而守不住"清太多"，等于没守。

本文件用**多标的 + 成功者排在失败者之后**的编排（[B, C, A]：B/C 仍败、A 成功）
来钉死这条：A 成功时若误用 `clear()`，会把同轮刚记下的 B/C 一并抹掉。

覆盖
----
1. 只增不减**真的消失**（端到端 3 只全失败 → 全恢复 ⇒ failed==set()）
2. 反向且**抗过度清理**：3 只里只有 A 恢复 ⇒ 必须是 {B,C}，不得是 {}
3. 消费侧：上述两种状态在 HTTP 线路上的 `data.failed_count` 与内部集合一致
4. rebuild 可重试性：失败标的不得被 `_resume_skip_set("rebuild")` 跳过（带"跳过确实生效"的对照）
5. 批 3 日志 `[:200]` 只套 f-string、提示语在括号**外**（超长异常 repr 下提示仍在尾部）

纪律：不打桩被测逻辑；只替身外部 IO（日历/抓取/重构/TestClient）；
隔离 `_sync` 单例；HTTP 恒 200，断言信封 `code` 而非 HTTP 状态。
"""
from __future__ import annotations

import contextlib
import os
import sys
from datetime import date, timedelta
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

BACKEND_ROOT = Path(__file__).resolve().parents[1]
if str(BACKEND_ROOT) not in sys.path:
    sys.path.insert(0, str(BACKEND_ROOT))

import app.api.v1.datacenter as dc  # noqa: E402
import app.data.ingest.tasks as tasks  # noqa: E402
from app.data import repair  # noqa: E402
from app.domain.calendar import build_calendar  # noqa: E402
from app.main import app  # noqa: E402
import app.services.sync_service as ss  # noqa: E402

A, B, C = "600000.SH", "600001.SH", "600002.SH"
HINT = "（已记入 failed，可 resume 重试）"
_ADMIN_H = {
    "Authorization": f"Bearer {os.environ.get('ADMIN_TOKEN', 'aqp-dev-token-change-me')}"
}


@pytest.fixture()
def iso_sync():
    """隔离并复位 `_sync` 单例（防跨用例泄漏）。"""
    s = dc._sync
    saved = {
        "running": s.running, "completed": set(s.completed), "failed": set(s.failed),
        "completed_mode": s.completed_mode, "error": s.error, "done": s.done,
        "total": s.total, "current": s.current, "task_id": s.task_id, "mode": s.mode,
        "rows_written": s.rows_written, "logs": list(s.logs),
    }
    s.cancel_event.clear()
    try:
        yield s
    finally:
        s.running = saved["running"]
        s.completed = saved["completed"]
        s.failed = saved["failed"]
        s.completed_mode = saved["completed_mode"]
        s.error = saved["error"]
        s.done = saved["done"]
        s.total = saved["total"]
        s.current = saved["current"]
        s.task_id = saved["task_id"]
        s.mode = saved["mode"]
        s.rows_written = saved["rows_written"]
        s.logs = saved["logs"]
        s.cancel_event.clear()


@pytest.fixture(scope="module")
def client():
    with TestClient(app) as c:
        yield c


def _reset() -> None:
    dc._sync.failed = set()
    dc._sync.completed = set()
    dc._sync.completed_mode = None
    dc._sync.done = 0
    dc._sync.error = None


def _stub_incremental(monkeypatch) -> None:
    """隔离 `_run_incremental` 的日历 / 网络依赖（沿用作者同款手法）。"""
    monkeypatch.setattr(ss, "_refresh_trade_calendar", lambda: None)
    days = [date.today() - timedelta(days=i) for i in range(1, 61)]
    monkeypatch.setattr(ss, "get_calendar", lambda: build_calendar(days))
    monkeypatch.setattr(ss, "_symbol_last_date", lambda sym, year: None)
    monkeypatch.setattr(dc._sync, "log", lambda level, msg: None)


def _fwdb(ok_syms: set[str], boom_msg="all sources down"):
    """按标的分流：在 ok_syms 里的成功，其余抛异常（`code` 口径见 _run_fetch/_run_incremental）。"""
    def _f(code, start, end, adjusts=("", "hfq"), fetcher=None):
        if code in ok_syms:
            return 5, []
        raise RuntimeError(boom_msg)
    return _f


_CODES = {s: s.split(".")[0] for s in (A, B, C)}


# ---------------------------------------------------------------------------
# 1. 只增不减真的消失（端到端 3 只）
# ---------------------------------------------------------------------------
def test_all_recover_leaves_failed_empty(iso_sync, monkeypatch):
    """第 1 轮 A/B/C 全败 ⇒ failed={A,B,C}；第 2 轮 resume=True 全恢复 ⇒ failed 必须为空。

    这是用户批准批 2 的**唯一理由**，故用多标的端到端证明，而不是单标的。
    """
    _stub_incremental(monkeypatch)
    _reset()

    monkeypatch.setattr(tasks, "fetch_and_write_daily_bars", _fwdb(set()))
    ss._run_incremental([A, B, C], resume=False)
    assert dc._sync.failed == {A, B, C}, f"第 1 轮应全记 failed，实得 {dc._sync.failed}"

    monkeypatch.setattr(tasks, "fetch_and_write_daily_bars",
                        _fwdb({_CODES[A], _CODES[B], _CODES[C]}))
    ss._run_incremental([A, B, C], resume=True)

    assert dc._sync.completed == {A, B, C}, f"第 2 轮应全进 completed: {dc._sync.completed}"
    assert dc._sync.failed == set(), (
        f"第 2 轮全部恢复后 failed 仍为 {dc._sync.failed} ⇒ 「只增不减」未消除，"
        "前端「失败 N 只」会把已修好的标的继续算作失败")
    assert dc._sync.snapshot()["failed_count"] == 0


# ---------------------------------------------------------------------------
# 2. 反向 + 抗过度清理（本文件的核心：成功者排在失败者之后）
# ---------------------------------------------------------------------------
def test_partial_recovery_keeps_only_still_failed_incremental(iso_sync, monkeypatch):
    """3 只里只有 A 恢复 ⇒ failed 必须**恰好**是 {B,C}（顺序 [B,C,A]）。

    该编排专门抓「把 discard 写成 clear()」：A 成功若误用 clear()，
    会把本轮刚记下的 B/C 一并抹掉 ⇒ failed 变成 set()，而作者的单标用例全绿。
    """
    _stub_incremental(monkeypatch)
    _reset()

    monkeypatch.setattr(tasks, "fetch_and_write_daily_bars", _fwdb(set()))
    ss._run_incremental([B, C, A], resume=False)
    assert dc._sync.failed == {A, B, C}

    # 只让 A 恢复，且 A 排在 B/C 之后处理
    monkeypatch.setattr(tasks, "fetch_and_write_daily_bars", _fwdb({_CODES[A]}))
    ss._run_incremental([B, C, A], resume=True)

    assert A in dc._sync.completed and A not in dc._sync.failed, "A 恢复后必须摘出 failed"
    assert dc._sync.failed == {B, C}, (
        f"部分恢复后 failed 应为 {{B,C}}，实得 {dc._sync.failed} ⇒ "
        "疑似把 discard 写成无条件 clear()（清太多：把同轮仍失败的标的也抹了）")
    assert dc._sync.snapshot()["failed_count"] == 2


def test_partial_recovery_keeps_only_still_failed_fetch(iso_sync, monkeypatch):
    """同上，但走 `_run_fetch`（datacenter 侧的 discard）。"""
    monkeypatch.setattr(dc._sync, "log", lambda level, msg: None)
    _reset()

    monkeypatch.setattr(tasks, "fetch_and_write_daily_bars", _fwdb(set()))
    dc._run_fetch([B, C, A], "stock", "2024-01-01", "2024-01-31",
                  slot=contextlib.nullcontext())
    assert dc._sync.failed == {A, B, C}

    monkeypatch.setattr(tasks, "fetch_and_write_daily_bars", _fwdb({_CODES[A]}))
    dc._run_fetch([B, C, A], "stock", "2024-01-01", "2024-01-31",
                  slot=contextlib.nullcontext())

    assert A in dc._sync.completed and A not in dc._sync.failed
    assert dc._sync.failed == {B, C}, (
        f"_run_fetch 部分恢复后 failed 应为 {{B,C}}，实得 {dc._sync.failed} ⇒ "
        "疑似无条件 clear()（清太多）")


# ---------------------------------------------------------------------------
# 3. 消费侧：线路上的 failed_count 与内部集合一致
# ---------------------------------------------------------------------------
def test_wire_failed_count_tracks_internal_state(iso_sync, client):
    """两种状态下 HTTP `data.failed_count` 必须等于 `len(_sync.failed)`。"""
    for expect in (0, 2, 3):
        dc._sync.failed = {A, B, C} if expect == 3 else ({B, C} if expect == 2 else set())
        r = client.get("/api/v1/datacenter/sync/status", headers=_ADMIN_H)
        assert r.status_code == 200, r.text
        body = r.json()
        assert body.get("code") == 0, body
        assert body["data"]["failed_count"] == expect, (
            f"线路 failed_count={body['data']['failed_count']} 与内部 "
            f"len(failed)={len(dc._sync.failed)} 不一致 ⇒ 前端读到假数字")


# ---------------------------------------------------------------------------
# 4. rebuild 可重试性（批 3 补齐 failed.add 后，失败标的不得反过来被跳过）
# ---------------------------------------------------------------------------
def test_run_rebuild_failed_symbol_still_retried(iso_sync, monkeypatch):
    """rebuild 失败 ⇒ 记 failed；下一轮 resume=True **不得**被跳过（否则缺口固化）。"""
    calls: list[str] = []
    state = {"ok": False}

    def _build(root, sym):
        calls.append(sym)
        if not state["ok"]:
            raise RuntimeError("qfq rebuild failed")
        return 3

    monkeypatch.setattr(repair, "build_qfq_dataset", _build)
    monkeypatch.setattr(dc._sync, "log", lambda level, msg: None)
    _reset()

    ss._run_rebuild([A], resume=False)
    assert A in dc._sync.failed, "批 3：rebuild 失败必须记入 failed"
    assert A not in dc._sync.completed, "失败不得记入 completed"

    state["ok"] = True
    calls.clear()
    ss._run_rebuild([A], resume=True)
    assert calls == [A], (
        f"失败标的在下一轮被 `_resume_skip_set('rebuild')` 跳过（calls={calls}）⇒ "
        "缺口被永久固化，与 failed 的语义（可重试）自相矛盾")
    assert A in dc._sync.completed and A not in dc._sync.failed

    # 对照（非恒真）：已完成的标的必须被 resume 跳过 —— 证明上面的"未被跳过"有判别力
    calls.clear()
    ss._run_rebuild([A], resume=True)
    assert calls == [], f"对照失败：已完成标的仍被重复处理（calls={calls}）"


# ---------------------------------------------------------------------------
# 5. 批 3 日志：提示语必须在 [:200] 之外（超长异常 repr 下不被截断）
# ---------------------------------------------------------------------------
def test_run_rebuild_long_error_log_keeps_retry_hint(iso_sync, monkeypatch):
    """异常 repr 远超 200 字符时，可重试提示**仍必须完整出现在日志尾部**。

    历史坑：`last_error[:120]` 从头部截而提示拼在尾部 ⇒ P0 提示 100% 被截掉。
    这里用 500 字符的异常 repr 实际触发，而不是只看源码字符串。
    """
    calls: list[tuple[str, str]] = []

    def _boom(root, sym):
        raise RuntimeError("X" * 500)

    monkeypatch.setattr(repair, "build_qfq_dataset", _boom)
    monkeypatch.setattr(dc._sync, "log", lambda level, msg: calls.append((level, msg)))
    _reset()

    ss._run_rebuild([A], resume=False)

    mine = [m for lv, m in calls if A in m]
    assert mine, "rebuild 失败必须留下与本标的相关日志"
    msg = mine[-1]
    assert len(msg) > 200, f"本用例需要超长日志才能验证截断行为，实得 len={len(msg)}"
    assert HINT in msg, (
        f"超长异常下可重试提示被 [:200] 截断（msg={msg[:80]}...）⇒ 用户看不到补救路径")
    assert msg.endswith(HINT), f"提示不在尾部: ...{msg[-40:]}"


def test_rebuild_log_slice_placed_on_fstring_only():
    """源码级：rebuild except 的日志必须是 `f"..."[:200] + "提示"`（提示在切片外）。"""
    src = (BACKEND_ROOT / "app" / "services" / "sync_service.py").read_text(encoding="utf-8")
    blk = src.split("def _run_rebuild(", 1)[1].split("def _sync_worker(", 1)[0]
    i_slice = blk.find("[:200]")
    i_hint = blk.find(HINT)
    assert i_slice != -1, "rebuild except 未找到 [:200]（锚点失效，需重审本用例）"
    assert i_hint != -1, "rebuild except 未找到可重试提示（锚点失效）"
    assert i_slice < i_hint, "提示语出现在切片之前 ⇒ 会被 [:200] 截掉"
    between = blk[i_slice + len("[:200]"):i_hint]
    assert "+" in between, (
        "提示语被写进被切片的对象内部（形如 f'...{HINT}'[:200]）⇒ 超长异常下必被截断")
