"""数据中心一键同步：状态机 + 断点续传 + autoSync 调度（Task 16 下沉）。

自 api/v1/datacenter.py 平移（纯移动，行为不变）：
- _SyncState/_sync         进程内单任务串行状态 + 环形日志缓冲
- _persist/restore         断点续传状态落 SQLite kv（P2-14）
- _run_incremental/_run_repair/_run_rebuild + _sync_worker
                           三种同步模式的真实执行体（后台线程）
- autoSync kv 配置 + auto_sync_scheduler（main.py lifespan 启动的后台协程）

为何离开 api 层：1344 行路由文件里内嵌整套同步编排，属于业务逻辑；
路由层只应做参数校验与响应组装。本模块依赖 data/db/domain，
禁止反向 import app.api。
"""
from __future__ import annotations

import asyncio
import threading
import time
from datetime import date, datetime
from pathlib import Path

from loguru import logger

from ..core.config import get_settings
from ..core.resilience import is_fatal_base_exception, log_contained
from ..data.calendar_store import get_calendar
from ..data.parquet_store import read_all_symbols
from ..domain.calendar import last_completed_trade_day
from .stats_cache import invalidate_stats_cache
from .task_store import update_task


class _SyncState:
    """同步任务状态：单任务串行，进度 + 环形日志缓冲。

    支持断点续传：``completed`` 记录本次任务已成功抓取的 symbol；
    若任务被 cancel/异常中断，``completed`` 保留，下次同 mode 启动且
    ``resume=True`` 时跳过这些 symbol。任务正常完成（running 全跑完）
    时清空 ``completed``。

    ``completed`` 通过 app_state 表持久化（P2-14）：任务中断（cancel/
    异常/进程重启）后，下次同 mode 启动且 ``resume=True`` 仍可跳过已
    完成 symbol。运行中每 30s 落盘一次进度，任务正常完成时清空记录。
    """

    def __init__(self) -> None:
        self.lock = threading.Lock()
        self.running = False
        self.task_id: str | None = None
        self.mode: str | None = None
        self.total = 0
        self.done = 0
        # 缺陷 D（P1）：本次任务真实落库行数——空跑判定（done==0/total>0）的辅证，
        # 并让完成日志/SSE 文案带上有意义的计数。每次任务启动时重置。
        self.rows_written = 0
        self.current = ""
        self.started_at: float | None = None
        self.finished_at: float | None = None
        self.error: str | None = None
        self.logs: list[dict] = []
        # 断点续传：记录已完成的 symbol，cancel/exception 后下次 resume 跳过
        self.completed: set[str] = set()
        # Task 9（整改 A-P1-4）：抓取故障的 symbol（可重试）——与 completed
        # 分离，resume 只跳过 completed，故障标的重试，缺口不再静默固化
        self.failed: set[str] = set()
        # 优雅停止：worker 循环内检查，cancel 后尽快退出
        self.cancel_event = threading.Event()
        # P2-14：进度持久化的时间片（每次 log 触发检查，30s 一次）
        self._last_persist = 0.0

    def log(self, level: str, msg: str) -> None:
        with self.lock:
            self.logs.append({
                "ts": datetime.now().strftime("%H:%M:%S"),
                "level": level,
                "message": msg[:200],
            })
            self.logs = self.logs[-200:]
        # 每个 symbol 拉取完都会 log 一次，借此处做时间片进度持久化：
        # 硬崩溃（进程被杀）也能从最近一次持久化点续传
        self._maybe_persist()

    def _maybe_persist(self) -> None:
        """运行中周期性把 completed 落盘（P2-14，30s 一次）。"""
        now = time.monotonic()
        if now - self._last_persist < 30.0 or not self.running:
            return
        self._last_persist = now
        with self.lock:
            mode, completed = self.mode, list(self.completed)
            failed = list(self.failed)
            task_id = self.task_id
            progress = {"done": self.done, "total": self.total,
                        "current": self.current}
        _persist_sync_state(mode, completed, failed, False)
        if task_id:
            try:
                update_task(task_id, "running", progress=progress)
            except Exception as exc:  # noqa: BLE001 - progress persistence is best effort
                logger.warning(f"[datacenter] persist task progress failed: {exc!r}")

    def snapshot(self) -> dict:
        with self.lock:
            pct = round(self.done / self.total * 100) if self.total else 0
            return {
                "task_id": self.task_id,
                "running": self.running,
                "mode": self.mode,
                "total": self.total,
                "done": self.done,
                "percent": pct,
                "current": self.current,
                "error": self.error,
                "started_at": self.started_at,
                "elapsed_ms": int((time.monotonic() - (self.started_at or time.monotonic())) * 1000),
                "logs": self.logs[-60:],
                "completed_count": len(self.completed),
                "failed_count": len(self.failed),
                "cancelled": self.cancel_event.is_set(),
            }


