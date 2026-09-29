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
from typing import TYPE_CHECKING, Annotated, Any

if TYPE_CHECKING:  # 仅为类型检查提供 polars 名称（运行时按需在函数内导入）
    import polars as pl

from fastapi import APIRouter, Depends, Query
from loguru import logger
from pydantic import BaseModel, Field

from ...cache.keys import k_datacenter_overview
from ...cache.swr import cached_or_build
from ...core.auth import require_role
from ...core.compute_guard import compute_slot_ctx, compute_slot_optional
from ...core.config import get_settings
from ...core.errors import (APIResponse, AQPException, ERR_DATA_EMPTY,
                            ERR_NOT_FOUND, ERR_PIPELINE_BUSY,
                            ERR_PARAMS, ERR_SYSTEM, ERR_TRAIN, fail, ok)
# 审计 P1-31（2026-09-21）：管道锁改为"请求线程申请 → 跨线程交接"，
# 故需在**模块级**可见 acquire_pipeline_slot / PipelineSlotHandle / PipelineBusy
# 与 pipeline_slot（此前只在嵌套函数里 import，已发生过一次 NameError 事故）。
from ...core.pipeline_lock import (PipelineBusy, PipelineSlotHandle,
                                   acquire_pipeline_slot, pipeline_slot)
from ...data.calendar_store import get_calendar
from ...data.parquet_store import (DATASET_DATE_COLUMN, DEFAULT_DATE_COLUMN,
                                   MANIFEST_SCHEMA, MANIFEST_SCHEMA_KEY,
                                   manifest_scan_and_store, read_all_symbols)
# Task 16（A-P1-7）：同步状态机/autoSync 调度/统计缓存下沉 services；
# 此处仅重导出，路由与既有消费方（tests 的 dc._sync 等）路径保持不变。
from ...services.stats_cache import _cached, cache_peek, invalidate_stats_cache  # noqa: F401
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

# 数据集 -> 日期列名（默认 DEFAULT_DATE_COLUMN）。
# 背景（2026-09-19 真 bug）：announcements 的 parquet schema 是
# ['symbol','pub_date','title','type','sentiment','source','url'] —— **没有 date 列**。
# 而 _scan_dataset 的 symbol 分区分支此前**无条件**读 columns=["date"] ⇒
# ColumnNotFoundError 被数据集级 except 吞掉 ⇒ 返回 None ⇒ /datasets 里
# `if not st: continue` 把「公告摘要」整行删掉（用户报「部分数据面板加载失败」）。
# 显式登记各数据集的日期列，避免再靠猜。
#
# ⚠️ 2026-09-29：映射的**唯一定义处已下沉到 data.parquet_store**（manifest 的
# footer 扫描同样需要它，而 data 层不得反向依赖 api 层）；此处仅为重导出，
# 供既有调用方与测试（dc.DATASET_DATE_COLUMN / dc.DEFAULT_DATE_COLUMN）保持路径不变。

# 统计缓存 TTL（秒）——2026-09-26 上调，理由与安全性见下。
#
# 为什么必须拉长：`datasets` / `quality` 的冷算要遍历 3.3 万个 parquet，实测（单飞 +
# footer 统计优化之后、机器空载）`datasets` 23.8s、`quality` 31.6s。旧 TTL 分别是
# 120s / 300s ⇒ 用户每 2～5 分钟就会再撞上一次冷扫，而前端默认超时只有 15s —— 于是
# /data 页长期显示「部分数据面板加载失败：请求超时」。TTL 拉长后冷扫只在
# **后端启动后首次** 与 **同步写库后首次** 发生。
#
# 为什么拉长**在满足前提时**是安全的：`datasets` / `quality` 的缓存键只含 DATA_ROOT
# （见 `_data_cache_key`，不含 revision/mtime），刷新**完全依赖**写入路径调用
# `invalidate_stats_cache()`（见 `app/services/stats_cache.py`）。因此长 TTL 的安全性
# 是**有条件**的：**只有**会写数据、且**确实调用了 invalidate** 的路径，才能保证
# 用户不会看到陈旧统计。当前会调用的路径：
#   - `app/services/sync_service.py`：`_run_incremental` / `_run_repair` / `_run_rebuild`；
#   - `app/api/v1/datacenter.py`：`/sync`、`/sync/fetch`（自定义抓取）、`/settings`、
#     `/overview?refresh=1`。
# 此前**不会**调用（本次 2026-09-26 收口已补上）：
#   - `app/orchestrator.py` 的 `run_pipeline`（晚间例行 / ops 流水线）——它写
#     features / predictions / screener / universe_daily / cs 镜像，却从未失效缓存，
#     ⇒ 这些数据集的最坏陈旧窗口从 ≤120s/300s 被这次 TTL 上调放大到 1800s；
#   - `app/data/text_ingest.py`（announcements_docs / text_features）、
#     `app/data/cross_section.py`（cs 镜像）——影响 /overview 的磁盘占用与"最近更新"。
# 另：`cached()` 增加了**代际守卫**，堵住"扫描进行中发生 invalidate、旧值又被写回"
# 的时序漏洞（见 `stats_cache.cached` 的 docstring）——否则逐步骤补的 invalidate 仍会
# 被在飞扫描覆盖。**以上任一环缺失，长 TTL 就会让用户看到陈旧数字**：不要把本注释读成
# "普适安全"。
_TTL_DATASETS = 1800
_TTL_QUALITY = 1800


def _sqlite_ro() -> sqlite3.Connection:
    """只读打开 SQLite（统计聚合用；与 calendar_store 同款只读 URI 方式）。"""
    s = get_settings()
    db_path = s.SQLITE_PATH
    if not db_path.exists():
        raise FileNotFoundError(f"SQLite 不存在: {db_path}")
    return sqlite3.connect(f"file:{db_path}?mode=ro", uri=True, timeout=30)


