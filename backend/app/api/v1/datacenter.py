"""数据中心 API（AQP）：运维监控 + 一键数据同步。

设计原则：
1. **只读统计全部来自真实落库数据**——parquet 分区元数据（行数/日期区间/磁盘占用）
   、SQLite data_jobs / data_update_log、backend/logs/app.log，绝不伪造数字；
   数据缺失时返回空集合，由前端降级展示。
2. **统计结果带 TTL 进程级缓存**——parquet 元数据扫描（数百个小文件）约 1~2s，
   缓存 120s 足够运维看板刷新频率，同步任务完成后主动失效。
3. **同步任务是进程内后台线程**——单实例部署下足够；进度/日志放在模块级状态里，
   通过 /sync/status 轮询（与参考图"实时进度条 + 终端日志"一致）。

端点：
    GET  /overview       全局数据状态（存储总量/覆盖标的/最近更新/AKShare 健康）
    GET  /datasets       本地资产清单（逐数据集：起止时间/行数/复权状态）
    GET  /quality        数据质量与缺漏（按交易日历扫描 daily_bar 缺失交易日）
    GET  /logs           近期同步日志（app.log 尾部解析）
    GET  /api-stats      API 请求统计（按日调用频次与平均响应延迟）
    POST /sync           触发后台同步任务（incremental/repair/rebuild）
    GET  /sync/status    同步任务实时进度 + 运行日志
"""
from __future__ import annotations

import asyncio
import hashlib
import json
import os
import re
import sqlite3
import threading
import time
from datetime import date, datetime
from pathlib import Path
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:  # 仅为类型检查提供 polars 名称（运行时按需在函数内导入）
    import polars as pl

from fastapi import APIRouter, Depends, Query
from loguru import logger
from pydantic import BaseModel, Field

from ...cache.keys import k_datacenter_overview
from ...cache.swr import cached_or_build
from ...core.auth import require_role
from ...core.config import get_settings
from ...core.errors import (APIResponse, AQPException, ERR_DATA_EMPTY,
                            ERR_PIPELINE_BUSY,
                            ERR_PARAMS, ERR_TRAIN, fail, ok)
from ...data.calendar_store import get_calendar
from ...data.parquet_store import read_all_symbols
# Task 16（A-P1-7）：同步状态机/autoSync 调度/统计缓存下沉 services；
# 此处仅重导出，路由与既有消费方（tests 的 dc._sync 等）路径保持不变。
from ...services.stats_cache import _cached, invalidate_stats_cache  # noqa: F401
from ...services.sync_service import (  # noqa: F401
    _AUTO_SYNC_DEFAULT_TIME,
    _auto_sync_path,
    _auto_sync_today_done,
    _check_cancel,
    _load_auto_sync,
    _persist_sync_state,
    _record_auto_sync,
    _record_sync_job,
    _refresh_trade_calendar,
    _run_incremental,
    _run_rebuild,
    _run_repair,
    _save_auto_sync,
    _start_sync_bg,
    _sync,
    _symbol_last_date,
    _sync_worker,
    auto_sync_scheduler,
    restore_sync_state,
)
from ...services.task_store import claim_task, create_task, get_task, update_task

router = APIRouter()

# ---------------------------------------------------------------------------
# 进程级缓存（TTL）——实现已下沉 app/services/stats_cache.py，此处重导出
# ---------------------------------------------------------------------------

DATASET_META: dict[str, dict[str, str]] = {
    # dataset key -> 前端展示名（顺序即展示顺序）
    "daily_bar": {"label": "A股日线", "table": "daily_bar"},
    "daily_bar_qfq": {"label": "A股日线(前复权)", "table": "daily_bar_qfq"},
    "daily_bar_hfq": {"label": "A股日线(后复权)", "table": "daily_bar_hfq"},
    "features": {"label": "因子特征", "table": "features"},
    "predictions": {"label": "模型预测", "table": "predictions"},
    "screener": {"label": "选股快照", "table": "screener"},
    "universe_daily": {"label": "股票池", "table": "universe_daily"},
    "announcements": {"label": "公告摘要", "table": "announcements"},
}


def _sqlite_ro() -> sqlite3.Connection:
    """只读打开 SQLite（统计聚合用；与 calendar_store 同款只读 URI 方式）。"""
    s = get_settings()
    db_path = s.SQLITE_PATH
    if not db_path.exists():
        raise FileNotFoundError(f"SQLite 不存在: {db_path}")
    return sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)


# ---------------------------------------------------------------------------
# parquet 元数据扫描
# ---------------------------------------------------------------------------
def _scan_dataset(root: Path) -> dict[str, Any] | None:
    """扫描单个数据集：行数 / 符号数 / 日期区间 / 磁盘占用（只读元数据 + date 列）。"""
    if not root.exists():
        return None
    try:
        import polars as pl

        rows = 0
        bytes_ = 0
        symbols: list[str] = []
        dmin: date | None = None
        dmax: date | None = None
        for sym_dir in root.iterdir():
            if sym_dir.is_dir() and sym_dir.name.startswith("symbol="):
                symbols.append(sym_dir.name.split("=", 1)[1])
        # symbol 分区数据集：逐文件读 date 列拿区间；非分区结构走通用扫描
        if symbols:
            for sym in symbols:
                for f in (root / f"symbol={sym}").glob("*.parquet"):
                    bytes_ += f.stat().st_size
                    df = pl.read_parquet(f, columns=["date"])
                    rows += df.height
                    if df.height:
                        col = df["date"]
                        lo, hi = _as_date(col.min()), _as_date(col.max())
                        dmin = lo if dmin is None or lo < dmin else dmin
                        dmax = hi if dmax is None or hi > dmax else dmax
        else:
            files = sorted(root.rglob("*.parquet"))
            if not files:
                return None
            for f in files:
                bytes_ += f.stat().st_size
                import polars as pl

                df_any: Any = (pl.read_parquet(f, columns=["date"])
                               if _has_date(f) else None)
                rows += _parquet_rows(f)
                if df_any is not None and df_any.height:
                    col = df_any["date"]
                    lo, hi = _as_date(col.min()), _as_date(col.max())
                    dmin = lo if dmin is None or lo < dmin else dmin
                    dmax = hi if dmax is None or hi > dmax else dmax
        if rows == 0:
            return None
        return {
            "symbols": len(symbols),
            "rows": rows,
            "bytes": bytes_,
            "start": dmin.isoformat() if dmin else None,
            "end": dmax.isoformat() if dmax else None,
        }
    except Exception as e:  # 单个数据集损坏不拖垮整个清单
        logger.warning(f"[datacenter] scan {root.name} failed: {e!r}")
        return None