_sync = _SyncState()


def _check_cancel() -> bool:
    """worker 循环内调用：返回 True 表示用户请求停止，应尽快优雅退出。"""
    return _sync.cancel_event.is_set()


def _persist_sync_state(mode: str | None, completed: list[str],
                        failed: list[str], finished: bool) -> None:
    """同步续传状态落 SQLite（P2-14）；失败只记日志，不影响同步本身。"""
    try:
        from ..db.kv import kv_set
        kv_set("sync_state",
               {"mode": mode, "completed": completed, "failed": failed,
                "finished": finished})
    except Exception as e:  # noqa: BLE001
        logger.warning(f"[datacenter] persist sync state fail: {e!r}")


def restore_sync_state() -> None:
    """启动时恢复上次中断同步任务的续传记录（P2-14，lifespan 调用）。"""
    try:
        from ..db.kv import kv_get
        st = kv_get("sync_state")
        # 只恢复"未完成且有进度"的记录（正常完成的任务已写 finished=True）
        if not st or st.get("finished", True) or not st.get("completed"):
            return
        with _sync.lock:
            _sync.completed = set(st["completed"])
            # Task 9：兼容旧记录（无 failed 字段）——resume 重试 failed
            _sync.failed = set(st.get("failed", []))
            _sync.mode = st.get("mode")
        logger.info(f"[datacenter] restored sync resume state: "
                    f"{len(st['completed'])} completed, "
                    f"{len(_sync.failed)} failed (mode={st.get('mode')})")
    except Exception as e:  # noqa: BLE001
        logger.warning(f"[datacenter] restore sync state fail: {e!r}")


def _refresh_trade_calendar() -> None:
    """刷新交易日历：网络拉取（含未来日期）→ upsert SQLite → 重载进程缓存。

    日历只靠手动 CLI 灌入会逐渐过期，prev_trade_day 随之停在旧日期，
    增量同步目标日被冻结（新交易日行情永远同步不进来且无告警）。
    因此每次同步任务前先刷新；失败仅告警，降级用现有日历。
    本函数在同步工作线程中调用（无事件循环），asyncio.run 安全。
    """
    from ..data.calendar_store import load_from_db_sync, set_calendar
    from ..data.ingest.akshare_adapter import fetch_trade_calendar
    from ..data.ingest.tasks import upsert_calendar

    try:
        cal = fetch_trade_calendar()  # 不传 end：保留源返回的全年交易日
        if cal.empty:
            logger.warning("[datacenter] refresh calendar: source returned empty")
            return
        n = asyncio.run(upsert_calendar(cal))
        set_calendar(load_from_db_sync())
        logger.info(f"[datacenter] trade calendar refreshed: {n} rows")
    except Exception as e:  # noqa: BLE001
        logger.warning(f"[datacenter] refresh trade calendar fail: {e!r}")


