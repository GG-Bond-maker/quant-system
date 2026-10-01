"""审计 P1-29 / P1-30 / P1-31 / P1-42 防回归：数据流水线状态失真链。

四项共同的病根是**状态说谎**，即"看起来成功、实际没做"：

| 项 | 位置 | 说谎方式 |
|---|---|---|
| P1-29 | `jobs/evening_routine.py` | 流水线 FAILED 仍 `_mark("done")` ⇒ 终态无失败、当日不重试 |
| P1-30 | `services/market_service.py` | 3 个子源只成功 1 个仍 `status="ok"` 且无 `reason` ⇒ `data_freshness` 报 fresh |
| P1-31 | `api/v1/datacenter.py` `/sync/fetch` | 预检取锁后即释放、worker 二次取锁 ⇒ 窗口内被抢则已回 `started:true` 却零执行 |
| P1-42 | `orchestrator.py` | 回测读的 `universe_daily_bt` **不在任何步骤集**里 ⇒ 夜间养的是另一份数据 |
"""
from __future__ import annotations

import sys
import threading
from datetime import date, datetime
from pathlib import Path

BACKEND_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(BACKEND_ROOT))

import pytest  # noqa: E402

from app.core.pipeline_lock import (  # noqa: E402
    PipelineBusy,
    acquire_pipeline_slot,
    current_pipeline_owner,
    pipeline_slot,
)


# ==================== P1-30：部分降级必须报 degraded ====================

def _money_flow(monkeypatch, *, north_ok: bool, main_ok: bool,
                sector_ok: bool) -> dict:
    """按指定的子源成功与否调用 build_money_flow。

    ⚠️ 必须用**真实 pandas 对象**：`pd.to_numeric([...])` 在 list 上返回 ndarray
    （无 `.iloc`），而 sector 分支要 `.assign/.sort_values/.head/.iterrows` ——
    手写替身会同时污染三条分支的成功/失败归因（本用例第一版就被这一点坑过）。
    """
    import pandas as pd

    import app.services.market_service as ms

    class _AK:
        def stock_hsgt_fund_flow_summary_em(self):
            if not north_ok:
                raise RuntimeError("north down")
            # 真实表是**双向**汇总（列 资金方向 = 北向/南向，单位亿元）：
            # 北向 1.5 亿 + 南向 9.9 亿。P1-33 的过滤必须只求和北向行。
            return pd.DataFrame({
                "资金方向": ["北向", "南向"],
                "净流入": [1.5, 9.9],
            })

    # 注意调用形态：`ak = get_akshare; ak().stock_xxx(...)`
    # ⇒ get_akshare 必须返回**客户端实例**（返回类会缺 self 参数）
    client = _AK()
    monkeypatch.setattr(ms, "get_akshare", lambda: client)
    monkeypatch.setattr(ms, "_safe_call", lambda fn, *a, **k: fn(*a, **k))

    # 2026-10-01（审计 F1）：大盘 / 板块资金流已从 akshare 改为
    # `realtime.fetch_market_fund_flow` / `fetch_sector_fund_flow`
    # （akshare 硬编码 push2/push2his 在本机被阻断）。故此处必须 patch
    # **新适配器**，否则打到真实网络 ⇒ "失败注入"失效、本用例假绿。
    def _mf(north: bool = False) -> list[dict]:
        if not main_ok:
            raise RuntimeError("main down")
        return [{"日期": "2026-09-30", "主力净流入-净额": 2.5e8}]

    def _sf(sector_type: str = "行业资金流") -> list[dict]:
        if not sector_ok:
            raise RuntimeError("sector down")
        # ⚠️ 返回**东财原始字段名**（f14=名称, f66=超大单, f72=大单,
        # f78=中单, f84=小单）——与 `realtime._fetch_em_sector_fflow` 的
        # `fields` 契约一致；早前用例用的是 akshare 中文列名，已随适配器切换更正。
        return [{
            "f14": "银行",
            "f66": 1e8, "f72": 2e7, "f78": -5e7, "f84": -7e7,
        }]

    monkeypatch.setattr(ms._realtime, "fetch_market_fund_flow", _mf)
    monkeypatch.setattr(ms._realtime, "fetch_sector_fund_flow", _sf)
    return ms.build_money_flow()


def test_all_three_sources_up_is_ok(monkeypatch) -> None:
    r = _money_flow(monkeypatch, north_ok=True, main_ok=True, sector_ok=True)
    assert r["status"] == "ok"
    assert r["n_ok"] == 3
    assert "reason" not in r, "全部成功时不应带 reason"


