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
from datetime import date, datetime, timedelta
from decimal import Decimal
from pathlib import Path
from typing import Any, Literal

import polars as pl
import pandas as pd
from loguru import logger

from ..core.config import get_settings

__all__ = [
    "list_symbols_with_data",
    "read_parquet_columns",
    "manifest_invalidate",
    "path_for",
    "path_for_year",
    "read_all_symbols",
    "read_symbol_dataset",
    "read_symbol_year",
    "today_trade_date_or_last",
    "write_partition",
    "write_whole_symbol",
    "write_year_batch",
]


# ---------- 数据集 manifest（L2-3：免 glob/免 footer 的符号与行数索引） ----------
# DATA_ROOT/.manifest.json：{dataset: {symbol: {"rows": n, "first": d, "last": d}}}
# - 写路径（write_partition/write_year_batch/write_whole_symbol）增量更新（内存+节流落盘）；
# - 读路径（read_all_symbols / list_symbols_with_data）优先取 manifest，
#   首次无 manifest 时全量扫描一次（pyarrow footer 读行数）并回写；
# - 定位是「读优化缓存」而非事实源：旁路写文件（repair 直写等）不感知，
#   manifest_invalidate() 供旁路写后显式失效。
_MANIFEST_LOCK = threading.Lock()
_manifest: dict | None = None
_manifest_last_flush = 0.0
_MANIFEST_FLUSH_MIN_INTERVAL = 5.0

# 一致性自愈审计（缺陷 C）：每个 dataset 每进程最多触发一次强制重扫。
# 重扫后若仍存在「manifest 条目 < 磁盘 symbol= 目录」，说明差异来自合法空目录
# （rows=0），记入本集合后不再重扫、不再刷 warning（避免热路径反复全扫）。
_audited: set[str] = set()


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

    同时清空 ``_audited`` 自愈预算（缺陷 C）：显式失效意味着调用方已知数据变更，
    下次读取应做一次权威全扫；重置预算可让此后再出现的旁路写入仍能得到一次自愈。
    """
    global _manifest
    with _MANIFEST_LOCK:
        _manifest = None
        _audited.clear()
        try:
            _manifest_path().unlink(missing_ok=True)
        except OSError:
            pass


def _manifest_record(dataset: str, symbol: str, df: pl.DataFrame) -> None:
    """写路径成功后增量登记（rows + 日期区间）。"""
    m = _manifest_get().setdefault(dataset, {})
    entry: dict = {"rows": df.height}
    if df.height and "date" in df.columns:
        try:
            entry["first"] = str(df["date"].min())[:10]
            entry["last"] = str(df["date"].max())[:10]
        except Exception:  # noqa: BLE001 日期列异常不影响 rows 登记
            pass
    m[symbol] = entry
    with _MANIFEST_LOCK:
        _manifest_flush_locked()


def _manifest_scan_dataset(dataset: str) -> dict[str, dict]:
    """全量扫描单数据集（footer 读行数，一次性成本）；日期区间留空由写路径补齐。"""
    import pyarrow.parquet as pq

    root = get_settings().DATA_ROOT / dataset
    out: dict[str, dict] = {}
    if not root.exists():
        return out
    for sym_dir in root.iterdir():
        if not sym_dir.is_dir() or not sym_dir.name.startswith("symbol="):
            continue
        rows = 0
        for f in sym_dir.glob("year=*.parquet"):
            try:
                rows += pq.ParquetFile(f).metadata.num_rows
            except Exception:  # noqa: BLE001 坏文件按 0 计，不阻塞扫描
                continue
        out[sym_dir.name.split("=", 1)[1]] = {"rows": rows}
    return out


def _count_symbol_dirs(dataset: str) -> int:
    """廉价统计磁盘上 ``symbol=`` 目录数（一次 iterdir，不读 parquet footer）。"""
    root = get_settings().DATA_ROOT / dataset
    if not root.exists():
        return 0
    n = 0
    for child in root.iterdir():
        if child.is_dir() and child.name.startswith("symbol="):
            n += 1
    return n


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
    """
    m = _manifest_get()
    ds = m.get(dataset)
    if ds is None:
        ds = _manifest_scan_dataset(dataset)
        with _MANIFEST_LOCK:
            m[dataset] = ds
            _manifest_flush_locked(force=True)
    else:
        n_man = len(ds)
        n_dir = _count_symbol_dirs(dataset)
        if n_man < n_dir and dataset not in _audited:
            logger.warning(
                f"[manifest] dataset={dataset} manifest 条目={n_man} "
                f"磁盘目录={n_dir}，已强制重扫（存在未登记的旁路写入）")
            ds = _manifest_scan_dataset(dataset)
            with _MANIFEST_LOCK:
                m[dataset] = ds
                _manifest_flush_locked(force=True)
            # 每 dataset 每进程至多重扫一次：重扫后若仍 N_man<N_dir，说明差异来自
            # 合法空目录，记入 _audited 后不再重扫、也不再刷 warning。
            _audited.add(dataset)
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
    """
    out: list[pl.DataFrame] = []
    for f in files:
        try:
            schema = pl.read_parquet_schema(f)
            if any(c not in schema for c in columns):
                continue
            df = pl.read_parquet(f, columns=columns)
        except Exception as e:  # noqa: BLE001 坏文件跳过（不造数，行数如实缩水）
            logger.debug(f"[parquet_store] skip unreadable {f.name}: {e!r}")
            continue
        if date_cast and "date" in df.columns and df.schema["date"] != pl.Date:
            df = df.with_columns(pl.col("date").cast(pl.Date))
        out.append(df)
    return pl.concat(out, how="vertical_relaxed") if out else pl.DataFrame()


# ---------- 路径约定 ----------
def path_for_year(dataset: str, symbol: str, year: int | str) -> Path:
    """按年分区路径：DATA_ROOT/dataset/symbol=XXX/year=YYYY.snappy.parquet。"""
    base = get_settings().DATA_ROOT / dataset / f"symbol={symbol}"
    _ensure_dir(base)
    return base / f"year={year}.snappy.parquet"


def path_for(dataset: str, symbol: str, d: date | None = None) -> Path:
    """兼容接口：d 为 None 返回全量文件（all），否则返回该年分区文件。"""
    base = get_settings().DATA_ROOT / dataset / f"symbol={symbol}"
    _ensure_dir(base)
    if d is None:
        return base / "all.snappy.parquet"
    return path_for_year(dataset, symbol, _year_of(d))


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
    """
    if df.is_empty():
        raise ValueError("write_partition: df 为空")
    df = _normalize_date(df)
    year = _year_of(d)
    target = path_for_year(dataset, symbol, year)
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
    """整年批量写入（首次拉取或历史回补时使用，直接覆盖该年文件）。"""
    if df.is_empty():
        raise ValueError("write_year_batch: df 为空")
    df = _normalize_date(df)
    target = path_for_year(dataset, symbol, year)
    _atomic_write_parquet(target, df)
    _manifest_record(dataset, symbol, df)
    logger.trace(f"parquet year-batch write ok: {target} rows={len(df)}")
    return target


def write_whole_symbol(dataset: str, symbol: str, df: pl.DataFrame) -> Path:
    """小表全量写入（如 instruments 汇总）：单文件 all.snappy.parquet。"""
    if df.is_empty():
        raise ValueError("write_whole_symbol: df 为空")
    s = get_settings()
    base = s.DATA_ROOT / dataset / f"symbol={symbol}"
    _ensure_dir(base)
    target = base / "all.snappy.parquet"
    _atomic_write_parquet(target, df)
    _manifest_record(dataset, symbol, df)
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