def _has_date(f: Path) -> bool:
    try:
        import polars as pl

        return "date" in pl.read_parquet_schema(f)
    except Exception:
        return False


def _parquet_rows(f: Path) -> int:
    try:
        import pyarrow.parquet as pq

        return pq.ParquetFile(f).metadata.num_rows
    except Exception:
        return 0


def _as_date(value: Any) -> date:
    """把 parquet date 列的标量统一收敛为 ``date``。

    ``col.min()`` 的静态类型是 Any（polars 未带类型信息），直接参与比较会让
    mypy 无法校验比较语义，也容易掩盖 datetime/date 混用问题。

    Args:
        value: date / datetime / ISO 字符串等 parquet 标量。

    Returns:
        对应的 ``date``；无法解析时抛 ValueError（调用方按数据异常处理）。
    """
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    return date.fromisoformat(str(value)[:10])


def _scan_all_datasets() -> dict[str, dict[str, Any]]:
    s = get_settings()
    out: dict[str, dict[str, Any]] = {}
    for key in DATASET_META:
        meta = _scan_dataset(s.DATA_ROOT / key)
        if meta:
            out[key] = meta
    return out


def _data_cache_key(name: str) -> str:
    """Keep process caches isolated when tests or workers switch DATA_ROOT."""
    return f"{name}:{get_settings().DATA_ROOT.resolve()}"


def _manifest_dataset_summary(root: Path) -> dict[str, dict[str, Any]]:
    """从写路径维护的 manifest 聚合行数/标的数/日期范围，避免读取 Parquet。"""
    path = root / ".manifest.json"
    if not path.exists():
        return {}
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError, TypeError) as exc:
        logger.warning(f"[datacenter] manifest unavailable: {exc!r}")
        return {}
    out: dict[str, dict[str, Any]] = {}
    for dataset in DATASET_META:
        entries = raw.get(dataset)
        if not isinstance(entries, dict) or not entries:
            continue
        rows = 0
        starts: list[str] = []
        ends: list[str] = []
        for meta in entries.values():
            if not isinstance(meta, dict):
                continue
            rows += int(meta.get("rows") or 0)
            if meta.get("first"):
                starts.append(str(meta["first"])[:10])
            if meta.get("last"):
                ends.append(str(meta["last"])[:10])
        if rows:
            out[dataset] = {
                "symbols": sum(
                    1 for meta in entries.values()
                    if isinstance(meta, dict) and int(meta.get("rows") or 0) > 0),
                "rows": rows,
                "bytes": None,
                "start": min(starts) if starts else None,
                "end": max(ends) if ends else None,
            }
    return out


def _storage_stats(root: Path) -> tuple[int, float]:
    """单次 scandir 遍历同时聚合字节和最新 mtime，避免 overview 三次扫树。"""
    cache_key = _data_cache_key(f"storage_stats:{root.resolve()}")

    def _scan() -> tuple[int, float]:
        total = 0
        latest = 0.0
        stack = [root]
        while stack:
            current = stack.pop()
            try:
                with os.scandir(current) as entries:
                    for entry in entries:
                        try:
                            if entry.is_dir(follow_symlinks=False):
                                stack.append(Path(entry.path))
                            elif entry.is_file(follow_symlinks=False):
                                stat = entry.stat(follow_symlinks=False)
                                total += stat.st_size
                                if entry.name.endswith(".parquet"):
                                    latest = max(latest, stat.st_mtime)
                        except OSError:
                            continue
            except OSError:
                continue
        return total, latest

    return _cached(cache_key, 120, _scan)


def _overview_cache_identity() -> tuple[str, str]:
    """生成目录隔离键与数据修订号，不读 Parquet 内容。"""
    settings = get_settings()
    root_key = hashlib.sha256(
        str(settings.DATA_ROOT.resolve()).encode("utf-8")).hexdigest()[:12]
    revisions: list[str] = []
    for path in (settings.DATA_ROOT / ".manifest.json", settings.SQLITE_PATH):
        try:
            revisions.append(str(path.stat().st_mtime_ns))
        except OSError:
            revisions.append("0")
    revision = hashlib.sha256(":".join(revisions).encode("ascii")).hexdigest()[:12]
    return root_key, revision


# 注：旧的 refresh=1 后台刷新状态机（_refresh_overview/_OVERVIEW_REFRESHING/
# _OVERVIEW_REFRESH_LOCK/_OVERVIEW_LAST）已被 cached_or_build 取代，属死代码，本轮删除。