# ==================== P1-33：北向必须按资金方向过滤 ====================

def _money_flow_with_north(monkeypatch, north_frame) -> dict:
    """只替换北向子源的表结构，其余两个子源保持成功（隔离 P1-33 的口径）。"""
    import pandas as pd

    import app.services.market_service as ms

    class _AK:
        def stock_hsgt_fund_flow_summary_em(self):
            return north_frame

        def stock_market_fund_flow(self):
            return pd.DataFrame({"主力净流入-净额": [2.5e8]})

        def stock_sector_fund_flow_rank(self, **kw):
            return pd.DataFrame({
                "名称": ["银行"],
                "超大单净流入-净额": [1e8], "大单净流入-净额": [2e7],
                "中单净流入-净额": [-5e7], "小单净流入-净额": [-7e7],
            })

    client = _AK()
    monkeypatch.setattr(ms, "get_akshare", lambda: client)
    monkeypatch.setattr(ms, "_safe_call", lambda fn, *a, **k: fn(*a, **k))
    return ms.build_money_flow()


def test_north_flow_excludes_southbound(monkeypatch) -> None:
    """**P1-33 本体**：南向（港股通）净流入不得被计入"北向净流入"。

    真实 `stock_hsgt_fund_flow_summary_em` 是双向汇总表（列 `资金方向`），
    原实现取 `north_cols[0]` 后对**全表** sum ⇒ B7a 实测 840.0 亿 vs 真实
    北向 0.0（港股通(沪)+港股通(深) 全部算进北向），直接显示在前端
    `MoneyFlowPanel.tsx:104`「北向净流入」卡片。
    """
    import pandas as pd

    frame = pd.DataFrame({
        "类型": ["沪股通", "深股通", "港股通(沪)", "港股通(深)"],
        "资金方向": ["北向", "北向", "南向", "南向"],
        "净流入": [0.0, 1.5, 400.0, 440.0],
    })
    r = _money_flow_with_north(monkeypatch, frame)
    assert r["status"] == "ok"
    # 只算北向两行；若未过滤会是 841.5（= 全表 sum）
    assert r["north_net_today"] == 1.5, r
    assert r["north_net_today"] != 841.5


def test_north_flow_direction_column_missing_degrades(monkeypatch) -> None:
    """口径不可验证（缺 `资金方向` 列）时**不得**静默全表求和，必须 degraded。"""
    import pandas as pd

    r = _money_flow_with_north(monkeypatch, pd.DataFrame({"净流入": [1.5, 9.9]}))
    assert r["status"] == "degraded"
    assert r["north_net_today"] is None
    assert "north" in r["reason"]


def test_north_flow_without_northbound_rows_degrades(monkeypatch) -> None:
    """`资金方向` 无"北向"行时同样不得给出 0 冒充"北向净流入为 0"。"""
    import pandas as pd

    frame = pd.DataFrame({"资金方向": ["南向", "南向"],
                          "净流入": [400.0, 440.0]})
    r = _money_flow_with_north(monkeypatch, frame)
    assert r["status"] == "degraded"
    assert r["north_net_today"] is None
    assert "north" in r["reason"]


@pytest.mark.parametrize("north,main,sector,n_ok", [
    (True, False, False, 1),
    (False, True, False, 1),
    (False, False, True, 1),
    (True, True, False, 2),
    (True, False, True, 2),
    (False, True, True, 2),
])
def test_partial_sources_report_degraded(monkeypatch, north, main, sector, n_ok) -> None:
    """**P1-30 本体**：部分可用必须是 degraded 且带 reason，不得报 ok。

    原实现只要任一子源成功就 `status="ok"` 且**不带 reason**，而消费方
    `api/v1/market.py` 的 degraded 判据是 status ∈ (degraded, unavailable)
    ⇒ `data_freshness` 报 fresh，外部源降级被当健康。
    """
    r = _money_flow(monkeypatch, north_ok=north, main_ok=main, sector_ok=sector)
    assert r["status"] == "degraded", f"{n_ok}/3 可用却报 {r['status']}"
    assert r["n_ok"] == n_ok
    assert r.get("reason"), "degraded 必须给出原因（否则运维无法定位）"
    # 已拿到的数据仍要交付（降级不等于丢数据）
    assert "sector_flows" in r and "north_net_today" in r and "main_net_today" in r


