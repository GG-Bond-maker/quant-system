"""
Parquet 分区列式仓库（AQP）。

分区粒度设计（⚠️ 关键约束：禁止产生海量微型文件）：
    daily_bar/symbol=600519.SH/year=2024.snappy.parquet
按【标的 + 年份】分区：5000 只 × 10 年 = 5 万个文件，每个文件约 240 行 × 15 列，
读写性能与增量更新均优异（对比按日分区会产生 1200 万个小文件，不可接受）。

写入语义（原子替换，防半写文件损坏）：
    1. 先写入同目录的随机名 .tmp 文件；
    2. fsync 刷盘；
    3. os.replace 原子覆盖旧文件（Windows NTFS 同样支持原子 replace）；
    4. 当日增量更新：读取该年已有文件 -> concat 新行 -> 按 date 去重(保留新) -> 原子重写整年文件。
"""
from __future__ import annotations

import json
import os
import threading
import time
import uuid
from concurrent.futures import ThreadPoolExecutor
from contextlib import contextmanager
from datetime import date, datetime, timedelta
from decimal import Decimal
from pathlib import Path
from typing import Any, Iterable, Iterator, Literal

import polars as pl
import pandas as pd
from loguru import logger

from ..core.config import get_settings
from ..core.errors import ERR_DATA_EMPTY, AQPException

# parquet 元数据/投影读的并行度（2026-09-30 全检 P0-3/P0-4）。
# 取 8：实测 footer 全扫 283.8s → 8.3s（34×）、投影读 19.8s → ~2.5s；
# 再往上收益递减（受磁盘随机读与 GIL 之外的 IO 等待限制），且会与
# 计算池/默认池争抢 IO。这些是**短生命周期**的独立池（用完即关），
# 不常驻、不与 COMPUTE_POOL 共享，避免长任务把扫描饿死。
_SCAN_MAX_WORKERS = 8

__all__ = [
    "list_symbols_with_data",
    "manifest_scan_and_store",
    "read_parquet_columns",
    "manifest_invalidate",
    "path_for_year",
    "read_all_symbols",
    "read_symbol_dataset",
    "read_symbol_year",
    "today_trade_date_or_last",
    "write_partition",
    "write_year_batch",
]


# ---------- 数据集 manifest（L2-3：免 glob/免 footer 的符号与行数索引） ----------
# DATA_ROOT/.manifest.json：{dataset: {symbol: {"rows": n, "first": d, "last": d}}}
# - 写路径（write_partition/write_year_batch）增量更新（内存+节流落盘）；
# - 读路径（read_all_symbols / list_symbols_with_data）优先取 manifest，
#   首次无 manifest 时全量扫描一次（pyarrow footer 读行数）并回写；
# - 定位是「读优化缓存」而非事实源：旁路写文件（repair 直写等）不感知，
#   manifest_invalidate() 供旁路写后显式失效。
_MANIFEST_LOCK = threading.Lock()
_manifest: dict | None = None
_manifest_last_flush = 0.0
_MANIFEST_FLUSH_MIN_INTERVAL = 5.0

# 数据集 -> 日期列名（默认 "date"）。announcements 的 parquet schema 是
# ['symbol','pub_date',...]，**没有 date 列**（2026-09-19 真 bug：无条件读 date 列
# 导致「公告摘要」整行从 /datasets 消失）。本映射是唯一定义处，api 层从此处导入，
# 避免两处映射各自漂移。
DEFAULT_DATE_COLUMN = "date"
DATASET_DATE_COLUMN: dict[str, str] = {
    "announcements": "pub_date",
}

# manifest 条目格式版本。2 = 条目带 first/last（row-group 统计零解码），且非分区
# 数据集（无 symbol= 目录）以 ``__all__`` 登记。读取方据 ``__schema__`` 判断旧格式
# （条目只有 rows / 非分区数据集整段缺席）并触发**一次**重建，重建后即长期命中快路径。
MANIFEST_SCHEMA = 2
MANIFEST_SCHEMA_KEY = "__schema__"

# 一致性自愈审计（缺陷 C → 审计 P1-41）：
# `dataset -> (已核对的 symbol= 目录数, 上次重扫的 monotonic 时刻)`。
#
# ⚠️ P1-41：原实现是 `set[str]` 的**一次性预算** —— 首次旁路写触发重扫后就把
# dataset 记入集合，**之后新增的 symbol 永久静默缺失且不再 WARNING**（实测：旁路写
# 1 次后自愈生效，再旁路写就再也不修）。现改为"增长驱动"：
#   * 从未核对过 → 重扫；
#   * 磁盘目录数**比上次核对时又增长了** → 说明有新的旁路写入 → 再重扫（含 WARNING）；
#   * 目录数没变而仍 `N_man < N_dir` → 差异来自合法空目录（rows=0），不重扫（防刷屏）。
# 同时保留 `_AUDIT_MIN_INTERVAL` 节流：批量扩容脚本逐只建目录时，若每加一只就全量
# 扫一遍 footer（daily_bar_hfq ≈ 2500 只 × ~10 年 ≈ 2.5 万次读）代价过高；节流只
# 推迟自愈，不取消自愈（下次目录数仍"增长"即会补上）。
_audited: dict[str, tuple[int, float]] = {}
_AUDIT_MIN_INTERVAL = 30.0

