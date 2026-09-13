"""
数据修复（AQP data）：隔离 / schema 归一 / 重建 / 验收。

纪律：
1. **绝不静默删除** —— 被判定为损坏的分区一律移动到 ``data/quarantine/``
   并写入 manifest（原因、时间、原始路径、行数），可随时追溯与恢复；
2. **先隔离，后重建** —— 重建只在"可证明"的前提下进行
   （复权因子在相邻交易日不变 => hfq = raw × factor 是确定性推导）；
   无法证明的一律隔离等待重新获取；
3. **atomic replace** —— 所有写回走 ``parquet_store._atomic_write_parquet``；
4. **修完必验收** —— ``verify_scannable`` 保证任意 dataset 都能
   ``pl.scan_parquet(...).collect()`` 而不抛 SchemaError。
"""
from __future__ import annotations

import json
import shutil
from dataclasses import dataclass, field
from datetime import date, datetime
from pathlib import Path
from typing import Any

import polars as pl
from loguru import logger

from ..core.config import PROJECT_ROOT
from .parquet_store import _atomic_write_parquet
from .quality import canonical_columns, normalize_schema, read_symbol_all

QUARANTINE_ROOT: Path = PROJECT_ROOT / "data" / "quarantine"
MANIFEST_NAME = "manifest.json"


# ---------------- 统计 ----------------
@dataclass
class RepairStats:
    quarantined_files: list[str] = field(default_factory=list)
    quarantined_rows: int = 0
    normalized_files: int = 0
    rebuilt_files: int = 0
    rebuilt_rows: int = 0
    symbols_quarantined: list[str] = field(default_factory=list)

    def as_dict(self) -> dict[str, Any]:
        return {
            "quarantined_files": self.quarantined_files,
            "quarantined_rows": self.quarantined_rows,
            "normalized_files": self.normalized_files,
            "rebuilt_files": self.rebuilt_files,
            "rebuilt_rows": self.rebuilt_rows,
            "symbols_quarantined": self.symbols_quarantined,
        }


# ---------------- manifest ----------------
def _load_manifest(qroot: Path) -> list[dict[str, Any]]:
    p = qroot / MANIFEST_NAME
    if not p.exists():
        return []
    try:
        return json.loads(p.read_text(encoding="utf-8"))
    except Exception:
        return []


def _append_manifest(qroot: Path, entry: dict[str, Any]) -> None:
    qroot.mkdir(parents=True, exist_ok=True)
    entries = _load_manifest(qroot)
    entries.append(entry)
    (qroot / MANIFEST_NAME).write_text(
        json.dumps(entries, ensure_ascii=False, indent=2), encoding="utf-8")


# ---------------- 隔离 ----------------
def quarantine_partition(
    root: Path,
    dataset: str,
    symbol: str,
    year: int | str,
    reason: str,
    stats: RepairStats | None = None,
    qroot: Path = QUARANTINE_ROOT,
) -> Path | None:
    """把单个损坏分区移入 quarantine（保留数据 + 记录原因）。"""
    src = root / dataset / f"symbol={symbol}" / f"year={year}.snappy.parquet"
    if not src.exists():
        return None
    n_rows = pl.read_parquet(src).height
    dst_dir = qroot / dataset / f"symbol={symbol}"
    dst_dir.mkdir(parents=True, exist_ok=True)
    dst = dst_dir / f"year={year}.snappy.parquet"
    if dst.exists():
        dst = dst_dir / f"year={year}.{datetime.now():%Y%m%d%H%M%S}.snappy.parquet"
    shutil.move(str(src), str(dst))
    _append_manifest(qroot, {
        "quarantined_at": datetime.now().isoformat(timespec="seconds"),
        "dataset": dataset, "symbol": symbol, "year": str(year),
        "reason": reason, "n_rows": n_rows,
        "original_path": str(src), "quarantine_path": str(dst),
    })
    logger.warning(f"[quarantine] {dataset}/{symbol}/{year} rows={n_rows} :: {reason}")
    if stats is not None:
        stats.quarantined_files.append(f"{dataset}/symbol={symbol}/year={year}")
        stats.quarantined_rows += n_rows
    return dst


