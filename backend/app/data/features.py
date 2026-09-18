"""统一读取版本化 features 数据集，防止多版本面板混合计算。"""
from __future__ import annotations

from pathlib import Path

import polars as pl
from loguru import logger

from ..core.config import get_settings
from ..core.errors import AQPException, ERR_DATA_EMPTY


def _version_name(version_dir: Path) -> str:
    """从 ``version=<name>`` 分区目录取得特征版本名。"""
    prefix = "version="
    if not version_dir.name.startswith(prefix):
        raise ValueError(f"非法 features 版本目录: {version_dir}")
    return version_dir.name[len(prefix):]


def resolve_feature_version(version: str | None = None) -> str:
    """解析要读取的单一 features 版本。

    显式参数优先，其次使用 ``FEATURE_VERSION`` 配置；二者均为空时，选择
    最后写入 parquet 数据最新的版本。自动选择结果会记入日志，便于审计。
    """
    settings = get_settings()
    requested = (version or settings.FEATURE_VERSION).strip()
    root = settings.DATA_ROOT / "features"
    if requested:
        version_dir = root / f"version={requested}"
        if not version_dir.is_dir():
            raise AQPException(
                ERR_DATA_EMPTY,
                f"features 版本不存在: {requested}（目录: {version_dir}）",
            )
        return requested

    candidates: list[tuple[int, str]] = []
    for version_dir in root.glob("version=*"):
        if not version_dir.is_dir():
            continue
        files = list(version_dir.glob("year=*.parquet"))
        if files:
            newest_mtime = max(file.stat().st_mtime_ns for file in files)
            candidates.append((newest_mtime, _version_name(version_dir)))
    if not candidates:
        raise AQPException(ERR_DATA_EMPTY, f"features 数据不存在（目录: {root}）")

    selected = max(candidates, key=lambda item: (item[0], item[1]))[1]
    logger.info(f"[features] FEATURE_VERSION 未设置，自动选择最新版本: {selected}")
    return selected


def feature_files(version: str | None = None) -> tuple[str, list[Path]]:
    """返回指定（或自动选择）单一版本下的年度 parquet 文件。"""
    settings = get_settings()
    selected = resolve_feature_version(version)
    root = settings.DATA_ROOT / "features" / f"version={selected}"
    files = sorted(root.glob("year=*.parquet"))
    if not files:
        raise AQPException(
            ERR_DATA_EMPTY,
            f"features 版本 {selected} 没有年度数据文件（目录: {root}）",
        )
    return selected, files


def assert_unique_feature_rows(frame: pl.DataFrame, version: str) -> None:
    """确保单一版本中每个 ``(date, symbol)`` 仅存在一行。

    同一版本内的重复数据同样会静默污染因子计算，因此在任何共享读取入口
    fail-fast，而不是依赖 pivot 的 ``aggfunc=last`` 掩盖问题。
    """
    required = {"date", "symbol"}
    missing = required.difference(frame.columns)
    if missing:
        raise AQPException(
            ERR_DATA_EMPTY,
            f"features 版本 {version} 缺少唯一键列: {sorted(missing)}",
        )
    duplicate_rows = (
        frame.group_by(["date", "symbol"])
        .len()
        .filter(pl.col("len") > 1)
        .select((pl.col("len") - 1).sum().alias("duplicate_rows"))
        .item()
    )
    count = int(duplicate_rows or 0)
    if count:
        raise AQPException(
            ERR_DATA_EMPTY,
            f"features 版本 {version} 存在 {count} 个重复 (date, symbol) 行，已拒绝计算",
        )


def read_feature_frame(version: str | None = None) -> tuple[str, pl.DataFrame]:
    """读取并验证一个 features 版本的完整长表。"""
    selected, files = feature_files(version)
    frame = pl.concat([pl.read_parquet(file) for file in files], how="diagonal_relaxed")
    if "date" in frame.columns and frame.schema["date"] != pl.Date:
        frame = frame.with_columns(pl.col("date").cast(pl.Date))
    assert_unique_feature_rows(frame, selected)
    return selected, frame
