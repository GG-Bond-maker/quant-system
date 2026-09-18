"""同步完整性修复回归（FIX-SPEC-sync-integrity.md，2026-09-17）。

覆盖两处 P1 缺陷：

- 缺陷 1（§1）：``_run_incremental`` 跳过判据必须**同时**比对 raw 与 hfq，
  而非只看 ``daily_bar``——否则 raw 最新的标的其落后的 ``daily_bar_hfq`` 会被
  永久跳过（hfq 是 ``step_build_features`` 的唯一输入 ⇒ 静默固化坏样本）。
- 缺陷 2（§2）：``orchestrator.STEPS`` / 晚间例行 / 手动「重跑」
  （``ops.dag_rerun``）必须从**单一事实源** ``FULL_STEPS`` 派生，且覆盖
  ``rebuild_qfq`` / ``build_universe`` / ``build_cs_mirror``。

每个缺陷均带**变异反证**说明（见各用例 docstring 末尾）：把实现改回旧行为时，
对应用例必须变红。
"""
from __future__ import annotations

import asyncio
import sys
from datetime import date, timedelta
from pathlib import Path

BACKEND_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(BACKEND_ROOT))

import pytest  # noqa: E402

import app.services.sync_service as ss  # noqa: E402
from app.domain.calendar import build_calendar, last_completed_trade_day  # noqa: E402


# ---------------------------------------------------------------------------
# 缺陷 1：增量同步跳过判据
# ---------------------------------------------------------------------------
@pytest.fixture()
def sync_env(monkeypatch):
    """复位同步状态并隔离网络/日历；last-date 读取由各用例按需 patch。"""
    ss._sync.completed.clear()
    ss._sync.failed.clear()
    ss._sync.done = 0
    ss._sync.total = 0
    ss._sync.rows_written = 0
    ss._sync.logs.clear()
    ss._sync.error = None
    ss._sync.cancel_event.clear()
    ss._sync.task_id = None

    monkeypatch.setattr(ss, "_refresh_trade_calendar", lambda: None)
    # 60 个自然日的伪日历（今天-1 为目标交易日），与既有 isolated_sync 一致
    days = [date.today() - timedelta(days=i) for i in range(1, 61)]
    monkeypatch.setattr(ss, "get_calendar", lambda: build_calendar(days))
    yield ss._sync


def _target() -> date:
    """当前用例环境下的目标交易日（与 _run_incremental 同口径）。"""
    return last_completed_trade_day(ss.get_calendar())


def test_raw_latest_but_hfq_stale_still_processed(sync_env, monkeypatch):
    """缺陷 1：raw 已最新但 hfq 落后 ⇒ 该标的**仍必须被处理**，且留 WARNING。

    正向证据（生产）：``300001.SZ`` raw=2026-09-15 / hfq=2026-09-04。
    旧实现只看 raw：raw>=target 即 continue，落后的 hfq 永不回补。

    变异反证：把 ``_run_incremental`` 的跳过判据改回"只看 raw"
    （``if last is not None and last >= target: continue``），本用例在
    ``assert fetched`` 处变红（fetch 不被调用）。
    """
    target = _target()
    raw_last = target                        # raw 已最新
    hfq_last = target - timedelta(days=7)    # hfq 落后
    fetched: list[tuple[str, str, str]] = []

    # raw 走 2 参接口（保持 patch 兼容），hfq 走新的 dataset 参数化接口
    monkeypatch.setattr(ss, "_symbol_last_date", lambda sym, year: raw_last)
    monkeypatch.setattr(
        ss, "_symbol_last_date_in",
        lambda dataset, sym, year: raw_last if dataset == "daily_bar" else hfq_last)

    def fake_fetch(code, start, end, adjusts=("", "hfq"), fetcher=None):
        fetched.append((code, start, end))
        return 1, []

    monkeypatch.setattr("app.data.ingest.tasks.fetch_and_write_daily_bars", fake_fetch)

    sync_env.total = 1
    ss._run_incremental(["300001.SZ"], resume=False)

    assert fetched, "raw 最新但 hfq 落后时，该标的必须仍被处理（旧实现会跳过）"
    assert fetched[0][0] == "300001"
    assert "300001.SZ" in sync_env.completed

    # 可观测性：必须留 WARNING（含 symbol 与两侧日期），不得静默
    warns = [lg for lg in sync_env.logs
             if lg["level"] == "WARNING" and "300001.SZ" in lg["message"]]
    assert warns, "raw 最新但对侧落后时必须留 WARNING"
    assert raw_last.isoformat() in warns[0]["message"]
    assert hfq_last.isoformat() in warns[0]["message"]


def test_both_datasets_latest_skips_silently(sync_env, monkeypatch):
    """守卫：两侧都 >= target 才跳过，且保持静默（重复运行幂等、不误报）。"""
    target = _target()
    fetched: list[str] = []

    monkeypatch.setattr(ss, "_symbol_last_date", lambda sym, year: target)
    monkeypatch.setattr(ss, "_symbol_last_date_in", lambda dataset, sym, year: target)

    def fake_fetch(code, start, end, adjusts=("", "hfq"), fetcher=None):
        fetched.append(code)
        return 1, []

    monkeypatch.setattr("app.data.ingest.tasks.fetch_and_write_daily_bars", fake_fetch)

    sync_env.total = 1
    ss._run_incremental(["600001.SH"], resume=False)

    assert fetched == [], "两侧都已最新时不应再发起任何抓取"
    assert "600001.SH" in sync_env.completed
    assert not [lg for lg in sync_env.logs if lg["level"] == "WARNING"], \
        "两侧都已最新时不应产生 WARNING（保持静默）"