# ---------------------------------------------------------------------------
# parquet 元数据扫描
# ---------------------------------------------------------------------------
def _scan_dataset(root: Path, dataset: str | None = None) -> dict[str, Any] | None:
    """扫描单个数据集：行数 / 符号数 / 日期区间 / 磁盘占用。

    Args:
        root: 数据集根目录（``DATA_ROOT/<dataset>``）。
        dataset: 数据集 key，用于查 :data:`DATASET_DATE_COLUMN`；缺省取 ``root.name``
            （保持既有直接调用/测试的兼容）。

    韧性设计（2026-09-19）：
        1. **日期列可配置**：非 ``date`` 列的数据集（如 announcements 用 ``pub_date``）
           按映射取列；列不存在时**不读该列**，行数走 pyarrow footer 元数据（零解码，
           见 :func:`_scan_file` 的单句柄实现），日期区间留 ``None``。
        2. **按文件隔离**：单个文件损坏/不可读只记 warning 并跳过，**保留已成功部分的
           统计**——此前是「一个坏文件 → 整个数据集从清单里消失」，属本项目零容忍的
           「静默给出错误信息」。
        3. **``rows == 0`` 仍返回 None**（真空数据集不该显示在清单里）——既有行为保留。
    """
    if not root.exists():
        return None
    dataset_key = dataset or root.name
    date_col = DATASET_DATE_COLUMN.get(dataset_key, DEFAULT_DATE_COLUMN)
    try:
        rows = 0
        bytes_ = 0
        symbols: list[str] = []
        dmin: date | None = None
        dmax: date | None = None
        for sym_dir in root.iterdir():
            if sym_dir.is_dir() and sym_dir.name.startswith("symbol="):
                symbols.append(sym_dir.name.split("=", 1)[1])
        # symbol 分区数据集：逐文件读日期列拿区间；非分区结构走通用递归扫描
        if symbols:
            files = [f for sym in symbols for f in (root / f"symbol={sym}").glob("*.parquet")]
        else:
            files = sorted(root.rglob("*.parquet"))
            if not files:
                return None

        failed = 0
        for f in files:
            try:
                size = f.stat().st_size
            except OSError as e:  # noqa: BLE001 单个文件元数据不可读
                failed += 1
                logger.warning(
                    f"[datacenter] {dataset_key} 文件 {f.name} 大小不可读，已跳过: {e!r}")
                continue
            bytes_ += size
            try:
                n, lo, hi = _scan_file(f, date_col)
            except Exception as e:  # noqa: BLE001 单文件坏不得让整个数据集消失
                failed += 1
                logger.warning(
                    f"[datacenter] {dataset_key} 文件 {f.name} 不可读/损坏，已跳过"
                    f"（其余文件统计保留）: {e!r}")
                continue
            rows += n
            if lo is not None and (dmin is None or lo < dmin):
                dmin = lo
            if hi is not None and (dmax is None or hi > dmax):
                dmax = hi
        if failed:
            logger.warning(
                f"[datacenter] {dataset_key} 扫描完成："
                f"{len(files) - failed}/{len(files)} 个文件成功，{failed} 个被跳过")

        if rows == 0:
            return None
        return {
            "symbols": len(symbols),
            "rows": rows,
            "bytes": bytes_,
            "start": dmin.isoformat() if dmin else None,
            "end": dmax.isoformat() if dmax else None,
        }
    except Exception as e:  # 数据集级（目录遍历等）异常不拖垮整个清单
        logger.warning(f"[datacenter] scan {dataset_key} failed: {e!r}")
        return None


def _scan_file(f: Path, date_col: str) -> tuple[int, date | None, date | None]:
    """读单个 parquet 文件，返回 ``(行数, 最小日期, 最大日期)``。

    性能（2026-09-26 修复，冷扫 74s → 秒级）：
        原先对**同一个文件开了三次**——``pl.read_parquet_schema`` 探可读性、
        ``pq.ParquetFile(...).metadata.num_rows`` 取行数、``pl.read_parquet`` 再解码
        ``date_col`` 求 min/max。daily_bar / _qfq / _hfq 共 3.3 万个文件 ⇒ 约 10 万次
        parquet 打开 + 解码。现改为：

        1. **一个** ``pq.ParquetFile`` 句柄同时拿到 schema 与行数；
        2. 日期区间**优先取 row group statistics**（footer 里已写好的 min/max，
           **零解码**）。实测本项目全部数据集（daily_bar*、features、announcements）
           的日期列统计均可用；
        3. 统计缺失 / 不可解析 / 列索引对不上时**回退**解码该列（行为与旧实现一致）。

    Raises:
        Exception: 文件无法作为 parquet 打开（损坏 / 被覆盖 / 截断）时抛出。
            ⚠️ 可读性探针必须是**真的打开 footer**（``pq.ParquetFile``）。不能依赖
            任何把失败吞成 0 的封装（如 :func:`_parquet_rows`）——否则无法区分
            「真空文件」与「损坏文件」，调用方就失去了告警的依据。
    """
    import pyarrow.parquet as pq

    pf = pq.ParquetFile(f)  # 可读性探针（失败即抛）
    rows = pf.metadata.num_rows
    try:
        idx = pf.schema_arrow.names.index(date_col)
    except ValueError:
        return rows, None, None  # 该数据集没有映射的日期列：不读该列
    rng = _date_range_from_stats(pf, idx, date_col)
    if rng is not None:
        return rows, rng[0], rng[1]
    lo, hi = _date_range_from_column(f, date_col)
    return rows, lo, hi


def _date_range_from_stats(
    pf: Any, idx: int, date_col: str
) -> tuple[date | None, date | None] | None:
    """从 row group statistics 取日期区间（**零解码**）。

    Args:
        pf: 已打开的 ``pyarrow.parquet.ParquetFile``。
        idx: ``date_col`` 在 ``pf.schema_arrow.names`` 里的下标。
        date_col: 日期列名，用于校验 leaf 列真的对得上。

    Returns:
        ``(lo, hi)``：统计可用时给出区间；``None`` 表示**统计不可用**
        （缺失 / 无 min-max / 列索引与名字错位 / 值不可解析），
        调用方应回退到 :func:`_date_range_from_column`。

    为什么要校验 ``path_in_schema``：``md.row_group(g).column(i)`` 用的是 **parquet
    leaf 序号**，含嵌套（struct/list）时与 ``schema_arrow.names`` 的下标会**错位**。
    对不上就宁可放弃快路径，也不能拿别的列的统计去冒充日期区间。
    """
    md = pf.metadata
    if md.num_row_groups == 0:
        return None  # 无 row group ⇒ 无统计；空文件交给回退路径（代价可忽略）
    try:
        if md.row_group(0).column(idx).path_in_schema != date_col:
            return None
        lo: date | None = None
        hi: date | None = None
        for g in range(md.num_row_groups):
            st = md.row_group(g).column(idx).statistics
            if st is None or not st.has_min_max:
                return None
            for raw in (st.min, st.max):
                d = _as_date(raw)
                if lo is None or d < lo:
                    lo = d
                if hi is None or d > hi:
                    hi = d
    except Exception:  # noqa: BLE001 统计形态异常：回退解码列，行数不受影响
        return None
    if lo is None or hi is None:
        return None
    return lo, hi


def _date_range_from_column(f: Path, date_col: str) -> tuple[date | None, date | None]:
    """回退路径：解码 ``date_col`` 列求 min/max（仅当 row group 统计不可用时走这里）。

    空文件 / 全空 / 值不可解析一律返回 ``(None, None)`` —— 行数由调用方另行提供，
    不受本函数影响。
    """
    import polars as pl

    try:
        df = pl.read_parquet(f, columns=[date_col])
    except Exception as e:  # noqa: BLE001 列不可读：仅区间缺失，行数仍有效
        logger.debug(f"[datacenter] {f.name} 日期列 {date_col!r} 不可读，跳过区间: {e!r}")
        return None, None
    if df.height == 0:
        return None, None
    col = df[date_col]
    if col.null_count() == df.height:
        return None, None
    try:
        return _as_date(col.min()), _as_date(col.max())
    except Exception:  # noqa: BLE001 值不可解析：行数仍有效，仅区间缺失
        logger.debug(f"[datacenter] {f.name} 日期列 {date_col!r} 值不可解析，跳过区间")
        return None, None