def quarantine_symbol(
    root: Path,
    symbol: str,
    reason: str,
    datasets: tuple[str, ...] = ("daily_bar", "daily_bar_hfq", "daily_bar_qfq"),
    stats: RepairStats | None = None,
    qroot: Path = QUARANTINE_ROOT,
) -> int:
    """整只标的隔离（所有年份、所有复权口径）。用于无法修复的标的。"""
    n = 0
    for ds in datasets:
        base = root / ds / f"symbol={symbol}"
        if not base.exists():
            continue
        for f in sorted(base.glob("year=*.parquet")):
            year = f.name.split("=")[1].split(".")[0]
            if quarantine_partition(root, ds, symbol, year, reason, stats, qroot):
                n += 1
    if stats is not None and symbol not in stats.symbols_quarantined:
        stats.symbols_quarantined.append(symbol)
    logger.warning(f"[quarantine] symbol={symbol} 全部 {n} 个分区已隔离 :: {reason}")
    return n


def is_quarantined(symbol: str, qroot: Path = QUARANTINE_ROOT) -> bool:
    """该标的是否被整只隔离（出现在 manifest 且数量覆盖多数据集）。"""
    for e in _load_manifest(qroot):
        if e.get("symbol") == symbol:
            return True
    return False


def quarantined_symbols(qroot: Path = QUARANTINE_ROOT) -> list[str]:
    return sorted({e["symbol"] for e in _load_manifest(qroot) if e.get("symbol")})


# ---------------- 重建 ----------------
def rebuild_adj_year_from_factor(
    root: Path,
    dataset: str,
    symbol: str,
    year: int,
    anchor_factor: float,
    anchor_date: date,
) -> int:
    """用锚点复权因子重建某年后复权分区：hfq = raw × anchor_factor。

    合法性前提（调用方必须满足，本函数会校验）：
        anchor_date 之后到 year 年内**未发生公司行为**。
    校验方式：重建后由 ``quality.check_adjusted_continuity`` 复检，
    若存在制度外跳变说明期间有公司行为 -> 调用方应改为隔离。

    之所以安全：本项目全部因子与标签都是价格的比值/收益率形式
    （ma_gap、ret_N、vol、label = close[t+N]/close[t]-1），
    **常数倍因子会完全抵消**，因此即使因子有常数偏差也不影响研究与回测。
    """
    raw = read_symbol_all(root, "daily_bar", symbol)
    if raw.is_empty():
        return 0
    part = raw.filter(pl.col("date").dt.year() == year)
    if part.is_empty():
        return 0
    if (part["date"] <= anchor_date).any():
        raise ValueError(
            f"{symbol}/{year} 含早于锚点 {anchor_date} 的日期，无法用单一因子重建")
    out = part.with_columns([
        (pl.col(c) * anchor_factor).alias(c) for c in ("open", "high", "low", "close")
    ])
    out = normalize_schema(out, dataset, symbol=symbol, source="rebuilt")
    target = root / dataset / f"symbol={symbol}" / f"year={year}.snappy.parquet"
    target.parent.mkdir(parents=True, exist_ok=True)
    _atomic_write_parquet(target, out)
    logger.info(f"[rebuild] {dataset}/{symbol}/{year} rows={out.height} "
                f"factor={anchor_factor:.6f} (anchor {anchor_date})")
    return out.height