# ---------------------------------------------------------------------------
# 读-改-写互斥（审计 P1-40）
# ---------------------------------------------------------------------------
# `write_partition` 是「读该年文件 -> concat -> 去重 -> 原子重写」的读-改-写，
# `_atomic_write_parquet` 只保证**单文件不写半截**，不保证两个写者之间的先后：
# 两个写者各持一份旧快照时，后写者会覆盖先写者的新增行（实测 3 行变 2 行，
# 且 manifest 与"丢失后的事实"一致 ⇒ 事后无痕）。
#
# 真实对手**跨进程**：夜间 `build_universe`（持 pipeline_slot）vs
# `scripts/build_universe.py` CLI（不在同一进程、不受 pipeline_slot 保护，
# 且写同一个 `universe_daily/symbol=__all__`）。`core/pipeline_lock.py` 自述
# 「进程级互斥」，跨进程无效，故这里必须用**文件锁**。
#
# 设计：进程内按目标路径分锁（不同 symbol/年 不互相阻塞）+ 跨进程 O_EXCL 锁文件。
# 崩溃残留的锁文件由 mtime 老化接管（`_LOCK_STALE_SECONDS`），避免永久卡死。
_PATH_LOCKS: dict[str, threading.Lock] = {}
_PATH_LOCKS_GUARD = threading.Lock()
_LOCK_WAIT_SECONDS = 60.0     # 等锁上限：超过则明确报错，绝不静默丢行
_LOCK_STALE_SECONDS = 300.0   # 锁文件 mtime 超过此值视为崩溃残留，可接管
_LOCK_POLL_SECONDS = 0.05


def _path_lock(target: Path) -> threading.Lock:
    """取目标文件对应的进程内锁（按路径分片，避免全局串行）。"""
    key = str(target)
    with _PATH_LOCKS_GUARD:
        lk = _PATH_LOCKS.get(key)
        if lk is None:
            lk = threading.Lock()
            _PATH_LOCKS[key] = lk
        return lk


@contextmanager
def _rw_lock(target: Path) -> Iterator[None]:
    """读-改-写独占：进程内 threading.Lock + 跨进程锁文件。

    超时抛 :class:`TimeoutError`（带锁路径与处置建议）——**宁可显式失败也不静默
    覆盖别人的新增行**。注意锁文件与被保护文件同目录，命名 `.<name>.rwlock`。
    """
    lock_path = target.with_name(f".{target.name}.rwlock")
    inner = _path_lock(target)
    if not inner.acquire(timeout=_LOCK_WAIT_SECONDS):
        raise TimeoutError(
            f"等待写锁超时（{_LOCK_WAIT_SECONDS}s）：{target}；"
            f"同进程内有其它写者长期占用，请检查是否有卡死的写入任务")
    fd: int | None = None
    try:
        deadline = time.monotonic() + _LOCK_WAIT_SECONDS
        while True:
            try:
                lock_path.parent.mkdir(parents=True, exist_ok=True)
                fd = os.open(lock_path, os.O_CREAT | os.O_EXCL | os.O_RDWR)
                try:
                    os.write(fd, f"{os.getpid()}\n".encode())
                except OSError:      # 写入 pid 失败不影响互斥语义
                    pass
                break
            except FileExistsError:
                # 崩溃残留：锁文件够老则接管（删除后重试）
                try:
                    age = time.time() - lock_path.stat().st_mtime
                except OSError:
                    age = 0.0
                if age > _LOCK_STALE_SECONDS:
                    logger.warning(
                        f"[parquet_store] 接管陈旧的写锁（{age:.0f}s > "
                        f"{_LOCK_STALE_SECONDS:.0f}s，疑似崩溃残留）：{lock_path}")
                    try:
                        lock_path.unlink()
                    except OSError as e:
                        logger.debug(f"[parquet_store] 删除陈旧锁失败: {e!r}")
                    continue
                if time.monotonic() > deadline:
                    raise TimeoutError(
                        f"等待跨进程写锁超时（{_LOCK_WAIT_SECONDS}s）：{lock_path}。"
                        f"另一个进程正在写同一分区（例如 CLI 与夜间任务并发）。"
                        f"若确认对方已崩溃，删除该锁文件即可重试。") from None
                time.sleep(_LOCK_POLL_SECONDS)
        yield
    finally:
        if fd is not None:
            try:
                os.close(fd)
            except OSError:
                pass
            try:
                lock_path.unlink()
            except OSError as e:
                logger.debug(f"[parquet_store] 释放写锁失败: {e!r}")
        inner.release()


def _manifest_path() -> Path:
    return get_settings().DATA_ROOT / ".manifest.json"


def _manifest_get() -> dict:
    """进程内 manifest 缓存（惰性从磁盘加载；损坏/缺失按空处理）。"""
    global _manifest
    with _MANIFEST_LOCK:
        if _manifest is None:
            try:
                p = _manifest_path()
                _manifest = json.loads(p.read_text(encoding="utf-8")) if p.exists() else {}
            except Exception as e:  # noqa: BLE001 损坏则重建
                logger.warning(f"[manifest] load failed, will rebuild: {e!r}")
                _manifest = {}
        return _manifest