def test_all_sources_down_is_unavailable(monkeypatch) -> None:
    r = _money_flow(monkeypatch, north_ok=False, main_ok=False, sector_ok=False)
    assert r["status"] == "unavailable"
    assert r.get("reason")


def test_degraded_money_flow_makes_data_freshness_degraded() -> None:
    """消费方判据：money_flow=degraded ⇒ 整块 data_freshness 必须 degraded。"""
    from app.api.v1.market import _is_degraded

    assert _is_degraded({"status": "degraded"}) is True
    assert _is_degraded({"status": "unavailable"}) is True
    assert _is_degraded({"status": "ok"}) is False
    assert _is_degraded(None) is False
    assert _is_degraded({"no_status": 1}) is False
    # 端到端：三块里只有 money_flow 降级 ⇒ degraded
    blocks = {"indices": {"status": "ok"},
              "money_flow": {"status": "degraded", "reason": "x"},
              "anomalies": {"status": "ok"}}
    assert any(_is_degraded(b) for b in blocks.values())


# ==================== P1-29：流水线失败必须成为终态 ====================

@pytest.fixture()
def routine(monkeypatch):
    import app.jobs.evening_routine as er

    store: dict = {}
    monkeypatch.setattr(er, "kv_get", lambda k: store.get(k))
    monkeypatch.setattr(er, "kv_set", lambda k, v: store.__setitem__(k, v))
    monkeypatch.setattr(er, "_today_key", lambda: "2026-09-21")
    monkeypatch.setattr("app.domain.calendar.is_trade_day", lambda *a, **k: True)
    monkeypatch.setattr("app.data.calendar_store.get_calendar", lambda: None)
    monkeypatch.setattr("app.ml.monitor.run_monitor",
                        lambda *a, **k: {"ok": True, "state": "ok"})
    monkeypatch.setattr("app.api.v1.report.generate_and_store_report",
                        lambda *a, **k: {"date": "2026-09-21"})
    monkeypatch.setattr("app.core.events.publish_threadsafe", lambda *a, **k: None)
    monkeypatch.setattr("app.data.parquet_store.read_all_symbols",
                        lambda *a, **k: ["000001.SZ"])
    return er, store


class _Job:
    def __init__(self, status: str) -> None:
        self.status = status
        self.current_step = "build_features"
        self.error_message = "PermissionError"


def test_pipeline_failure_marks_failed_not_done(routine, monkeypatch) -> None:
    """**P1-29 本体**：整条流水线 FAILED ⇒ 终态 `failed`，不是 `done`。"""
    er, store = routine
    import app.orchestrator as orch

    monkeypatch.setattr(orch, "run_pipeline",
                        lambda *a, **k: (_Job("FAILED"), True))
    er._mark("running", attempts=1)
    er._run_routine()
    rec = store["evening_routine_last"]
    assert rec["status"] == "failed", (
        f"终态是 {rec['status']} ⇒ 状态机不可辨、当日不再重试（P1-29 未修）")
    assert "build_features" in str(rec["detail"].get("pipeline"))
    assert rec["attempts"] == 1


def test_pipeline_exception_also_marks_failed(routine, monkeypatch) -> None:
    """直接抛异常（如 PipelineBusy 撞车）同样属"流水线未完成"。"""
    er, store = routine
    import app.orchestrator as orch

    def _boom(*a, **k):
        raise PipelineBusy("sync")

    monkeypatch.setattr(orch, "run_pipeline", _boom)
    er._mark("running", attempts=1)
    er._run_routine()
    assert store["evening_routine_last"]["status"] == "failed"


def test_successful_pipeline_still_marks_done(routine, monkeypatch) -> None:
    er, store = routine
    import app.orchestrator as orch

    monkeypatch.setattr(orch, "run_pipeline",
                        lambda *a, **k: (_Job("SUCCESS"), True))
    er._mark("running", attempts=1)
    er._run_routine()
    assert store["evening_routine_last"]["status"] == "done"


def test_report_failure_alone_keeps_done(routine, monkeypatch) -> None:
    """设计约束不变：单步（监控/日报）失败不阻断，仍记 done 但留痕。"""
    er, store = routine
    import app.orchestrator as orch

    monkeypatch.setattr(orch, "run_pipeline",
                        lambda *a, **k: (_Job("SUCCESS"), True))

    def _boom(*a, **k):
        raise RuntimeError("report down")

    monkeypatch.setattr("app.api.v1.report.generate_and_store_report", _boom)
    er._mark("running", attempts=1)
    er._run_routine()
    rec = store["evening_routine_last"]
    assert rec["status"] == "done"
    assert "error" in str(rec["detail"]["report"])


