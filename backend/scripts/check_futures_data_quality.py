"""Audit and optionally clean the local futures cache.

Examples:
    python scripts/check_futures_data_quality.py
    python scripts/check_futures_data_quality.py --apply
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

BACKEND_ROOT = Path(__file__).resolve().parents[1]
PROJECT_ROOT = BACKEND_ROOT.parent
sys.path.insert(0, str(BACKEND_ROOT))

from app.core.config import get_settings
from app.data.futures_quality import (
    QualityThresholds,
    run_futures_quality,
)
from app.domain.futures_rules import FUTURES_SPECS


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Audit futures cache quality and optionally clean it in place."
    )
    parser.add_argument(
        "--cache-dir",
        type=Path,
        default=None,
        help="Futures cache directory; defaults to DATA_ROOT/futures_cache.",
    )
    parser.add_argument(
        "--backup-root",
        type=Path,
        default=PROJECT_ROOT / "data" / "backups",
        help="Root directory for timestamped pre-clean backups.",
    )
    parser.add_argument(
        "--report-root",
        type=Path,
        default=PROJECT_ROOT / "data" / "quality_reports",
        help="Directory for JSON reports and quarantined rows.",
    )
    parser.add_argument(
        "--apply",
        action="store_true",
        help="Back up the cache and rewrite cleaned parquet files.",
    )
    parser.add_argument(
        "--extreme-return",
        type=float,
        default=0.30,
        help="Absolute return threshold for low-liquidity anomaly detection.",
    )
    parser.add_argument(
        "--low-liquidity-ratio",
        type=float,
        default=0.01,
        help="Volume/OI ratio versus local median that counts as a liquidity collapse.",
    )
    parser.add_argument(
        "--window",
        type=int,
        default=21,
        help="Rolling median window for volume and open interest.",
    )
    parser.add_argument(
        "--min-samples",
        type=int,
        default=5,
        help="Minimum prior observations required for the rolling liquidity median.",
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    """Run the futures quality audit and return a process exit code."""

    args = _build_parser().parse_args(argv)
    cache_dir = args.cache_dir or (get_settings().DATA_ROOT / "futures_cache")
    thresholds = QualityThresholds(
        extreme_return_abs=args.extreme_return,
        low_liquidity_ratio=args.low_liquidity_ratio,
        liquidity_window=args.window,
        min_liquidity_samples=args.min_samples,
    )

    result = run_futures_quality(
        cache_dir=cache_dir,
        backup_root=args.backup_root,
        report_root=args.report_root,
        apply=args.apply,
        expected_symbols=FUTURES_SPECS,
        thresholds=thresholds,
    )

    mode = "APPLY" if args.apply else "DRY-RUN"
    print(f"Futures quality audit ({mode})")
    print(f"cache: {cache_dir}")
    print(
        f"rows: {result.total_rows_before} -> {result.total_rows_after}; "
        f"removed: {result.removed_rows}"
    )
    print(f"issues: {result.issue_counts or 'none'}")
    print(f"warnings: {result.warning_counts or 'none'}")
    print(f"report: {result.report_path}")
    if result.quarantine_path:
        print(f"quarantine: {result.quarantine_path}")
    if result.backup_dir:
        print(f"backup: {result.backup_dir}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