def _manifest_flush_locked(force: bool = False) -> None:
    """节流落盘（原子写）：批量抓取场景下每只都写盘代价过高，5s 合并一次。"""
    global _manifest_last_flush
    now = time.time()
    if not force and now - _manifest_last_flush < _MANIFEST_FLUSH_MIN_INTERVAL:
        return
    try:
        _manifest_path().parent.mkdir(parents=True, exist_ok=True)
        tmp = _manifest_path().with_suffix(f".{uuid.uuid4().hex[:8]}.tmp")
        tmp.write_text(json.dumps(_manifest, ensure_ascii=False,
                                  separators=(",", ":")), encoding="utf-8")
        os.replace(tmp, _manifest_path())
        _manifest_last_flush = now
    except Exception as e:  # noqa: BLE001 落盘失败只影响下次冷启动的索引质量
        logger.debug(f"[manifest] flush failed: {e!r}")


def manifest_invalidate() -> None:
    """丢弃进程内 manifest（旁路写文件后调用；下次读取自动重建）。

    同时清空 ``_audited`` 自愈账本（缺陷 C / P1-41）：显式失效意味着调用方已知
    数据变更，下次读取应做一次权威全扫；重置账本也让此后再出现的旁路写入能立即
    被增长判据捕获（无需等待 ``_AUDIT_MIN_INTERVAL`` 节流窗口过去）。
    """
    global _manifest
    with _MANIFEST_LOCK:
        _manifest = None
        _audited.clear()
        try:
            _manifest_path().unlink(missing_ok=True)
        except OSError:
            pass


def _as_date_scalar(value: Any) -> date:
    """parquet 日期标量 -> ``date``（datetime / date / ISO 字符串统一口径）。"""
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    return date.fromisoformat(str(value)[:10])


def _footer_date_range(pf: Any, idx: int, date_col: str) -> tuple[date, date] | None:
    """从 row group statistics 取日期区间（**零解码**）；不可用时返回 ``None``。

    ``md.row_group(g).column(i)`` 用的是 parquet **leaf 序号**，含嵌套列时与
    ``schema_arrow.names`` 的下标会错位 ⇒ 先校验 ``path_in_schema``，对不上宁可
    放弃快路径，也不拿别的列的统计冒充日期区间（同 api 层 _date_range_from_stats）。
    """
    md = pf.metadata
    if md.num_row_groups == 0:
        return None
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
                d = _as_date_scalar(raw)
                if lo is None or d < lo:
                    lo = d
                if hi is None or d > hi:
                    hi = d
    except Exception:  # noqa: BLE001 统计形态异常：回退解码列，行数不受影响
        return None
    if lo is None or hi is None:
        return None
    return lo, hi


def _decode_date_range(f: Path, date_col: str) -> tuple[date, date] | None:
    """回退路径：解码 ``date_col`` 求 min/max（仅当 footer 统计不可用时）。"""
    try:
        col = pl.read_parquet(f, columns=[date_col])[date_col]
    except Exception:  # noqa: BLE001 列不可读：仅区间缺失，行数仍有效
        return None
    if col.len() == 0 or col.null_count() == col.len():
        return None
    try:
        return _as_date_scalar(col.min()), _as_date_scalar(col.max())
    except Exception:  # noqa: BLE001 值不可解析：仅区间缺失
        return None


def _parquet_rows_and_range(f: Path, date_col: str | None) -> tuple[int, str | None, str | None]:
    """一个 pyarrow 句柄同时取 ``(行数, first, last)``（区间为 ISO 字符串）。

    行数走 footer 元数据；日期区间**优先 row group statistics（零解码）**，统计缺失
    时回退解码该列。数据集没有映射的日期列时区间为 ``None``（**不报错**）。
    """
    import pyarrow.parquet as pq

    pf = pq.ParquetFile(f)  # 可读性探针：损坏文件照常抛异常（不吞成 0）
    rows = pf.metadata.num_rows
    if not date_col:
        return rows, None, None
    try:
        idx = pf.schema_arrow.names.index(date_col)
    except ValueError:
        return rows, None, None
    rng = _footer_date_range(pf, idx, date_col) or _decode_date_range(f, date_col)
    if rng is None:
        return rows, None, None
    return rows, rng[0].isoformat(), rng[1].isoformat()


def _scan_files_entry(files: Iterable[Path], date_col: str | None) -> dict:
    """汇总一批 parquet 的 rows 与日期区间（单文件损坏按 0 计并留 WARNING）。

    2026-09-30 全检 P0-3 修复：原实现**逐文件串行** ``pq.ParquetFile`` 扫 footer，
    实测 36500 个文件需 **283.8s**，远超 240s 全局兜底 ⇒ ``/datacenter/datasets``
    必然 504（日志两次实锤）。改为 8 路并行后实测 **8.3s（34×）**。

    并发安全性：``_parquet_rows_and_range`` 只读文件元数据、不共享可变状态，
    polars 的 footer 读取是线程安全的；聚合（sum/min/max）与顺序无关，
    但仍用 ``pool.map`` 保持**输入顺序**，使 WARNING 日志顺序确定、便于比对。
    """
    files = list(files)
    if not files:
        return {"rows": 0}

    def _one(f: Path) -> tuple[Path, tuple[int, str | None, str | None] | None]:
        try:
            return f, _parquet_rows_and_range(f, date_col)
        except Exception as e:  # noqa: BLE001 坏文件按 0 计，不阻塞扫描
            logger.warning(f"[manifest] 文件不可读已跳过: {f}（{e!r}）")
            return f, None

    rows = 0
    lo: str | None = None
    hi: str | None = None
    with ThreadPoolExecutor(max_workers=_SCAN_MAX_WORKERS,
                            thread_name_prefix="manifest-scan") as pool:
        for _f, parsed in pool.map(_one, files):
            if parsed is None:
                continue
            n, f_lo, f_hi = parsed
            rows += n
            if f_lo and (lo is None or f_lo < lo):
                lo = f_lo
            if f_hi and (hi is None or f_hi > hi):
                hi = f_hi
    entry: dict = {"rows": rows}
    if lo:
        entry["first"] = lo
    if hi:
        entry["last"] = hi
    return entry