# ---------------------------------------------------------------------------
# GET /overview
# ---------------------------------------------------------------------------
@router.get("/overview")
async def overview(
    refresh: int = Query(0, ge=0, le=1,
                         description="1=失效统计缓存强制重扫（重扫结果回写进程级缓存）"),
    _user: dict = Depends(require_role("viewer")),
) -> APIResponse[dict]:
    """全局数据状态看板：存储总量 / 覆盖标的 / 最近更新 / AKShare 健康。

    统计扫描（数百个 parquet 文件）带 120s 进程级缓存；refresh=1 时先
    失效缓存再重扫（响应标 from_cache="refreshed"）。
    """

    def _calc() -> dict:
        s = get_settings()
        # overview 只需要聚合口径；直接使用写路径维护的 manifest，禁止逐文件读取 date 列。
        datasets = _cached(
            _data_cache_key("overview_manifest"), 120,
            lambda: _manifest_dataset_summary(s.DATA_ROOT))
        storage_bytes, latest_parquet_mtime = _storage_stats(s.DATA_ROOT)
        # 覆盖标的：instrument 表全量（含未落库），另报已落库 symbol 数
        covered_total: int | None = 0
        try:
            with _sqlite_ro() as conn:
                covered_total = conn.execute(
                    "SELECT COUNT(*) FROM instrument").fetchone()[0]
        except Exception:
            # instrument 表读取失败时如实返回 None（前端显示不可用），
            # 不用 daily_bar 落库数冒充"收录目标"（语义不同，会误导 ⓘ 文案）
            covered_total = None

        # 最近更新：data_jobs 最新完成时间，兜底 parquet 最新 mtime
        last_sync = None
        last_sync_source = "parquet"
        try:
            with _sqlite_ro() as conn:
                row = conn.execute(
                    "SELECT MAX(COALESCE(finished_at, started_at, created_at)) "
                    "FROM data_jobs").fetchone()
                if row and row[0]:
                    last_sync = str(row[0])
                    last_sync_source = "pipeline"
        except Exception:
            pass
        if last_sync is None and latest_parquet_mtime:
            last_sync = datetime.fromtimestamp(latest_parquet_mtime).strftime(
                "%Y-%m-%d %H:%M:%S")

        # AKShare 健康：最近流水线任务状态 + 最后一次数据落库距今
        health, health_msg = "yellow", "暂无同步记录，请执行一次数据同步"
        try:
            with _sqlite_ro() as conn:
                row = conn.execute(
                    "SELECT status, error_message FROM data_jobs "
                    "ORDER BY COALESCE(finished_at, started_at) DESC LIMIT 1").fetchone()
            if row:
                if row[0] == "SUCCESS":
                    health, health_msg = "green", "AKShare 连接正常，最近同步成功"
                elif row[0] == "RUNNING":
                    health, health_msg = "green", "同步任务执行中"
                else:
                    health, health_msg = "yellow", f"最近同步异常：{str(row[1])[:60]}"
        except Exception:
            pass
        if datasets.get("daily_bar", {}).get("end"):
            end = date.fromisoformat(datasets["daily_bar"]["end"])
            stale_days = (date.today() - end).days
            if stale_days > 5 and health == "green":
                health, health_msg = "yellow", f"行情已 {stale_days} 天未更新，建议执行增量同步"

        # 磁盘占用仪表：DATA_ROOT 所在盘使用率
        usage = _disk_usage_percent(s.DATA_ROOT)
        return {
            "storage_bytes": storage_bytes,
            "storage_gb": round(storage_bytes / 1024**3, 1),
            "covered_total": covered_total,
            "covered_with_data": datasets.get("daily_bar", {}).get("symbols", 0),
            "dataset_count": len(datasets),
            "last_sync": last_sync,
            "last_sync_source": last_sync_source,
            "akshare_health": health,
            "akshare_message": health_msg,
            "disk_usage_percent": usage,
            "daily_bar_range": [
                datasets.get("daily_bar", {}).get("start"),
                datasets.get("daily_bar", {}).get("end"),
            ],
        }

    if refresh:
        invalidate_stats_cache()
    root_key, revision = _overview_cache_identity()
    cache_key = k_datacenter_overview(date.today().isoformat(), root_key, revision)

    async def _build() -> dict[str, Any]:
        return await _run_sync_ctx(_calc)

    result = await cached_or_build(
        cache_key,
        _build,
        ttl=120,
        stale_window=600,
        refresh=refresh,
        rebuild_lock_ttl=15,
    )
    return ok(result)


def _disk_usage_percent(root: Path) -> float | None:
    """root 所在磁盘已用百分比（Windows/Linux 通用）。

    获取失败返回 None（前端显示不可用），绝不静默返回 0.0 冒充真实值。
    """
    import os
    try:
        if hasattr(os, "statvfs"):  # POSIX
            st = os.statvfs(root)
            total, free = st.f_blocks * st.f_frsize, st.f_bavail * st.f_frsize
        else:  # Windows
            import ctypes

            free_bytes = ctypes.c_ulonglong(0)
            total_bytes = ctypes.c_ulonglong(0)
            ctypes.windll.kernel32.GetDiskFreeSpaceExW(
                str(root), None, ctypes.byref(total_bytes), ctypes.byref(free_bytes))
            total, free = total_bytes.value, free_bytes.value
        return round((1 - free / total) * 100, 1) if total else None
    except Exception:
        return None


async def _run_sync_ctx(fn):
    """扫描是纯 CPU/IO，丢线程池避免阻塞事件循环。"""
    import asyncio

    return await asyncio.to_thread(fn)


