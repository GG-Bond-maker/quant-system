"""Futures cache quality auditing and safe cleanup.

The cleaner removes only structural data errors and extreme moves accompanied by
a collapse in local liquidity. Valid high-volatility sessions and zero open
interest rows are reported but retained because dropping them would silently
change the historical sample.
"""
from __future__ import annotations

import json
import shutil
from collections.abc import Iterable
from dataclasses import dataclass, replace
from datetime import datetime
from pathlib import Path
from typing import Any

import polars as pl

from .futures_store import (
    parquet_checksum,
    read_futures_manifest,
    write_futures_manifest,
)
from .parquet_store import atomic_write_parquet

REQUIRED_COLUMNS: tuple[str, ...] = (
    "date",
    "open",
    "high",
    "low",
    "close",
    "volume",
    "open_interest",
)

NUMERIC_COLUMNS: tuple[str, ...] = (
    "open",
    "high",
    "low",
    "close",
    "volume",
    "open_interest",
)

@dataclass(frozen=True)
class QualityThresholds:
    """Thresholds used by the futures data quality audit."""

    extreme_return_abs: float = 0.30
    high_return_warning_abs: float = 0.10
    low_liquidity_ratio: float = 0.01
    liquidity_window: int = 21
    min_liquidity_samples: int = 5

    def __post_init__(self) -> None:
        if not 0 < self.high_return_warning_abs < self.extreme_return_abs:
            raise ValueError(
                "high_return_warning_abs must be positive and below extreme_return_abs"
            )
        if not 0 < self.low_liquidity_ratio < 1:
            raise ValueError("low_liquidity_ratio must be between 0 and 1")
        if self.liquidity_window < 2:
            raise ValueError("liquidity_window must be at least 2")
        if not 2 <= self.min_liquidity_samples <= self.liquidity_window:
            raise ValueError(
                "min_liquidity_samples must be between 2 and liquidity_window"
            )


@dataclass
class FuturesFrameAudit:
    """Audit result for one futures symbol."""

    symbol: str
    path: Path
    original_rows: int
    clean: pl.DataFrame
    quarantine: pl.DataFrame
    issue_counts: dict[str, int]
    warning_counts: dict[str, int]
    first_date: str | None
    last_date: str | None

    @property
    def clean_rows(self) -> int:
        return int(self.clean.height)

    @property
    def removed_rows(self) -> int:
        return self.original_rows - self.clean_rows

    def summary(self) -> dict[str, Any]:
        """Return a JSON-serializable symbol summary."""

        return {
            "rows_before": self.original_rows,
            "rows_after": self.clean_rows,
            "removed_rows": self.removed_rows,
            "first": self.first_date,
            "last": self.last_date,
            "issue_counts": self.issue_counts,
            "warning_counts": self.warning_counts,
        }


@dataclass
class FuturesCacheAudit:
    """Aggregate audit result for a futures cache directory."""

    cache_dir: Path
    frames: dict[str, FuturesFrameAudit]
    missing_symbols: list[str]
    unexpected_files: list[str]

    @property
    def total_rows_before(self) -> int:
        return sum(frame.original_rows for frame in self.frames.values())

    @property
    def total_rows_after(self) -> int:
        return sum(frame.clean_rows for frame in self.frames.values())

    @property
    def removed_rows(self) -> int:
        return self.total_rows_before - self.total_rows_after

    @property
    def issue_counts(self) -> dict[str, int]:
        return _sum_counts(frame.issue_counts for frame in self.frames.values())

    @property
    def warning_counts(self) -> dict[str, int]:
        return _sum_counts(frame.warning_counts for frame in self.frames.values())


@dataclass(frozen=True)
class FuturesCleanupResult:
    """Paths and counts produced by a quality run."""

    report_path: Path
    quarantine_path: Path | None
    backup_dir: Path | None
    total_rows_before: int
    total_rows_after: int
    removed_rows: int
    issue_counts: dict[str, int]
    warning_counts: dict[str, int]
    symbols: dict[str, dict[str, Any]]


def _sum_counts(count_groups: Iterable[dict[str, int]]) -> dict[str, int]:
    totals: dict[str, int] = {}
    for counts in count_groups:
        for code, count in counts.items():
            totals[code] = totals.get(code, 0) + count
    return dict(sorted(totals.items()))


def _false_if_null(expr: pl.Expr) -> pl.Expr:
    return expr.fill_null(False)


def _issue_frame(
    frame: pl.DataFrame,
    mask: pl.Expr,
    *,
    symbol: str,
    issue_code: str,
    issue_detail: str,
    original_columns: list[str],
) -> pl.DataFrame:
    return (
        frame.filter(_false_if_null(mask))
        .with_columns(
            pl.lit(symbol).alias("symbol"),
            pl.lit(issue_code).alias("issue_code"),
            pl.lit(issue_detail).alias("issue_detail"),
        )
        .select(["symbol", *original_columns, "issue_code", "issue_detail"])
    )