def _symbol_last_date_in(dataset: str, sym: str, ref_year: int) -> date | None:
    """读取标的在**指定数据集**内最后交易日（只读最新年份分区，缺失时逐年回退）。

    单文件 parquet 读取，避免全量历史加载；最多回退 3 年兜底长期停牌。

    缺陷 1（P1）：增量同步的跳过判据必须**同时**比对 raw（``daily_bar``）与
    hfq（``daily_bar_hfq``）两个数据集，故把 dataset 参数化；否则只看 raw 会把
    "hfq 落后"的标的永久跳过（hfq 是 step_build_features 的唯一输入 ⇒ 静默固化坏样本）。
    """
    from ..data.parquet_store import read_symbol_year

    year = ref_year
    for _ in range(3):
        df = read_symbol_year(dataset, sym, year)
        if not df.is_empty() and "date" in df.columns:
            # polars 标量静态类型是 Any，需显式收敛为 date（签名承诺 date | None）
            last = df["date"].max()
            if isinstance(last, datetime):
                return last.date()
            return last if isinstance(last, date) else date.fromisoformat(str(last)[:10])
        year -= 1
    return None


def _symbol_last_date(sym: str, ref_year: int) -> date | None:
    """读取标的 ``daily_bar``（raw）库内最后交易日（2 参便捷封装）。

    ⚠️ 保持 2 参签名不变：``app/api/v1/datacenter.py`` 重导出本函数，且
    ``tests/test_resume_failed_semantics.py`` 以 ``lambda sym, year: None`` patch 它。
    内部委托 :func:`_symbol_last_date_in`；需要读取 hfq 等其它数据集时直接调用后者。
    """
    return _symbol_last_date_in("daily_bar", sym, ref_year)