# ---------------------------------------------------------------------------
# GET /datasets
# ---------------------------------------------------------------------------
@router.get("/datasets")
async def datasets(    _user: dict = Depends(require_role("viewer")),
) -> APIResponse[dict]:
    """本地资产清单：逐数据集的行数 / 日期区间 / 磁盘占用 / 复权状态。"""
    items = await _run_sync_ctx(lambda: _cached(_data_cache_key("datasets"), 120, _scan_all_datasets))
    out = []
    for key, meta in DATASET_META.items():
        st = items.get(key)
        if not st:
            continue
        # 复权状态：raw 日线依赖 qfq/hfq 派生集是否齐备
        if key == "daily_bar":
            adjust = "前复权✓" if "daily_bar_qfq" in items and "daily_bar_hfq" in items \
                else "缺复权数据"
        elif key in ("daily_bar_qfq", "daily_bar_hfq"):
            adjust = "不适用"
        elif key in ("features", "predictions", "screener"):
            adjust = "不适用"
        else:
            adjust = "不适用"
        out.append({
            "dataset": key,
            "label": meta["label"],
            "table": meta["table"],
            "source": "AKShare",
            "start": st["start"],
            "end": st["end"],
            "rows": st["rows"],
            "symbols": st["symbols"],
            "bytes": st["bytes"],
            "adjust_status": adjust,
        })
    return ok({"items": out})


# ---------------------------------------------------------------------------
# GET /quality
# ---------------------------------------------------------------------------
@router.get("/quality")
async def quality(
    limit: int = 50,
    _user: dict = Depends(require_role("viewer")),
) -> APIResponse[dict]:
    """数据质量：按交易日历扫描 daily_bar 每只标的的缺失交易日（停牌除外无法区分）。"""
    res = await _run_sync_ctx(lambda: _cached(_data_cache_key("quality"), 300, _quality_calc))
    data = dict(res)
    data["items"] = data["items"][:limit]
    return ok(data)


# ---------------------------------------------------------------------------
# GET /logs
# ---------------------------------------------------------------------------
_LOG_LINE = re.compile(
    r"^(\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2}(?:\.\d+)?)\s*\|\s*(\w+)\s*\|\s*(.+)$")


def _parse_log_line(line: str) -> dict | None:
    """解析一条 loguru 文本日志：ts / level / message。

    文件格式为 ``ts | LEVEL | name:func:line - message``（部分 sink 为 ``| message``）。
    """
    m = _LOG_LINE.match(line.strip())
    if not m:
        return None
    rest = m.group(3)
    if " - " in rest:
        msg = rest.split(" - ", 1)[1]
    elif " | " in rest:
        msg = rest.split(" | ", 1)[1]
    else:
        msg = rest
    return {
        "ts": m.group(1).split(".")[0].replace("-", "/"),
        "level": m.group(2),
        "message": msg[:200],
    }


@router.get("/logs")
async def logs(limit: int = 60,
               _user: dict = Depends(require_role("researcher"))) -> APIResponse[dict]:
    """近期运行日志：backend/logs/app.log 尾部解析（INFO 及以上全部纳入）。"""

    def _calc() -> list[dict]:
        path = get_settings().LOG_DIR / "app.log"
        if not path.exists():
            return []
        with path.open("r", encoding="utf-8", errors="replace") as f:
            f.seek(0, 2)
            size = f.tell()
            f.seek(max(0, size - 400_000))
            raw = f.read().splitlines()
        out: list[dict] = []
        prev: str | None = None
        for line in raw[1:]:  # 第一行可能是截断的半行
            parsed = _parse_log_line(line)
            if parsed is None or parsed["level"] not in ("INFO", "WARNING", "ERROR"):
                continue
            # 抑制熔断/降级这类每分钟重复的告警刷屏（保留第一条）
            if parsed["message"] == prev:
                continue
            prev = parsed["message"]
            out.append(parsed)
        return out[-limit:]

    return ok({"items": await _run_sync_ctx(_calc)})


# ---------------------------------------------------------------------------
# GET /task-stats（P2-11：原 /api-stats 命名失实 —— 数据源是流水线任务表
# data_update_log / data_jobs，并非 API 请求埋点，故按实际语义改名）
# ---------------------------------------------------------------------------
@router.get("/task-stats")
async def task_stats(    _user: dict = Depends(require_role("viewer")),
) -> APIResponse[dict]:
    """数据任务统计：data_update_log / data_jobs 按日聚合任务次数与平均耗时。"""

    def _calc() -> list[dict]:
        try:
            with _sqlite_ro() as conn:
                # SUM+COUNT 而非 AVG：两表按日合并时才能算出真实加权平均，
                # 取两表 AVG 的 max 会系统性失真（非该日实际平均耗时）
                rows = conn.execute(
                    "SELECT date(created_at), COUNT(*), SUM(duration_ms) "
                    "FROM data_update_log GROUP BY 1 ORDER BY 1 DESC LIMIT 14"
                ).fetchall()
                jobs = conn.execute(
                    "SELECT date(created_at), COUNT(*), SUM(duration_ms) "
                    "FROM data_jobs WHERE duration_ms > 0 "
                    "GROUP BY 1 ORDER BY 1 DESC LIMIT 14").fetchall()
        except Exception:
            return []
        merged: dict[str, dict[str, float]] = {}
        for d, n, total_ms in rows + jobs:
            if not d:
                continue
            e = merged.setdefault(d, {"calls": 0, "total_ms": 0.0})
            e["calls"] += n
            e["total_ms"] += total_ms or 0
        return [{"date": d, "calls": int(v["calls"]),
                 "avg_latency_ms": round(v["total_ms"] / v["calls"]) if v["calls"] else 0}
                for d, v in sorted(merged.items())][-14:]

    return ok({"points": await _run_sync_ctx(_calc)})


