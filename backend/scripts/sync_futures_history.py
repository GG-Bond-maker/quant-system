"""Synchronize the configured futures universe from the real Sina source."""
from __future__ import annotations

import argparse
import sys
from collections.abc import Callable
from datetime import date
from pathlib import Path

import pandas as pd

BACKEND_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(BACKEND_ROOT))

from app.core.config import get_settings
from app.data.futures_sync import FuturesSyncResult, sync_futures_universe
from app.domain.futures_catalog import (
    FUTURES_CORE,
    FUTURES_EXTENDED,
    get_futures_instrument,
)


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Fetch real futures history into the manifest-backed parquet cache."
    )
    parser.add_argument("--tier", choices=("core", "extended", "all"), default=None)
    parser.add_argument("--symbols", default=None, help="Comma-separated product codes.")
    parser.add_argument("--start-date", default=None, help="Optional YYYY-MM-DD cutoff.")
    parser.add_argument("--force", action="store_true", help="Ignore the fresh-cache window.")
    parser.add_argument(
        "--apply",
        action="store_true",
        help="Write parquet caches and manifest updates; default is a dry run.",
    )
    parser.add_argument("--max-workers", type=int, default=4)
    parser.add_argument(
        "--cache-dir",
        type=Path,
        default=None,
        help=argparse.SUPPRESS,
    )
    return parser


def _selected_symbols(args: argparse.Namespace, parser: argparse.ArgumentParser) -> list[str]:
    if args.symbols:
        requested = [part.strip().upper() for part in args.symbols.split(",") if part.strip()]
        unknown = [symbol for symbol in requested if get_futures_instrument(symbol) is None]
        if unknown:
            parser.error(f"unknown futures symbols: {', '.join(unknown)}")
        return requested

    if args.tier is None:
        parser.error("at least one of --tier or --symbols is required")
    if args.tier == "core":
        return list(FUTURES_CORE)
    if args.tier == "extended":
        return list(FUTURES_EXTENDED)
    return [*FUTURES_CORE, *FUTURES_EXTENDED]


def _print_result(result: FuturesSyncResult) -> None:
    print(
        f"{result.symbol}: success={result.success} wrote_cache={result.wrote_cache} "
        f"quality={result.quality_status} rows={result.rows_before}->{result.rows_after} "
        f"{result.message}"
    )


def main(
    argv: list[str] | None = None,
    *,
    fetch_fn: Callable[[str], pd.DataFrame] | None = None,
) -> int:
    parser = _build_parser()
    args = parser.parse_args(argv)
    symbols = _selected_symbols(args, parser)
    try:
        requested_start_date = (
            date.fromisoformat(args.start_date) if args.start_date else None
        )
    except ValueError:
        parser.error("--start-date must use YYYY-MM-DD")

    cache_dir = args.cache_dir or (get_settings().DATA_ROOT / "futures_cache")
    results = sync_futures_universe(
        symbols,
        cache_dir=cache_dir,
        requested_start_date=requested_start_date,
        force=args.force,
        max_workers=args.max_workers,
        fetch_fn=fetch_fn,
        apply=args.apply,
    )

    core_requested = {symbol for symbol in symbols if get_futures_instrument(symbol).tier == "core"}
    print(f"{len(symbols)} symbols requested ({len(core_requested)} core); apply={args.apply}")
    for result in results:
        _print_result(result)

    failed_core = [
        result
        for result in results
        if result.symbol in core_requested and result.quality_status != "ok"
    ]
    if failed_core:
        print(
            "core quality failures: "
            + ", ".join(f"{result.symbol}={result.quality_status}" for result in failed_core)
        )
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