def _run_incremental(symbols: list[str], resume: bool = False) -> None:
    """一键更新：对已落库标的增量拉取三口径行情至最近一个交易日（真实 AKShare 调用）。

    同步前先刷新交易日历（防止目标日冻结在过期日历）；
    按每个标的库内最后交易日做区间拉取，自动回补停机期间错过的交易日
    （write_partition 按 date 去重合并，重复区间幂等）。

    跳过判据（缺陷 1，P1）：raw 与 hfq **两侧都** >= target 才跳过；任一落后即
    进入抓取（区间起点取两侧中较早者的次一交易日），并在 raw 最新但对侧落后时留
    WARNING。此前只看 raw，导致 hfq 缺口被永久跳过、同步假成功。
    """
    from ..data.ingest.tasks import fetch_and_write_daily_bars
    from ..domain.calendar import next_trade_day

    _refresh_trade_calendar()
    cal = get_calendar()
    # ⚠️ 不能用 prev_trade_day(date.today())：那会让"今天"的行情只能等次日
    # 同步才进库（实测 2026-09-07 盘后同步仍以 09-04 为目标）。用已收盘语义。
    target = last_completed_trade_day(cal)
    ds = target.isoformat()
    # resume：跳过上次已完成的 symbol
    pending = [s for s in symbols if not (resume and s in _sync.completed)] if resume else list(symbols)
    skipped = len(symbols) - len(pending)
    _sync.log("INFO", f"开始增量同步：目标交易日 {ds}，共 {len(symbols)} 只"
              + (f"（resume 跳过已完成的 {skipped} 只）" if resume and skipped else ""))
    # resume 且 pending 为空 = 上次已全部完成：必须把 done 推到 total，否则
    # _sync_worker 的"空跑断言"（total>0 且 done==0）会把它误判成 NOOP/FAILED。
    if not pending:
        with _sync.lock:
            _sync.done = len(symbols)
        _sync.log("INFO", f"全部 {len(symbols)} 只标的已完成，无需重复处理（resume 跳过）")
        return
    for i, sym in enumerate(pending, 1):
        if _check_cancel():
            _sync.log("WARNING", "用户停止同步，任务已中断（已完成的 symbol 已记录，可断点续传）")
            return
        code = sym.split(".")[0]
        # 区间起点 = 库内最后交易日的次一交易日；无数据/已最新时退化为单日。
        # 缺陷 1（P1）：raw（daily_bar）与 hfq（daily_bar_hfq）可**双向漂移**，
        # "只看 raw 是否 >= target"不足以判定已完成——必须两侧都最新才可跳过；
        # 任一落后即进入抓取，且以**两侧中较早**的 last 作为区间起点，保证落后
        # 口径被一并回补（write_partition 按 date 去重合并，重复区间幂等）。
        last = _symbol_last_date(sym, target.year)  # raw，保持 2 参 patch 兼容
        last_hfq = _symbol_last_date_in("daily_bar_hfq", sym, target.year)
        if (last is not None and last >= target
                and last_hfq is not None and last_hfq >= target):
            with _sync.lock:
                _sync.completed.add(sym)
                _sync.done = i + skipped
            continue  # 两侧都已最新（重复运行幂等，直接跳过网络请求，保持静默）
        # 可观测性（缺陷 1）：raw 已最新但对侧 hfq 落后 ⇒ 仍须抓取，且必须留 WARNING
        # （含 symbol 与两侧日期），不得静默；否则同步显示"成功"却埋下坏样本。
        if last is not None and last >= target:
            _sync.log(
                "WARNING",
                f"{sym} daily_bar 已至 {last.isoformat()} 但 daily_bar_hfq 仅至 "
                f"{last_hfq.isoformat() if last_hfq is not None else '（空）'}，"
                f"hfq 缺口须回补（避免静默固化坏样本）")
        # 起始参考日取两侧中较早者：hfq 落后时从 hfq 缺口处补起
        last_ref = min([d for d in (last, last_hfq) if d is not None], default=None)
        try:
            start = next_trade_day(last_ref, cal).isoformat() if last_ref is not None else ds
        except ValueError:
            start = ds  # 日历数据不足（刷新失败降级），退化为单日拉取
        rng = f"（{start} ~ {ds} 回补）" if start != ds else ""
        _sync.current = f"拉取 {sym} 日线数据 ({i}/{len(pending)})"
        try:
            n, failed_adj = fetch_and_write_daily_bars(code, start, ds, adjusts=("", "hfq"))
            with _sync.lock:
                # Task 9：completed / failed 分离——全败不记完成，缺口不固化
                if n > 0 and not failed_adj:
                    _sync.completed.add(sym)
                elif n > 0 or failed_adj:
                    _sync.failed.add(sym)
                else:
                    _sync.completed.add(sym)  # 全口径无数据（停牌/退市），不重试
                if n > 0:
                    _sync.rows_written += n  # 缺陷 D：累计真实落库行数
            if failed_adj:
                _sync.log("WARNING", f"拉取 {sym} 部分口径失败: {failed_adj}（已记入 failed，可 resume 重试）")
            else:
                _sync.log("INFO", f"拉取 {sym} {rng}... {n} 行成功" if n else f"{sym} 无数据（可能停牌）")
        except Exception as e:
            with _sync.lock:
                _sync.failed.add(sym)
            _sync.log("WARNING", f"拉取 {sym} 失败: {e!r}"[:200])
        with _sync.lock:
            _sync.done = i + skipped  # 进度含跳过的
    invalidate_stats_cache()
    _sync.log("INFO", f"增量同步完成：{ds}")