def test_failed_then_retried_then_exhausted(routine) -> None:
    """重试状态机：failed 可重试 → 用尽上限后当日不再重试（避免整晚每 60s 重跑）。"""
    er, store = routine
    er._mark("failed", {"pipeline": "FAILED/x"}, attempts=1)
    store["evening_routine_last"]["at"] = datetime.now().isoformat(timespec="seconds")
    # 未用尽但**未过退避** ⇒ 不重试
    assert er._already_done_today() is True
    # 未用尽且已过退避 ⇒ 允许重试
    store["evening_routine_last"]["at"] = "2026-09-21T00:00:00"
    assert er._already_done_today() is False, "失败后应允许重试（退避已过）"
    # 用尽上限 ⇒ 停止重试，但终态仍是 failed
    er._mark("failed", {"pipeline": "FAILED/x"}, attempts=er._MAX_ATTEMPTS)
    assert er._already_done_today() is True
    assert store["evening_routine_last"]["status"] == "failed"


def test_done_and_skipped_stay_terminal(routine) -> None:
    er, store = routine
    for st in ("done", "skipped"):
        er._mark(st, attempts=1)
        assert er._already_done_today() is True


def test_attempts_reset_across_days_and_carried_within_day(routine) -> None:
    er, store = routine
    er._mark("failed", {"pipeline": "x"}, attempts=2)
    assert er._attempts_today() == 2
    store["evening_routine_last"]["day"] = "2026-09-20"
    assert er._attempts_today() == 0, "跨日必须归零"


# ==================== P1-31：/sync/fetch 不得谎报已启动 ====================

def test_acquire_returns_transferable_handle() -> None:
    h = acquire_pipeline_slot("fetch")
    try:
        assert current_pipeline_owner() == "fetch"
        assert h.released is False
        with pytest.raises(PipelineBusy):
            acquire_pipeline_slot("mirror")
    finally:
        h.release()
    assert current_pipeline_owner() is None
    # 释放后可再次申请（未泄漏）
    with pipeline_slot("mirror"):
        pass


def test_handle_release_is_idempotent_and_does_not_double_release() -> None:
    """重复 release 必须无害（否则会释放别人的锁 ⇒ 互斥静默失效）。"""
    h = acquire_pipeline_slot("fetch")
    h.release()
    h.release()                      # 幂等：不得再次 _LOCK.release()
    with pipeline_slot("training"):  # 锁仍可正常申请
        assert current_pipeline_owner() == "training"


def test_handle_works_as_context_manager() -> None:
    h = acquire_pipeline_slot("mirror")
    with h:
        assert current_pipeline_owner() == "mirror"
    assert current_pipeline_owner() is None
    assert h.released is True


def test_run_fetch_accepts_prequalified_slot(monkeypatch) -> None:
    """交接模式：`_run_fetch(slot=h)` 不再二次申请（否则就是 P1-31 的 TOCTOU）。"""
    from app.api.v1 import datacenter as dc

    seen: dict = {}

    def _fake_fetch(code, start, end, adjusts=None, fetcher=None):
        seen["code"] = code
        return (5, [])

    import app.data.ingest.tasks as tasks
    monkeypatch.setattr(tasks, "fetch_and_write_daily_bars", _fake_fetch)
    monkeypatch.setattr(dc, "_check_cancel", lambda: False)

    h = acquire_pipeline_slot("fetch")
    try:
        dc._run_fetch(["000001.SZ"], "stock", "2026-01-02", "2026-01-02", slot=h)
    finally:
        h.release()
    assert seen["code"] == "000001"
    assert h.released is True, "_run_fetch 退出时必须释放交接来的句柄"


def test_run_fetch_without_slot_still_rejects_when_busy() -> None:
    """旧语义保持：不传 slot 时自行申请，锁忙抛 PipelineBusy（既有测试依赖）。"""
    from app.api.v1 import datacenter as dc

    with pipeline_slot("sync"):
        with pytest.raises(PipelineBusy):
            dc._run_fetch(["000001.SZ"], "stock", "2026-01-02", "2026-01-02")