def _scan_symbol_entry(sym_dir: Path, date_col: str | None) -> dict:
    """扫描一个 symbol 目录的全部年份文件，返回 ``{rows, first, last}``。"""
    return _scan_files_entry(sorted(sym_dir.glob("*.parquet")), date_col)


def _manifest_record(dataset: str, symbol: str, df: pl.DataFrame) -> None:
    """写路径成功后增量登记（rows + 日期区间，**按 symbol 汇总**）。

    ⚠️ 2026-09-29 修复（原缺陷，正确性 P0）：本函数被 ``write_partition`` 以**单年**
    合并帧调用，旧实现拿 ``df`` 直接覆盖 ``rows`` / ``first`` / ``last`` ⇒ 每写一年就把
    该 symbol 的行数缩成"该年行数"、把起始日期前移到"该年最早"（实测生产 manifest 里
    ``daily_bar.first`` 被写成 2026-01-05，而磁盘数据实际始于 2022）⇒ /overview 的
    ``daily_bar_range`` 与 /datasets 全扫结果**不一致**。

    现在改为按 symbol 重扫其**全部年份 footer**（零解码，代价 ≈ 该 symbol 的文件数）
    后登记，行数与区间都与磁盘事实一致；再与旧条目取并集（防跨进程并发写导致旧值
    更新）。重扫失败时退回用 ``df`` 登记。
    """
    date_col = DATASET_DATE_COLUMN.get(dataset, DEFAULT_DATE_COLUMN)
    sym_dir = get_settings().DATA_ROOT / dataset / f"symbol={symbol}"
    entry: dict | None = None
    if sym_dir.exists():
        try:
            scanned = _scan_symbol_entry(sym_dir, date_col)
            if scanned["rows"] or not df.height:
                entry = scanned
        except Exception as e:  # noqa: BLE001 目录不可读 → 退回 df 口径
            logger.debug(f"[manifest] 重扫 {dataset}/{symbol} 失败，退回 df 口径: {e!r}")
    if entry is None:
        entry = {"rows": df.height}
        if df.height and "date" in df.columns:
            try:
                entry["first"] = str(df["date"].min())[:10]
                entry["last"] = str(df["date"].max())[:10]
            except Exception:  # noqa: BLE001 日期列异常不影响 rows 登记
                pass
    m = _manifest_get().setdefault(dataset, {})
    old = m.get(symbol)
    if isinstance(old, dict):
        if old.get("first") and entry.get("first"):
            entry["first"] = min(old["first"], entry["first"])
        elif old.get("first"):
            entry["first"] = old["first"]
        if old.get("last") and entry.get("last"):
            entry["last"] = max(old["last"], entry["last"])
        elif old.get("last"):
            entry["last"] = old["last"]
    m[symbol] = entry
    with _MANIFEST_LOCK:
        _manifest_flush_locked()


def _manifest_scan_dataset(dataset: str) -> dict[str, dict]:
    """全量扫描单数据集（footer 读行数 + 日期区间，零解码优先）。

    - ``symbol=`` 分区数据集：逐 symbol 汇总 rows 与 ``[first, last]``；
    - 非分区数据集（无 ``symbol=`` 目录，如 features/predictions/screener：parquet
      直接落在根目录或 ``version=*`` 等中间目录下）：统一登记为 ``__all__`` 一条；
    - 日期列按 :data:`DATASET_DATE_COLUMN` 映射（announcements -> pub_date）；
      数据集没有该列时区间留空，**不报错**。
    """
    root = get_settings().DATA_ROOT / dataset
    out: dict[str, dict] = {}
    if not root.exists():
        return out
    date_col = DATASET_DATE_COLUMN.get(dataset, DEFAULT_DATE_COLUMN)
    sym_dirs = [d for d in root.iterdir()
                if d.is_dir() and d.name.startswith("symbol=")]
    if sym_dirs:
        for sym_dir in sym_dirs:
            out[sym_dir.name.split("=", 1)[1]] = _scan_symbol_entry(sym_dir, date_col)
        return out
    files = sorted(root.rglob("*.parquet"))
    if not files:
        return out
    out["__all__"] = _scan_files_entry(files, date_col)
    return out


def manifest_scan_and_store(datasets: Iterable[str]) -> dict[str, dict[str, dict]]:
    """对给定数据集做一次全量 footer 扫描并原子回写 manifest（含格式版本标记）。

    供统计读路径在 manifest 缺失 / 格式过期时重建索引：一次扫描即得行数与日期区间，
    与 :func:`_manifest_scan_dataset` 同源 ⇒ 重建后的 manifest 与磁盘事实一致，
    后续读取即可走零扫描快路径。数据集不存在时写入空条目（``{}``）而非缺席，
    以免读取方每次都判"缺失"而反复重扫。
    """
    m = _manifest_get()
    out: dict[str, dict[str, dict]] = {}
    for ds in datasets:
        entries = _manifest_scan_dataset(ds)
        m[ds] = entries
        out[ds] = entries
    m[MANIFEST_SCHEMA_KEY] = MANIFEST_SCHEMA
    with _MANIFEST_LOCK:
        _manifest_flush_locked(force=True)
    return out