def _parquet_rows(f: Path) -> int:
    """⚠️ 已废弃（2026-09-26）：保留仅为兼容可能的旧调用方，**新代码不得使用**。

    它把读文件失败**吞成 0**，无法区分「真空文件」与「损坏文件」⇒ 调用方会失去
    告警依据（本项目零容忍的"静默给出错误信息"）。行数请直接用
    ``pq.ParquetFile(f).metadata.num_rows``（见 :func:`_scan_file`），让损坏文件
    照常抛异常。
    """
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
        meta = _scan_dataset(s.DATA_ROOT / key, key)
        if meta:
            out[key] = meta
    return out


def _data_cache_key(name: str) -> str:
    """Keep process caches isolated when tests or workers switch DATA_ROOT."""
    return f"{name}:{get_settings().DATA_ROOT.resolve()}"


def _dataset_bytes(root: Path, dataset: str) -> int | None:
    """数据集 parquet 字节数：一次递归 ``os.scandir`` 累加文件大小（不读内容）。

    目录不存在时返回 ``None``（未知），**不返回 0** —— 0 会被前端当成"占用 0 字节"
    的真实读数，属本项目零容忍的"静默给出错误信息"。
    """
    base = root / dataset
    if not base.exists():
        return None
    total = 0
    stack = [base]
    while stack:
        try:
            with os.scandir(stack.pop()) as entries:
                for entry in entries:
                    try:
                        if entry.is_dir(follow_symlinks=False):
                            stack.append(Path(entry.path))
                        elif (entry.is_file(follow_symlinks=False)
                              and entry.name.endswith(".parquet")):
                            total += entry.stat(follow_symlinks=False).st_size
                    except OSError:
                        continue
        except OSError:
            continue
    return total


def _count_symbol_dirs(root: Path, dataset: str) -> int:
    """数据集下 ``symbol=`` 目录数（一次 iterdir，不读 parquet footer）。

    目录不存在时返回 ``-1``（未知），用于区分"非分区数据集（0 个 symbol= 目录）"
    与"目录缺失（口径未知，退回条目计数）"。
    """
    base = root / dataset
    if not base.exists():
        return -1
    n = 0
    try:
        for child in base.iterdir():
            if child.is_dir() and child.name.startswith("symbol="):
                n += 1
    except OSError:
        return -1
    return n


def _read_manifest_raw(root: Path) -> dict:
    """读取 ``root/.manifest.json``（缺失/损坏按空 dict 处理，绝不抛）。"""
    path = root / ".manifest.json"
    if not path.exists():
        return {}
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError, TypeError) as exc:
        logger.warning(f"[datacenter] manifest unavailable: {exc!r}")
        return {}
    return raw if isinstance(raw, dict) else {}


def _summarize_manifest_entries(root: Path, raw: dict) -> dict[str, dict[str, Any]]:
    """把 manifest 条目聚合成与 :func:`_scan_dataset` 同形状的统计（不读 parquet）。"""
    out: dict[str, dict[str, Any]] = {}
    for dataset in DATASET_META:
        entries = raw.get(dataset)
        if not isinstance(entries, dict) or not entries:
            continue
        rows = 0
        n_with_rows = 0
        starts: list[str] = []
        ends: list[str] = []
        for meta in entries.values():
            if not isinstance(meta, dict):
                continue
            r = int(meta.get("rows") or 0)
            rows += r
            if r > 0:
                n_with_rows += 1
            if meta.get("first"):
                starts.append(str(meta["first"])[:10])
            if meta.get("last"):
                ends.append(str(meta["last"])[:10])
        if not rows:
            continue
        # symbols 口径与 _scan_dataset 对齐：按磁盘 ``symbol=`` 目录数计（含空目录，
        # 行数为 0 的隔离标的也占一个目录），非分区数据集则为 0；目录缺失
        # （-1，测试/多根场景）退回"有条目的标的数"。
        n_dirs = _count_symbol_dirs(root, dataset)
        symbols = n_dirs if n_dirs >= 0 else n_with_rows
        out[dataset] = {
            "symbols": symbols,
            "rows": rows,
            "bytes": _dataset_bytes(root, dataset),
            "start": min(starts) if starts else None,
            "end": max(ends) if ends else None,
        }
    return out


def _manifest_needs_rebuild(root: Path, raw: dict) -> bool:
    """manifest 需重建的判据：旧格式（无 ``__schema__`` 标记）或磁盘有数据集未登记。"""
    if raw.get(MANIFEST_SCHEMA_KEY) != MANIFEST_SCHEMA:
        return True
    for key in DATASET_META:
        if (root / key).exists() and raw.get(key) is None:
            return True
    return False


def _rebuild_manifest(root: Path, raw: dict, *, force: bool = False) -> None:
    """全量 footer 扫描重建 manifest（一次扫描即得行数与日期区间）并原子回写。

    - ``force=True``：无条件重扫所有磁盘上存在的数据集（过期回退路径用）；
    - ``force=False``：只在格式过期或磁盘有数据集未登记时重扫（/overview 快路径用，
      避免每次请求都触发全量扫描）。

    ⚠️ 只在 ``root`` 就是本进程 ``DATA_ROOT`` 时执行：测试/多根场景（如
    ``_manifest_dataset_summary(tmp_path)``）下 manifest 由调用方自备，
    **绝不**写真实索引、更不扫描真实数据目录。
    """
    if root.resolve() != get_settings().DATA_ROOT.resolve():
        return
    if force or raw.get(MANIFEST_SCHEMA_KEY) != MANIFEST_SCHEMA:
        targets = [k for k in DATASET_META if (root / k).exists()]
    else:
        targets = [k for k in DATASET_META
                   if (root / k).exists() and raw.get(k) is None]
    if not targets:
        return
    try:
        manifest_scan_and_store(targets)
    except Exception as e:  # noqa: BLE001 重建失败只影响本次统计质量，不拖垮端点
        logger.warning(f"[datacenter] manifest 重建失败: {e!r}")


def _manifest_dataset_summary(root: Path) -> dict[str, dict[str, Any]]:
    """从写路径维护的 manifest 聚合行数/标的数/日期范围/字节数，避免读取 Parquet。

    磁盘真实存在但 manifest 未登记的数据集（如从未经写路径 / 自愈路径的
    ``daily_bar_qfq``）会先触发一次 footer 扫描并回写，再参与聚合；磁盘上不存在的
    数据集**不会**被硬造出来。
    """
    raw = _read_manifest_raw(root)
    if _manifest_needs_rebuild(root, raw):
        _rebuild_manifest(root, raw)
        raw = _read_manifest_raw(root)
    return _summarize_manifest_entries(root, raw)