def _run_repair(symbols: list[str], resume: bool = False) -> None:
    """修复缺漏：对有缺失的标的重新拉取完整历史并按年重分区（AKShare 单次返回全量）。"""
    from ..data.ingest.tasks import fetch_and_write_daily_bars

    _refresh_trade_calendar()  # 修复截止日同样依赖日历，先刷新防止回补上限冻结
    # ⚠️ 与 _run_incremental 保持同一"已收盘"语义：prev_trade_day(date.today())
    # 会让修复截止日恒落后一个交易日（domain/calendar.py docstring 已将其列为
    # 反模式），盘后跑完 repair 仍拿不到当天数据，用户以为修好了其实没修到最新。
    end = last_completed_trade_day(get_calendar()).isoformat()
    pending = [s for s in symbols if not (resume and s in _sync.completed)] if resume else list(symbols)
    skipped = len(symbols) - len(pending)
    _sync.log("INFO", f"开始修复K线缺漏：{len(symbols)} 只，回补至 {end}"
              + (f"（resume 跳过已完成的 {skipped} 只）" if resume and skipped else ""))
    if not pending:  # 同 _run_incremental：resume 全部已完成 → done=total，避免误判空跑
        with _sync.lock:
            _sync.done = len(symbols)
        _sync.log("INFO", f"全部 {len(symbols)} 只标的已完成，无需重复处理（resume 跳过）")
        return
    for i, sym in enumerate(pending, 1):
        if _check_cancel():
            _sync.log("WARNING", "用户停止同步，任务已中断（可断点续传）")
            return
        code = sym.split(".")[0]
        _sync.current = f"修复 {sym} 历史K线 ({i}/{len(pending)})"
        try:
            n, failed_adj = fetch_and_write_daily_bars(code, "2015-01-01", end,
                                                       adjusts=("", "hfq"))
            with _sync.lock:
                if n > 0 and not failed_adj:
                    _sync.completed.add(sym)
                elif failed_adj:
                    _sync.failed.add(sym)
                else:
                    _sync.completed.add(sym)
                if n > 0:
                    _sync.rows_written += n  # 缺陷 D：累计真实落库行数
            _sync.log("INFO", f"修复 {sym} ... {n} 行成功" if not failed_adj
                      else f"修复 {sym} 部分口径失败: {failed_adj}（已记入 failed）")
        except Exception as e:
            with _sync.lock:
                _sync.failed.add(sym)
            _sync.log("WARNING", f"修复 {sym} 失败: {e!r}"[:200])
        with _sync.lock:
            _sync.done = i + skipped
    invalidate_stats_cache()
    _sync.log("INFO", "修复完成")


def _run_rebuild(symbols: list[str], resume: bool = False) -> None:
    """全量重构：离线由 raw + 复权因子重建前复权数据集（不访问网络）。"""
    from ..data.repair import build_qfq_dataset

    root = get_settings().DATA_ROOT / "daily_bar"
    pending = [s for s in symbols if not (resume and s in _sync.completed)] if resume else list(symbols)
    skipped = len(symbols) - len(pending)
    _sync.log("INFO", f"开始全量重构前复权数据集：{len(symbols)} 只（离线计算）"
              + (f"（resume 跳过已完成的 {skipped} 只）" if resume and skipped else ""))
    if not pending:  # 同 _run_incremental：resume 全部已完成 → done=total，避免误判空跑
        with _sync.lock:
            _sync.done = len(symbols)
        _sync.log("INFO", f"全部 {len(symbols)} 只标的已完成，无需重复处理（resume 跳过）")
        return
    for i, sym in enumerate(pending, 1):
        if _check_cancel():
            _sync.log("WARNING", "用户停止同步，任务已中断（可断点续传）")
            return
        _sync.current = f"重构 {sym} 前复权序列 ({i}/{len(pending)})"
        try:
            n = build_qfq_dataset(root, sym)
            with _sync.lock:
                _sync.completed.add(sym)
                if n > 0:
                    _sync.rows_written += n  # 缺陷 D：累计真实落库行数
            _sync.log("INFO", f"重构 {sym} ... {n} 行")
        except Exception as e:
            _sync.log("WARNING", f"重构 {sym} 失败: {e!r}"[:200])
        with _sync.lock:
            _sync.done = i + skipped
    invalidate_stats_cache()
    _sync.log("INFO", "全量重构完成")


