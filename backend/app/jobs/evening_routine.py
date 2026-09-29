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
- **流水线失败记 `failed` 终态**，并按「最多 3 次 + 15min 退避」重试
  （审计 P1-29：原实现恒记 `done` ⇒ 终态无失败、当日不重试）；
- 调度循环绝不因单次异常退出。
"""
from __future__ import annotations

import asyncio
from datetime import date, datetime

from loguru import logger

from ..core.config import get_settings
from ..core.logging import setup_logging
from ..core.resilience import is_fatal_base_exception, log_contained
from ..db.kv import kv_get, kv_set

_ROUTINE_KEY = "evening_routine_last"
# 审计 P1-29（2026-09-21）：失败的当日重试策略。
# 为何必须有界：调度循环每 60s 检查一次，若把失败标成"可重试"而不设上限，
# 一次失败会变成**整晚每 60s 重跑整条流水线**（单次 ≈60s + 49min 级全市场
# 网络调用），把偶发撞车放大成资源风暴。故：最多 3 次、且两次之间退避 15 分钟。
_MAX_ATTEMPTS = 3
_RETRY_BACKOFF_SECONDS = 900


def _today_key() -> str:
    return date.today().isoformat()


def _attempts_today() -> int:
    """今日已尝试次数（跨重启保留；跨日自动归零）。"""
    rec = kv_get(_ROUTINE_KEY) or {}
    if rec.get("day") != _today_key():
        return 0
    return int(rec.get("attempts") or 0)


def _already_done_today() -> bool:
    """今日是否**无需再触发**（审计 P1-29：语义由"已完成"扩为"无需再触发"）。

    - `done` / `skipped`：当日收工；
    - `failed` 且**未用尽重试**、且距上次尝试已过退避：返回 False ⇒ 允许重试；
    - `failed` 且用尽重试：返回 True ⇒ 停止整晚重跑（终态仍是 `failed`，如实可查）；
    - `running` / 未知状态：返回 False ⇒ 允许触发（进程崩溃后的恢复路径）。

    ⚠️ 原实现只认 `done`/`skipped`，而 `_run_routine` 在流水线 FAILED 时
    仍**无条件** `_mark("done")` ⇒ 终态永远没有 `failed`、当日也永不重试
    （15:45/17:30 撞车即可触发：**当晚榜单停更而日报照常生成**）。
    """
    rec = kv_get(_ROUTINE_KEY) or {}
    if rec.get("day") != _today_key():
        return False
    st = rec.get("status")
    if st in ("done", "skipped"):
        return True
    if st != "failed":
        return False
    if int(rec.get("attempts") or 0) >= _MAX_ATTEMPTS:
        return True
    try:
        last = datetime.fromisoformat(str(rec.get("at")))
    except (TypeError, ValueError):
        return False
    return (datetime.now() - last).total_seconds() < _RETRY_BACKOFF_SECONDS


def _mark(status: str, detail: dict | None = None,
          attempts: int | None = None) -> None:
    rec: dict = {"day": _today_key(), "status": status,
                 "at": datetime.now().isoformat(timespec="seconds")}
    if attempts is not None:
        rec["attempts"] = attempts
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
    pipeline_failed = False

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
            # 审计 P1-29：流水线失败必须成为**终态**（而非仅一条 SSE + 字符串）
            pipeline_failed = True
            events.publish_threadsafe(
                "monitor", f"晚间例行：流水线失败（{job.error_message}）")
    except Exception as e:  # noqa: BLE001
        # 直接抛异常（如 PipelineBusy 撞车）同样属"流水线未完成"，必须记失败
        pipeline_failed = True
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

    # 4) ETF 概览快照归档（让 KPI 卡片的"真实 30 日序列"能长出来）
    #    为什么放在这里：ETF 概览统计 100% 实时来自外部源、本地无落库，唯一历史
    #    来源就是每日快照。此前归档只在「用户当天首次访问 /etf/overview」时被动
    #    写入 ⇒ 无人访问的日子永远缺档，序列永远长不出来。
    #    ⚠️ 双判据（交易日 + 盘后）由 should_archive_etf_snapshot 提供：本函数已
    #    在函数开头判过交易日，这里再验一次「盘后时点」—— 晚间例行的调度时点若
    #    被改到盘中，归档会把盘中值当成收盘值混进序列。
    #    单独 try/except：归档失败绝不影响主流程终态。
    try:
        import asyncio as _asyncio

        from ..core.config import get_settings as _get_settings
        from ..data.kpi_series import should_archive_etf_snapshot

        ok_to_archive, why = should_archive_etf_snapshot()
        if not ok_to_archive:
            detail["etf_archive"] = f"skipped: {why}"
        else:
            from ..api.v1.etf import _overview_snapshot, append_etf_snapshot

            snap = _overview_snapshot()
            _s = _get_settings()
            if not getattr(_s, "REDIS_ENABLED", False):
                detail["etf_archive"] = "skipped: REDIS_ENABLED=False"
            else:
                # _run_routine 跑在 asyncio.to_thread 里，本线程无运行中的事件循环
                # ⇒ 用 asyncio.run 起一个临时循环执行协程（与 RedisClient 的行为
                # 无关：append_etf_snapshot 只用 RedisClient 的 async API，后者在
                # 每个 loop 上自建连接池，短生命周期 loop 不会污染主循环）。
                written = _asyncio.run(append_etf_snapshot(snap))
                detail["etf_archive"] = "written" if written else "skipped: 当日已有存档"
    except Exception as e:  # noqa: BLE001 归档失败不阻断例行（独立降级）
        detail["etf_archive"] = f"error: {type(e).__name__}: {e}"
        logger.warning(f"[routine] ETF 快照归档失败（不影响例行终态）: {e!r}")

    # 审计 P1-29（2026-09-21）：终态必须如实反映流水线结果。
    #   · 流水线失败 ⇒ `failed`（可被 _already_done_today 按退避/上限重试，
    #     且运维在 app_state 里能直接看到，而不是只从 detail 字符串里挖）；
    #   · 仅监控/日报失败 ⇒ 仍记 `done`（模块设计明确"单步失败不阻断后续，
    #     features 失败也照出日报"），但 detail 已逐项留痕。
    n = _attempts_today()
    if pipeline_failed:
        _mark("failed", detail, attempts=n)
        events.publish_threadsafe(
            "monitor",
            f"晚间例行失败（第 {n}/{_MAX_ATTEMPTS} 次尝试）："
            f"{detail.get('pipeline')}——将按退避重试"
            if n < _MAX_ATTEMPTS else
            f"晚间例行失败（已用尽 {_MAX_ATTEMPTS} 次重试，当日不再重试）："
            f"{detail.get('pipeline')}")
    else:
        _mark("done", detail, attempts=n)
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
                    _mark("running", attempts=_attempts_today() + 1)
                    await asyncio.to_thread(_run_routine)
        except Exception as e:  # noqa: BLE001 调度循环绝不因单次异常退出
            logger.warning(f"[routine] scheduler error: {e!r}")
        except BaseException as exc:  # noqa: BLE001 panic 等非 Exception 兜底
            # [AQP panic 收口 D] 后台协程**不经 ASGI 中间件栈** ⇒ B 段 PanicGuard
            # 覆盖不到；而 polars 单列 sort 在 dtype=pl.Null 上抛的 PanicException
            # 是 BaseException ⇒ 漏接会让晚间例行**永久停摆且无任何日志**。
            # 必须放行的（CancelledError 等，见 is_fatal_base_exception）仍原样抛，
            # 其余兜住留痕后进入下一轮。
            if is_fatal_base_exception(exc):
                raise
            log_contained("evening_routine", exc)
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
    except BaseException as exc:  # noqa: BLE001 panic 等非 Exception 兜底
        # [AQP panic 收口 D] 一次性后台任务：panic 兜住留痕后**正常返回**——
        # 否则异常滞留 Task 无人取回，只在 GC 时留一条「Task exception was never
        # retrieved」，排障价值为零。
        if is_fatal_base_exception(exc):
            raise
        log_contained("startup_catchup", exc)