def _manifest_is_fresh(root: Path) -> bool:
    """保守新鲜度判据：manifest 文件 mtime **不早于** DATA_ROOT 下最新 parquet mtime。

    只要有一个 parquet 比 manifest 新就判"过期"（宁可多扫不可漏更新）。
    ``_storage_stats`` 同时给出最新 parquet mtime（其结果自带 120s 缓存，且写库后
    会被 ``invalidate_stats_cache()`` 清掉，故不会拿旧 mtime 误判"新鲜"）。
    """
    try:
        manifest_mtime = (root / ".manifest.json").stat().st_mtime
    except OSError:
        return False
    _, latest_parquet_mtime = _storage_stats(root)
    return latest_parquet_mtime > 0 and manifest_mtime >= latest_parquet_mtime


def _scan_all_datasets_fresh() -> dict[str, dict[str, Any]]:
    """数据集统计快路径：manifest 新鲜时聚合自 manifest，过期则全扫重建后聚合。

    背景：``/datasets`` / ``/quality`` 冷扫要打开约 3.65 万个 parquet footer
    （实测 26.5s），而 manifest 聚合 <1s。故在 manifest 新鲜时走 manifest；
    过期时**回退全扫**（一次 footer 扫描，顺带把 manifest 重建为新格式），
    使后续调用能命中快路径 —— 否则写路径 5s 的落盘节流会让 manifest 长期比最新
    parquet 旧几秒，"过期"判据几乎恒真、快路径永不生效。
    """
    root = get_settings().DATA_ROOT
    raw = _read_manifest_raw(root)
    if _manifest_is_fresh(root) and not _manifest_needs_rebuild(root, raw):
        summary = _summarize_manifest_entries(root, raw)
        if summary:
            return summary
    _rebuild_manifest(root, raw, force=True)
    return _manifest_dataset_summary(root)


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
    refresh: Annotated[int, Query(
        ge=0, le=1,
        description="1=失效统计缓存强制重扫（重扫结果回写进程级缓存）")] = 0,
    _user: dict = Depends(require_role("viewer")),
) -> APIResponse[dict]:
    """全局数据状态看板：存储总量 / 覆盖标的 / 最近更新 / AKShare 健康。

    统计扫描（数百个 parquet 文件）带 120s 进程级缓存；refresh=1 时先
    失效缓存再重扫（响应标 from_cache="refreshed"）。

    ⚠️ 2026-09-21（§4.8 序 16）修复：原签名为 `refresh: int = Query(0, ...)`。
    直接调用（`app_settings.py:157` 就是这么调的）时 `refresh` 拿到的是
    **`Query` 对象本身**，而 `bool(Query(0)) is True` ⇒ `:466 if refresh:` 恒真
    ⇒ **每次 `/settings` 都 `invalidate_stats_cache()`**，逼出 27~30s 全量重扫
    （前端注释里"冷算 27~30s"的根因）。改为 `Annotated` + 普通默认值 `0`：
    FastAPI 仍按查询参数校验，而内部无参调用拿到的是真正的 `0`。
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
                    # [只读侧红线] RUNNING 是**非终态**，不得当绿灯（进程被杀留下的
                    # RUNNING 行不能点亮"健康"）。是否卡死交给**启动时**的
                    # task_store.reap_stale_running_tasks / monitor.reclaim_stale_retrain
                    # 兜底，此处刻意**不猜时间阈值**（避免引入易误判的新常量）。
                    health, health_msg = "yellow", "同步任务执行中（结果未确认）"
                elif row[0] == "PENDING":
                    # PENDING 同样非终态；单独分支，避免落进下方"异常"文案把 NULL 渲染成
                    # "最近同步异常：None"。
                    health, health_msg = "yellow", "同步任务排队中"
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
        # 与 /datasets 共用同一个"要不要全扫"判据与同一道闸门：manifest 新鲜时
        # _calc 只读 manifest JSON + 目录 mtime，不该为它排队。
        if await asyncio.to_thread(_parquet_rescan_needed):
            async with compute_slot_ctx(timeout=_SCAN_GATE_WAIT_SECONDS):
                return await _run_sync_ctx(_calc)
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


# 重扫闸门的**有界等待**（秒）。compute_slot 默认 10s 是给"前端 ComputeQueue 已串行化"
# 的算力端点调的；数据中心重扫冷算 24~32s，10s 等待会把第三个并发请求误判为"资源繁忙"。
# 放宽到 45s 的算术依据：CONCURRENCY=2 ⇒ 最坏情形是排在**一次**冷扫之后
# （≈32s 等待 + ≈32s 计算 = 64s），仍在前端 datasets/quality 的 90s 预算之内，
# 也远小于服务端 240s 全局兜底（core/timeout_guard.py）。
_SCAN_GATE_WAIT_SECONDS = 45.0


def _parquet_rescan_needed() -> bool:
    """是否需要真正打开 parquet footer（全量重扫）——统一闸门的"要不要过闸"判据。

    为假 ⇒ 相关路径只读 manifest JSON + 目录 mtime（<2s），无需占用 compute slot；
    为真 ⇒ 会打开约 3.65 万个 parquet footer（冷扫 24~32s），必须过闸门串行化。
    """
    root = get_settings().DATA_ROOT
    raw = _read_manifest_raw(root)
    if _manifest_needs_rebuild(root, raw):
        return True
    return not _manifest_is_fresh(root)


async def _gated_scan(key: str, ttl: float, fn, *, scan_needed=None):
    """数据中心**统一重扫闸门**：所有会全量打开 parquet 的统计路径共用 compute_slot。

    为什么需要（2026-09-29 P1）：``stats_cache.cached`` 的 single-flight 只对**同一
    key** 去重；而 /datasets、/quality、/overview 各用不同 key，三者的全量 footer /
    逐标的分区扫描会**同时**抢磁盘 IO —— 这正是"单请求被放大到 50~74s"的机制。

    设计要点（避免闸门本身制造超时）：
    1. 缓存新鲜命中 ⇒ 直接返回，**不占 slot**（否则每次页面加载都要排队）；
    2. ``scan_needed()`` 为假（manifest 新鲜且无需重建）⇒ 也**不占 slot**；
    3. 只有确需真扫描时才进闸门；进闸门后由 ``cached()`` 内部的二次缓存检查兜住
       "排队期间别的请求已算完"的情况，不会重复扫。
    """
    fresh, val = cache_peek(key, ttl)
    if fresh:
        return val
    if scan_needed is not None and not await asyncio.to_thread(scan_needed):
        return await asyncio.to_thread(lambda: _cached(key, ttl, fn))
    async with compute_slot_ctx(timeout=_SCAN_GATE_WAIT_SECONDS):
        return await asyncio.to_thread(lambda: _cached(key, ttl, fn))


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
    items = await _gated_scan(
        _data_cache_key("datasets"), _TTL_DATASETS, _scan_all_datasets_fresh,
        scan_needed=_parquet_rescan_needed)
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
    # 上界取 10000 而非 500：前端"展开全部"正是用 limit=10000 表达（真实 item
    # 数 ≈ 标的数 2499），收得更紧会让该操作被 40000 拒绝；仍封死 10**9。
    limit: int = Query(50, ge=1, le=10000,
                       description="返回条数上限（items 被截断；affected_symbols 为截断前总数）"),
    _user: dict = Depends(require_role("viewer")),
) -> APIResponse[dict]:
    """数据质量：按交易日历扫描 daily_bar 每只标的的缺失交易日（停牌除外无法区分）。"""
    # _quality_calc 对每只标的做一次多文件读（约 2488 次打开），没有比"缓存过期"更
    # 便宜的判据 ⇒ 冷路径一律过闸门。
    res = await _gated_scan(
        _data_cache_key("quality"), _TTL_QUALITY, _quality_calc)
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
async def logs(limit: int = Query(60, ge=1, le=500,
                                  description="返回条数上限（尾部 N 条）"),
               _user: dict = Depends(require_role("researcher"))) -> APIResponse[dict]:
    """近期运行日志：backend/logs/app.log 尾部解析（INFO 及以上全部纳入）。"""

    def _calc() -> tuple[list[dict], int]:
        path = get_settings().LOG_DIR / "app.log"
        if not path.exists():
            return [], 0
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
        return out[-limit:], len(out)

    # 审计 R10 / P5-S12：尾部截断必须披露（此前 out[-limit:] 静默截断，
    # 前端把返回条数当"全部日志"）。前端消费 `{items}`，新增键不改形状。
    items, total = await _run_sync_ctx(_calc)
    return ok({"items": items, "total": total, "returned": len(items),
               "limit": limit, "truncated": len(items) < total})


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
            return fail(ERR_PIPELINE_BUSY, "已有同步任务在执行中，请等待完成或先停止")
        _sync.running = True
        task_id = create_task(f"datacenter.sync.{req.mode}")
        if not claim_task(task_id, "api-sync", lease_seconds=300):
            _sync.running = False
            # R9：原为裸码 `5000`（两端都未登记）。语义是"刚建的任务被别人抢走"
            # ⇒ 与上面同属互斥冲突，映射 `ERR_PIPELINE_BUSY(40900)`。
            # （§8.3 建议 `50000/52000` 二选一，但那会丢"冲突"语义、且 52000 是训练码。）
            return fail(ERR_PIPELINE_BUSY, "同步任务已被其他执行者领取，请稍后重试")
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
            # failed 与 completed 同生命周期：口径定为「本轮（最近一次任务）」，
            # 否则 failed_count 会在进程内累计，与前端「上次任务」文案自相矛盾。
            # resume=True 时两者一并保留（续传需知道哪些标的仍待重试）。
            _sync.failed.clear()
            # 审计 P1-28：记录续传集的归属 mode，防止跨 mode 误跳过
            _sync.completed_mode = req.mode
    symbols = req.symbols or read_all_symbols("daily_bar")
    if req.mode == "repair" and not req.symbols:
        # 只修复有缺漏的标的（quality 缓存 5 分钟内有效）
        q = _cached(_data_cache_key("quality"), _TTL_QUALITY, _quality_calc)
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
        # R9：原用 `ERR_DATA_EMPTY(51001)` 表"任务不存在"——语义错位（"本地无数据"
        # vs "请求的资源不存在"），前端据此无法区分"没有数据"与"任务 id 写错"。
        # §8.3 建议改 `ERR_NOT_FOUND(40400)`，采纳。
        return fail(ERR_NOT_FOUND, "任务不存在", {"task_id": task_id})
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
    """与 /quality 相同的计算体（供 repair 模式取缺漏标的复用）。

    性能（2026-09-26）：原先对每只标的**逐个年份文件** ``pl.read_parquet``（2488 只
    × ~5 个文件 ≈ 1.2 万次打开），实测冷算 86s。改为**一次多文件读**同一个标的的
    全部年份（polars 接受文件列表，合成单个 frame），打开次数降到 2488 次；
    日期列统一收敛为 ``date`` 后再走既有的集合差逻辑，语义不变。
    """
    import polars as pl

    s = get_settings()
    root = s.DATA_ROOT / "daily_bar"
    symbols = read_all_symbols("daily_bar")
    if not symbols:
        return {"checked": 0, "total_missing_days": 0, "items": []}
    cal = get_calendar()
    end_iso = _cached(_data_cache_key("datasets"), _TTL_DATASETS, _scan_all_datasets_fresh)\
        .get("daily_bar", {}).get("end")
    today = min(date.today(), date.fromisoformat(end_iso)) if end_iso else date.today()
    items: list[dict] = []
    total_missing = 0
    for sym in symbols:
        files = sorted((root / f"symbol={sym}").glob("year=*.parquet"))
        if not files:
            continue
        try:
            col = pl.read_parquet([str(f) for f in files], columns=["date"])["date"]
        except Exception as e:  # noqa: BLE001 单标的不读只跳过，不拖垮全局统计
            logger.warning(f"[datacenter] quality 读取 {sym} 日期列失败，已跳过: {e!r}")
            continue
        if col.len() == 0:
            continue
        # 统一成 date：列可能是 Date，也可能是 Datetime（本项目 features 即 Datetime）
        if col.dtype == pl.Datetime:
            col = col.dt.date()
        elif col.dtype != pl.Date:
            col = col.cast(pl.Date, strict=False)
        dates = [d for d in col.to_list() if d is not None]
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
    """拉取单只 ETF 日线，返回标准 Polars DataFrame（签名对齐 ``akshare_adapter.fetch_daily_bar``）。

    作为 fetcher 注入 ``fetch_and_write_daily_bars``，与股票共用同一写入路径
    （daily_bar 分区）。多源降级与「空 / 故障分离」语义**逐字对齐**
    :func:`app.data.ingest.akshare_adapter.fetch_daily_bar`：

        源1 东方财富 ``fund_etf_hist_em``（支持 ``adjust=""`` / ``"qfq"`` / ``"hfq"``）；
        源2 新浪 ``fund_etf_hist_sina``（**仅当** ``adjust==""``）。

    ⚠️ 为什么新浪只用在不复权口径：新浪只提供**不复权**全量历史（``fund_etf_hist_sina``
    无 ``adjust`` 参数），复权序列无法从新浪取得。若对 ``adjust="hfq"`` 降级到新浪，
    就会把「不复权」冒充「后复权」落库、制造假的复权基准 —— 故复权口径**不得**降级。

    语义（与股票多源链一致）：
        * **空**（某适用源成功返回但无数据 / 裁剪到区间外）⇒ 记 ``saw_empty``，最终返回
          **空** DataFrame（下游按「无数据」处理，不算抓取故障）；
        * **故障**（全部**适用**源均以异常失败）⇒ 抛 :class:`DataSourceUnavailable`
          （→ ``fetch_and_write_daily_bars`` 记入 ``failed`` → ``_run_fetch`` 计入
          ``_sync.failed``，而非 ``completed``），**绝不静默吞异常成空表**。

    ⚠️ symbol 由 ``_standardize_daily`` 无条件按 ``code_to_symbol(code)`` 决定
    （``159915 -> 159915.SZ``），故新浪外呼用的 ``sz``/``sh`` 前缀仅用于**取数**、
    与落库 symbol 无关。此处**不得**调用 ``akshare_adapter._sina_symbol``：其对深市
    基金 ``1xxxxx`` 会抛 ``ValueError``（自有判据只认 ``6/9``→sh、``0/2/3``→sz），
    统一委托 ``domain.a_share_rules.market_prefix``（唯一事实来源）。

    :param code:   6 位纯数字代码，如 ``"159915"``
    :param start:  起始日期 ``"YYYY-MM-DD"``
    :param end:    结束日期 ``"YYYY-MM-DD"``
    :param adjust: ``""`` 不复权 / ``"qfq"`` 前复权 / ``"hfq"`` 后复权
    """
    import polars as pl
    import akshare as ak
    from ...core.errors import DataSourceUnavailable
    from ...data.ingest.akshare_adapter import _standardize_daily  # 同包内复用列规范化
    from ...domain.a_share_rules import market_prefix

    # 源1：东方财富（列名与股票一致 -> 复用 _standardize_daily）
    def _source_em() -> "pl.DataFrame":
        df = ak.fund_etf_hist_em(
            symbol=code, period="daily",
            start_date=start.replace("-", ""),
            end_date=end.replace("-", ""),
            adjust=adjust)
        if df is None or df.empty:
            return pl.DataFrame()
        return _standardize_daily(df, code)

    # 源2：新浪（不复权全量历史；需按 [start, end] 闭区间裁剪）
    def _source_sina() -> "pl.DataFrame":
        sym = f"{market_prefix(code)}{code}"  # 159915 -> sz159915（委托唯一事实来源）
        df = ak.fund_etf_hist_sina(symbol=sym)
        if df is None or df.empty:
            return pl.DataFrame()
        frame = _standardize_daily(df, code)
        if frame.is_empty():
            return frame
        lo, hi = date.fromisoformat(start), date.fromisoformat(end)
        return frame.filter((pl.col("date") >= lo) & (pl.col("date") <= hi))

    # 适用源清单：复权口径下新浪不可用（只提供不复权），故只挂东财
    sources: list[tuple[str, Any]] = [("eastmoney", _source_em)]
    if adjust == "":
        sources.append(("sina", _source_sina))

    errs: list[str] = []
    saw_empty = False
    for name, fn in sources:
        try:
            frame = fn()
        except Exception as e:  # noqa: BLE001 换源：异常只进日志与聚合，绝不吞成空表
            logger.warning(
                f"[datacenter] etf fetch code={code} adjust={adjust!r} "
                f"source={name} error={e!r}")
            errs.append(f"{name}:{type(e).__name__}")
            continue
        if not frame.is_empty():
            return frame
        # 该源成功但无数据（停牌 / 退市 / 区间外）—— 记录后继续尝试后续适用源
        logger.debug(
            f"[datacenter] etf fetch code={code} adjust={adjust!r} source={name} 无数据")
        saw_empty = True

    if saw_empty:
        return pl.DataFrame()
    raise DataSourceUnavailable(
        f"ETF 日线全部数据源失败 {code} adjust={adjust!r}: {'; '.join(errs) or '无可用源'}")


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


def _run_fetch(symbols: list[str], asset_type: str, start: str, end: str,
               slot: PipelineSlotHandle | None = None) -> None:
    """自定义抓取 worker：按 asset_type 选 fetcher，逐只抓取并写入 daily_bar 分区。

    复用 _sync 状态机（进度/日志/cancel/resume 与 sync 模式一致）。

    :param slot: **已持有**的管道槽句柄（审计 P1-31：请求线程申请、跨线程交接）。
        为 ``None`` 时按旧语义自行申请（供直接调用方与既有测试使用）——
        此时锁忙会抛 :class:`PipelineBusy`。
    """
    from ...data.ingest.tasks import fetch_and_write_daily_bars

    # 与 sync/pipeline/mirror/training 使用同一把非阻塞锁，完整覆盖所有分区写入。
    # 交接模式下由本 `with` 负责在退出时释放（handle.__exit__ → release）。
    with (slot if slot is not None else pipeline_slot("fetch")):
        fetcher = _fetch_etf_bar if asset_type == "etf" else None
        label = "ETF" if asset_type == "etf" else "股票"
        # 本函数固定抓这两种口径（单一事实来源：抓取调用与日志统计都用它）
        _FETCH_ADJUSTS: tuple[str, ...] = ("", "hfq")
        # 复权口径 -> 中文标签（仅供日志；未知口径原样回显，绝不臆造）
        _adj_names = {"": "不复权", "qfq": "前复权", "hfq": "后复权"}

        def _adj_label(adjs) -> str:
            return "、".join(_adj_names.get(a, a) for a in adjs) or "—"

        _sync.log("INFO", f"开始自定义抓取：{label} {len(symbols)} 只，区间 {start} ~ {end}")
        for i, sym in enumerate(symbols, 1):
            if _check_cancel():
                _sync.log("WARNING", "用户停止抓取，任务已中断（可断点续传）")
                return
            code = sym.split(".")[0]
            _sync.current = f"抓取 {sym} {label}日线 ({i}/{len(symbols)})"
            try:
                n, failed_adj = fetch_and_write_daily_bars(
                    code, start, end, adjusts=_FETCH_ADJUSTS, fetcher=fetcher)
                with _sync.lock:
                    # 不变式：任何把 sym 写进 completed 的路径，都必须在**同一把锁内**
                    # 把它从 failed 摘掉。failed 只增不减 ⇒ resume 续跑成功后，前端
                    # 「上次任务：N/N 完成 · 失败 X 只」仍会把本轮已修好的标的算作失败。
                    if n > 0 and not failed_adj:
                        _sync.completed.add(sym)
                        _sync.failed.discard(sym)
                    elif failed_adj:
                        # ⚠️ 有意保守：``_sync.failed`` **同时**装「全口径失败」与
                        # 「部分口径失败」（如 raw 成功、hfq 失败）——两者都需 resume
                        # 重试，故一律记为失败。**不**引入"部分成功"新状态：
                        # 本批只批了「日志如实分流 + 前端展示 failed_count」两件事。
                        _sync.failed.add(sym)
                    else:
                        _sync.completed.add(sym)
                        _sync.failed.discard(sym)
                # 日志必须与事实一致（禁止"报喜不报忧"）：四种组合如实分流 ——
                # 只要**有任何口径失败**，就绝不出现"成功"二字。
                if not failed_adj:
                    _sync.log("INFO", f"抓取 {sym} ... {n} 行成功" if n
                              else f"{sym} 无数据")
                elif n > 0:
                    _sync.log(
                        "WARNING",
                        f"抓取 {sym} 部分口径失败：已写入 {n} 行"
                        f"（{_adj_label([a for a in _FETCH_ADJUSTS if a not in failed_adj])}）；"
                        f"失败口径 {_adj_label(failed_adj)} 不可用"
                        f"（已记入 failed，可 resume 重试）")
                else:
                    _sync.log(
                        "WARNING",
                        f"抓取 {sym} 全部口径失败：{_adj_label(failed_adj)} 均不可用"
                        f"（已记入 failed，可 resume 重试）")
            except Exception as e:
                # 异常分支同样必须计入 failed（与上面两个"口径失败"分支对齐）：
                # 否则该标的既不在 completed 也不在 failed，在前端被静默漏掉。
                with _sync.lock:
                    _sync.failed.add(sym)
                _sync.log(
                    "WARNING",
                    (f"抓取 {sym} 异常失败：{e!r}"
                     f"（已记入 failed，可 resume 重试）")[:200])
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
    # 纯参数校验放最前（无副作用 ⇒ 不可能泄漏后面申请的管道锁）
    try:
        date.fromisoformat(req.start)
        date.fromisoformat(req.end)
    except ValueError:
        return fail(ERR_PARAMS, "start/end 必须为 YYYY-MM-DD")
    if req.start > req.end:
        return fail(ERR_PARAMS, "start 不能晚于 end")

    # 先解析标的（DB IO，无副作用；失败时无需归还任何锁）
    if req.symbols:
        symbols = req.symbols
    else:
        symbols = _instrument_symbols_by_type(req.asset_type, req.limit)
    if not symbols:
        return fail(ERR_DATA_EMPTY, f"未找到 {req.asset_type} 类型的标的（instrument 表可能为空）")

    # 审计 P1-31（2026-09-21）：**在请求线程内真正持有**管道锁，再交接给 worker。
    # 原实现是「预检取锁 → 立即释放 → worker 二次取锁」，两次申请之间的窗口里锁
    # 一旦被 sync/pipeline/mirror/training 抢走，就会出现：接口已回 started:true，
    # 而 worker 一进 _run_fetch 即 PipelineBusy ⇒ 任务零执行、data_jobs 零行、
    # 且 _sync.error 会在下一个请求被清零 ⇒ **静默落空且不留案底**。
    # 现在改为申请一次、跨线程交接：拿不到锁就当场返回业务码，绝不谎报已启动。
    try:
        slot = acquire_pipeline_slot("fetch")
    except PipelineBusy as exc:
        return fail(ERR_PIPELINE_BUSY, str(exc))

    with _sync.lock:
        if _sync.running:
            slot.release()          # 唯一一处"已持有但未交接"的早退分支，必须归还
            return fail(ERR_PIPELINE_BUSY, "已有同步任务在执行中，请等待完成或先停止")
        _sync.running = True
        _sync.mode = "fetch"
        _sync.error = None
        _sync.done = 0
        _sync.started_at = time.monotonic()
        _sync.logs = []
        _sync.completed.clear()
        # failed 与 completed 同生命周期（口径=本轮）：新一轮抓取开始时清零历史失败，
        # 使 failed_count 只反映「最近一次任务」（与前端文案一致）。
        _sync.failed.clear()
        _sync.cancel_event.clear()
        _sync.total = len(symbols)

    def _worker() -> None:
        try:
            _run_fetch(symbols, req.asset_type, req.start, req.end, slot=slot)
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
            # 兜底释放：_run_fetch 正常/异常路径都已释放（幂等），此处覆盖
            # "进 _run_fetch 之前就抛"的极端路径，确保锁不永久泄漏。
            slot.release()

    t = threading.Thread(target=_worker, name=f"aqp-fetch-{req.asset_type}", daemon=True)
    t.start()
    logger.info(f"[datacenter] fetch started: type={req.asset_type} symbols={len(symbols)} "
                f"{req.start}~{req.end}")
    return ok({"started": True, "asset_type": req.asset_type,
               "total": len(symbols), "start": req.start, "end": req.end})


@router.get("/instruments")
async def list_instruments(
    asset_type: str = "stock",
    limit: int = Query(50, ge=1, le=500,
                       description="返回条数上限；total 为**不受 limit 影响**的真实总量"),
    _user: dict = Depends(require_role("viewer")),
) -> APIResponse[dict]:
    """标的列表（供前端抓取面板预览：按类型取前 N 只的 symbol/name）。

    [AQP §4.8b S12 修复 2026-09-21] 截断契约（§4.13.3）：

    * 原实现 ``"total": len(rows)`` 把**返回条数**当成总数 ⇒ ``limit=1`` 时
      ``total=1``（真实 5552）——**总数随 limit 一起缩小**，比没有 total 更误导；
      前端 ``DataCenter/index.tsx:307`` 的「可抓取 N 只」直接吃这个数字。
      现在 ``total`` 由独立 ``COUNT(*)`` 得出，不受 limit 影响。
    * 另带 ``returned`` / ``truncated`` / ``limit`` 三件套，调用方可判断是否被截断。
    * ``limit`` 由裸默认值改为 ``Query(ge=1, le=500)``：原先 ``limit=-1`` 在
      SQLite 里等于 **无上限**（返回全表），``limit=0`` 又静默空列表。
    """
    try:
        with _sqlite_ro() as conn:
            if asset_type == "all":
                total = conn.execute("SELECT COUNT(*) FROM instrument").fetchone()[0]
                rows = conn.execute(
                    "SELECT symbol, name, instrument_type FROM instrument "
                    "ORDER BY symbol LIMIT ?", (limit,)).fetchall()
            else:
                total = conn.execute(
                    "SELECT COUNT(*) FROM instrument WHERE instrument_type=?",
                    (asset_type,)).fetchone()[0]
                rows = conn.execute(
                    "SELECT symbol, name, instrument_type FROM instrument "
                    "WHERE instrument_type=? ORDER BY symbol LIMIT ?",
                    (asset_type, limit)).fetchall()
        return ok({"items": [{"symbol": r[0], "name": r[1], "type": r[2]} for r in rows],
                   "total": int(total), "returned": len(rows),
                   "truncated": int(total) > len(rows), "limit": limit})
    except Exception as e:  # noqa: BLE001
        # R9：原为裸码 `5001`（两端都未登记）+ 把 `{e!r}` 异常串回前端（同
        # B7a-06 类）。这是**本地 SQLite 读取**失败，不是外部数据源 ⇒ 用
        # `ERR_SYSTEM(50000)` 而不是 §8.3 建议的 `ERR_DATA_SOURCE(51000)`
        # （后者语义是"外部数据源失败"，错用会稀释该码含义——正是本批要消灭的问题）。
        logger.warning(f"[datacenter] 读取 instrument 表失败: {type(e).__name__}: {e!r}")
        return fail(ERR_SYSTEM, "读取标的列表失败（本地库异常，详见服务端日志）")


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

    # 性能（2026-09-11 补测；2026-09-29 复核）：mirror_status 要对 3 个镜像数据集各做
    # 一次 `_scan_source`，即逐文件读取 ~3.2 万个源 parquet 的 date 列。旧注释"21~23s"
    # 是孤立空载测量；真实冷扫 50~74s、页面并发风暴下 ~62s，远超前端预算。
    # TTL 由硬编码 120s 上调到 _TTL_DATASETS(1800s)，与 datasets/quality 同因。
    #
    # 长 TTL 的安全性**是有条件的**（理由同 _TTL_DATASETS 注释块，勿读成"普适安全"）：
    # 缓存键只含 DATA_ROOT，刷新完全依赖写入路径调用 invalidate_stats_cache()。
    # mirror_status 依赖的源日期集合只在写路径变化，而镜像构建收尾处
    # （cross_section.build_mirror:144）会调 invalidate_stats_cache()，该函数清空
    # 除 `logs` 外的**全部**键（含 mirror_status）⇒ 镜像一落盘本缓存即失效。
    # 若日后新增"会改源日期集合却不调 invalidate"的写路径，须同步补上，否则用户会看到
    # 最长 1800s 的陈旧镜像新鲜度。
    return ok(await asyncio.to_thread(
        lambda: _cached(_data_cache_key("mirror_status"), _TTL_DATASETS, mirror_status)))


class MirrorRebuildRequest(BaseModel):
    dataset: str | None = Field(None, description="缺省重建全部 bar 数据集镜像")


@router.post("/mirror/rebuild")
async def cs_mirror_rebuild(
    req: MirrorRebuildRequest | None = None,
    _user: dict = Depends(require_role("researcher")),
) -> APIResponse[list]:
    """增量重建截面镜像（幂等；晚间例行每日自动执行）。

    ``data`` 是**数组**（逐数据集一条 ``{dataset, dates, built, skipped, rows}``），
    故注解必须是 ``APIResponse[list]``：此前误标 ``APIResponse[dict]``，FastAPI
    response_model 校验失败（``data: Input should be a valid dictionary``）⇒ **每次调用
    都 HTTP 500**，尽管镜像其实已成功重建。前端 ``datacenter.ts`` 亦按数组消费。
    """
    # ⚠️ 历史事故留痕：PipelineBusy 曾只在嵌套函数 _rebuild 里 import，管道互斥
    # 触发时 `except PipelineBusy` 抛 NameError，把本该 ERR_PIPELINE_BUSY 的冲突
    # 错报成裸 50000（mypy name-defined 抓到）。审计 P1-31 起两者已提到模块级。
    from ...data.cross_section import MIRROR_DATASETS, build_mirror

    targets = [req.dataset] if req and req.dataset else list(MIRROR_DATASETS)

    def _rebuild() -> list:
        # 管道互斥（C-02）：与 sync / pipeline / training 互斥，后写者覆盖先写者
        # 的静默数据污染在这里被挡下；晚间例行的镜像构建在 pipeline 锁内执行。
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
async def train_readiness_status(
    _user: dict = Depends(require_role("viewer")),
) -> APIResponse[dict]:
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


# ==================== 启动/周期预热（数据中心统计） ====================
async def warm_datacenter_stats() -> bool:
    """把数据中心统计的冷扫挪到后台，首个页面加载不再扛并发冷风暴。

    背景（2026-09-29 根因）：``/datacenter`` 页会**并发**发起 datasets / quality /
    mirror_status 等重扫；三者合计对同一批 parquet 抢磁盘 IO，单请求实测被放大到
    50~74s，远超前端预算 ⇒ 面板红条「请求超时，请稍后重试」。预热把它们提前算好并
    写进各自进程级缓存（键与端点完全一致），页面加载即命中。

    **必须顺序预热**（datasets → quality → mirror_status → storage_stats）：
    并行预热会重现"多路全量扫描抢磁盘"的原始故障，与预热目的相反。datasets 必须
    最先：``_quality_calc`` 内部会读 datasets 缓存（见本文件 ``_quality_calc`` 里的
    ``_cached(_data_cache_key("datasets"), _TTL_DATASETS, _scan_all_datasets_fresh)``），
    先算 datasets 可让其直接命中，省掉一次重复全量扫描。

    ⚠️ 第 4 步 ``storage_stats`` **不在"周期保温"之列**（独立验证 2026-09-29 指出）：
    ``_storage_stats`` 自身是**硬编码 120s** TTL（见其定义），而本预热循环间隔 1500s
    ⇒ 该键在每轮之间有 1380s 是冷的，本函数**无法**为它续期。保留这一步只为让紧随预热
    之后的首次页面加载命中；不指望它保温。刻意**不**把它的 TTL 拉长到 1800s ——
    ``/overview`` 的磁盘占用读数（当前水位 93%）需要保持新鲜。它冷算仅 ~1.2s，
    远在 ``/overview`` 的 60s 预算之内，即便未命中也不会造成超时。

    2026-09-29 P1 追加：预热以**后台任务**身份与用户请求**并发**运行，是"并发扫描风暴"
    的最大单一贡献者。现在每一步都额外穿过共享的 ``compute_slot`` 重扫闸门
    （:func:`_gated_scan` 同款），因此预热会**排在**用户发起的扫描之后、而不是与其抢
    磁盘 IO；拿不到闸门时跳过该步（不抛错），不影响启动。

    失败只记 warning、返回 False，绝不抛出——预热不得阻断启动（对齐
    ``ops.warm_lineage_cache`` / ``market.warm_overview_cache``）。
    """
    async def _step(label: str, fn) -> None:
        """过统一重扫闸门跑一步；拿不到闸门就跳过本轮（预热不得抛错）。"""
        async with compute_slot_optional(timeout=_SCAN_GATE_WAIT_SECONDS) as acquired:
            if not acquired:
                logger.warning(
                    f"[datacenter] warm step {label} 跳过：重扫闸门等待超时")
                return
            await asyncio.to_thread(fn)

    try:
        s = get_settings()
        t0 = time.monotonic()
        # 顺序 await，逐个填缓存；**顺序即防 IO 争抢的关键，勿改成 gather**。
        await _step(
            "datasets",
            lambda: _cached(_data_cache_key("datasets"), _TTL_DATASETS,
                            _scan_all_datasets_fresh))
        await _step(
            "quality",
            lambda: _cached(_data_cache_key("quality"), _TTL_QUALITY, _quality_calc))
        from ...data.cross_section import mirror_status
        await _step(
            "mirror_status",
            lambda: _cached(_data_cache_key("mirror_status"), _TTL_DATASETS, mirror_status))
        # storage_stats 自带 120s TTL（< 1500s 循环间隔）⇒ 这一步**保温不了它**，
        # 只为让紧随预热后的首次页面加载命中；详见本函数 docstring 的 ⚠️ 段。
        await _step("storage_stats", lambda: _storage_stats(s.DATA_ROOT))
        # 成功路径也留痕：本函数不抛异常，且被预热的 4 个 callee 自身都不打日志 ⇒
        # 若此处不打，运维将**无法确认预热是否真的跑过**（对齐 market.warm_overview_cache
        # 的 "warm cache done in Xs"）。2026-09-29 独立验证时正是因缺这行而无法从
        # backend-run 日志判断预热状态。
        logger.info(f"[datacenter] warm stats done in {time.monotonic() - t0:.1f}s")
        return True
    except Exception as e:  # noqa: BLE001 预热失败不阻断启动
        logger.warning(f"[datacenter] warm stats failed: {e!r}")
        return False