class SyncRequest(BaseModel):
    mode: str = "incremental"  # incremental / repair / rebuild
    """指定标的列表（可选）。repair 模式缺省自动取缺漏标的清单。"""
    symbols: list[str] | None = None
    # 断点续传：True 时跳过 _sync.completed 中上次已完成的 symbol（仅同进程内有效）
    resume: bool = False


class AutoSyncRequest(BaseModel):
    """autoSync 配置变更请求（body JSON）。"""
    enabled: bool
    time: str = "15:45"  # HH:MM 24h；与 _AUTO_SYNC_DEFAULT_TIME 保持一致


@router.post("/sync")
async def trigger_sync(
    req: SyncRequest,
    _user: dict = Depends(require_role("researcher")),
) -> APIResponse[dict]:
    """触发后台同步任务（同一时刻仅允许一个）。"""
    if req.mode not in ("incremental", "repair", "rebuild"):
        return fail(ERR_PARAMS, f"未知同步模式: {req.mode}")
    with _sync.lock:
        if _sync.running:
            return fail(4002, "已有同步任务在执行中，请等待完成或先停止")
        _sync.running = True
        task_id = create_task(f"datacenter.sync.{req.mode}")
        if not claim_task(task_id, "api-sync", lease_seconds=300):
            _sync.running = False
            return fail(5000, "无法领取同步任务")
        _sync.task_id = task_id
        _sync.mode = req.mode
        _sync.error = None
        _sync.done = 0
        _sync.rows_written = 0
        _sync.started_at = time.monotonic()
        _sync.logs = []
        _sync.cancel_event.clear()
        # resume=False 时清空历史续传记录；resume=True 时保留 _sync.completed
        if not req.resume:
            _sync.completed.clear()
    symbols = req.symbols or read_all_symbols("daily_bar")
    if req.mode == "repair" and not req.symbols:
        # 只修复有缺漏的标的（quality 缓存 5 分钟内有效）
        q = _cached(_data_cache_key("quality"), 300, _quality_calc)
        symbols = [it["symbol"] for it in q["items"]] or symbols
    with _sync.lock:
        _sync.total = len(symbols)
    t = threading.Thread(target=_sync_worker, args=(req.mode, symbols, req.resume),
                         name=f"aqp-sync-{req.mode}", daemon=True)
    # Persist the running state before starting the thread, otherwise a very
    # short sync can finish and then be overwritten back to running.
    update_task(task_id, "running", progress={"done": 0, "total": len(symbols)})
    t.start()
    logger.info(f"[datacenter] sync started: mode={req.mode} symbols={len(symbols)} resume={req.resume}")
    return ok({"started": True, "task_id": task_id, "mode": req.mode, "total": len(symbols),
               "resume": req.resume, "skipped": len(_sync.completed) if req.resume else 0})


@router.get("/sync/status")
async def sync_status(
    _user: dict = Depends(require_role("researcher")),
) -> APIResponse[dict]:
    return ok(_sync.snapshot())


@router.get("/sync/tasks/{task_id}")
async def persisted_sync_status(
    task_id: str,
    _user: dict = Depends(require_role("researcher")),
) -> APIResponse[dict]:
    """按 task_id 查询持久化任务，供页面刷新后恢复查看。"""
    task = await asyncio.to_thread(get_task, task_id)
    if task is None:
        return fail(ERR_DATA_EMPTY, "任务不存在", {"task_id": task_id})
    return ok(task)


def _auto_sync_payload() -> dict:
    """组装 autoSync 配置响应（GET/POST 共用，保证两处口径一致）。"""
    cfg = _load_auto_sync()
    return {
        "enabled": bool(cfg.get("enabled", True)),
        "time": str(cfg.get("time", _AUTO_SYNC_DEFAULT_TIME)),
        "today_done": _auto_sync_today_done(),
    }


@router.get("/sync/auto")
async def get_auto_sync(
    _user: dict = Depends(require_role("researcher")),
) -> APIResponse[dict]:
    """读取 autoSync 调度配置（enabled / time / today_done）。

    调度由后端 asyncio 任务常驻执行（services.auto_sync_scheduler），浏览器
    关闭后仍会触发；本端点只负责配置的持久化读写。
    """
    return ok(_auto_sync_payload())


@router.post("/sync/auto")
async def set_auto_sync(
    req: AutoSyncRequest,
    _user: dict = Depends(require_role("researcher")),
) -> APIResponse[dict]:
    """更新 autoSync 调度配置（写入 DATA_ROOT.parent/.auto_sync.json）。"""
    # 强校验时间格式：调度器按 "HH:MM" 字符串比较触发，格式错误会静默永不触发
    if not re.fullmatch(r"([01]\d|2[0-3]):[0-5]\d", req.time):
        return fail(ERR_PARAMS, f"时间格式应为 HH:MM（24 小时制），收到 {req.time!r}")
    _save_auto_sync(req.enabled, req.time)
    logger.info(f"[datacenter] autoSync 配置更新: enabled={req.enabled} time={req.time}")
    return ok(_auto_sync_payload())