def _empty_issue_frame(frame: pl.DataFrame) -> pl.DataFrame:
    schema: dict[str, Any] = {"symbol": pl.String}
    schema.update({column: frame.schema[column] for column in REQUIRED_COLUMNS})
    schema.update({"issue_code": pl.String, "issue_detail": pl.String})
    return pl.DataFrame(schema=schema)


def _count_mask(frame: pl.DataFrame, mask: pl.Expr) -> int:
    if frame.is_empty():
        return 0
    value = frame.select(_false_if_null(mask).cast(pl.Int64).sum().alias("n")).item()
    return int(value or 0)


def audit_futures_frame(
    frame: pl.DataFrame,
    symbol: str,
    *,
    thresholds: QualityThresholds | None = None,
) -> FuturesFrameAudit:
    """Audit one futures frame and return cleaned and quarantined records."""

    thresholds = thresholds or QualityThresholds()
    missing = [column for column in REQUIRED_COLUMNS if column not in frame.columns]
    if missing:
        raise ValueError(f"{symbol}: missing required columns: {', '.join(missing)}")

    original_columns = [column for column in frame.columns if column != "symbol"]
    work = frame.drop("symbol") if "symbol" in frame.columns else frame.clone()
    work = work.with_columns(
        pl.int_range(0, pl.len(), dtype=pl.Int64).alias("_row_id"),
        pl.col("date").cast(pl.Utf8).alias("_date_key"),
    )
    parsed_date = pl.col("_date_key").str.strptime(pl.Date, "%Y-%m-%d", strict=False)

    null_mask = pl.any_horizontal([pl.col(column).is_null() for column in REQUIRED_COLUMNS])
    non_finite_mask = pl.any_horizontal(
        [
            (
                pl.col(column).is_not_null()
                & ~pl.col(column).cast(pl.Float64).is_finite()
            )
            for column in NUMERIC_COLUMNS
        ]
    )
    nonpositive_price_mask = pl.any_horizontal(
        [
            pl.col(column).cast(pl.Float64) <= 0
            for column in ("open", "high", "low", "close")
        ]
    )
    invalid_ohlc_mask = (
        (pl.col("high") < pl.max_horizontal("open", "close"))
        | (pl.col("low") > pl.min_horizontal("open", "close"))
        | (pl.col("high") < pl.col("low"))
    )
    negative_volume_mask = pl.col("volume") < 0
    negative_oi_mask = pl.col("open_interest") < 0
    invalid_date_mask = pl.col("_date_key").is_null() | parsed_date.is_null()

    structural_issues: list[tuple[str, pl.Expr, str]] = [
        ("invalid_date", invalid_date_mask, "date cannot be parsed as YYYY-MM-DD"),
        ("missing_value", null_mask, "one or more required fields are null"),
        ("non_finite_value", non_finite_mask, "numeric field is NaN or infinite"),
        ("nonpositive_price", nonpositive_price_mask, "OHLC price must be positive"),
        ("invalid_ohlc", invalid_ohlc_mask, "OHLC range is internally inconsistent"),
        ("negative_volume", negative_volume_mask, "volume must not be negative"),
        (
            "negative_open_interest",
            negative_oi_mask,
            "open interest must not be negative",
        ),
    ]
    structural_any = pl.any_horizontal([mask for _, mask, _ in structural_issues])

    issue_frames: list[pl.DataFrame] = []
    for code, mask, detail in structural_issues:
        issue_frames.append(
            _issue_frame(
                work,
                mask,
                symbol=symbol,
                issue_code=code,
                issue_detail=detail,
                original_columns=original_columns,
            )
        )

    valid = work.filter(~_false_if_null(structural_any)).sort(["_date_key", "_row_id"])
    keep_ids = valid.unique(
        subset=["_date_key"], keep="last", maintain_order=True
    ).select("_row_id")
    base = valid.join(keep_ids, on="_row_id", how="inner")
    duplicates = valid.join(keep_ids, on="_row_id", how="anti")
    issue_frames.append(
        _issue_frame(
            duplicates,
            pl.lit(True),
            symbol=symbol,
            issue_code="duplicate_date",
            issue_detail="duplicate date; the last row was retained",
            original_columns=original_columns,
        )
    )

    base = base.with_columns(
        (
            pl.col("close").cast(pl.Float64)
            / pl.col("close").cast(pl.Float64).shift(1)
            - 1.0
        ).alias("_return"),
        pl.col("volume")
        .cast(pl.Float64)
        .shift(1)
        .rolling_median(
            window_size=thresholds.liquidity_window,
            min_periods=thresholds.min_liquidity_samples,
        )
        .alias("_volume_median"),
        pl.col("open_interest")
        .cast(pl.Float64)
        .shift(1)
        .rolling_median(
            window_size=thresholds.liquidity_window,
            min_periods=thresholds.min_liquidity_samples,
        )
        .alias("_oi_median"),
    )
    volume_ratio = pl.col("volume").cast(pl.Float64) / pl.col("_volume_median")
    oi_ratio = (
        pl.col("open_interest").cast(pl.Float64) / pl.col("_oi_median")
    )
    low_liquidity = (
        ((pl.col("_volume_median") > 0) & (volume_ratio < thresholds.low_liquidity_ratio))
        | ((pl.col("_oi_median") > 0) & (oi_ratio < thresholds.low_liquidity_ratio))
    )
    extreme_move_mask = (
        (pl.col("_return").abs() > thresholds.extreme_return_abs)
        & low_liquidity
    )
    anomaly_rows = base.filter(_false_if_null(extreme_move_mask))
    issue_frames.append(
        _issue_frame(
            anomaly_rows,
            pl.lit(True),
            symbol=symbol,
            issue_code="extreme_return_low_liquidity",
            issue_detail=(
                "absolute return exceeded "
                f"{thresholds.extreme_return_abs:.0%} while volume or open "
                f"interest was below {thresholds.low_liquidity_ratio:.0%} "
                "of its prior local median"
            ),
            original_columns=original_columns,
        )
    )

    clean_base = base.filter(~_false_if_null(extreme_move_mask))
    warning_masks = {
        "zero_volume": pl.col("volume") == 0,
        "zero_open_interest": pl.col("open_interest") == 0,
        "high_volatility": (
            pl.col("_return").abs() > thresholds.high_return_warning_abs
        ),
    }
    warning_counts = {
        code: _count_mask(clean_base, mask) for code, mask in warning_masks.items()
    }
    warning_counts = {
        code: count for code, count in warning_counts.items() if count > 0
    }

    non_empty_issues = [part for part in issue_frames if not part.is_empty()]
    quarantine = (
        pl.concat(non_empty_issues, how="vertical_relaxed")
        if non_empty_issues
        else _empty_issue_frame(work)
    ).sort(["symbol", "date", "issue_code"])
    issue_counts = {
        str(row["issue_code"]): int(row["len"])
        for row in quarantine.group_by("issue_code").len().sort("issue_code").to_dicts()
    }

    clean = clean_base.select(original_columns)
    date_values = clean_base["_date_key"].drop_nulls().to_list()
    return FuturesFrameAudit(
        symbol=symbol,
        path=Path(),
        original_rows=frame.height,
        clean=clean,
        quarantine=quarantine,
        issue_counts=issue_counts,
        warning_counts=warning_counts,
        first_date=str(date_values[0]) if date_values else None,
        last_date=str(date_values[-1]) if date_values else None,
    )