def _sync_worker(mode: str, symbols: list[str], resume: bool) -> None:
    from ..core.pipeline_lock import PipelineBusy, pipeline_slot

    try:
        with pipeline_slot("sync"):
            runner = {"incremental": _run_incremental,
                      "repair": _run_repair,
                      "rebuild": _run_rebuild}[mode]
            runner(symbols, resume=resume)
    except PipelineBusy as e:
        # 管道互斥（C-01）：mirror/pipeline/training 执行中拒绝同步，状态必须闭环
        with _sync.lock:
            _sync.error = f"PipelineBusy: {e}"
        _sync.log("ERROR", f"管道互斥，同步未执行：{e}")
    except Exception as e:
        with _sync.lock:
            _sync.error = f"{type(e).__name__}: {e}"
        _sync.log("ERROR", f"任务终止: {e!r}"[:200])
    except BaseException as exc:  # noqa: BLE001 panic 等非 Exception 兜底
        # [AQP panic 收口 D] daemon **线程**（非协程）里的 panic：兜住并留痕，
        # **不吞掉**下方 finally（_sync.running 复位 / 终态落库 / 续传记录仍执行）。
        # ⚠️ 必须**同时**置 ``_sync.error``：下方 finally 用
        # ``worker_error = _sync.error``（L426）决定终态，并据此写
        # ``_record_sync_job``。若只兜不置错，本轮 panic 会被记成 **SUCCESS**
        # ——那是数据造假（与本项目「真实性红线」冲突），故与 except Exception
        # 分支保持**同口径**。
        if is_fatal_base_exception(exc):
            raise
        with _sync.lock:
            _sync.error = f"{type(exc).__name__}: {exc}"
        _sync.log("ERROR", f"任务终止（不可捕获异常）: {exc!r}"[:200])
        log_contained("sync_worker", exc)
    finally:
        with _sync.lock:
            _sync.running = False
            _sync.finished_at = time.monotonic()
            cancelled = _sync.cancel_event.is_set()
            # 任务正常跑完（非 cancel、非 exception）时清空续传记录；
            # cancel/exception 时保留 _sync.completed 供下次 resume 跳过。
            if not cancelled and not _sync.error:
                _sync.completed.clear()
            # 重置 cancel 标志，下次任务从干净状态开始
            _sync.cancel_event.clear()
            done, total = _sync.done, _sync.total
            rows_written = _sync.rows_written
            task_id = _sync.task_id
            worker_error = _sync.error
            completed_left = list(_sync.completed)
            failed_left = list(_sync.failed)
            # finished_at/started_at 为 Optional[float]（任务未跑完时可能为 None）
            _fin = _sync.finished_at or _sync.started_at or 0.0
            _sta = _sync.started_at or 0.0
            duration_ms = int(max(0.0, (_fin - _sta) * 1000))
        # 真实性断言（缺陷 D，P1）：done==0 且 total>0 唯一对应"循环一次都没跑"
        # —— _run_incremental 的跳过分支与处理分支都会推进 _sync.done（见其循环体
        # L234/L261），故 done 仍为 0 说明本次同步未处理任何标的（如 09-14 的
        # 1109ms 空跑），不得再冒充 SUCCESS 去点亮 /overview 健康灯、污染 AI 日报。
        # 不误伤"全部标的都已最新"语义：那种情况 done==total（>0），noop 为 False。
        noop = total > 0 and done == 0
        counts = (f"completed={len(completed_left)} failed={len(failed_left)} "
                  f"rows={rows_written}")
        # P2-14：终态持久化（正常完成清空续传记录；中断/空跑保留供跨重启 resume）
        _persist_sync_state(mode, completed_left, failed_left,
                            not cancelled and not worker_error and not noop)
        logger.info(f"[datacenter] sync {mode} 结束: done={done}/{total} {counts} "
                    f"duration_ms={duration_ms} cancelled={cancelled} "
                    f"error={worker_error} noop={noop}")
        _sync.log("WARNING" if (cancelled or worker_error or noop) else "INFO",
                  f"同步结束（{mode}）：{counts}，进度={done}/{total}")
        # data_jobs 只在任务真实结束后按实际结果落一条记录（真实 status/耗时，
        # 供 /overview 健康灯、task-stats、AI 日报消费；同日同模式 upsert 覆盖）
        if cancelled:
            _record_sync_job(mode, "FAILED", duration_ms,
                             "用户主动停止（非异常，可断点续传）")
            if task_id:
                update_task(task_id, "cancelled", progress={"done": done, "total": total},
                            error="用户主动停止")
        elif worker_error:
            _record_sync_job(mode, "FAILED", duration_ms, str(worker_error)[:500])
            if task_id:
                update_task(task_id, "failed", progress={"done": done, "total": total},
                            error=str(worker_error))
        elif noop:
            _record_sync_job(
                mode, "FAILED", duration_ms,
                f"NOOP: 未处理任何标的（done=0/total={total}，{counts}）")
            if task_id:
                update_task(task_id, "failed", progress={"done": done, "total": total},
                            error=f"无实际工作量（NOOP：done=0/total={total}）")
        else:
            _record_sync_job(mode, "SUCCESS", duration_ms, None)
            if task_id:
                update_task(task_id, "succeeded", progress={"done": done, "total": total},
                            result={"mode": mode, "done": done, "total": total,
                                    "rows": rows_written})
        # P2-15：同步结束通知（worker 线程 → SSE 推送到前端顶栏铃铛）
        try:
            from ..core.events import publish_threadsafe
            if cancelled:
                publish_threadsafe("sync", f"数据同步已停止（{mode}）：{done}/{total}，"
                                           f"可断点续传（{counts}）")
            elif worker_error:
                publish_threadsafe("sync", f"数据同步异常终止（{mode}）：{done}/{total}，"
                                           f"可断点续传（{counts}）")
            elif noop:
                publish_threadsafe("sync", f"数据同步空跑（{mode}）：未处理任何标的 "
                                           f"0/{total}（{counts}）")
            else:
                publish_threadsafe("sync", f"数据同步完成（{mode}）：{done}/{total} 只标的"
                                           f"（{counts}）")
        except Exception:  # noqa: BLE001 通知失败无副作用
            pass
        with _sync.lock:
            if _sync.task_id == task_id:
                _sync.task_id = None