@router.post("/sync/cancel")
async def cancel_sync(
    _user: dict = Depends(require_role("researcher")),
) -> APIResponse[dict]:
    """请求停止正在运行的同步任务（优雅停止：worker 在下一个 symbol 边界退出）。

    已成功抓取的 symbol 会记录在 _sync.completed，下次 resume=True 时跳过。
    若任务未运行则无操作。
    """
    with _sync.lock:
        if not _sync.running:
            return ok({"cancelled": False, "reason": "无运行中的任务"})
        _sync.cancel_event.set()
    _sync.log("INFO", "收到停止请求，将在当前 symbol 完成后退出...")
    return ok({"cancelled": True, "reason": "已请求停止，worker 将在下一个边界退出"})


def _quality_calc() -> dict:
    """与 /quality 相同的计算体（供 repair 模式取缺漏标的复用）。"""
    import polars as pl

    s = get_settings()
    root = s.DATA_ROOT / "daily_bar"
    symbols = read_all_symbols("daily_bar")
    if not symbols:
        return {"checked": 0, "total_missing_days": 0, "items": []}
    cal = get_calendar()
    end_iso = _cached(_data_cache_key("datasets"), 120, _scan_all_datasets)\
        .get("daily_bar", {}).get("end")
    today = min(date.today(), date.fromisoformat(end_iso)) if end_iso else date.today()
    items: list[dict] = []
    total_missing = 0
    for sym in symbols:
        dates: list[date] = []
        for f in (root / f"symbol={sym}").glob("year=*.parquet"):
            col = pl.read_parquet(f, columns=["date"])["date"]
            dates.extend(d.date() if isinstance(d, datetime) else d for d in col.to_list())
        if not dates:
            continue
        lo, present = min(dates), set(dates)
        missing = sorted(d for d in cal.trade_days
                         if lo <= d <= today and d not in present)
        if missing:
            total_missing += len(missing)
            items.append({
                "dataset": "daily_bar", "symbol": sym,
                "missing_days": len(missing),
                "first_missing": missing[0].isoformat(),
                "last_missing": missing[-1].isoformat(),
                "sample": [d.isoformat() for d in missing[:5]],
            })
    items.sort(key=lambda x: -x["missing_days"])
    return {"checked": len(symbols), "total_missing_days": total_missing,
            "affected_symbols": len(items), "items": items}


# ---------------------------------------------------------------------------
# 自定义抓取（POST /sync/fetch）：按标的类型(股票/ETF)+起止日期抓取
# ---------------------------------------------------------------------------
def _fetch_etf_bar(code: str, start: str, end: str, adjust: str) -> "pl.DataFrame":
    """拉取单只 ETF 日线（东财 fund_etf_hist_em，列名与股票一致，复用 _standardize_daily）。

    与 ``akshare_adapter.fetch_daily_bar`` 签名对齐，可作为 fetcher 注入
    ``fetch_and_write_daily_bars``，使 ETF 与股票共用同一写入路径（daily_bar 分区）。
    """
    import polars as pl
    import akshare as ak
    from ...data.ingest.akshare_adapter import _standardize_daily  # 同包内复用列规范化

    try:
        df = ak.fund_etf_hist_em(symbol=code, period="daily",
                                  start_date=start.replace("-", ""),
                                  end_date=end.replace("-", ""),
                                  adjust=adjust)
    except Exception as e:  # noqa: BLE001
        logger.warning(f"[datacenter] etf fetch {code} adjust={adjust!r} fail: {e!r}")
        return pl.DataFrame()
    if df is None or df.empty:
        return pl.DataFrame()
    return _standardize_daily(df, code)


def _instrument_symbols_by_type(asset_type: str, limit: int | None = None) -> list[str]:
    """从 instrument 表按 instrument_type 取标的 symbol 列表。

    - stock: A 股（instrument_type='stock'）
    - etf: 场内 ETF（instrument_type='etf'）
    - all: 全部
    """
    try:
        with _sqlite_ro() as conn:
            if asset_type == "all":
                rows = conn.execute("SELECT symbol FROM instrument ORDER BY symbol").fetchall()
            else:
                rows = conn.execute(
                    "SELECT symbol FROM instrument WHERE instrument_type=? ORDER BY symbol",
                    (asset_type,)).fetchall()
        out = [r[0] for r in rows]
        return out[:limit] if limit and limit > 0 else out
    except Exception as e:  # noqa: BLE001
        logger.warning(f"[datacenter] load instruments type={asset_type} fail: {e!r}")
        return []


def _run_fetch(symbols: list[str], asset_type: str, start: str, end: str) -> None:
    """自定义抓取 worker：按 asset_type 选 fetcher，逐只抓取并写入 daily_bar 分区。

    复用 _sync 状态机（进度/日志/cancel/resume 与 sync 模式一致）。
    """
    from ...core.pipeline_lock import pipeline_slot
    from ...data.ingest.tasks import fetch_and_write_daily_bars

    # 与 sync/pipeline/mirror/training 使用同一把非阻塞锁，完整覆盖所有分区写入。
    with pipeline_slot("fetch"):
        fetcher = _fetch_etf_bar if asset_type == "etf" else None
        label = "ETF" if asset_type == "etf" else "股票"
        _sync.log("INFO", f"开始自定义抓取：{label} {len(symbols)} 只，区间 {start} ~ {end}")
        for i, sym in enumerate(symbols, 1):
            if _check_cancel():
                _sync.log("WARNING", "用户停止抓取，任务已中断（可断点续传）")
                return
            code = sym.split(".")[0]
            _sync.current = f"抓取 {sym} {label}日线 ({i}/{len(symbols)})"
            try:
                n, failed_adj = fetch_and_write_daily_bars(
                    code, start, end, adjusts=("", "hfq"), fetcher=fetcher)
                with _sync.lock:
                    if n > 0 and not failed_adj:
                        _sync.completed.add(sym)
                    elif failed_adj:
                        _sync.failed.add(sym)
                    else:
                        _sync.completed.add(sym)
                _sync.log("INFO", f"抓取 {sym} ... {n} 行成功" if n else f"{sym} 无数据")
            except Exception as e:
                _sync.log("WARNING", f"抓取 {sym} 失败: {e!r}"[:200])
            with _sync.lock:
                _sync.done = i
        invalidate_stats_cache()
        _sync.log("INFO", "自定义抓取完成")