def _symbol_dir_keys(dataset: str) -> tuple[set[str], int] | None:
    """**一次** iterdir 同时得到磁盘 ``symbol=`` 目录的 key 集合与数量。

    返回 ``None`` 表示数据集根目录不可用（不存在 / 不可读）——与"存在但没有
    symbol= 目录"（``(set(), 0)``）是两种不同口径，调用方不得混同。
    不读 parquet footer，故可安全用于热路径。
    """
    root = get_settings().DATA_ROOT / dataset
    if not root.exists():
        return None
    keys: set[str] = set()
    try:
        for child in root.iterdir():
            if child.is_dir() and child.name.startswith("symbol="):
                keys.add(child.name.split("=", 1)[1])
    except OSError:
        return None
    return keys, len(keys)


def list_symbols_with_data(dataset: str, skip_empty: bool = True) -> set[str]:
    """数据集已落库符号集合（manifest 优先；缺失/不一致时扫描并回写）。

    供 read_all_symbols 与批量抓取脚本的"已有分区跳过"共用。

    一致性自愈（缺陷 C，P0）：manifest 只在写路径 ``_manifest_record`` 增量登记，
    凡绕过 write_partition 的旁路写入（离线重建 / 扩容脚本）都不会登记，且此前
    仅在 dataset 键**缺失**时才全量重扫 —— 一旦键存在就永不修复，导致磁盘有数据
    的标的被静默排除在标的池之外（实测 daily_bar_hfq 磁盘 2500 只、manifest 仅
    1729 只 → features 只覆盖 1729）。现在增加「manifest 条目数 < 磁盘 symbol=
    目录数」的一致性校验：命中即强制重扫一次并回写（每 dataset 每进程至多一次，
    见 ``_audited``），并打印 WARNING 留痕，杜绝静默缩水。

    条目真实性过滤（2026-09-29）：manifest 里可能存在**磁盘上没有对应
    ``symbol=<key>`` 目录**的条目 —— 非分区数据集（features/predictions/screener）
    的合成汇总键 ``__all__`` 就是这种（它只是"整个数据集"的聚合记录，不是一只标的）。
    本函数按"该 key 在磁盘上有对应目录"求交后返回，故 ``__all__`` 不会被当成标的；
    而 universe_daily / announcements 的 ``symbol=__all__`` 是**真实存在的目录**，
    会被保留（判据是目录存在性，**不硬编码排除任何 key 名**）。
    过滤复用上面**同一次** iterdir 的 key 集合，绝不逐 key 调 ``is_dir()``
    （否则同步路径的"已有分区跳过"会对 2499 个 key 各做一次系统调用）。
    """
    m = _manifest_get()
    ds = m.get(dataset)
    if not isinstance(ds, dict):
        # 缺失 / 损坏（非 dict）都按"无条目"处理。注意 manifest 顶层还有
        # ``__schema__`` 这类非数据集键，绝不能被当成数据集条目参与 len() 比较。
        ds = None
    on_disk = _symbol_dir_keys(dataset)
    if ds is None:
        ds = _manifest_scan_dataset(dataset)
        with _MANIFEST_LOCK:
            m[dataset] = ds
            _manifest_flush_locked(force=True)
    else:
        n_man = len(ds)
        n_dir = on_disk[1] if on_disk is not None else 0
        # P1-41：判据从"一次性预算"改为"增长驱动 + 节流"。见 _audited 的注释。
        rec = _audited.get(dataset)
        grew = rec is None or n_dir > rec[0]
        throttled = rec is not None and (time.monotonic() - rec[1]) < _AUDIT_MIN_INTERVAL
        if n_man < n_dir and grew and not throttled:
            logger.warning(
                f"[manifest] dataset={dataset} manifest 条目={n_man} "
                f"磁盘目录={n_dir}，已强制重扫（存在未登记的旁路写入）")
            ds = _manifest_scan_dataset(dataset)
            with _MANIFEST_LOCK:
                m[dataset] = ds
                _manifest_flush_locked(force=True)
            # 记下**本次核对时的目录数**：目录数不再增长即不再重扫（合法空目录
            # 导致的 N_man<N_dir 不会被反复扫），但**将来新增目录会再次触发**——
            # 这正是 P1-41 修复点（原实现记入集合后永久静默缺失）。
            _audited[dataset] = (n_dir, time.monotonic())
    if on_disk is not None:
        # 只返回"磁盘上确有 symbol=<key> 目录"的条目（合成汇总键 __all__ 在此被
        # 剔除；真实存在的 symbol=__all__ 目录则保留）。on_disk 为 None（根目录
        # 不可用）时口径未知，退回信任 manifest，避免因一次 OSError 静默丢标的。
        disk_keys = on_disk[0]
        ds = {k: v for k, v in ds.items() if k in disk_keys}
    if skip_empty:
        return {sym for sym, e in ds.items() if e.get("rows", 0) > 0}
    return set(ds)


# ---------- 小工具 ----------
def _weekend_rewind(today: date) -> date:
    """无日历可用时的兜底：周末回退到周五（不识别节假日，仅降级路径）。"""
    if today.weekday() == 5:  # Saturday
        return today - timedelta(days=1)
    if today.weekday() == 6:  # Sunday
        return today - timedelta(days=2)
    return today


