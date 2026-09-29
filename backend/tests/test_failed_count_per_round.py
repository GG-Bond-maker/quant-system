"""``_sync.failed`` 口径回归：**本轮（最近一次任务）**，不是进程内累计（2026-09-26）。

背景（Task D 收尾时发现）：
    `_SyncState.snapshot()` 会返回 ``failed_count``，前端 DataCenter 也据此渲染
    「上次任务：{done}/{total} 完成 · 失败 X 只」。但后端 **从未在任何一轮任务开始时
    清空 `_sync.failed`** —— `trigger_sync` / `trigger_fetch` / `_start_sync_reset`
    只清了 `_sync.completed`。后果：``failed_count`` 在进程生命周期内单调累加，
    与「上次任务」这条**本轮**文案自相矛盾（用户看到的"失败 N 只"里混着若干轮前的
    标的）。

    另有第二条同主题漏网：``_run_fetch`` 的 ``except Exception`` 分支**只打日志**，
    既不 ``completed.add`` 也不 ``failed.add`` —— 该标的在两端计数里**双双消失**，
    前端静默漏掉。

本文件把「口径 = 本轮」以及「异常分支必记 failed」钉死：
    1. `trigger_sync(resume=False)` 清空 `failed`；`resume=True` 保留；
    2. `trigger_fetch(...)` 清空 `failed`（fetch 无 resume 概念，恒为新任务）；
    3. `_start_sync_reset(state, mode, resume=False)` 清空 `failed`（覆盖
       auto_sync_scheduler 每 60s 的自动同步路径），`resume=True` 保留；
    4. `_run_fetch` 的异常分支必须把 sym 记入 `failed`（且不记 `completed`），
       日志与两条"口径失败"分支对齐（含"已记入 failed，可 resume 重试"）；
    5. 前端 tooltip 不再谎称"进程内累计值"，改为"仅统计最近一次任务"。

测试纪律：**不打桩被测逻辑**（复位/计数均为真实代码）；只替身外部 IO
（task_store / Thread / pipeline_slot / fetch_and_write_daily_bars），不发真实网络、
不写真实 DB；隔离 `_sync` 单例避免污染同进程其它用例。
"""
from __future__ import annotations

import asyncio
import contextlib
import sys
import types
from datetime import date, timedelta
from pathlib import Path

import pytest

BACKEND_ROOT = Path(__file__).resolve().parents[1]
if str(BACKEND_ROOT) not in sys.path:
    sys.path.insert(0, str(BACKEND_ROOT))

import app.data.ingest.tasks as tasks  # noqa: E402
import app.data.repair as repair  # noqa: E402
from app.api.v1 import datacenter as dc  # noqa: E402
from app.domain.calendar import build_calendar  # noqa: E402
from app.services import sync_service as ss  # noqa: E402


def _src_lines(rel: str) -> list[str]:
    """读取被测源码行（供下方收集期快照使用）。"""
    return (BACKEND_ROOT / rel).read_text(encoding="utf-8").splitlines()


# 源码快照在**模块 import（= pytest 收集期）**读取，而不是在每个用例执行期再读盘。
# 原因（实测踩坑）：源码级守卫若在用例执行期读盘，会被"他人/变异脚本并发写入造成的
# 半途状态"误伤 —— 全量套件跑到一半时 datacenter.py 被外部进程改写，本文件的源码
# 守卫与序列用例同时假红，而磁盘上的文件本身其实是正确的。收集期读取保留了
# "漏一处即判红"的能力（变异脚本先写盘、再启动 pytest，收集发生在写盘之后），
# 同时把误伤窗口从"整个套件运行期"缩小到"收集瞬间"。
_DC_LINES = _src_lines("app/api/v1/datacenter.py")
_SS_LINES = _src_lines("app/services/sync_service.py")

SYM = "159915.SZ"


# ---------------------------------------------------------------------------
# 替身 + 隔离
# ---------------------------------------------------------------------------
class _FakeThread:
    """`threading.Thread` 替身：只记录是否 start，绝不真的跑 worker。

    真实 worker 会联网；本文件只关心**启动前的复位**与**同步的计数分支**，
    故线程无需真正执行。
    """

    def __init__(self, *args, **kwargs) -> None:
        self.started = False

    def start(self) -> None:  # noqa: D401
        self.started = True