class FetchRequest(BaseModel):
    asset_type: str = "stock"  # stock / etf / all
    start: str  # YYYY-MM-DD
    end: str    # YYYY-MM-DD
    symbols: list[str] | None = None  # 指定代码列表；缺省按 asset_type 取全量
    limit: int | None = None  # 数量上限（symbols 为空时取前 N 只）


@router.post("/sync/fetch")
async def trigger_fetch(
    req: FetchRequest,
    _user: dict = Depends(require_role("researcher")),
) -> APIResponse[dict]:
    """自定义抓取：按标的类型 + 起止日期抓取行情并落库 daily_bar。

    - symbols 非空：只抓指定代码（优先级最高，忽略 asset_type/limit）
    - symbols 为空：按 asset_type 从 instrument 表取全量，limit 截断前 N 只
    """
    if req.asset_type not in ("stock", "etf", "all"):
        return fail(ERR_PARAMS, f"未知标的类型: {req.asset_type}（支持 stock/etf/all）")
    # 在启动后台线程前预检同一互斥锁，繁忙时按管道约定即时返回而非伪造已启动。
    from ...core.pipeline_lock import PipelineBusy, pipeline_slot
    try:
        with pipeline_slot("fetch"):
            pass
    except PipelineBusy as exc:
        return fail(ERR_PIPELINE_BUSY, str(exc))
    try:
        date.fromisoformat(req.start)
        date.fromisoformat(req.end)
    except ValueError:
        return fail(ERR_PARAMS, "start/end 必须为 YYYY-MM-DD")
    if req.start > req.end:
        return fail(ERR_PARAMS, "start 不能晚于 end")
    with _sync.lock:
        if _sync.running:
            return fail(4002, "已有同步任务在执行中，请等待完成或先停止")
        _sync.running = True
        _sync.mode = "fetch"
        _sync.error = None
        _sync.done = 0
        _sync.started_at = time.monotonic()
        _sync.logs = []
        _sync.completed.clear()
        _sync.cancel_event.clear()

    if req.symbols:
        symbols = req.symbols
    else:
        symbols = _instrument_symbols_by_type(req.asset_type, req.limit)
    if not symbols:
        with _sync.lock:
            _sync.running = False
        return fail(4003, f"未找到 {req.asset_type} 类型的标的（instrument 表可能为空）")

    with _sync.lock:
        _sync.total = len(symbols)

    def _worker() -> None:
        try:
            _run_fetch(symbols, req.asset_type, req.start, req.end)
        except Exception as e:
            with _sync.lock:
                _sync.error = f"{type(e).__name__}: {e}"
            _sync.log("ERROR", f"抓取任务终止: {e!r}"[:200])
        finally:
            with _sync.lock:
                _sync.running = False
                _sync.finished_at = time.monotonic()
                if not _sync.cancel_event.is_set() and not _sync.error:
                    _sync.completed.clear()
                _sync.cancel_event.clear()

    t = threading.Thread(target=_worker, name=f"aqp-fetch-{req.asset_type}", daemon=True)
    t.start()
    logger.info(f"[datacenter] fetch started: type={req.asset_type} symbols={len(symbols)} "
                f"{req.start}~{req.end}")
    return ok({"started": True, "asset_type": req.asset_type,
               "total": len(symbols), "start": req.start, "end": req.end})


@router.get("/instruments")
async def list_instruments(
    asset_type: str = "stock", limit: int = 50,
    _user: dict = Depends(require_role("viewer")),
) -> APIResponse[dict]:
    """标的列表（供前端抓取面板预览：按类型取前 N 只的 symbol/name）。"""
    try:
        with _sqlite_ro() as conn:
            if asset_type == "all":
                rows = conn.execute(
                    "SELECT symbol, name, instrument_type FROM instrument "
                    "ORDER BY symbol LIMIT ?", (limit,)).fetchall()
            else:
                rows = conn.execute(
                    "SELECT symbol, name, instrument_type FROM instrument "
                    "WHERE instrument_type=? ORDER BY symbol LIMIT ?",
                    (asset_type, limit)).fetchall()
        return ok({"items": [{"symbol": r[0], "name": r[1], "type": r[2]} for r in rows],
                   "total": len(rows)})
    except Exception as e:  # noqa: BLE001
        return fail(5001, f"读取 instrument 表失败: {e!r}")


# ==================== 文本数据（1-2 FinLLM 数据前置） ====================

class TextImportRequest(BaseModel):
    """公告文档批量导入（JSON；无抓取源阶段的人工/第三方落地通道）。"""
    docs: list[dict] = Field(..., min_length=1, max_length=2000,
                             description="[{symbol, date, title, content}, ...]")
    source: str = Field("manual", max_length=32)


@router.get("/text/status")
async def text_data_status(    _user: dict = Depends(require_role("viewer")),
) -> APIResponse[dict]:
    """公告文档与情绪因子状态（docs 数/覆盖面/因子行数/LLM 是否启用）。"""
    from ...data.text_ingest import text_status

    return ok(await asyncio.to_thread(text_status))


