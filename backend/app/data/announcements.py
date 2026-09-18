"""公告（announcements）读取**唯一口径**（AQP）。

背景（审计 F-01 / E-01）
------------------------
同一「公告」语义此前存在两套读取实现、两条存储：
    - ``api/v1/market.py::_latest_announcements`` 读 ``data/parquet/announcements``
      （真实有数据，由 ``data/ingest/announcements.save_announcements`` 写入）；
    - ``data/panels.py::build_events`` 却查**空的** SQLite ``news_announcement`` 表
      （全仓无写入方），回退到 ``realtime.fetch_events``（恒抛异常），
      导致个股「近期事件」块 100% 不可见。
本模块把「读公告」抽成 data 层公共函数，让上述两处**共用同一读取口径**
（按 ``symbol`` 过滤 + 按 ``pub_date`` 倒序 + ``limit``），消除重复实现导致的行为漂移。

存储事实源
----------
``DATA_ROOT/announcements/symbol=__all__/year=YYYY.snappy.parquet``，列：
``symbol, pub_date, title, type, sentiment, source[, url]``。
注意分区目录名固定为 ``symbol=__all__``（单文件承载全市场），真正的标的在
**行内** ``symbol`` 列，故不能按 ``symbol=<code>`` 目录定位，必须读全表再按列过滤。

约定
----
- 本模块所有函数不抛异常：无数据/坏文件 → 空表或空列表（不造数）。
- 纯读、无副作用。
"""
from __future__ import annotations

from pathlib import Path
from typing import Any

import polars as pl
from loguru import logger

from ..core.config import get_settings

#: 公告数据集目录名（DATA_ROOT 下）。
ANNOUNCEMENT_DATASET = "announcements"

#: 读取时投影/使用的规范列（缺失列会被安全跳过，不强制）。
_FRAME_COLUMNS = ("symbol", "pub_date", "title", "type", "sentiment", "source", "url")


def announcement_files() -> list[Path]:
    """公告分区文件列表（跨 symbol 目录、按路径升序）。目录不存在时返回空列表。"""
    root = get_settings().DATA_ROOT / ANNOUNCEMENT_DATASET
    if not root.exists():
        return []
    return sorted(root.rglob("*.parquet"))


def read_announcement_frame() -> pl.DataFrame:
    """读取公告全表（跨年分区、列集对齐容忍）。

    Returns:
        合并后的 ``pl.DataFrame``；无文件 / 全部不可读 → 空表。绝不抛异常。
    """
    files = announcement_files()
    if not files:
        return pl.DataFrame()

    frames: list[pl.DataFrame] = []
    for f in files:
        try:
            frames.append(pl.read_parquet(f))
        except Exception as e:  # noqa: BLE001 坏文件跳过（如实缩水，不造数）
            logger.debug(f"[announcements] skip unreadable {f.name}: {type(e).__name__}")
    if not frames:
        return pl.DataFrame()

    try:
        return pl.concat(frames, how="diagonal_relaxed")
    except Exception as e:  # noqa: BLE001 列集差异过大时退化为严格对角对齐
        logger.debug(f"[announcements] concat relaxed failed, retry strict: {type(e).__name__}")
        try:
            return pl.concat(frames, how="diagonal")
        except Exception as e2:  # noqa: BLE001 仍失败则放弃（返回空表）
            logger.warning(f"[announcements] concat failed: {type(e2).__name__}")
            return pl.DataFrame()


def _normalize_pub_date(df: pl.DataFrame) -> pl.DataFrame:
    """把 ``pub_date`` 统一为 ``pl.Date``（容忍 str/Datetime 混存），失败则原样返回。"""
    if "pub_date" not in df.columns:
        return df
    try:
        return df.with_columns(pl.col("pub_date").cast(pl.Date, strict=False))
    except Exception as e:  # noqa: BLE001 类型异常不影响其余字段读取
        logger.debug(f"[announcements] pub_date cast skipped: {type(e).__name__}")
        return df


def _iso_date(value: Any) -> str | None:
    """日期标量 → ``YYYY-MM-DD`` 字符串；None → None。"""
    if value is None:
        return None
    return str(value)[:10]


def read_symbol_announcements(symbol: str, limit: int = 3) -> list[dict[str, Any]]:
    """按 ``symbol`` 过滤 + 按 ``pub_date`` 倒序 + ``limit`` 读取公告。

    Args:
        symbol: 标准标的代码（如 ``600519.SH``）。
        limit: 返回条数上限（<=0 时按 1 处理，避免读全表）。

    Returns:
        ``[{title, date, url, event_type, sentiment, source}]``（按日期倒序）；
        无匹配 / 无数据 → ``[]``。绝不抛异常。
    """
    df = read_announcement_frame()
    if df.is_empty() or "symbol" not in df.columns:
        return []
    try:
        if df.schema["symbol"] != pl.String:
            df = df.with_columns(pl.col("symbol").cast(pl.String, strict=False))
        df = df.filter(pl.col("symbol") == symbol)
    except Exception as e:  # noqa: BLE001 过滤失败按无数据处理
        logger.debug(f"[announcements] filter failed for {symbol}: {type(e).__name__}")
        return []
    if df.is_empty():
        return []

    df = _normalize_pub_date(df)
    if "pub_date" in df.columns:
        df = df.sort("pub_date", descending=True, nulls_last=True)
    df = df.head(max(1, int(limit)))

    out: list[dict[str, Any]] = []
    for r in df.iter_rows(named=True):
        out.append({
            "title": str(r.get("title") or ""),
            "date": _iso_date(r.get("pub_date")),
            "url": r.get("url"),
            "event_type": r.get("type"),
            "sentiment": r.get("sentiment"),
            "source": r.get("source") or "announcements-parquet",
        })
    return out


def latest_announcement_by_symbol() -> dict[str, dict[str, Any]]:
    """``symbol`` -> 最新一条公告摘要 ``{title, sentiment, pub_date}``。

    供 ``api/v1/market.py::_latest_announcements``（推荐榜附注口径）复用，与
    :func:`read_symbol_announcements` 同源同过滤口径。

    无数据 → ``{}``。绝不抛异常。
    """
    df = read_announcement_frame()
    if df.is_empty() or "symbol" not in df.columns:
        return {}
    df = _normalize_pub_date(df)
    if "pub_date" in df.columns:
        # 升序遍历 + 覆盖写 => 每个 symbol 最终保留 pub_date 最大的一条
        df = df.sort("pub_date", nulls_last=True)

    out: dict[str, dict[str, Any]] = {}
    for r in df.iter_rows(named=True):
        sym = r.get("symbol")
        if sym is None:
            continue
        out[str(sym)] = {
            "title": r.get("title"),
            "sentiment": r.get("sentiment"),
            "pub_date": str(r.get("pub_date")) if r.get("pub_date") is not None else None,
        }
    return out