def test_sync_fetch_endpoint_rejects_busy_without_claiming_started() -> None:
    """**P1-31 本体**：锁忙时接口必须回 ERR_PIPELINE_BUSY，而不是 started:true。

    用真实端点（走 FastAPI 依赖注入太重，故直调签名函数并注入用户）。
    """
    import asyncio

    from app.api.v1 import datacenter as dc

    with pipeline_slot("sync"):
        resp = asyncio.run(dc.trigger_fetch(
            dc.FetchRequest(asset_type="stock", start="2026-01-02",
                            end="2026-01-02", symbols=["000001.SZ"]),
            _user={"role": "researcher"}))
    body = resp.model_dump() if hasattr(resp, "model_dump") else resp
    assert body.get("code") != 0, f"锁忙却回了成功：{body}"
    assert body.get("data") in (None, {}), f"锁忙却报 started：{body}"
    assert "管道任务" in str(body.get("message") or "")
    assert dc._sync.running is False, "被拒请求不得把状态机置为 running"


def test_sync_fetch_endpoint_releases_slot_when_sync_state_busy() -> None:
    """`_sync.running` 已占用时的早退分支必须归还管道锁（否则锁永久泄漏）。"""
    import asyncio

    from app.api.v1 import datacenter as dc

    with dc._sync.lock:
        dc._sync.running = True
    try:
        resp = asyncio.run(dc.trigger_fetch(
            dc.FetchRequest(asset_type="stock", start="2026-01-02",
                            end="2026-01-02", symbols=["000001.SZ"]),
            _user={"role": "researcher"}))
        body = resp.model_dump() if hasattr(resp, "model_dump") else resp
        assert body.get("code") != 0
    finally:
        with dc._sync.lock:
            dc._sync.running = False
    assert current_pipeline_owner() is None, (
        "早退分支未归还管道锁 ⇒ 后续 sync/pipeline/training 全部永久被拒")
    with pipeline_slot("training"):
        pass


# ==================== P1-42：回测数据集必须进步骤集 ====================

def test_backtest_universe_is_in_full_and_evening_steps() -> None:
    """**P1-42 本体**：回测读的 `universe_daily_bt` 必须有流水线步骤。"""
    import app.orchestrator as orch

    assert "build_universe_bt" in orch.FULL_STEPS, "回测数据集未接线（P1-42）"
    assert "build_universe_bt" in orch.EVENING_STEPS
    assert "build_universe_bt" in orch.STEP_FUNCTIONS, "步骤集有名字但无实现"
    # 顺序：紧随 build_universe、在 build_features 之前
    i_uni = orch.FULL_STEPS.index("build_universe")
    i_bt = orch.FULL_STEPS.index("build_universe_bt")
    i_ft = orch.FULL_STEPS.index("build_features")
    assert i_uni < i_bt < i_ft


def test_evening_steps_remains_ordered_derivation_of_full() -> None:
    """既有不变量：EVENING_STEPS 仍是 FULL_STEPS 的有序剔除（不能硬编码）。"""
    import app.orchestrator as orch

    assert orch.EVENING_STEPS == [s for s in orch.FULL_STEPS
                                  if s != "update_daily"]
    assert [s for s in orch.FULL_STEPS if s in set(orch.EVENING_STEPS)] \
        == orch.EVENING_STEPS


def test_step_functions_cover_all_steps() -> None:
    import app.orchestrator as orch

    assert set(orch.FULL_STEPS) <= set(orch.STEP_FUNCTIONS), (
        f"步骤集有未实现的名字：{set(orch.FULL_STEPS) - set(orch.STEP_FUNCTIONS)}")


def test_build_universe_bt_step_persists(monkeypatch) -> None:
    """步骤必须真的调 `build_universe_backtest(persist=True)` 并回报规模。"""
    import app.orchestrator as orch

    captured: dict = {}

    class _StubSeries:
        def n_unique(self) -> int:
            return 7

    def _fake(**kw):
        captured.update(kw)
        return _StubSeriesResult()

    class _StubSeriesResult:
        height = 123
        columns = ["date", "symbol"]

        def __getitem__(self, k):
            return _StubSeries()

    import app.data.parquet_store as ps
    import app.data.universe as uni
    monkeypatch.setattr(ps, "read_all_symbols", lambda ds: ["600001.SH"])
    monkeypatch.setattr(uni, "build_universe_backtest", _fake)
    out = orch.step_build_universe_bt(date(2026, 9, 21), ["000001.SZ"])
    assert captured.get("persist") is True, "必须落盘（否则回测仍读旧数据）"
    assert "rows=123" in out