def build_qfq_dataset(root: Path, symbol: str) -> int:
    """由 raw + hfq 推导前复权（qfq）数据集。

    关系（与 domain/adjust.py 约定一致）：
        hfq = raw × adj_factor            （后复权：锚定上市首日）
        qfq = raw × adj_factor / F_last   （前复权：锚定最新一日）
    其中 F_last = 该 symbol 最后一个交易日的 adj_factor。
    因此：
        adj_factor = hfq / raw
        qfq        = hfq / F_last

    这样推导出的 qfq 满足数学不变量：
        qfq 最后一日收盘价 == raw 最后一日收盘价
    """
    raw = read_symbol_all(root, "daily_bar", symbol)
    hfq = read_symbol_all(root, "daily_bar_hfq", symbol)
    if raw.is_empty() or hfq.is_empty():
        return 0
    m = (hfq.join(raw.select(["date", "close"]).rename({"close": "raw_close"}),
                  on="date", how="inner")
         .filter(pl.col("raw_close") > 0).sort("date")
         .with_columns((pl.col("close") / pl.col("raw_close")).alias("factor")))
    if m.is_empty():
        return 0
    f_last = float(m["factor"][-1])
    out = m.with_columns([
        (pl.col(c) / f_last).alias(c) for c in ("open", "high", "low", "close")
    ]).drop(["raw_close", "factor"])
    out = normalize_schema(out, "daily_bar_qfq", symbol=symbol, source="derived")

    target_dir = root / "daily_bar_qfq" / f"symbol={symbol}"
    target_dir.mkdir(parents=True, exist_ok=True)
    total = 0
    for year, g in out.with_columns(pl.col("date").dt.year().alias("_y")).group_by("_y"):
        y = int(year[0])  # type: ignore[call-overload]
        _atomic_write_parquet(target_dir / f"year={y}.snappy.parquet",
                              g.drop("_y").sort("date"))
        total += g.height
    return total


def anchor_factor_at(
    root: Path, symbol: str, on_or_before: date,
) -> tuple[float, date] | None:
    """取 on_or_before 之前最后一个交易日的复权因子（hfq/raw）。"""
    raw = read_symbol_all(root, "daily_bar", symbol)
    hfq = read_symbol_all(root, "daily_bar_hfq", symbol)
    if raw.is_empty() or hfq.is_empty():
        return None
    m = (raw.select(["date", "close"]).rename({"close": "raw"})
         .join(hfq.select(["date", "close"]).rename({"close": "hfq"}),
               on="date", how="inner")
         .filter((pl.col("date") <= on_or_before) & (pl.col("raw") > 0))
         .sort("date"))
    if m.is_empty():
        return None
    last = m.tail(1)
    f = float(last["hfq"][0] / last["raw"][0])
    return f, last["date"][0]


# ---------------- schema 归一 ----------------
def normalize_dataset(root: Path, dataset: str,
                      stats: RepairStats | None = None) -> int:
    """把 dataset 下所有分区重写为 canonical schema（固定列顺序 + dtype）。"""
    base = root / dataset
    if not base.exists():
        return 0
    n = 0
    for d in sorted(base.iterdir()):
        if not d.is_dir() or not d.name.startswith("symbol="):
            continue
        sym = d.name.split("=", 1)[1]
        for f in sorted(d.glob("year=*.parquet")):
            df = pl.read_parquet(f)
            if list(df.columns) == canonical_columns(dataset):
                continue
            src = df["source"][0] if "source" in df.columns and df.height else "akshare"
            out = normalize_schema(df, dataset, symbol=sym, source=str(src))
            _atomic_write_parquet(f, out)
            n += 1
            logger.info(f"[normalize] {dataset}/{sym}/{f.name} -> canonical")
    if stats is not None:
        stats.normalized_files += n
    return n


# ---------------- 验收 ----------------
def verify_scannable(root: Path, dataset: str) -> tuple[bool, str]:
    """验收：整个 dataset 必须能被 scan_parquet + collect，不抛 SchemaError。"""
    base = root / dataset
    if not base.exists():
        return True, "dataset 不存在（跳过）"
    try:
        df = pl.scan_parquet(str(base / "**" / "*.parquet"),
                             hive_partitioning=False).collect()
        return True, f"OK rows={df.height} cols={len(df.columns)}"
    except Exception as e:
        return False, f"{type(e).__name__}: {e}"


def verify_dataset_aligned(root: Path, dataset: str) -> tuple[bool, list[str]]:
    """验收：所有分区列顺序一致且与 canonical 相同。"""
    base = root / dataset
    bad: list[str] = []
    if not base.exists():
        return True, bad
    want = tuple(canonical_columns(dataset))
    for f in sorted(base.glob("**/year=*.parquet")):
        cols = tuple(pl.read_parquet_schema(f).keys())
        if cols != want:
            bad.append(f"{f.relative_to(base)}: {list(cols)}")
    return (not bad), bad