def today_trade_date_or_last() -> date:
    """最近交易日：优先按真实交易日历回溯，日历不可用时降级为周末回退。

    历史问题（审计 B7）：此前是纯占位实现——直接返回今天、仅周末回退周五，
    完全不认节假日与调休，于是国庆/春节等长假期间 trade_date 会指向
    根本不存在的交易日，前端却拿它当"今日"判定，产出假状态。

    现在：交易日历（SQLite trade_calendar，数千个真实交易日）可用时用
    ``domain.calendar.prev_trade_day`` 精确回溯；日历为空（DB 缺失/未初始化）
    才退回周末回退，并打 warning 明示降级。
    """
    from .calendar_store import get_calendar
    from ..domain.calendar import prev_trade_day

    today = date.today()
    cal = get_calendar()
    if len(cal) == 0:
        logger.warning("[parquet_store] 交易日历为空，"
                       "today_trade_date_or_last 降级为周末回退（不识别节假日）")
        return _weekend_rewind(today)
    try:
        # prev_trade_day 从 d-1 起向前找，故传 tomorrow 以覆盖"今天就是交易日"
        return prev_trade_day(today + timedelta(days=1), cal)
    except ValueError as e:
        # 超长假期或日历区间不足，20 天窗口内未命中
        logger.warning(f"[parquet_store] 日历回溯失败，降级为周末回退: {e}")
        return _weekend_rewind(today)


def missing_columns(df: pl.DataFrame, needed: "list[str] | tuple[str, ...]") -> list[str]:
    """返回 ``needed`` 中**不在** ``df`` 里的列名（顺序同 ``needed``）。

    [AQP 第 10 轮] 数据集是按年分区的"追加写 + schema 漂移"结构：
    ``read_symbol_dataset(columns=...)`` 与直接 ``pl.read_parquet`` 都**容忍**缺列
    （投影读会退化为"可用列子集"，文档也明说如此），于是"缺列"会一路穿到消费方的
    ``df["close"]`` / ``pl.col("board")`` 才抛 ``ColumnNotFoundError`` —— 被全局
    处理器归成 **code=50000（未分类系统异常）**，而它其实是"本地数据不完整"这种
    本该 ``51001`` 的可解释降级。本函数是**纯判据**，供调用方自行决定降级方式
    （跳过该标的 / 关掉该段筛选 / 报 ERR_DATA_EMPTY），避免四处各写一套列检查。
    """
    return [c for c in needed if c not in df.columns]


def require_columns(df: pl.DataFrame, needed: "list[str] | tuple[str, ...]",
                    *, dataset: str, context: str = "") -> None:
    """缺列即 ``ERR_DATA_EMPTY(51001)``；消息精确点名缺了哪些列。

    与 :func:`missing_columns` 的分工：**没有可用降级路径**时用本函数（例如
    `desk/capacity` 的成交额必须由 close×volume 得出，缺任一列就无法计算），
    有降级路径时用 `missing_columns` 自行分支（例如 watchlist 的单标的 K 线
    可以退回外部源）。
    """
    miss = missing_columns(df, needed)
    if miss:
        where = dataset + (f"（{context}）" if context else "")
        raise AQPException(
            ERR_DATA_EMPTY,
            f"{where} 缺少必需列 {miss}（schema 漂移），请重建该数据集")


def as_py_date(value: Any) -> date:
    """把 polars 标量收敛为 ``date``。

    polars 的 ``Series.min()/max()`` 静态类型是标量联合类型
    （``int | float | Decimal | date | time | timedelta | str | bytes | list | None``），
    直接参与日期运算会让类型检查完全失效，也掩盖 date/datetime 混用问题。

    Args:
        value: date / datetime / ISO 字符串等 parquet 标量。

    Returns:
        对应的 ``date``。

    Raises:
        TypeError: 值既不是日期类也不能解析为 ISO 日期。
    """
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    try:
        return date.fromisoformat(str(value)[:10])
    except ValueError as e:
        raise TypeError(f"无法收敛为 date: {value!r}") from e


def as_py_float(value: Any) -> float:
    """把 polars 数值标量收敛为 ``float``（None → NaN，不造数）。

    Args:
        value: int / float / Decimal / None 等 parquet 标量。

    Returns:
        浮点值；``None`` 返回 ``float('nan')``，与项目 NaN 缺失语义一致。

    Raises:
        TypeError: 值不是数值类标量（字符串等）。
    """
    if value is None:
        return float("nan")
    if isinstance(value, bool):
        return float(int(value))
    if isinstance(value, (int, float, Decimal)):
        return float(value)
    raise TypeError(f"非数值标量: {type(value).__name__}")


def _year_of(d: date | datetime | str) -> str:
    """提取年份字符串，用于按年分区路径。"""
    if isinstance(d, (date, datetime)):
        return str(d.year)
    if isinstance(d, str):
        return d[:4]
    raise TypeError(f"bad date type: {type(d)}")


def _ensure_dir(p: Path) -> None:
    p.mkdir(parents=True, exist_ok=True)


