"""2026-10-03 修复的交易日口径护栏（QA §6 覆盖缺口 #4 / #2 / #1 / #2b）。

背景（见 docs/audit/2026-10-03/_fix/04-qa-verification.md §3、§6）——QA 变异验证发现：
  - #4 ``last_completed_trade_date_or_last`` 的 **15:30 收盘截断** 无测试守护：
    现有 hotpath 测试 ``monkeypatch`` 掉该符号本身（等于把被测函数换掉），
    看不到**函数体**变异（把函数体退回旧口径 ``today_trade_date_or_last`` 后仍全绿）；
  - #2 ``build_daily_report`` 的**交易日门禁**无测试守护：既有用例只断言
    ``rep["date"]`` 是真值，改回 ``date.today()`` 仍全绿；
  - #1 ``auto_sync_scheduler`` / #2b ``startup_catchup`` 的非交易日门禁无测试守护。

本文件按 QA §6 补最小用例，**每条都能区分新/旧口径**（把对应修复改回缺陷形态即变红）。

日历来源：本仓测试隔离（``conftest.pytest_configure``）把 SQLITE 重定向到**空的**
临时库 ⇒ 测试中 ``get_calendar()`` 恒为空，真实日历不可用。故本文件注入一个与
**真实 ``trade_calendar`` 逐日一致**的交易日集合（不含 09-25 中秋假期），
核对来源：QA 只读查询 ``data/sqlite/aqp.db`` 的 ``trade_calendar`` 表
（``04-qa-verification.md §2.1``：09-18/21/22/23/24/28/29/30、10-08/09 为交易日，
**09-25 非交易日**）。这**不是**"自证式假日历"——集合内容与真实日历一致且注明来源。
"""
from __future__ import annotations

import asyncio
from datetime import date, datetime

import pytest

from app.domain.calendar import build_calendar

# 与真实 trade_calendar 一致的交易日（09-25 中秋假期缺席；10-01..10-07 国庆缺席）。
_TRADE_DAYS = [
    date(2026, 9, 18), date(2026, 9, 21), date(2026, 9, 22), date(2026, 9, 23),
    date(2026, 9, 24), date(2026, 9, 28), date(2026, 9, 29), date(2026, 9, 30),
    date(2026, 10, 8), date(2026, 10, 9),
]


@pytest.fixture()
def real_calendar(monkeypatch):
    """注入"与真实日历一致"的交易日集合。

    通过替换 ``app.data.calendar_store.get_calendar``（各被测函数在调用点**局部 import**
    该符号，故运行时会取到本补丁）实现，不触碰 ``is_trade_day``（避免"恒真/恒假"使
    门禁被判据之外的因素掩盖），保持"依赖日历"语义。
    """
    cal = build_calendar(_TRADE_DAYS)
    monkeypatch.setattr("app.data.calendar_store.get_calendar", lambda: cal)
    return cal


class _FrozenToday(date):
    """把某模块的 ``date.today()`` 冻结到固定日期（非交易日门禁用例）。"""

    _frozen: date = date(2026, 10, 5)  # 2026-10-05 周一，国庆假期 ⇒ 真实非交易日

    @classmethod
    def today(cls) -> date:
        return cls._frozen


class _FrozenTradeToday(date):
    """同上，但冻结到一个**交易日**（正向对照：门禁放行应触发）。"""

    _frozen: date = date(2026, 10, 8)  # 2026-10-08 周四，交易日

    @classmethod
    def today(cls) -> date:
        return cls._frozen


# ===========================================================================
# #4 last_completed_trade_date_or_last：15:30 收盘截断（最高危缺口）
# ===========================================================================
def test_last_completed_trade_date_or_last_applies_close_cutoff(real_calendar):
    """交易日（10-08）盘前/盘中 ⇒ 上一交易日 09-30；收盘后（≥15:30）⇒ 当天 10-08。

    反证（变异）：把函数体退回旧口径 ``today_trade_date_or_last()``（**无** 15:30 截断）
    ⇒ 盘中断言 10-08 ≠ 09-30 ⇒ 本用例变红。
    """
    from app.data.parquet_store import last_completed_trade_date_or_last

    before_close = last_completed_trade_date_or_last(now=datetime(2026, 10, 8, 10, 0))
    after_close = last_completed_trade_date_or_last(now=datetime(2026, 10, 8, 16, 0))

    # 盘前/盘中：绝不能返回"今天"（否则盘中快照会被写入缓存、收盘后被同一键命中）
    assert before_close == date(2026, 9, 30), before_close
    # 收盘后（≥15:30）：返回当天
    assert after_close == date(2026, 10, 8), after_close
    # 两种口径必须不同 —— 这正是"新旧口径"的判别点
    assert before_close != after_close