def audit_futures_cache(
    cache_dir: Path,
    *,
    expected_symbols: Iterable[str] | None = None,
    thresholds: QualityThresholds | None = None,
) -> FuturesCacheAudit:
    """Audit every parquet file in a futures cache directory."""

    cache_dir = Path(cache_dir)
    if not cache_dir.exists():
        raise FileNotFoundError(f"futures cache directory does not exist: {cache_dir}")
    files = sorted(cache_dir.glob("*.parquet"))
    if not files:
        raise FileNotFoundError(f"no futures parquet files found in: {cache_dir}")

    frames: dict[str, FuturesFrameAudit] = {}
    for path in files:
        result = audit_futures_frame(
            pl.read_parquet(path),
            path.stem,
            thresholds=thresholds,
        )
        result.path = path
        frames[path.stem] = result

    expected = {symbol.upper() for symbol in expected_symbols or ()}
    found = set(frames)
    missing_symbols = sorted(expected - found)
    unexpected_files = sorted(found - expected) if expected else []
    return FuturesCacheAudit(
        cache_dir=cache_dir,
        frames=frames,
        missing_symbols=missing_symbols,
        unexpected_files=unexpected_files,
    )


def _unique_backup_dir(backup_root: Path, timestamp: str) -> Path:
    candidate = backup_root / f"futures_cache_{timestamp}"
    suffix = 1
    while candidate.exists():
        candidate = backup_root / f"futures_cache_{timestamp}_{suffix:02d}"
        suffix += 1
    return candidate


def _ensure_outside_cache(target: Path, cache_dir: Path) -> None:
    cache_resolved = cache_dir.resolve()
    target_resolved = target.resolve()
    if target_resolved == cache_resolved or cache_resolved in target_resolved.parents:
        raise ValueError(f"output directory must be outside the cache: {target}")