# ---------- 多文件投影读（列集演进容忍） ----------
def read_parquet_columns(files: list[Path], columns: list[str],
                         *, date_cast: bool = True) -> pl.DataFrame:
    """跨文件投影读：跳过缺失投影列的分区（历史列集演进容忍）+ date 统一 cast。

    背景：pl.read_parquet(多文件, columns=...) 要求各文件 schema 完全一致，
    任一旧分区列集不足即整体崩溃（projection index out of bounds）。本辅助
    逐文件 footer 校验后投影，慢于单次扫描（每文件一次元数据读），但对
    「13 列新分区 + 精简旧分区」混合数据集稳定；date 列混存 Date/Datetime
    时统一 cast（与 _normalize_date 写入口径闭环）。

    Args:
        files: parquet 文件列表。
        columns: 投影列（须全部存在于目标文件，否则该文件被跳过）。
        date_cast: 是否把 date 列 cast 为 pl.Date。

    Returns:
        合并后的 DataFrame（空集时返回空 DataFrame）。

    2026-09-30 全检 P0-4 修复：原实现**逐文件串行** schema 读 + 投影读，
    实测 10924 个文件需 **19.8s**；改为 8 路并行后同口径耗时降至约 **2.5s**
    （优于单次 ``scan_parquet`` 的 3.3s，且**保留了"跳过列集不足的分区"语义** ——
    这是本项目"13 列新分区 + 精简旧分区"混合数据集所必需的，故不采用
    ``scan_parquet`` 整批扫描：polars 1.6 尚无 ``missing_columns`` 参数，
    整批扫会因 schema 不一致直接抛错）。

    并发安全性：每个任务只碰自己的 ``f``，结果按输入顺序回填，
    故 ``pl.concat`` 的行序与串行版**完全一致**。
    """
    if not files:
        return pl.DataFrame()

    def _one(f: Path) -> pl.DataFrame | None:
        try:
            schema = pl.read_parquet_schema(f)
            if any(c not in schema for c in columns):
                return None
            df = pl.read_parquet(f, columns=columns)
        except Exception as e:  # noqa: BLE001 坏文件跳过（不造数，行数如实缩水）
            logger.debug(f"[parquet_store] skip unreadable {f.name}: {e!r}")
            return None
        if date_cast and "date" in df.columns and df.schema["date"] != pl.Date:
            df = df.with_columns(pl.col("date").cast(pl.Date))
        return df

    with ThreadPoolExecutor(max_workers=_SCAN_MAX_WORKERS,
                            thread_name_prefix="parquet-read") as pool:
        out: list[pl.DataFrame] = [df for df in pool.map(_one, files) if df is not None]
    return pl.concat(out, how="vertical_relaxed") if out else pl.DataFrame()


# ---------- 路径约定 ----------
def path_for_year(dataset: str, symbol: str, year: int | str) -> Path:
    """按年分区路径：DATA_ROOT/dataset/symbol=XXX/year=YYYY.snappy.parquet。"""
    base = get_settings().DATA_ROOT / dataset / f"symbol={symbol}"
    _ensure_dir(base)
    return base / f"year={year}.snappy.parquet"


# ---------- schema 对齐（跨年/跨批次分区列集可能演进，如 P1-2 的 source 列） ----------
def _align_concat(dfs: list[pl.DataFrame]) -> pl.DataFrame:
    """按列集 union 对齐后 concat：缺失列补 null（dtype 取自任一已有分区）。"""
    if len(dfs) == 1:
        return dfs[0]
    union: dict[str, pl.DataType] = {}
    for df in dfs:
        for c, dt in df.schema.items():
            union.setdefault(c, dt)
    aligned: list[pl.DataFrame] = []
    for df in dfs:
        missing = [pl.lit(None, dt).alias(c)
                   for c, dt in union.items() if c not in df.columns]
        if missing:
            df = df.with_columns(missing)
        aligned.append(df.select(list(union)))
    return pl.concat(aligned, how="vertical_relaxed")


# ---------- 读 ----------
def read_symbol_dataset(
    dataset: str,
    symbol: str,
    start: date | None = None,
    end: date | None = None,
    columns: list[str] | None = None,
) -> pl.DataFrame:
    """读取某只标的的全部或区间数据（跨年份自动拼接、按 date 升序）。

    分区间逐一读取并做 schema 对齐（容忍各年分区列集差异），date 过滤在内存中完成。

    :param columns: L3 列裁剪（opt-in）：只投影这些列（逐文件 footer 校验，
        缺列文件按可用列子集读）。IO 随列数线性下降（features 44 列取 8~10 列
        时 IO 降 75%+）；None = 全列读（向后兼容）。
    """
    base = get_settings().DATA_ROOT / dataset / f"symbol={symbol}"
    if not base.exists():
        return pl.DataFrame()
    files = sorted(base.glob("year=*.parquet"))
    if not files:
        return pl.DataFrame()
    if columns:
        dfs = []
        for f in files:
            schema = pl.read_parquet_schema(f)
            cols = [c for c in columns if c in schema]
            dfs.append(pl.read_parquet(f, columns=cols))
        df = _align_concat(dfs)
    else:
        dfs = [pl.read_parquet(f) for f in files]
        df = _align_concat(dfs)
    if df.is_empty():
        return df
    if "date" in df.columns:
        df = df.with_columns(pl.col("date").cast(pl.Date))
        if start is not None:
            df = df.filter(pl.col("date") >= start)
        if end is not None:
            df = df.filter(pl.col("date") <= end)
        df = df.sort("date")
    return df


def read_symbol_year(dataset: str, symbol: str, year: int | str) -> pl.DataFrame:
    """读取某只标的某一年的数据（单文件最快路径）。"""
    target = path_for_year(dataset, symbol, year)
    if not target.exists():
        return pl.DataFrame()
    df = pl.read_parquet(target)
    if "date" in df.columns:
        df = df.with_columns(pl.col("date").cast(pl.Date)).sort("date")
    return df