# ---------------------------------------------------------------------------
# autoSync 后端调度（替代前端 setInterval）：lifespan 启动后台 asyncio 任务
# ---------------------------------------------------------------------------
_AUTO_SYNC_DEFAULT_TIME = "15:45"


def _auto_sync_path() -> Path:
    """kv 文件路径：生产 = <项目>/data/.auto_sync.json（与历史位置一致）。

    从 DATA_ROOT.parent 派生而非 project_root：测试环境 conftest 只重定向
    DATA_ROOT/SQLITE_URL 等到临时目录，从 DATA_ROOT 派生可保证测试读写
    落在临时目录，不污染真实 kv（调度器在 TestClient lifespan 中也会启动）。
    """
    return get_settings().DATA_ROOT.parent / ".auto_sync.json"


def _load_auto_sync() -> dict:
    p = _auto_sync_path()
    if not p.exists():
        return {"enabled": True, "time": _AUTO_SYNC_DEFAULT_TIME}
    try:
        import json
        return json.loads(p.read_text(encoding="utf-8"))
    except Exception:
        return {"enabled": True, "time": _AUTO_SYNC_DEFAULT_TIME}


def _save_auto_sync(enabled: bool, time_str: str) -> None:
    """合并写入：保留 last_run_date 等其他 kv 字段，不整文件覆盖。"""
    import json
    cfg = _load_auto_sync()
    cfg["enabled"] = bool(enabled)
    cfg["time"] = time_str
    p = _auto_sync_path()
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps(cfg, ensure_ascii=False), encoding="utf-8")


def _auto_sync_today_done() -> bool:
    """今日是否已自动触发过（读 kv 配置文件的 last_run_date 字段）。

    注意：防重标记只存 kv（data/.auto_sync.json），不写 data_jobs ——
    data_jobs 是任务结果表，只允许真实任务完成后按实际状态写入，
    预写 SUCCESS 占位会污染 /overview 健康灯、AI 日报与 task-stats。
    """
    try:
        last = _load_auto_sync().get("last_run_date")
        return last == date.today().isoformat()
    except Exception:
        return False


def _record_auto_sync() -> None:
    """记录今日已自动触发（更新 kv 的 last_run_date，不写任何任务表）。"""
    try:
        cfg = _load_auto_sync()
        cfg["last_run_date"] = date.today().isoformat()
        import json
        p = _auto_sync_path()
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(json.dumps(cfg, ensure_ascii=False), encoding="utf-8")
    except Exception as e:  # noqa: BLE001
        logger.warning(f"[datacenter] record auto_sync fail: {e!r}")