def test_last_completed_trade_date_or_last_default_uses_now(real_calendar, monkeypatch):
    """默认 ``now=None`` 时取真实"当前时刻"（生产调用方不传参的行为不被改坏）。"""
    import app.data.parquet_store as ps

    captured: dict = {}

    def _fake_lcd(cal, now=None):
        captured["now"] = now
        return date(2026, 9, 30)

    # 冻结"当前时刻"，观察默认路径是否把 now 透传给 domain 层（生产行为）
    class _FrozenNow(datetime):
        @classmethod
        def now(cls, tz=None):  # noqa: ANN001
            return datetime(2026, 10, 8, 10, 0)

    monkeypatch.setattr(ps, "datetime", _FrozenNow)
    # 把 domain 层函数换成探针（真实日历来自 real_calendar 补丁）
    import app.domain.calendar as cal_mod
    monkeypatch.setattr(cal_mod, "last_completed_trade_day", _fake_lcd)

    got = ps.last_completed_trade_date_or_last()
    assert got == date(2026, 9, 30)
    assert captured["now"] == datetime(2026, 10, 8, 10, 0), (
        "默认路径必须把'当前时刻'透传给 domain 层（否则 15:30 截断失效）")


# ===========================================================================
# #2 build_daily_report：交易日门禁 + 空日历兜底
# ===========================================================================
def test_build_daily_report_uses_last_completed_trade_day(real_calendar, monkeypatch):
    """2026-09-27（周日）⇒ 日报默认日 = 2026-09-24（真日历的上一交易日）。

    反证（变异）：把 report.py 的 ``day = last_completed_trade_day(get_calendar())``
    改回 ``day = date.today()`` ⇒ 得到自然日（≠ 09-24）⇒ 本用例变红。
    """
    import app.api.v1.report as report_api
    import app.domain.calendar as cal_mod

    class _FrozenNow(datetime):
        @classmethod
        def now(cls, tz=None):  # noqa: ANN001
            return datetime(2026, 9, 27, 20, 0, 0)  # 周日晚

    monkeypatch.setattr(cal_mod, "datetime", _FrozenNow)

    rep = report_api.build_daily_report()

    assert rep["date"] == "2026-09-24", rep["date"]
    assert rep["date"] != date.today().isoformat(), (
        "日报默认日绝不能是自然日（周末/节假日会被误当交易日）")


def test_build_daily_report_falls_back_when_calendar_empty(monkeypatch):
    """日历为空 ⇒ 不抛异常、退回 ``date.today()``、并留 WARNING 明示降级。

    反证（变异）：删掉 build_daily_report 里的 ``try/except`` ⇒ 空日历下
    ``last_completed_trade_day`` 抛 ValueError 直穿 ⇒ 本用例（期望不抛）变红。
    """
    from loguru import logger

    import app.api.v1.report as report_api

    monkeypatch.setattr("app.data.calendar_store.get_calendar",
                        lambda: build_calendar([]))

    records: list[str] = []
    sink_id = logger.add(lambda m: records.append(str(m)), level="WARNING")
    try:
        rep = report_api.build_daily_report()  # 不得抛出
    finally:
        logger.remove(sink_id)

    assert rep["date"] == date.today().isoformat(), rep["date"]
    assert any("交易日历不可用" in r for r in records), (
        f"空日历降级必须留 WARNING，实际日志: {records}")


# ===========================================================================
# #1 auto_sync_scheduler：非交易日门禁
# ===========================================================================
def _install_scheduler_prereqs(monkeypatch, sync_mod):
    """让触发分支的**其余**前置条件全部满足，使"只剩交易日门禁"能拦住触发。"""
    started: list[str] = []
    monkeypatch.setattr(sync_mod, "_start_sync_bg",
                        lambda mode, resume: started.append(mode))
    monkeypatch.setattr(sync_mod, "_load_auto_sync",
                        lambda: {"enabled": True, "time": "00:00"})
    monkeypatch.setattr(sync_mod, "_auto_sync_today_done", lambda: False)
    monkeypatch.setattr(sync_mod, "_record_auto_sync", lambda: None)

    async def _stop(_sec):  # 驱动"恰好一轮"后停住（sleep 在 try 之外）
        raise asyncio.CancelledError()

    monkeypatch.setattr(sync_mod.asyncio, "sleep", _stop)
    return started