def read_all_symbols(dataset: str, skip_empty: bool = True) -> list[str]:
    """枚举某 dataset 下所有已落地的 symbol（L2-3：manifest 优先，免逐目录 glob）。

    :param skip_empty: 跳过行数为 0 的符号（被隔离标的留空目录，
        不跳过会让下游读到空 DataFrame，产生"在池子里实际没数据"的静默错误）。
    """
    root = get_settings().DATA_ROOT / dataset
    if not root.exists():
        return []
    return sorted(list_symbols_with_data(dataset, skip_empty=skip_empty))


def _normalize_date(df: pl.DataFrame) -> pl.DataFrame:
    """归一化 date 列为 pl.Date，避免 Datetime/Date 混合分区导致 Schema 冲突。"""
    if "date" in df.columns and df.schema["date"] != pl.Date:
        df = df.with_columns(pl.col("date").cast(pl.Date))
    return df


# ---------- 写（原子替换，按年分区） ----------
def write_partition(dataset: str, symbol: str, d: date, df: pl.DataFrame,
                    dedup_keys: tuple[str, ...] = ("date",)) -> Path:
    """写入数据到对应年份分区（增量更新路径）。

    逻辑：读取该年已有文件 -> concat 新数据 -> 按 dedup_keys 去重(保留新行) -> 原子重写。
    多标的合表（如 universe_daily，symbol=__all__）需传 dedup_keys=("date", "symbol")。

    ⚠️ 审计 P1-40：整个「读-改-写」必须在 `_rw_lock` 内完成（进程内 + 跨进程），
    否则两个写者各持旧快照时会**静默丢失对方的新增行**（实测 3 行变 2 行，
    且 manifest 记的是"丢失后的事实"，事后无痕）。
    """
    if df.is_empty():
        raise ValueError("write_partition: df 为空")
    df = _normalize_date(df)
    year = _year_of(d)
    target = path_for_year(dataset, symbol, year)
    with _rw_lock(target):
        if target.exists():
            existing = pl.read_parquet(target)
            df = _align_concat([existing, df])
            if all(k in df.columns for k in dedup_keys):
                df = df.unique(subset=list(dedup_keys), keep="last").sort(list(dedup_keys))
        _atomic_write_parquet(target, df)
    _manifest_record(dataset, symbol, df)
    logger.trace(f"parquet write ok: {target} rows={len(df)}")
    return target


def write_year_batch(dataset: str, symbol: str, year: int | str, df: pl.DataFrame) -> Path:
    """整年批量写入（首次拉取或历史回补时使用，直接覆盖该年文件）。

    ⚠️ 审计 P1-40：覆盖写同样要持锁 —— 否则它会与并发的 `write_partition`
    （读-改-写）交错：本函数按旧快照整年覆盖时，会把对方刚 merge 进来的行抹掉。
    """
    if df.is_empty():
        raise ValueError("write_year_batch: df 为空")
    df = _normalize_date(df)
    target = path_for_year(dataset, symbol, year)
    with _rw_lock(target):
        _atomic_write_parquet(target, df)
    _manifest_record(dataset, symbol, df)
    logger.trace(f"parquet year-batch write ok: {target} rows={len(df)}")
    return target


# ---------- 原子写 ----------
# 统一压缩（路线图 L3）：zstd level 7 较默认 snappy 体积约 -30~40%，读性能持平。
# 仅影响新写入，存量文件不重写；读取按 footer 自描述，新旧压缩混存兼容。
# polars 的 compression 参数是 Literal 枚举，显式标注避免下游类型检查失败
PARQUET_COMPRESSION: Literal["zstd"] = "zstd"
PARQUET_COMPRESSION_LEVEL = 7


def atomic_write_parquet(target: Path, df: "pl.DataFrame | pd.DataFrame") -> None:
    """公开原子写入口（Task 10 整改）：所有旁路 parquet 落盘统一走此处。

    接受 polars / pandas DataFrame；tmp -> fsync -> os.replace，压缩统一
    zstd-7。中途崩溃不留半写文件（守卫测试 test_no_bypass_parquet_writes）。
    """
    if isinstance(df, pd.DataFrame):
        df = pl.from_pandas(df)
    _atomic_write_parquet(Path(target), df)


def _atomic_write_parquet(target: Path, df: pl.DataFrame) -> None:
    """tmp -> fsync -> os.replace 原子替换，防止进程中断产生半写文件。"""
    tmp = target.with_suffix(f".{uuid.uuid4().hex[:8]}.tmp")
    try:
        df.write_parquet(tmp, compression=PARQUET_COMPRESSION,
                         compression_level=PARQUET_COMPRESSION_LEVEL)
        _fsync_and_replace(tmp, target)
    finally:
        # 任何异常下都清理残留 tmp，避免垃圾文件堆积
        if tmp.exists():
            tmp.unlink(missing_ok=True)


def _fsync_and_replace(tmp: Path, target: Path) -> None:
    """fsync 刷盘 + 原子 rename（Windows NTFS 支持原子 replace）。

    注意：Windows 上 os.fsync 要求句柄可写，必须用 O_RDWR
    （Linux 允许 O_RDONLY 句柄 fsync，直接照抄会踩 Errno 9）。
    """
    fd = os.open(tmp, os.O_RDWR)
    try:
        os.fsync(fd)
    finally:
        os.close(fd)
    os.replace(tmp, target)