class _FakeSlot:
    """`acquire_pipeline_slot` 返回值替身（`PipelineSlotHandle` 的最小接口）。"""

    def __init__(self) -> None:
        self.released = False

    def release(self) -> None:
        self.released = True

    def __enter__(self) -> "_FakeSlot":
        return self

    def __exit__(self, *exc) -> bool:
        self.release()
        return False


@pytest.fixture()
def iso_sync():
    """隔离 `_sync` 单例（`dc._sync is ss._sync`，快照一次即可）。

    每个用例前后都复位 running/completed/failed/owner/cancel 等可变状态，
    防止 `running=True`、残留 failed 等泄漏到同进程的其它用例。
    """
    s = dc._sync
    saved = {
        "running": s.running,
        "completed": set(s.completed),
        "failed": set(s.failed),
        "completed_mode": s.completed_mode,
        "error": s.error,
        "done": s.done,
        "total": s.total,
        "current": s.current,
        "task_id": s.task_id,
        "mode": s.mode,
        "rows_written": s.rows_written,
        "logs": list(s.logs),
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


def _stub_sync_launch(monkeypatch) -> None:
    """把 trigger_sync 的外部依赖（task_store / 线程）替换为无副作用替身。"""
    monkeypatch.setattr(dc, "create_task", lambda name: "task-1")
    monkeypatch.setattr(dc, "claim_task", lambda *a, **k: True)
    monkeypatch.setattr(dc, "update_task", lambda *a, **k: None)
    monkeypatch.setattr(dc, "read_all_symbols", lambda *a, **k: [])
    # 只替换 dc 模块内的 `threading` 引用，不动全局 threading 模块。
    monkeypatch.setattr(dc, "threading", types.SimpleNamespace(Thread=_FakeThread))


def _call_trigger_sync(*, resume: bool) -> None:
    req = dc.SyncRequest(mode="incremental", symbols=["600000.SH"], resume=resume)
    asyncio.run(dc.trigger_sync(req, _user={}))


def _call_trigger_fetch() -> None:
    req = dc.FetchRequest(asset_type="etf", start="2024-01-01", end="2024-01-31",
                          symbols=[SYM])
    asyncio.run(dc.trigger_fetch(req, _user={}))


# ---------------------------------------------------------------------------
# 1. trigger_sync：resume=False 清 failed，resume=True 保留
# ---------------------------------------------------------------------------
def test_trigger_sync_resume_false_clears_failed(iso_sync, monkeypatch):
    """非续传启动同步 ⇒ `failed` 必须清零（口径=本轮）。"""
    _stub_sync_launch(monkeypatch)
    dc._sync.running = False
    dc._sync.failed = {"OLD-FAIL"}
    dc._sync.completed = {"OLD-DONE"}
    dc._sync.completed_mode = "incremental"

    _call_trigger_sync(resume=False)

    assert dc._sync.failed == set(), (
        "resume=False 启动同步未清空 _sync.failed ⇒ failed_count 变成进程内累计，"
        "与前端「上次任务」文案矛盾（本文件锁定的核心缺陷）")
    assert dc._sync.completed == set(), "resume=False 仍应清空 completed（既有语义）"


def test_trigger_sync_resume_true_keeps_failed(iso_sync, monkeypatch):
    """续传启动 ⇒ `failed` 与 `completed` 一并保留（续传要知道哪些仍待重试）。"""
    _stub_sync_launch(monkeypatch)
    dc._sync.running = False
    dc._sync.failed = {"OLD-FAIL"}
    dc._sync.completed = {"OLD-DONE"}
    dc._sync.completed_mode = "incremental"

    _call_trigger_sync(resume=True)

    assert dc._sync.failed == {"OLD-FAIL"}, "resume=True 不应清空 failed"
    assert dc._sync.completed == {"OLD-DONE"}, "resume=True 不应清空 completed"


# ---------------------------------------------------------------------------
# 2. trigger_fetch：新任务恒清 failed
# ---------------------------------------------------------------------------
def test_trigger_fetch_clears_failed(iso_sync, monkeypatch):
    """自定义抓取没有 resume 概念 ⇒ 每次都是新任务，`failed` 必须清零。"""
    monkeypatch.setattr(dc, "acquire_pipeline_slot", lambda kind: _FakeSlot())
    monkeypatch.setattr(dc, "threading", types.SimpleNamespace(Thread=_FakeThread))
    dc._sync.running = False
    dc._sync.failed = {"OLD-FAIL"}
    dc._sync.completed = {"OLD-DONE"}

    _call_trigger_fetch()

    assert dc._sync.failed == set(), "抓取开始时未清空 _sync.failed"
    assert dc._sync.completed == set(), "抓取开始时仍应清空 completed"


# ---------------------------------------------------------------------------
# 3. _start_sync_reset：自动同步路径（auto_sync_scheduler 每 60s）
# ---------------------------------------------------------------------------
def test_start_sync_reset_resume_false_clears_failed(iso_sync):
    """非续传复位（自动同步路径）⇒ failed 清零。"""
    ss._sync.failed = {"OLD-FAIL"}
    ss._sync.completed = {"OLD-DONE"}

    ss._start_sync_reset(ss._sync, "incremental", False)

    assert ss._sync.failed == set()
    assert ss._sync.completed == set()
    assert ss._sync.completed_mode == "incremental"


def test_start_sync_reset_resume_true_keeps_failed(iso_sync):
    """续传复位 ⇒ failed 保留。"""
    ss._sync.failed = {"OLD-FAIL"}
    ss._sync.completed = {"OLD-DONE"}

    ss._start_sync_reset(ss._sync, "incremental", True)

    assert ss._sync.failed == {"OLD-FAIL"}
    assert ss._sync.completed == {"OLD-DONE"}


# ---------------------------------------------------------------------------
# 4. _run_fetch 异常分支：既记 failed，也不记 completed
# ---------------------------------------------------------------------------
def test_run_fetch_exception_records_failed(iso_sync, monkeypatch):
    """fetcher 抛异常（如东财/新浪全不可达被上层包装）⇒ sym 进 failed，不进 completed。"""
    calls: list[tuple[str, str]] = []

    def _boom(code, start, end, adjusts=("", "hfq"), fetcher=None):
        raise RuntimeError("all sources down")

    monkeypatch.setattr(tasks, "fetch_and_write_daily_bars", _boom)
    monkeypatch.setattr(dc._sync, "log", lambda level, msg: calls.append((level, msg)))
    dc._sync.failed = set()
    dc._sync.completed = set()

    dc._run_fetch([SYM], "etf", "2024-01-01", "2024-01-31",
                  slot=contextlib.nullcontext())

    assert SYM in dc._sync.failed, (
        "异常分支未把 sym 记入 failed ⇒ 该标的在 completed/failed 两端双双消失，"
        "前端静默漏掉")
    assert SYM not in dc._sync.completed, "异常不得记入 completed"


def test_run_fetch_exception_log_aligned_with_failure_branches(iso_sync, monkeypatch):
    """异常分支日志必须与两条"口径失败"分支对齐（含 resume 提示），不得谎报成功。"""
    calls: list[tuple[str, str]] = []

    def _boom(code, start, end, adjusts=("", "hfq"), fetcher=None):
        raise RuntimeError("boom")

    monkeypatch.setattr(tasks, "fetch_and_write_daily_bars", _boom)
    monkeypatch.setattr(dc._sync, "log", lambda level, msg: calls.append((level, msg)))
    dc._sync.failed = set()
    dc._sync.completed = set()

    dc._run_fetch([SYM], "etf", "2024-01-01", "2024-01-31",
                  slot=contextlib.nullcontext())

    warns = [(lv, m) for lv, m in calls if SYM in m]
    assert warns, "异常分支必须留下与本标的相关日志"
    assert all(lv == "WARNING" for lv, _ in warns), "异常失败不得打 INFO"
    assert not any("成功" in m for _, m in warns), "异常分支不得出现'成功'字样"
    assert any("已记入 failed" in m for _, m in warns), "异常分支应提示已记入 failed（可续传重试）"


def test_run_fetch_no_data_still_counts_completed(iso_sync, monkeypatch):
    """边界（不得被本次改动误伤）：n==0 且 failed_adj 为空 = 停牌/退市，记 completed、不记 failed。"""
    def _empty(code, start, end, adjusts=("", "hfq"), fetcher=None):
        return 0, []

    monkeypatch.setattr(tasks, "fetch_and_write_daily_bars", _empty)
    monkeypatch.setattr(dc._sync, "log", lambda level, msg: None)
    dc._sync.failed = set()
    dc._sync.completed = set()

    dc._run_fetch([SYM], "etf", "2024-01-01", "2024-01-31",
                  slot=contextlib.nullcontext())

    assert SYM in dc._sync.completed
    assert SYM not in dc._sync.failed


# ---------------------------------------------------------------------------
# 5. 源码级守卫（锁死 5 处改动与前端文案，防回退）
# ---------------------------------------------------------------------------
def test_backend_source_clears_failed_at_both_reset_sites():
    """datacenter.py 必须同时存在两处 `_sync.failed.clear()`（trigger_sync + trigger_fetch）。"""
    src = "\n".join(_DC_LINES)
    assert src.count("_sync.failed.clear()") == 2, (
        "必须有 trigger_sync 与 trigger_fetch 两处 failed 清零；"
        "少一处即回归为进程内累计")
    # 异常分支必须记失败
    assert "_sync.failed.add(sym)" in src


def test_start_sync_reset_source_clears_failed():
    """_start_sync_reset 函数体内必须有 `state.failed.clear()`。"""
    src = "\n".join(_SS_LINES)
    body = src.split("def _start_sync_reset(", 1)[1].split("def _start_sync_bg(", 1)[0]
    assert "state.failed.clear()" in body


def test_frontend_tooltip_says_per_round_not_cumulative():
    """前端 tooltip 不得再谎称"进程内累计值"，须为"仅统计最近一次任务"。"""
    p = BACKEND_ROOT.parent / "frontend" / "src" / "pages" / "DataCenter" / "index.tsx"
    src = p.read_text(encoding="utf-8")
    assert "仅统计最近一次任务" in src
    assert "为进程内累计值" not in src


def test_frontend_type_comment_not_cumulative():
    """types/datacenter.ts 的 failed_count 注释不得谎报为进程内累计。"""
    p = BACKEND_ROOT.parent / "frontend" / "src" / "types" / "datacenter.ts"
    src = p.read_text(encoding="utf-8")
    assert "本轮（最近一次任务）" in src
    assert "该计数为**进程内累计**" not in src


# ---------------------------------------------------------------------------
# 6. failed 只增不减（续跑修好后仍留在 failed ⇒ 前端谎报）
# ---------------------------------------------------------------------------
def _run_fetch_round(sym, fwdb, monkeypatch, log=None):
    """跑一遍 `_run_fetch`（单只），fetcher 由调用方决定成功/失败。"""
    monkeypatch.setattr(tasks, "fetch_and_write_daily_bars", fwdb)
    monkeypatch.setattr(dc._sync, "log", log or (lambda level, msg: None))
    dc._run_fetch([sym], "etf", "2024-01-01", "2024-01-31",
                  slot=contextlib.nullcontext())


def test_run_fetch_second_round_success_removes_failed(iso_sync, monkeypatch):
    """第 1 轮失败进 failed；第 2 轮同一标的成功 ⇒ 必须从 failed 摘掉。

    `failed` 只增不减的后果：resume 续跑把标的修好后，它同时留在 `failed` 里，
    前端「上次任务：N/N 完成 · 失败 X 只」会继续把已修好的标的算作失败。
    """
    boom = {"yes": True}

    def _fwdb(code, start, end, adjusts=("", "hfq"), fetcher=None):
        if boom["yes"]:
            raise RuntimeError("all sources down")
        return 12, []

    dc._sync.failed = set()
    dc._sync.completed = set()

    _run_fetch_round(SYM, _fwdb, monkeypatch)
    assert dc._sync.failed == {SYM}, "第 1 轮失败必须记入 failed"

    boom["yes"] = False
    _run_fetch_round(SYM, _fwdb, monkeypatch)

    assert SYM in dc._sync.completed, "第 2 轮成功必须记入 completed"
    assert SYM not in dc._sync.failed, (
        "第 2 轮已修好却仍留在 failed ⇒ 前端「失败 N 只」把本轮修好的标的继续算作失败")


def test_run_fetch_second_round_still_failed_keeps_failed(iso_sync, monkeypatch):
    """反向护栏：第 2 轮**仍然**失败 ⇒ failed 必须仍含该标的。

    防止把 discard 写成"成功路径无条件清 failed"以外的粗暴实现（例如每轮开头清空，
    那会掩盖真正的失败）。
    """
    def _boom(code, start, end, adjusts=("", "hfq"), fetcher=None):
        raise RuntimeError("still down")

    dc._sync.failed = set()
    dc._sync.completed = set()

    _run_fetch_round(SYM, _boom, monkeypatch)
    _run_fetch_round(SYM, _boom, monkeypatch)

    assert SYM in dc._sync.failed, "仍失败却被 discard 掉 ⇒ 失败被静默吞掉"
    assert SYM not in dc._sync.completed


def _stub_incremental_env(monkeypatch) -> None:
    """隔离 `_run_incremental` 的日历/DB 依赖（沿用 test_resume_failed_semantics 手法）。"""
    monkeypatch.setattr(ss, "_refresh_trade_calendar", lambda: None)
    days = [date.today() - timedelta(days=i) for i in range(1, 61)]
    monkeypatch.setattr(ss, "get_calendar", lambda: build_calendar(days))
    monkeypatch.setattr(ss, "_symbol_last_date", lambda sym, year: None)


def test_run_incremental_resume_success_removes_failed(iso_sync, monkeypatch):
    """sync_service 侧同款：resume=True 续跑成功 ⇒ 从 failed 摘掉。"""
    _stub_incremental_env(monkeypatch)
    boom = {"yes": True}

    def _fwdb(code, start, end, adjusts=("", "hfq"), fetcher=None):
        if boom["yes"]:
            raise RuntimeError("down")
        return 12, []

    monkeypatch.setattr(tasks, "fetch_and_write_daily_bars", _fwdb)
    monkeypatch.setattr(dc._sync, "log", lambda level, msg: None)
    dc._sync.failed = set()
    dc._sync.completed = set()
    dc._sync.completed_mode = None

    ss._run_incremental([SYM], resume=False)
    assert SYM in dc._sync.failed, "第 1 轮失败必须记入 failed"

    boom["yes"] = False
    ss._run_incremental([SYM], resume=True)

    assert SYM in dc._sync.completed
    assert SYM not in dc._sync.failed, "续跑修好却仍留在 failed ⇒ 前端失败数谎报"


def test_run_incremental_resume_still_failed_keeps_failed(iso_sync, monkeypatch):
    """反向护栏（sync_service 侧）：续跑仍失败 ⇒ failed 必须保留。"""
    _stub_incremental_env(monkeypatch)

    def _boom(code, start, end, adjusts=("", "hfq"), fetcher=None):
        raise RuntimeError("still down")

    monkeypatch.setattr(tasks, "fetch_and_write_daily_bars", _boom)
    monkeypatch.setattr(dc._sync, "log", lambda level, msg: None)
    dc._sync.failed = set()
    dc._sync.completed = set()
    dc._sync.completed_mode = None

    ss._run_incremental([SYM], resume=False)
    ss._run_incremental([SYM], resume=True)

    assert SYM in dc._sync.failed
    assert SYM not in dc._sync.completed


def test_completed_add_always_paired_with_failed_discard():
    """不变式守卫：**每一处**写 completed 的下一行必须就是 `failed.discard`。

    删掉（或漏加）8 处 discard 中的任意一处，本用例立即判红 —— 这是"漏掉一处
    discard"唯一能被抓到的地方（行为用例只覆盖 `_run_fetch` / `_run_incremental`，
    覆盖不到 repair / rebuild / 增量"已最新跳过"这三条路径）。
    """
    for rel, lines in (("app/api/v1/datacenter.py", _DC_LINES),
                       ("app/services/sync_service.py", _SS_LINES)):
        adds = [i for i, ln in enumerate(lines) if "_sync.completed.add(" in ln]
        discards = [i for i, ln in enumerate(lines) if "_sync.failed.discard(" in ln]
        assert adds, f"{rel}: 未找到 completed.add（锚点失效，需重审本用例）"
        assert len(adds) == len(discards), (
            f"{rel}: completed.add={len(adds)} 处 但 failed.discard={len(discards)} 处 "
            "⇒ 有写 completed 的路径没配对 discard（failed 只增不减回归）")
        for i in adds:
            nxt = lines[i + 1] if i + 1 < len(lines) else ""
            assert "_sync.failed.discard(" in nxt, (
                f"{rel}:{i + 2} 写进 completed 但紧邻下一行不是 discard ⇒ "
                "该标的成功后仍留在 failed（数字与事实不符）")


# ---------------------------------------------------------------------------
# 7. _run_rebuild 的 except 必须记 failed（与 _run_fetch except 同形状的漏网）
# ---------------------------------------------------------------------------
def _stub_rebuild(monkeypatch, fn) -> None:
    """替身 `_run_rebuild` 唯一的外部依赖（离线计算，无网络/日历）。"""
    monkeypatch.setattr(repair, "build_qfq_dataset", fn)
    monkeypatch.setattr(dc._sync, "log", lambda level, msg: None)
    dc._sync.failed = set()
    dc._sync.completed = set()
    dc._sync.completed_mode = None


def test_run_rebuild_exception_records_failed(iso_sync, monkeypatch):
    """rebuild 抛异常 ⇒ sym 必须进 failed（原本只打日志，两端计数双双漏掉）。"""
    def _boom(root, sym):
        raise RuntimeError("qfq rebuild failed")

    _stub_rebuild(monkeypatch, _boom)
    ss._run_rebuild([SYM], resume=False)

    assert SYM in dc._sync.failed, (
        "rebuild 失败未记入 failed ⇒ 该标的既不在 completed 也不在 failed，前端静默漏掉")
    assert SYM not in dc._sync.completed, "失败不得记入 completed"


def test_run_rebuild_exception_log_mentions_failed(iso_sync, monkeypatch):
    """rebuild 失败日志须带可重试提示，且与其它 runner 的 except 文案对齐。"""
    calls: list[tuple[str, str]] = []

    def _boom(root, sym):
        raise RuntimeError("boom")

    monkeypatch.setattr(repair, "build_qfq_dataset", _boom)
    monkeypatch.setattr(dc._sync, "log", lambda level, msg: calls.append((level, msg)))
    dc._sync.failed = set()
    dc._sync.completed = set()
    dc._sync.completed_mode = None

    ss._run_rebuild([SYM], resume=False)

    mine = [(lv, m) for lv, m in calls if SYM in m]
    assert mine, "rebuild 失败必须留下与本标的相关日志"
    assert all(lv == "WARNING" for lv, _ in mine)
    assert any("已记入 failed" in m for _, m in mine)


def test_run_rebuild_success_discards_stale_failed(iso_sync, monkeypatch):
    """反向护栏：rebuild 成功 ⇒ 进 completed，并把（历史）failed 记录摘掉。

    同时确认本轮新增的 except 改动没有打破 rebuild 的 discard 路径。
    """
    _stub_rebuild(monkeypatch, lambda root, sym: 0)
    dc._sync.failed = {SYM}  # 预置上一轮残留的失败记录

    ss._run_rebuild([SYM], resume=False)

    assert SYM in dc._sync.completed
    assert SYM not in dc._sync.failed, "rebuild 成功却仍留在 failed ⇒ 前端失败数谎报"