def test_build_universe_bt_skips_loudly_when_no_hfq(monkeypatch) -> None:
    """无 hfq 行情 ⇒ **显式跳过**（可见）而非 fail-fast 掐断整条流水线。

    为什么必须是跳过：本步是回测专用派生数据，硬失败会让尚未跑过 rebuild_qfq 的
    部署连当日榜单都产不出来（`test_pipe_fail_fast` 正是该场景：旧实现下流水线在
    `build_features` 之前就终止）。为什么必须可见：跳过若不留痕，就退回本缺陷最初
    的"静默停更"（P1-42）。
    """
    import app.orchestrator as orch
    import app.data.parquet_store as ps

    monkeypatch.setattr(ps, "read_all_symbols", lambda ds: [])
    called: dict = {}
    import app.data.universe as uni
    monkeypatch.setattr(uni, "build_universe_backtest",
                        lambda **kw: called.update(kw))
    out = orch.step_build_universe_bt(date(2026, 9, 21), ["000001.SZ"])
    assert out == "skipped=no_hfq_bars", f"detail 未披露跳过：{out}"
    assert called == {}, "无输入时不应调用构建器"


def test_builder_survives_all_null_list_date() -> None:
    """**全 NULL `list_date` 不得让构建器崩溃**（接线时实测到的潜伏缺陷）。

    接线 `build_universe_bt` 后暴露：若 instrument 表里**每一条** ``list_date`` 都为
    NULL（全新库 / 未跑 enrich；本仓库测试库正是此状态），polars 会把该列推断为
    ``Null`` dtype，而骨架的 ``date - list_date`` 抛
    ``InvalidOperationError: - not allowed on date and null`` ⇒ 整个构建器崩、夜间
    流水线在该步终止。修法：载入时显式 ``cast(pl.Date)``。

    该用例是"回归面"而非"数据面"：断言只有"不抛错"。
    端到端的"流水线仍能走到 build_features"由
    ``tests/test_pipeline.py::test_pipe_fail_fast`` 守（该步骤在隔离环境中的真实
    落盘副作用另见其 fixture 的 stub 说明）。
    """
    import asyncio

    from app.data.parquet_store import write_year_batch
    from app.data.universe import build_universe_backtest
    from app.db.init_db import init_database
    from app.db.models import Instrument
    from app.db.session import get_session_factory

    import polars as pl

    sym, code = "600777.SH", "600777"
    asyncio.run(init_database())

    async def _seed() -> None:
        factory = get_session_factory()
        async with factory() as sess:
            await sess.execute(Instrument.__table__.delete())
            sess.add(Instrument(symbol=sym, code=code, name="无上市日股",
                                market="SH", instrument_type="stock",
                                is_st=False, list_date=None))  # ← 关键：NULL
            await sess.commit()

    asyncio.run(_seed())
    days = [date(2022, 3, 1), date(2022, 3, 2)]
    bars = pl.DataFrame({
        "symbol": [sym] * 2, "code": [code] * 2, "date": days,
        "open": [10.0, 10.1], "high": [10.2, 10.3], "low": [9.8, 9.9],
        "close": [10.1, 10.2], "volume": [5e4, 5e4], "amount": [5.05e5, 5.1e5],
    })
    write_year_batch("daily_bar_hfq", sym, 2022, bars)
    write_year_batch("daily_bar", sym, 2022, bars)
    out = build_universe_backtest(persist=False)   # 修前在此抛 InvalidOperationError
    assert out.height > 0
    assert out["days_since_list"].null_count() == out.height, (
        "list_date 全空 ⇒ 上市天数应全为空（而不是崩溃或算出假值）")


def test_pipeline_step_order_documented_in_module_docstring() -> None:
    """模块 docstring 的顺序必须与 FULL_STEPS 一致（防止文档再次失真）。

    docstring 里的链条跨行折行，故先归一化空白再比对。
    """
    import app.orchestrator as orch

    chain = " → ".join(orch.FULL_STEPS)
    flat = " ".join((orch.__doc__ or "").split())
    assert chain in flat, f"docstring 未反映真实顺序：{chain}"


def test_concurrent_handoff_keeps_mutual_exclusion() -> None:
    """交接期间锁必须一直有效（worker 未释放前，他人拿不到）。"""
    h = acquire_pipeline_slot("fetch")
    result: dict = {}

    def _try():
        try:
            with pipeline_slot("mirror"):
                result["got"] = True
        except PipelineBusy:
            result["got"] = False

    t = threading.Thread(target=_try)
    t.start()
    t.join(timeout=5)
    assert result == {"got": False}, "交接期间锁被放开了 ⇒ 仍是 P1-31 的窗口"
    h.release()