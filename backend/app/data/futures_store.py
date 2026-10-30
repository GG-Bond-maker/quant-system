"""Manifest-backed persistence helpers for futures history caches."""
from __future__ import annotations

import hashlib
import json
import os
import tempfile
import threading
from collections.abc import Mapping
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any

import pandas as pd  # type: ignore[import-untyped]
import polars as pl

from ..domain.futures_catalog import FUTURES_SPECS, get_futures_instrument

FUTURES_COLUMNS: tuple[str, ...] = (
    "date",
    "open",
    "high",
    "low",
    "close",
    "volume",
    "open_interest",
)

SINA_COLUMN_MAP: dict[str, str] = {
    "日期": "date",
    "开盘价": "open",
    "最高价": "high",
    "最低价": "low",
    "收盘价": "close",
    "成交量": "volume",
    "持仓量": "open_interest",
}

_MANIFEST_LOCK = threading.RLock()
_MANIFEST_NAME = "manifest.json"


@dataclass(frozen=True)
class FuturesCacheMeta:
    """Auditable metadata for one futures parquet cache."""

    symbol: str
    source: str
    source_symbol: str
    fetched_at: str
    listing_date: str | None
    raw_source_first_date: str | None
    source_first_date: str | None
    source_last_date: str | None
    cache_first_date: str | None
    effective_start: str | None
    requested_start_date: str | None
    rows: int
    tier: str
    quality_status: str
    trade_enabled: bool
    issue_counts: dict[str, int]
    warning_counts: dict[str, int]
    checksum: str


def normalize_futures_frame(frame: pd.DataFrame) -> pl.DataFrame:
    """Normalize a Sina futures frame into the canonical cache schema."""

    renamed = frame.rename(columns=SINA_COLUMN_MAP)
    missing = [column for column in FUTURES_COLUMNS if column not in renamed.columns]
    if missing:
        raise ValueError(f"missing required columns: {', '.join(missing)}")

    work = renamed.loc[:, list(FUTURES_COLUMNS)].copy()
    raw_dates = work["date"].astype("string")
    parsed_dates = pd.to_datetime(raw_dates, errors="coerce")
    work["date"] = parsed_dates.dt.strftime("%Y-%m-%d").fillna(raw_dates)
    for column in FUTURES_COLUMNS[1:]:
        work[column] = pd.to_numeric(work[column], errors="coerce")

    normalized = pl.from_pandas(work, include_index=False).with_columns(
        pl.col("date").cast(pl.String),
        *[pl.col(column).cast(pl.Float64) for column in FUTURES_COLUMNS[1:]],
    )

    return (
        normalized
        .with_row_index("_source_order")
        .sort(["date", "_source_order"])
        .unique(subset=["date"], keep="last", maintain_order=True)
        .drop("_source_order")
        .select(FUTURES_COLUMNS)
    )


def _manifest_path(cache_dir: Path) -> Path:
    return cache_dir / _MANIFEST_NAME


def read_futures_manifest(cache_dir: Path) -> dict[str, FuturesCacheMeta]:
    """Read the manifest, treating missing or corrupt JSON as an empty index."""

    path = _manifest_path(Path(cache_dir))
    if not path.exists():
        return {}
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
        if not isinstance(payload, dict):
            return {}
        entries: dict[str, FuturesCacheMeta] = {}
        for symbol, raw in payload.items():
            if not isinstance(raw, dict):
                continue
            try:
                entries[str(symbol).upper()] = FuturesCacheMeta(**raw)
            except (TypeError, ValueError):
                continue
        return entries
    except (OSError, UnicodeError, json.JSONDecodeError):
        return {}


def _serialize_manifest(entries: Mapping[str, FuturesCacheMeta]) -> str:
    payload = {
        symbol.upper(): asdict(meta)
        for symbol, meta in sorted(entries.items(), key=lambda item: item[0].upper())
    }
    return json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n"


def write_futures_manifest(
    cache_dir: Path,
    entries: Mapping[str, FuturesCacheMeta],
) -> Path:
    """Atomically replace ``manifest.json`` with deterministic JSON."""

    cache_dir = Path(cache_dir)
    cache_dir.mkdir(parents=True, exist_ok=True)
    target = _manifest_path(cache_dir)
    text = _serialize_manifest(entries)

    with _MANIFEST_LOCK:
        temp_path: Path | None = None
        try:
            with tempfile.NamedTemporaryFile(
                mode="w",
                encoding="utf-8",
                newline="\n",
                dir=cache_dir,
                prefix=".manifest.",
                suffix=".tmp",
                delete=False,
            ) as handle:
                handle.write(text)
                handle.flush()
                os.fsync(handle.fileno())
                temp_path = Path(handle.name)
            temp_path.replace(target)
        finally:
            if temp_path is not None and temp_path.exists():
                temp_path.unlink(missing_ok=True)
    return target


def update_futures_manifest(cache_dir: Path, meta: FuturesCacheMeta) -> Path:
    """Merge one entry under a lock so parallel symbol syncs cannot lose rows."""

    with _MANIFEST_LOCK:
        entries = read_futures_manifest(cache_dir)
        entries[meta.symbol.upper()] = meta
        return write_futures_manifest(cache_dir, entries)


def parquet_checksum(path: Path) -> str:
    """Return a stable SHA-256 digest for a parquet cache."""

    digest = hashlib.sha256()
    with Path(path).open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return f"sha256:{digest.hexdigest()}"


def futures_trade_status(symbol: str, cache_dir: Path) -> tuple[bool, str]:
    """Return whether a symbol may enter trading, backtest or hedging flows."""

    instrument = get_futures_instrument(symbol)
    if instrument is None:
        return False, "未知期货品种"
    if instrument.tier != "core" or not instrument.default_enabled:
        return False, "品种属于扩展池，默认未启用"
    if instrument.code not in FUTURES_SPECS:
        return False, "品种未配置交易规格"

    meta = read_futures_manifest(Path(cache_dir)).get(instrument.code)
    if meta is None:
        return False, "品种缓存尚未同步"
    if meta.quality_status != "ok":
        return False, f"品种数据质量状态为 {meta.quality_status}"
    if not meta.trade_enabled:
        return False, "品种缓存未启用交易"
    return True, "ok"


def manifest_as_dict(entries: Mapping[str, FuturesCacheMeta]) -> dict[str, dict[str, Any]]:
    """Return a JSON-compatible copy for diagnostics and tests."""

    return {symbol: asdict(meta) for symbol, meta in entries.items()}