def _record_sync_job(mode: str, status: str, duration_ms: int,
                     error_message: str | None = None) -> None:
    """同步任务真实终态落库（data_jobs 唯一合法写入点）。

    job_type = sync_<mode>；与既有唯一约束 uq_datajob_type_date 兼容，
    同日同模式重复执行时 upsert 覆盖为最新一次结果。
    status 仅允许 SUCCESS/FAILED（FAILED 含用户主动停止，error_message 如实标注）。
    """
    try:
        import sqlite3
        s = get_settings()
        conn = sqlite3.connect(s.SQLITE_PATH)
        try:
            # updated_at 必须在首次插入时显式写入 localtime：该列默认值是
            # CURRENT_TIMESTAMP（**UTC**），而 created_at 显式写 localtime ⇒ 若不补，
            # 首次插入两列会差 8 小时（本机 GMT+8），令 "最近更新" 显示倒流。
            conn.execute(
                "INSERT INTO data_jobs (job_type, trade_date, status, "
                "duration_ms, finished_at, error_message, created_at, updated_at) "
                "VALUES (?, date('now','localtime'), ?, ?, "
                "datetime('now','localtime'), ?, datetime('now','localtime'), "
                "datetime('now','localtime')) "
                "ON CONFLICT(job_type, trade_date) DO UPDATE SET "
                "status=excluded.status, duration_ms=excluded.duration_ms, "
                "finished_at=excluded.finished_at, "
                "error_message=excluded.error_message, "
                "updated_at=datetime('now','localtime')",
                (f"sync_{mode}", status, int(duration_ms), error_message))
            conn.commit()
        finally:
            conn.close()
    except Exception as e:  # noqa: BLE001
        logger.warning(f"[datacenter] record sync job fail: {e!r}")


async def auto_sync_scheduler() -> None:
    """后台调度循环：每 60s 检查时间，到点触发增量同步（替代前端 setInterval）。

    在 main.py lifespan 中以 asyncio.create_task 启动；shutdown 时取消。
    """
    while True:
        try:
            cfg = _load_auto_sync()
            if cfg.get("enabled"):
                hhmm = datetime.now().strftime("%H:%M")
                if hhmm >= cfg["time"] and not _auto_sync_today_done():
                    if not _sync.running:
                        logger.info(f"[datacenter] auto sync triggered at {hhmm}")
                        _record_auto_sync()
                        # 复用同步路径（在线程池内调用，避免阻塞循环）
                        await asyncio.to_thread(
                            lambda: _start_sync_bg("incremental", resume=False))
        except Exception as e:  # noqa: BLE001 调度循环绝不因单次异常退出
            logger.warning(f"[datacenter] auto_sync_scheduler error: {e!r}")
        except BaseException as exc:  # noqa: BLE001 panic 等非 Exception 兜底
            # [AQP panic 收口 D] 同 evening_routine：后台协程不经 ASGI ⇒
            # B 段 PanicGuard 覆盖不到；panic 漏接会让 autoSync 永久停摆（静默）。
            if is_fatal_base_exception(exc):
                raise
            log_contained("auto_sync", exc)
        await asyncio.sleep(60)


def _start_sync_bg(mode: str, resume: bool) -> None:
    """后台线程启动同步（供 auto_sync_scheduler 在 to_thread 内调用）。"""
    with _sync.lock:
        if _sync.running:
            return
        _sync.running = True
        # autoSync is intentionally not represented by an API task. Clear a
        # previous API task id so a later automatic run cannot finish it.
        _sync.task_id = None
        _sync.mode = mode
        _sync.error = None
        _sync.done = 0
        _sync.rows_written = 0
        _sync.started_at = time.monotonic()
        _sync.logs = []
        _sync.completed.clear()
        _sync.cancel_event.clear()
    symbols = read_all_symbols("daily_bar")
    with _sync.lock:
        _sync.total = len(symbols)
    t = threading.Thread(target=_sync_worker, args=(mode, symbols, resume),
                         name=f"aqp-sync-{mode}", daemon=True)
    t.start()
