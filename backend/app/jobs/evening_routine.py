"""晚间例行调度（前沿演进 Phase 0：补齐修复报告遗留的调度入口）。

在 main.py lifespan 中以 asyncio.create_task 启动（与 datacenter autoSync
同款 60s 检查模式，不引入新依赖）。到点后在线程池顺序执行：

    每日流水线派生步骤集（orchestrator.EVENING_STEPS：从 FULL_STEPS 有序剔除
    update_daily，即 validate → rebuild_qfq → build_universe → build_features
    → infer → screener_dump → build_cs_mirror；复用 pipeline，data_jobs 幂等）
    → 因子健康度监控（ml/monitor，含漂移告警与自动重训触发）
    → 模板版 AI 日报（api/v1/report，经 events 推送顶栏铃铛）

设计约束：
- 当日幂等：完成（或显式跳过）后记录 app_state，重启不重复执行；
- 非交易日跳过（交易日历驱动，与 pipeline 口径一致）；
- 各环节独立 try/except，单步失败不阻断后续（如 features 构建失败仍出日报）；
- 调度循环绝不因单次异常退出。
"""
from __future__ import annotations

import asyncio
from datetime import date, datetime

from loguru import logger

from ..core.config import get_settings
from ..core.logging import setup_logging
from ..db.kv import kv_get, kv_set

_ROUTINE_KEY = "evening_routine_last"


def _today_key() -> str:
    return date.today().isoformat()


def _already_done_today() -> bool:
    rec = kv_get(_ROUTINE_KEY) or {}
    return rec.get("day") == _today_key() and rec.get("status") in ("done", "skipped")


def _mark(status: str, detail: dict | None = None) -> None:
    rec: dict = {"day": _today_key(), "status": status,
                 "at": datetime.now().isoformat(timespec="seconds")}
    if detail:
        rec["detail"] = detail
    kv_set(_ROUTINE_KEY, rec)


def _run_routine() -> None:
    """例行动作（同步，线程池内执行）：pipeline 子集 → 监控 → 日报。"""
    from ..domain.calendar import is_trade_day
    from ..data.calendar_store import get_calendar

    today = date.today()
    cal = get_calendar()
    if not is_trade_day(today, cal):
        _mark("skipped", {"reason": f"{today} 非交易日"})
        logger.info(f"[routine] {today} 非交易日，跳过晚间例行")
        return

    from ..core import events

    detail: dict = {}

    # 1) 每日流水线（单一顺序事实源：orchestrator.EVENING_STEPS 从 FULL_STEPS
    #    有序剔除 update_daily，见该常量注释；与手动「重跑」ops.dag_rerun
    #    （用 FULL_STEPS 全量）同源、不可能漂移）。
    #    为何剔除 update_daily：autoSync 15:45 已同步行情，此步无「已最新则跳过」
    #    判据，全市场 2499 只 × 2 口径 ≈ 4998 次网络调用，全局限速下限约 100min，
    #    属纯冗余重下载。
    #    validate **保留**：它是「昨有今无」式整日数据丢失的唯一门禁（09-14 正是
    #    昨有今无）；全市场直跑不会误杀临时停牌股（带容差，见 data/pipeline.py 的
    #    VALIDATE_MISSING_TOLERANCE_*）。
    try:
        from ..data.parquet_store import read_all_symbols
        from ..orchestrator import EVENING_STEPS, run_pipeline

        codes = read_all_symbols("daily_bar")
        job, _executed = run_pipeline(today, codes, steps=list(EVENING_STEPS))
        detail["pipeline"] = (f"{job.status}/{job.current_step}" if job else "skipped")
        if job is not None and job.status == "FAILED":
            events.publish_threadsafe(
                "monitor", f"晚间例行：流水线失败（{job.error_message}）")
    except Exception as e:  # noqa: BLE001
        detail["pipeline"] = f"error: {type(e).__name__}: {e}"
        logger.warning(f"[routine] pipeline 失败（继续后续环节）: {e!r}")

    # 2) 因子健康度监控（含漂移告警 / 自动重训触发）
    try:
        from ..ml import monitor

        snap = monitor.run_monitor(trigger="routine")
        detail["monitor"] = (f"state={snap.get('state')}" if snap.get("ok")
                             else f"error: {snap.get('error')}")
    except Exception as e:  # noqa: BLE001
        detail["monitor"] = f"error: {type(e).__name__}: {e}"
        logger.warning(f"[routine] monitor 失败（继续日报）: {e!r}")

    # 3) 模板版 AI 日报
    try:
        from ..api.v1.report import generate_and_store_report

        rep = generate_and_store_report()
        detail["report"] = f"date={rep.get('date')}" if rep else "empty"
    except Exception as e:  # noqa: BLE001
        detail["report"] = f"error: {type(e).__name__}: {e}"
        logger.warning(f"[routine] report 失败: {e!r}")

    _mark("done", detail)
    events.publish_threadsafe("monitor", f"晚间例行完成：{detail}")


async def evening_routine_scheduler() -> None:
    """后台调度循环：每 60s 检查时间，到点触发当日例行（幂等）。"""
    setup_logging(get_settings())
    while True:
        try:
            s = get_settings()
            if s.EVENING_ROUTINE_ENABLED and not _already_done_today():
                hhmm = datetime.now().strftime("%H:%M")
                if hhmm >= s.EVENING_ROUTINE_TIME:
                    logger.info(f"[routine] triggered at {hhmm}")
                    _mark("running")
                    await asyncio.to_thread(_run_routine)
        except Exception as e:  # noqa: BLE001 调度循环绝不因单次异常退出
            logger.warning(f"[routine] scheduler error: {e!r}")
        await asyncio.sleep(60)


async def startup_catchup() -> None:
    """启动补跑：当日尚无监控快照/日报时立即补一次（不等 17:30）。

    幂等约束：快照/日报任一缺失只补缺失项；两者都在则直接返回。
    """
    try:
        from ..api.v1.report import generate_and_store_report, get_latest_report
        from ..ml import monitor

        if monitor.get_health_snapshot() is None:
            await asyncio.to_thread(monitor.run_monitor, "startup")
        if get_latest_report() is None:
            await asyncio.to_thread(generate_and_store_report)
    except Exception as e:  # noqa: BLE001
        logger.warning(f"[routine] startup catchup error: {e!r}")
