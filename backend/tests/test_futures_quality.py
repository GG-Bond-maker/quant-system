"""Tests for futures cache quality auditing and cleanup."""
from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import polars as pl
import pytest

from app.data.futures_quality import (
    QualityThresholds,
    audit_futures_frame,
    run_futures_quality,
)
from app.data.futures_store import (
    FuturesCacheMeta,
    parquet_checksum,
    read_futures_manifest,
    write_futures_manifest,
)


def _frame(
    dates: list[str],
    closes: list[float],
    *,
    highs: list[float] | None = None,
    lows: list[float] | None = None,
    volumes: list[int] | None = None,
    open_interest: list[int] | None = None,
) -> pl.DataFrame:
    size = len(dates)
    return pl.DataFrame(
        {
            "date": dates,
            "open": closes,
            "high": highs or [value + 1.0 for value in closes],
            "low": lows or [value - 1.0 for value in closes],
            "close": closes,
            "volume": volumes or [1000] * size,
            "open_interest": open_interest or [1000] * size,
        }
    )


def test_audit_removes_invalid_ohlc_and_records_issue() -> None:
    frame = _frame(
        ["2020-01-02", "2020-01-03"],
        [100.0, 105.0],
        highs=[101.0, 104.0],
    )

    result = audit_futures_frame(frame, "IF")

    assert result.clean.height == 1
    assert result.clean["close"].to_list() == [100.0]
    assert result.issue_counts == {"invalid_ohlc": 1}


def test_audit_keeps_zero_open_interest_as_warning() -> None:
    frame = _frame(
        ["2020-01-02"],
        [100.0],
        open_interest=[0],
    )

    result = audit_futures_frame(frame, "IF")

    assert result.clean.height == 1
    assert result.warning_counts == {"zero_open_interest": 1}
    assert result.issue_counts == {}


def test_audit_removes_extreme_move_only_when_liquidity_collapses() -> None:
    frame = _frame(
        [f"2020-01-{day:02d}" for day in range(2, 8)],
        [100.0, 101.0, 102.0, 103.0, 104.0, 150.0],
        volumes=[1000, 1000, 1000, 1000, 1000, 1],
        open_interest=[1000, 1000, 1000, 1000, 1000, 1],
    )

    result = audit_futures_frame(
        frame,
        "I",
        thresholds=QualityThresholds(min_liquidity_samples=3),
    )

    assert result.clean.height == 5
    assert result.issue_counts == {"extreme_return_low_liquidity": 1}
    assert result.quarantine["date"].to_list() == ["2020-01-07"]


def test_audit_keeps_last_row_for_duplicate_date() -> None:
    frame = _frame(
        ["2020-01-02", "2020-01-02"],
        [100.0, 101.0],
    )

    result = audit_futures_frame(frame, "IF")

    assert result.clean.height == 1
    assert result.clean["close"].to_list() == [101.0]
    assert result.issue_counts == {"duplicate_date": 1}


def test_audit_rejects_missing_required_columns() -> None:
    with pytest.raises(ValueError, match="missing required columns"):
        audit_futures_frame(pl.DataFrame({"date": ["2020-01-02"]}), "IF")


def test_audit_accepts_input_with_existing_symbol_column() -> None:
    frame = _frame(["2020-01-02"], [100.0]).with_columns(
        pl.lit("IF").alias("symbol")
    )

    result = audit_futures_frame(frame, "IF")

    assert result.clean.height == 1
    assert "symbol" not in result.clean.columns


def test_run_quality_dry_run_does_not_modify_cache(tmp_path: Path) -> None:
    cache_dir = tmp_path / "cache"
    cache_dir.mkdir()
    target = cache_dir / "IF.parquet"
    _frame(
        ["2020-01-02", "2020-01-03"],
        [100.0, 105.0],
        highs=[101.0, 104.0],
    ).write_parquet(target)
    original_bytes = target.read_bytes()

    result = run_futures_quality(
        cache_dir=cache_dir,
        backup_root=tmp_path / "backups",
        report_root=tmp_path / "reports",
        apply=False,
        expected_symbols={"IF"},
        timestamp="20260101_000000",
    )

    assert target.read_bytes() == original_bytes
    assert result.backup_dir is None
    assert result.report_path.exists()
    assert result.quarantine_path is not None
    assert result.quarantine_path.exists()