@router.post("/text/import")
async def text_data_import(
    req: TextImportRequest,
    _user: dict = Depends(require_role("researcher")),
) -> APIResponse[dict]:
    """批量导入公告文档（幂等：hash(symbol|date|title) 去重）。"""
    from ...data.text_ingest import import_documents

    res = await asyncio.to_thread(import_documents, req.docs, req.source)
    logger.info(f"[datacenter] text import: {res}")
    return ok(res)


@router.post("/text/build-factor")
async def text_build_factor(
    _user: dict = Depends(require_role("researcher")),
) -> APIResponse[dict]:
    """对全部文档打分并重建日频情绪因子（LLM 启用时自动走 LLM，否则规则词库）。"""
    from ...data.text_ingest import score_and_build_factor

    res = await asyncio.to_thread(score_and_build_factor)
    if not res.get("ok"):
        raise AQPException(ERR_DATA_EMPTY, res.get("error") or "构建失败")
    return ok(res)


# ==================== 截面分区镜像（2-1 MED-003 数据前置） ====================

@router.get("/mirror/status")
async def cs_mirror_status(    _user: dict = Depends(require_role("viewer")),
) -> APIResponse[dict]:
    """截面镜像新鲜度：源 vs 镜像的日期覆盖与滞后。"""
    from ...data.cross_section import mirror_status

    # 性能（2026-09-11 补测发现）：mirror_status 要对 3 个镜像数据集各做一次
    # `_scan_source`，即逐文件读取 ~3.1 万个源 parquet 的 date 列，实测稳定
    # 21~23s（远超前端 15s 默认超时，导致 /data 页 TextDataPanel 必然超时）。
    # 源日期集合只在同步/重建时变化，按本模块既有约定加 120s 进程级缓存。
    return ok(await asyncio.to_thread(
        lambda: _cached(_data_cache_key("mirror_status"), 120, mirror_status)))


class MirrorRebuildRequest(BaseModel):
    dataset: str | None = Field(None, description="缺省重建全部 bar 数据集镜像")


@router.post("/mirror/rebuild")
async def cs_mirror_rebuild(
    req: MirrorRebuildRequest | None = None,
    _user: dict = Depends(require_role("researcher")),
) -> APIResponse[dict]:
    """增量重建截面镜像（幂等；晚间例行每日自动执行）。"""
    # ⚠️ PipelineBusy 必须在本作用域可见：此前只在嵌套函数 _rebuild 里 import 了
    # pipeline_slot，管道互斥触发时 `except PipelineBusy` 抛 NameError，
    # 把本该是 ERR_PIPELINE_BUSY 的互斥冲突错报成裸 50000（mypy name-defined 已抓到）。
    from ...core.pipeline_lock import PipelineBusy
    from ...data.cross_section import MIRROR_DATASETS, build_mirror

    targets = [req.dataset] if req and req.dataset else list(MIRROR_DATASETS)

    def _rebuild() -> list:
        # 管道互斥（C-02）：与 sync / pipeline / training 互斥，后写者覆盖先写者
        # 的静默数据污染在这里被挡下；晚间例行的镜像构建在 pipeline 锁内执行。
        from ...core.pipeline_lock import pipeline_slot

        with pipeline_slot("mirror"):
            return [build_mirror(ds) for ds in targets]

    try:
        out = await asyncio.to_thread(_rebuild)
    except PipelineBusy as e:
        return fail(ERR_PIPELINE_BUSY, str(e))
    except ValueError as e:
        raise AQPException(ERR_PARAMS, str(e)) from e
    return ok(out)


# ==================== 模型训练（3-1/3-2 前置的消费端：一键训练） ====================

class TrainStartRequest(BaseModel):
    """训练启动参数（服务层按模型白名单过滤，未知字段忽略）。"""
    model: str = Field(..., description="tft | gnn")
    params: dict = Field(default_factory=dict,
                         description="如 {lookback, horizon, epochs, batch}")


@router.get("/train/readiness")
async def train_readiness_status() -> APIResponse[dict]:
    """训练就绪度（torch/features/样本量/关系边四项门禁，按钮 enable 依据）。"""
    from ...ml.train_service import train_readiness

    return ok(await asyncio.to_thread(train_readiness))


@router.post("/train/start")
async def train_start(
    req: TrainStartRequest,
    _user: dict = Depends(require_role("researcher")),
) -> APIResponse[dict]:
    """启动一次训练（单任务串行；门禁不满足直接拒绝并给原因）。"""
    from ...ml.train_service import start_training

    try:
        res = await asyncio.to_thread(start_training, req.model, req.params)
    except AQPException:
        raise
    except Exception as e:  # noqa: BLE001
        raise AQPException(ERR_TRAIN, f"训练启动失败：{type(e).__name__}: {e}") from e
    logger.info(f"[datacenter] train start: {res}")
    return ok(res)


@router.get("/train/status")
async def train_status(
    _user: dict = Depends(require_role("researcher")),
) -> APIResponse[dict]:
    """当前/最近一次训练状态与日志（进程重启后仍可见上次结果）。"""
    from ...ml.train_service import training_status

    return ok(await asyncio.to_thread(training_status))


@router.post("/train/cancel")
async def train_cancel(
    _user: dict = Depends(require_role("researcher")),
) -> APIResponse[dict]:
    """请求优雅取消（逐 epoch 检查，当前 epoch 走完即停）。"""
    from ...ml.train_service import cancel_training

    return ok(await asyncio.to_thread(cancel_training))
