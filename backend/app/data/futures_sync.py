"""Synchronize real Sina futures history into the manifest-backed cache."""
from __future__ import annotations

import time
from collections.abc import Callable, Iterable
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import cast
from zoneinfo import ZoneInfo

import pandas as pd  # type: ignore[import-untyped]
import polars as pl
from loguru import logger

from ..domain.futures_catalog import FuturesInstrument, get_futures_instrument
from .futures_quality import audit_futures_frame
from .futures_store import (
    FuturesCacheMeta,
    normalize_futures_frame,
    parquet_checksum,
    read_futures_manifest,
    update_futures_manifest,
)
from .parquet_store import atomic_write_parquet

_SHANGHAI = ZoneInfo("Asia/Shanghai")
_FETCH_ATTEMPTS = 3
_RETRY_SLEEP_SECONDS = 1.2
_FRESH_FOR = timedelta(hours=24)


@dataclass(frozen=True)
class FuturesSyncResult:
    """Outcome of one symbol synchronization."""

    symbol: str
    success: bool
    wrote_cache: bool
    quality_status: str
    rows_before: int
    rows_after: int
    message: str


def _default_fetch(instrument: FuturesInstrument) -> pd.DataFrame:
    import akshare as ak  # type: ignore[import-untyped]

    return ak.futures_main_sina(symbol=instrument.sina_symbol)


def _fetch_with_retry(
    instrument: FuturesInstrument,
    fetch_fn: Callable[[str], pd.DataFrame] | None,
) -> pd.DataFrame:
    last_error: Exception | None = None
    for attempt in range(_FETCH_ATTEMPTS):
        try:
            if fetch_fn is None:
                frame = _default_fetch(instrument)
            else:
                frame = fetch_fn(instrument.sina_symbol)
            if not isinstance(frame, pd.DataFrame):
                raise TypeError("futures fetch must return a pandas DataFrame")
            return frame
        except Exception as exc:  # noqa: BLE001 - retry and report upstream errors
            last_error = exc
            if attempt + 1 < _FETCH_ATTEMPTS:
                time.sleep(_RETRY_SLEEP_SECONDS)
    raise RuntimeError(f"{instrument.code}: upstream fetch failed: {last_error}") from last_error


def _raw_first_date(frame: pd.DataFrame) -> str | None:
    for column in ("日期", "date"):
        if column not in frame.columns:
            continue
        dates: list[str] = []
        for value in frame[column].tolist():
            if isinstance(value, datetime):
                dates.append(value.date().isoformat())
                continue
            if isinstance(value, date):
                dates.append(value.isoformat())
                continue
            text = str(value).strip()[:10]
            try:
                dates.append(date.fromisoformat(text).isoformat())
            except ValueError:
                continue
        return min(dates) if dates else None
    return None


def _is_fresh(cache_dir: Path, symbol: str, cache_path: Path) -> bool:
    if not cache_path.exists():
        return False
    meta = read_futures_manifest(cache_dir).get(symbol)
    if meta is None or meta.quality_status == "failed":
        return False
    try:
        fetched_at = datetime.fromisoformat(meta.fetched_at)
    except ValueError:
        return False
    if fetched_at.tzinfo is None:
        fetched_at = fetched_at.replace(tzinfo=_SHANGHAI)
    return datetime.now(_SHANGHAI) - fetched_at < _FRESH_FOR


def _rows_in_cache(path: Path) -> int:
    if not path.exists():
        return 0
    try:
        return int(pl.read_parquet(path).height)
    except Exception:  # noqa: BLE001 - diagnostics only; sync still proceeds
        return 0


def _quality_status(
    instrument: FuturesInstrument,
    clean: pl.DataFrame,
    *,
    issue_counts: dict[str, int],
) -> str:
    if clean.is_empty():
        return "failed"
    if instrument.tier == "extended":
        return "warning"

    rows = clean.height
    last_date = date.fromisoformat(str(clean["date"][-1]))
    latest_is_fresh = 0 <= (date.today() - last_date).days <= 10
    trailing_start = (last_date - timedelta(days=365 * 3)).isoformat()
    trailing = clean.filter(pl.col("date") >= trailing_start)
    zero_volume_ratio = (
        float(
            trailing.select(
                (pl.col("volume") == 0).cast(pl.Float64).mean().alias("ratio")
            ).item()
            or 0.0
        )
        if not trailing.is_empty()
        else 1.0
    )
    median_value = trailing["volume"].median() if not trailing.is_empty() else None
    median_volume = (
        float(cast(int | float, median_value)) if median_value is not None else 0.0
    )
    if (
        rows < 250
        or issue_counts
        or not latest_is_fresh
        or zero_volume_ratio > 0.05
        or median_volume < 10_000
    ):
        return "failed"
    return "ok"