# ---------------------------------------------------------------------------
# 缺陷 2：步骤集单一事实源
# ---------------------------------------------------------------------------
def test_default_steps_include_offline_rebuilds():
    """缺陷 2：默认步骤集（run_pipeline/CLI/dag_rerun 共用）须覆盖三步离线重建，
    且顺序符合模块 docstring，且 STEPS 与 FULL_STEPS 同源。

    变异反证：把 ``FULL_STEPS`` 改回旧子集
    （``["update_daily","validate","build_features","infer","screener_dump"]``），
    本用例在 ``set(orch.STEPS) >= {...}`` 与顺序断言处变红。
    """
    from app import orchestrator as orch

    assert orch.STEPS is orch.FULL_STEPS, "STEPS 必须与单一事实源 FULL_STEPS 同源"
    assert set(orch.FULL_STEPS) >= {"rebuild_qfq", "build_universe", "build_cs_mirror"}
    # 顺序须与模块头部 docstring 一致
    assert orch.FULL_STEPS == [
        "update_daily", "validate", "rebuild_qfq", "build_universe",
        "build_features", "infer", "screener_dump", "build_cs_mirror",
    ]


def test_evening_steps_derived_from_full_steps(monkeypatch):
    """缺陷 2/3：手动「重跑」（ops.dag_rerun）跑 FULL_STEPS 全量；晚间例行
    （evening_routine）跑**有序派生**的 ``EVENING_STEPS``（从 FULL_STEPS 剔除
    ``update_daily`` 一步）。此用例钉死**派生关系**而非集合相等。

    为何晚间例行剔除 ``update_daily``：autoSync 15:45 已同步行情，此步无「已最新
    则跳过」判据，全市场 2499 只 × 2 口径 ≈ 4998 次网络调用，全局串行限速下限
    约 100min（纯冗余重下载）。

    为何**保留** ``validate``：它是「昨有今无」式整日数据丢失（缺陷 3）的唯一门禁
    （09-14 正是昨有今无）；全市场直跑不误杀临时停牌股，安全性由
    ``data/pipeline.py`` 的 ``VALIDATE_MISSING_TOLERANCE_*`` 容差保证。

    变异反证：
      A) 把 ``EVENING_STEPS`` 改回 ``FULL_STEPS``（即不剔除任何步）→
         ``evening_steps == [s for s in FULL_STEPS if s != "update_daily"]`` 与
         ``"update_daily" not in evening_steps`` 变红；
      B) 把 ``EVENING_STEPS`` 改成硬编码且乱序的列表 → 有序派生相等 /
         保序子序列断言变红。
    """
    from app import orchestrator as orch
    from app.api.v1 import ops as ops_mod
    from app.jobs import evening_routine as er

    captured: dict[str, list[str]] = {}

    def fake_run_pipeline(trade_date, codes=None, dry_run=False, steps=None):
        # 复刻 _run_pipeline_impl 的"None = 默认 STEPS"语义
        captured["steps"] = list(steps) if steps is not None else list(orch.STEPS)
        return (None, False)

    monkeypatch.setattr(orch, "run_pipeline", fake_run_pipeline)

    # --- A) 手动「重跑」入口：应为 FULL_STEPS 全量 ---
    req = ops_mod.RerunRequest(trade_date="2026-09-12")
    asyncio.run(ops_mod.dag_rerun(req, _user={"role": "researcher"}))
    dag_steps = captured.pop("steps")

    # --- B) 晚间例行（隔离日历/监控/日报副作用，只关心传给 run_pipeline 的步骤） ---
    monkeypatch.setattr("app.domain.calendar.is_trade_day", lambda *a, **k: True)
    monkeypatch.setattr("app.data.calendar_store.get_calendar", lambda: None)
    monkeypatch.setattr("app.ml.monitor.run_monitor", lambda *a, **k: {"ok": True, "state": "ok"})
    monkeypatch.setattr("app.api.v1.report.generate_and_store_report",
                        lambda *a, **k: {"date": "test"})
    monkeypatch.setattr("app.core.events.publish_threadsafe", lambda *a, **k: None)

    er._run_routine()
    evening_steps = captured.pop("steps")

    # 手动重跑 = 全量（唯一顺序源）
    assert dag_steps == list(orch.FULL_STEPS)

    # 晚间例行 = FULL_STEPS 的有序派生（钉死派生关系，而非集合相等）
    assert evening_steps == list(orch.EVENING_STEPS)
    assert evening_steps == [s for s in orch.FULL_STEPS if s != "update_daily"]
    # 保序（子序列）断言：乱序硬编码会在此变红
    assert [s for s in orch.FULL_STEPS if s in set(evening_steps)] == evening_steps

    # 缺陷 2 的核心目的仍在：三步离线重建必须包含
    assert set(evening_steps) >= {"rebuild_qfq", "build_universe", "build_cs_mirror"}
    # 必须剔除 update_daily（~4998 次调用/≈100min 纯冗余重下载）
    assert "update_daily" not in evening_steps
    # 必须**保留** validate：它是「昨有今无」式整日数据丢失的唯一门禁（09-14）；
    # 全市场直跑安全性由 data/pipeline.py 的 VALIDATE_MISSING_TOLERANCE_* 容差保证
    assert "validate" in evening_steps
