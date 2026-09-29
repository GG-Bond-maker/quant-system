"""截面分区镜像（MED-003 数据前置：解决 symbol=X/year=Y 小文件墙）。

背景：daily_bar 系按 (symbol, year) 分区，121 只 = 605 文件；扩池到 5000 只
意味着 ~25,000 个小文件，任何按日期取截面的查询都要全量打开。本模块为其
建立**按日期分区**的镜像（源数据不动，双store 并存）：

    DATA_ROOT/cs/<dataset>/year=YYYY/date=YYYYMMDD.parquet
        每个交易日一个文件：全部标的 × 全列（与源 schema 一致）

- ``build_mirror(dataset, incremental=True)``：按日期聚合源分区写出镜像；
  增量模式仅重建「缺失 或 源文件比镜像新」的日期（以文件 mtime 判定），
  幂等可反复执行；
- ``read_cross_section(dataset, d)``：单文件 O(1) 读取一个交易日全截面；
- ``read_cross_range(dataset, start, end)``：日期区间读取（谓词下推到目录）。

镜像独立于 ``DATA_ROOT/<dataset>`` 根，datacenter 的数据集扫描不受影响。
晚间例行（core/scheduler）在行情同步后自动重建三个 bar 数据集的镜像。
"""
from __future__ import annotations

from datetime import date

import polars as pl
from loguru import logger

from ..core.config import get_settings
from .parquet_store import _atomic_write_parquet  # 同包复用原子写
from ..services.stats_cache import invalidate_stats_cache

MIRROR_DATASETS = ("daily_bar", "daily_bar_hfq", "daily_bar_qfq")


def _source_files(dataset: str) -> list:
    s = get_settings()
    root = s.DATA_ROOT / dataset
    if not root.exists():
        return []
    return sorted(root.rglob("*.parquet"))


def _mirror_dir(dataset: str):
    return get_settings().DATA_ROOT / "cs" / dataset


def _date_of(df: pl.DataFrame) -> pl.DataFrame:
    if df.schema["date"] != pl.Date:
        df = df.with_columns(pl.col("date").cast(pl.Date))
    return df


def _scan_source(dataset: str) -> tuple[dict, dict[date, float]]:
    """扫描源分区一次，返回 (file -> 该文件日期列表, date -> 涉及它的源文件最新 mtime)。

    只裁剪读取 date 列（列裁剪，代价远低于全列读）；单个文件损坏不阻断整体。
    保留 file→dates 映射是为了让 build 阶段**只全量读取含待建日期的文件**——
    日常增量只新增一个交易日时，不必为它重读全部源文件。
    """
    file_dates: dict = {}
    date_mtime: dict[date, float] = {}
    for f in _source_files(dataset):
        mtime = f.stat().st_mtime
        try:
            days = [d for d in
                    _date_of(pl.read_parquet(f, columns=["date"]))["date"]
                    .unique().to_list() if d is not None]
        except Exception as e:  # noqa: BLE001 单文件损坏不阻断整体构建
            logger.warning(f"[cs-mirror] 读取源文件失败 {f.name}: {e!r}")
            continue
        file_dates[f] = days
        for d in days:
            if d not in date_mtime or mtime > date_mtime[d]:
                date_mtime[d] = mtime
    return file_dates, date_mtime


def _scan_source_dates(dataset: str) -> dict[date, float]:
    """date -> 该日期涉及的源文件最新 mtime（兼容入口；等价于 _scan_source 的第二项）。"""
    return _scan_source(dataset)[1]