def run_futures_quality(
    *,
    cache_dir: Path,
    backup_root: Path,
    report_root: Path,
    apply: bool = False,
    expected_symbols: Iterable[str] | None = None,
    thresholds: QualityThresholds | None = None,
    timestamp: str | None = None,
) -> FuturesCleanupResult:
    """Audit a futures cache, write a report, and optionally clean in place."""

    cache_dir = Path(cache_dir)
    backup_root = Path(backup_root)
    report_root = Path(report_root)
    _ensure_outside_cache(report_root, cache_dir)
    _ensure_outside_cache(backup_root, cache_dir)

    thresholds = thresholds or QualityThresholds()
    audit = audit_futures_cache(
        cache_dir,
        expected_symbols=expected_symbols,
        thresholds=thresholds,
    )
    timestamp = timestamp or datetime.now().astimezone().strftime("%Y%m%d_%H%M%S")
    report_root.mkdir(parents=True, exist_ok=True)

    quarantine_frames = [
        frame.quarantine for frame in audit.frames.values() if not frame.quarantine.is_empty()
    ]
    quarantine_path: Path | None = None
    if quarantine_frames:
        quarantine = pl.concat(quarantine_frames, how="vertical_relaxed")
        quarantine_path = report_root / f"futures_quarantine_{timestamp}.parquet"
        atomic_write_parquet(quarantine_path, quarantine)

    backup_dir: Path | None = None
    if apply:
        backup_root.mkdir(parents=True, exist_ok=True)
        backup_dir = _unique_backup_dir(backup_root, timestamp)
        backup_dir.mkdir(parents=True)
        source_files = sorted(cache_dir.glob("*.parquet"))
        for source in source_files:
            shutil.copy2(source, backup_dir / source.name)
        if len(list(backup_dir.glob("*.parquet"))) != len(source_files):
            raise RuntimeError(f"futures cache backup incomplete: {backup_dir}")

        for frame in audit.frames.values():
            if frame.removed_rows:
                atomic_write_parquet(frame.path, frame.clean)

        manifest = read_futures_manifest(cache_dir)
        if manifest:
            for symbol, frame in audit.frames.items():
                meta = manifest.get(symbol)
                if meta is None:
                    continue
                first_date = (
                    str(frame.clean["date"][0]) if not frame.clean.is_empty() else None
                )
                last_date = (
                    str(frame.clean["date"][-1]) if not frame.clean.is_empty() else None
                )
                manifest[symbol] = replace(
                    meta,
                    rows=frame.clean.height,
                    source_last_date=last_date,
                    cache_first_date=first_date,
                    effective_start=first_date,
                    quality_status=(
                        "failed" if frame.clean.is_empty() else meta.quality_status
                    ),
                    trade_enabled=(
                        False if frame.clean.is_empty() else meta.trade_enabled
                    ),
                    issue_counts={},
                    warning_counts=frame.warning_counts,
                    checksum=parquet_checksum(frame.path),
                )
            write_futures_manifest(cache_dir, manifest)

    report_path = report_root / f"futures_quality_{timestamp}.json"
    report = {
        "generated_at": datetime.now().astimezone().isoformat(),
        "apply": apply,
        "cache_dir": str(cache_dir),
        "backup_dir": str(backup_dir) if backup_dir else None,
        "quarantine_path": str(quarantine_path) if quarantine_path else None,
        "thresholds": {
            "extreme_return_abs": thresholds.extreme_return_abs,
            "high_return_warning_abs": thresholds.high_return_warning_abs,
            "low_liquidity_ratio": thresholds.low_liquidity_ratio,
            "liquidity_window": thresholds.liquidity_window,
            "min_liquidity_samples": thresholds.min_liquidity_samples,
        },
        "file_count": len(audit.frames),
        "rows_before": audit.total_rows_before,
        "rows_after": audit.total_rows_after,
        "removed_rows": audit.removed_rows,
        "issue_counts": audit.issue_counts,
        "warning_counts": audit.warning_counts,
        "missing_symbols": audit.missing_symbols,
        "unexpected_files": audit.unexpected_files,
        "symbols": {
            symbol: frame.summary()
            for symbol, frame in sorted(audit.frames.items())
        },
    }
    tmp_report = report_path.with_suffix(".json.tmp")
    tmp_report.write_text(
        json.dumps(report, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    tmp_report.replace(report_path)

    return FuturesCleanupResult(
        report_path=report_path,
        quarantine_path=quarantine_path,
        backup_dir=backup_dir,
        total_rows_before=audit.total_rows_before,
        total_rows_after=audit.total_rows_after,
        removed_rows=audit.removed_rows,
        issue_counts=audit.issue_counts,
        warning_counts=audit.warning_counts,
        symbols={symbol: frame.summary() for symbol, frame in audit.frames.items()},
    )