def sync_futures_symbol(
    symbol: str,
    *,
    cache_dir: Path,
    requested_start_date: date | None = None,
    force: bool = False,
    fetch_fn: Callable[[str], pd.DataFrame] | None = None,
    apply: bool = True,
) -> FuturesSyncResult:
    """Synchronize one product from the real upstream source."""

    instrument = get_futures_instrument(symbol)
    if instrument is None:
        raise ValueError(f"unknown futures symbol: {symbol}")

    cache_dir = Path(cache_dir)
    cache_path = cache_dir / f"{instrument.code}.parquet"
    rows_before = _rows_in_cache(cache_path)
    if not force and _is_fresh(cache_dir, instrument.code, cache_path):
        return FuturesSyncResult(
            symbol=instrument.code,
            success=True,
            wrote_cache=False,
            quality_status=read_futures_manifest(cache_dir)[instrument.code].quality_status,
            rows_before=rows_before,
            rows_after=rows_before,
            message="fresh cache; skipped",
        )

    try:
        raw = _fetch_with_retry(instrument, fetch_fn)
    except Exception as exc:  # noqa: BLE001 - preserve the last good cache
        logger.warning(f"[futures-sync] {instrument.code} fetch failed: {exc}")
        old = read_futures_manifest(cache_dir).get(instrument.code)
        return FuturesSyncResult(
            symbol=instrument.code,
            success=False,
            wrote_cache=False,
            quality_status=old.quality_status if old else "failed",
            rows_before=rows_before,
            rows_after=rows_before,
            message=str(exc),
        )

    raw_source_first_date = _raw_first_date(raw)
    try:
        normalized = normalize_futures_frame(raw)
    except (TypeError, ValueError) as exc:
        logger.warning(f"[futures-sync] {instrument.code} normalization failed: {exc}")
        old = read_futures_manifest(cache_dir).get(instrument.code)
        return FuturesSyncResult(
            symbol=instrument.code,
            success=False,
            wrote_cache=False,
            quality_status=old.quality_status if old else "failed",
            rows_before=rows_before,
            rows_after=rows_before,
            message=str(exc),
        )

    if normalized.is_empty():
        return FuturesSyncResult(
            symbol=instrument.code,
            success=False,
            wrote_cache=False,
            quality_status="failed",
            rows_before=rows_before,
            rows_after=rows_before,
            message="upstream returned no usable rows",
        )

    source_first_date = str(normalized["date"][0])
    audit = audit_futures_frame(normalized, instrument.code)
    clean = audit.clean
    if requested_start_date is not None:
        clean = clean.filter(pl.col("date") >= requested_start_date.isoformat())
    if clean.is_empty():
        return FuturesSyncResult(
            symbol=instrument.code,
            success=False,
            wrote_cache=False,
            quality_status="failed",
            rows_before=rows_before,
            rows_after=rows_before,
            message="cleaned frame is empty",
        )

    source_last_date = str(clean["date"][-1])
    quality_status = _quality_status(
        instrument,
        clean,
        issue_counts=audit.issue_counts,
    )
    trade_enabled = instrument.tier == "core" and quality_status == "ok"
    checksum = ""
    if apply:
        cache_path.parent.mkdir(parents=True, exist_ok=True)
        atomic_write_parquet(cache_path, clean)
        checksum = parquet_checksum(cache_path)
        meta = FuturesCacheMeta(
            symbol=instrument.code,
            source="sina_main",
            source_symbol=instrument.sina_symbol,
            fetched_at=datetime.now(_SHANGHAI).isoformat(timespec="seconds"),
            listing_date=(
                instrument.listing_date.isoformat() if instrument.listing_date else None
            ),
            raw_source_first_date=raw_source_first_date,
            source_first_date=source_first_date,
            source_last_date=source_last_date,
            cache_first_date=str(clean["date"][0]),
            effective_start=str(clean["date"][0]),
            requested_start_date=(
                requested_start_date.isoformat() if requested_start_date is not None else None
            ),
            rows=clean.height,
            tier=instrument.tier,
            quality_status=quality_status,
            trade_enabled=trade_enabled,
            issue_counts=audit.issue_counts,
            warning_counts=audit.warning_counts,
            checksum=checksum,
        )
        update_futures_manifest(cache_dir, meta)

    return FuturesSyncResult(
        symbol=instrument.code,
        success=True,
        wrote_cache=apply,
        quality_status=quality_status,
        rows_before=rows_before,
        rows_after=clean.height,
        message=(
            f"quality={quality_status}; effective_start={clean['date'][0]}; "
            f"rows={clean.height}"
        ),
    )


def sync_futures_universe(
    symbols: Iterable[str],
    *,
    cache_dir: Path,
    requested_start_date: date | None = None,
    force: bool = False,
    max_workers: int = 4,
    fetch_fn: Callable[[str], pd.DataFrame] | None = None,
    apply: bool = True,
) -> list[FuturesSyncResult]:
    """Synchronize a bounded set of products without losing manifest entries."""

    requested = [symbol.strip().upper() for symbol in symbols if symbol.strip()]
    if not requested:
        return []
    workers = max(1, min(int(max_workers), len(requested)))
    with ThreadPoolExecutor(max_workers=workers, thread_name_prefix="futures-sync") as pool:
        futures = [
            pool.submit(
                sync_futures_symbol,
                symbol,
                cache_dir=cache_dir,
                requested_start_date=requested_start_date,
                force=force,
                fetch_fn=fetch_fn,
                apply=apply,
            )
            for symbol in requested
        ]
        return [future.result() for future in futures]