def _run_one_scheduler_tick(sync_mod) -> None:
    with pytest.raises(asyncio.CancelledError):
        asyncio.run(sync_mod.auto_sync_scheduler())


def test_auto_sync_scheduler_skips_on_non_trade_day(real_calendar, monkeypatch):
    """非交易日（10-05 国庆假期）⇒ 调度一轮后 ``_start_sync_bg`` **未被调用**。

    反证（变异）：删掉 ``if is_trade_day(today, get_calendar()):`` 门禁
    ⇒ 非交易日也会触发 ⇒ ``started`` 非空 ⇒ 本用例变红。
    """
    from app.services import sync_service as sync_mod

    started = _install_scheduler_prereqs(monkeypatch, sync_mod)
    monkeypatch.setattr(sync_mod, "date", _FrozenToday)

    st = sync_mod._sync
    with st.lock:
        prev_running = st.running
        st.running = False
    try:
        _run_one_scheduler_tick(sync_mod)
    finally:
        with st.lock:
            st.running = prev_running

    assert started == [], f"非交易日不得触发同步，实际触发: {started}"


def test_auto_sync_scheduler_triggers_on_trade_day(real_calendar, monkeypatch):
    """正向对照：交易日（10-08）⇒ 调度一轮后 ``_start_sync_bg`` 被调用一次。

    防"门禁恒跳过"式的过度防御（若把门禁写成恒 False，本用例变红）。
    """
    from app.services import sync_service as sync_mod

    started = _install_scheduler_prereqs(monkeypatch, sync_mod)
    monkeypatch.setattr(sync_mod, "date", _FrozenTradeToday)

    st = sync_mod._sync
    with st.lock:
        prev_running = st.running
        st.running = False
    try:
        _run_one_scheduler_tick(sync_mod)
    finally:
        with st.lock:
            st.running = prev_running

    assert started == ["incremental"], f"交易日应触发一次增量同步，实际: {started}"


# ===========================================================================
# #2b startup_catchup：非交易日不补跑日报
# ===========================================================================
def _install_catchup_prereqs(monkeypatch):
    """让 startup_catchup 进入"日报缺失"分支，并记录 generate 调用。"""
    from app.api.v1 import report as report_api
    from app.ml import monitor as monitor_mod

    generated: list[str] = []
    monkeypatch.setattr(report_api, "generate_and_store_report",
                        lambda: generated.append("gen"))
    monkeypatch.setattr(report_api, "get_latest_report", lambda: None)
    # 命中快照存在 ⇒ 跳过 monitor 分支，确保只有"日报门禁"决定 generate 是否被调
    monkeypatch.setattr(monitor_mod, "get_health_snapshot", lambda: {"ok": True})
    return generated


@pytest.mark.asyncio
async def test_startup_catchup_skips_report_on_non_trade_day(real_calendar, monkeypatch):
    """非交易日（10-05）⇒ ``generate_and_store_report`` **未被调用**。

    反证（变异）：去掉 ``startup_catchup`` 里的交易日门禁（无条件补跑）
    ⇒ 非交易日也生成日报 ⇒ ``generated`` 非空 ⇒ 本用例变红。
    """
    from app.jobs import evening_routine as evening_mod

    generated = _install_catchup_prereqs(monkeypatch)
    monkeypatch.setattr(evening_mod, "date", _FrozenToday)

    await evening_mod.startup_catchup()

    assert generated == [], f"非交易日不得补跑日报，实际: {generated}"


@pytest.mark.asyncio
async def test_startup_catchup_generates_report_on_trade_day(real_calendar, monkeypatch):
    """正向对照：交易日（10-08）且当日日报缺失 ⇒ 补跑一次。"""
    from app.jobs import evening_routine as evening_mod

    generated = _install_catchup_prereqs(monkeypatch)
    monkeypatch.setattr(evening_mod, "date", _FrozenTradeToday)

    await evening_mod.startup_catchup()

    assert generated == ["gen"], f"交易日应补跑日报一次，实际: {generated}"