def build_mirror(dataset: str, incremental: bool = True) -> dict:
    """构建/刷新某数据集的截面镜像。返回统计 {dates, built, skipped, rows}。

    性能纪律（防回归——这是本模块存在的全部意义）：
    - **每文件只做一次 is_in 过滤 + 原生 partition_by**，绝不能退化成
      "对每个日期各 filter 一次"：那是 O(文件数 × 每年交易日) 次 DataFrame
      物化，实测 1000 只 ×250 日要 21s，而正确写法只要 1.8s（约 12 倍差距），
      扩池到 5000 只 ×5 年后是两个数量级。
    - **按年分批**：内存上界是「单年全截面」而非全历史，避免 5000 只 ×5 年
      一次性驻留。
    """
    if dataset not in MIRROR_DATASETS:
        raise ValueError(f"仅支持镜像 {MIRROR_DATASETS}，收到 {dataset!r}")
    file_dates, date_mtime = _scan_source(dataset)
    if not date_mtime:
        return {"dataset": dataset, "dates": 0, "built": 0, "skipped": 0, "rows": 0}

    mirror_dir = _mirror_dir(dataset)
    pending = {d for d, m in date_mtime.items()
               if not incremental or _stale(mirror_dir, d, m)}
    if not pending:
        return {"dataset": dataset, "dates": len(date_mtime), "built": 0,
                "skipped": len(date_mtime), "rows": 0}

    # 按年归拢待建文件：同一 (symbol, year) 分区只落在一个年份批次里
    by_year: dict[int, list] = {}
    for f, days in file_dates.items():
        for y in {d.year for d in days if d in pending}:
            by_year.setdefault(y, []).append(f)

    built = rows = 0
    for year in sorted(by_year):
        frames = []
        for f in by_year[year]:
            try:
                df = _date_of(pl.read_parquet(f))
            except Exception as e:  # noqa: BLE001 单文件损坏不阻断整体构建
                logger.warning(f"[cs-mirror] 跳过损坏文件 {f.name}: {e!r}")
                continue
            if not df.is_empty():
                frames.append(df)
        if not frames:
            continue
        year_df = pl.concat(frames, how="diagonal_relaxed")
        year_pending = [d for d in pending if d.year == year]
        for part in year_df.filter(pl.col("date").is_in(year_pending)) \
                           .partition_by("date"):
            d = part["date"][0]
            out_dir = mirror_dir / f"year={d.year}"
            out_dir.mkdir(parents=True, exist_ok=True)
            _atomic_write_parquet(
                out_dir / f"date={d.strftime('%Y%m%d')}.parquet",
                part.sort(["date", "symbol"]))
            built += 1
            rows += part.height
        del year_df, frames

    skipped = len(date_mtime) - built
    if built:
        # 2026-09-26 收口：本次确实写出了镜像文件（cs/ 在 DATA_ROOT 内，参与
        # /overview 磁盘占用遍历），使统计缓存失效。**任务收尾调一次**——本函数
        # 按日期批量落盘（可达数百文件），逐文件调会造成"写 N 次失 N 次"的锁风暴。
        # built==0（全部跳过/无待建）时无写入 ⇒ 不失效。
        invalidate_stats_cache()
    logger.info(f"[cs-mirror] {dataset}: built={built} skipped={skipped} rows={rows}")
    return {"dataset": dataset, "dates": len(date_mtime), "built": built,
            "skipped": skipped, "rows": rows}


def _stale(mirror_dir, d: date, source_mtime: float) -> bool:
    """镜像缺失，或任一源文件比镜像新 → 需要重建。"""
    target = mirror_dir / f"year={d.year}" / f"date={d.strftime('%Y%m%d')}.parquet"
    if not target.exists():
        return True
    return source_mtime > target.stat().st_mtime


def read_cross_section(dataset: str, d: date) -> pl.DataFrame:
    """读取单个交易日全截面（O(1) 文件数）；镜像缺失返回空表。"""
    target = _mirror_dir(dataset) / f"year={d.year}" / f"date={d.strftime('%Y%m%d')}.parquet"
    if not target.exists():
        return pl.DataFrame()
    return pl.read_parquet(target)


def read_cross_range(dataset: str, start: date, end: date) -> pl.DataFrame:
    """读取日期区间（glob 按年目录谓词下推；无数据返回空表）。"""
    files: list = []
    for year in range(start.year, end.year + 1):
        ydir = _mirror_dir(dataset) / f"year={year}"
        if ydir.exists():
            files.extend(sorted(ydir.glob("date=*.parquet")))
    if not files:
        return pl.DataFrame()
    lo, hi = start.strftime("%Y%m%d"), end.strftime("%Y%m%d")
    picked = [f for f in files if lo <= f.stem.split("=")[1] <= hi]
    if not picked:
        return pl.DataFrame()
    return pl.concat([pl.read_parquet(f) for f in picked], how="diagonal_relaxed")


def mirror_status() -> dict:
    """镜像新鲜度概览：源 vs 镜像的日期覆盖与滞后。

    ``lag_days`` = 源有而镜像没有的日期**个数**（沿用前端字段名）。另给出
    ``orphan_dates`` = 镜像有而源已没有的日期数——源分区被删除/重建后镜像
    不会自动清理，这个数不为 0 就该手动全量重建。
    """
    s = get_settings()
    out: dict = {}
    for ds in MIRROR_DATASETS:
        src_dates = sorted(_scan_source_dates(ds))
        mdir = _mirror_dir(ds)
        mirror_dates = sorted(
            date(int(f.parent.name.split("=")[1]),
                 int(f.stem.split("=")[1][4:6]), int(f.stem.split("=")[1][6:8]))
            for f in mdir.glob("year=*/date=*.parquet")) if mdir.exists() else []
        src_set, mir_set = set(src_dates), set(mirror_dates)
        out[ds] = {
            "source_dates": len(src_dates),
            "mirror_dates": len(mirror_dates),
            "source_last": str(src_dates[-1]) if src_dates else None,
            "mirror_last": str(mirror_dates[-1]) if mirror_dates else None,
            "lag_days": len(src_set - mir_set),
            "orphan_dates": len(mir_set - src_set),
        }
    out["root"] = str(s.DATA_ROOT / "cs")
    return out