def test_run_quality_apply_backs_up_then_rewrites(tmp_path: Path) -> None:
    cache_dir = tmp_path / "cache"
    cache_dir.mkdir()
    target = cache_dir / "IF.parquet"
    _frame(
        ["2020-01-02", "2020-01-03"],
        [100.0, 105.0],
        highs=[101.0, 104.0],
    ).write_parquet(target)

    result = run_futures_quality(
        cache_dir=cache_dir,
        backup_root=tmp_path / "backups",
        report_root=tmp_path / "reports",
        apply=True,
        expected_symbols={"IF"},
        timestamp="20260101_000000",
    )

    assert result.backup_dir is not None
    backup = result.backup_dir / "IF.parquet"
    assert backup.exists()
    assert pl.read_parquet(backup).height == 2
    assert pl.read_parquet(target).height == 1
    assert result.removed_rows == 1

    report = json.loads(result.report_path.read_text(encoding="utf-8"))
    assert report["apply"] is True
    assert report["removed_rows"] == 1
    assert report["symbols"]["IF"]["removed_rows"] == 1


def test_run_quality_apply_updates_manifest_facts(tmp_path: Path) -> None:
    cache_dir = tmp_path / "cache"
    cache_dir.mkdir()
    target = cache_dir / "IF.parquet"
    _frame(
        ["2020-01-02", "2020-01-03"],
        [100.0, 105.0],
        highs=[101.0, 104.0],
    ).write_parquet(target)
    write_futures_manifest(
        cache_dir,
        {
            "IF": FuturesCacheMeta(
                symbol="IF",
                source="sina_main",
                source_symbol="IF0",
                fetched_at="2026-10-30T18:00:00+08:00",
                listing_date="2010-04-16",
                raw_source_first_date="2020-01-02",
                source_first_date="2020-01-02",
                source_last_date="2020-01-03",
                cache_first_date="2020-01-02",
                effective_start="2020-01-02",
                requested_start_date=None,
                rows=2,
                tier="core",
                quality_status="ok",
                trade_enabled=True,
                issue_counts={},
                warning_counts={},
                checksum="sha256:stale",
            )
        },
    )

    run_futures_quality(
        cache_dir=cache_dir,
        backup_root=tmp_path / "backups",
        report_root=tmp_path / "reports",
        apply=True,
        expected_symbols={"IF"},
        timestamp="20260101_000000",
    )

    meta = read_futures_manifest(cache_dir)["IF"]
    assert meta.rows == 1
    assert meta.cache_first_date == "2020-01-02"
    assert meta.effective_start == "2020-01-02"
    assert meta.checksum == parquet_checksum(target)
    assert meta.trade_enabled is True


def test_quality_cli_reports_without_modifying_cache(tmp_path: Path) -> None:
    cache_dir = tmp_path / "cache"
    cache_dir.mkdir()
    target = cache_dir / "IF.parquet"
    _frame(["2020-01-02"], [100.0]).write_parquet(target)
    original_bytes = target.read_bytes()
    script = Path(__file__).resolve().parents[1] / "scripts" / "check_futures_data_quality.py"

    completed = subprocess.run(
        [
            sys.executable,
            str(script),
            "--cache-dir",
            str(cache_dir),
            "--backup-root",
            str(tmp_path / "backups"),
            "--report-root",
            str(tmp_path / "reports"),
        ],
        cwd=script.parents[1],
        capture_output=True,
        text=True,
        check=False,
    )

    assert completed.returncode == 0, completed.stderr
    assert "Futures quality audit" in completed.stdout
    assert target.read_bytes() == original_bytes
    reports = list((tmp_path / "reports").glob("futures_quality_*.json"))
    assert reports
    report = json.loads(reports[0].read_text(encoding="utf-8"))
    assert "CF" in report["missing_symbols"]
    assert "IF" not in report["missing_symbols"]
