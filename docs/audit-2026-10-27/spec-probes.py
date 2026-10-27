"""Read-only business probes using temporary stores and mocked network/model calls.

Run from the repository root:
    backend/.venv/Scripts/python.exe docs/audit-2026-10-27/spec-probes.py

Only the adjacent JSON report persists. All business data writes target temporary
directories. These probes record current behavior; they do not modify app code.
"""
from __future__ import annotations

import importlib.util
import json
import sqlite3
import sys
import tempfile
from datetime import date
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import pandas as pd

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "backend"))


def probe_cli_metadata() -> dict:
    spec = importlib.util.spec_from_file_location(
        "audit_infer_cli", ROOT / "backend/scripts/infer.py"
    )
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    with tempfile.TemporaryDirectory(prefix="aqp-audit-infer-") as tmp:
        root = Path(tmp)
        feature_dir = root / "features" / f"version={module.FEATURE_VERSION}"
        feature_dir.mkdir(parents=True)
        pd.DataFrame({
            "date": [pd.Timestamp("2026-10-26")],
            "symbol": ["000001.SZ"], "close": [10.0],
        }).to_parquet(feature_dir / "year=2026.parquet")
        with (
            patch.object(module, "get_settings", return_value=SimpleNamespace(DATA_ROOT=root)),
            patch.object(module, "setup_logging"),
            patch.object(module, "load_prod_model", return_value=(None, ["close"], root / "fake_model")),
            patch.object(module, "predict_with_contrib", return_value=(pd.Series([0.01]).to_numpy(), None)),
            patch.object(sys, "argv", ["infer.py"]),
        ):
            module.main()
        output = pd.read_parquet(root / "predictions/date=20261026.parquet")
        return {
            "columns": output.columns.tolist(),
            "model_version_present": "model_version" in output.columns,
            "feature_version_present": "feature_version" in output.columns,
            "label_horizon_present": "label_horizon" in output.columns,
            "consumer_requirement": "api/v1/backtest.py:170-172 rejects absent model_version",
        }


def probe_hk_no_quotes() -> dict:
    from app.api.v1.screener import _screen_hk

    with patch("app.data.realtime.fetch_quotes_batch", return_value=([], "unavailable")):
        result = _screen_hk(date(2024, 1, 2), "alpha_basic_v1", 10, "all")
    return {
        **{k: result[k] for k in ("date", "as_of", "status", "count", "coverage", "message")},
        "first_candidate": {k: result["items"][0][k] for k in (
            "symbol", "close", "score", "signal_strength"
        )},
        "network": "mocked empty quote response; no external requests",
    }


def probe_financial_scheduled_visibility() -> dict:
    from app.data.ingest.financials import load_financials_asof

    with tempfile.TemporaryDirectory(prefix="aqp-audit-financial-") as tmp:
        sqlite_path = Path(tmp) / "financial-probe.db"
        with sqlite3.connect(sqlite_path) as conn:
            conn.execute("""
                CREATE TABLE financial_report (
                    symbol TEXT, period TEXT, announce_date TEXT,
                    revenue REAL, net_profit REAL, roe REAL, roa REAL,
                    eps REAL, is_proxy_announce INTEGER, announce_basis TEXT,
                    gross_margin REAL, bps REAL
                )
            """)
            conn.executemany(
                "INSERT INTO financial_report VALUES (?,?,?,?,?,?,?,?,?,?,?,?)",
                [
                    ("PITPROBE.SH", "2023-12-31", "2024-04-01", 999.0, 99.0,
                     10.0, 5.0, 1.0, 1, "scheduled", 30.0, 8.0),
                    ("PITPROBE.SH", "2023-12-31", "2024-04-20", 999.0, 99.0,
                     10.0, 5.0, 1.0, 0, "actual", 30.0, 8.0),
                ],
            )
        conn.close()
        with patch("app.core.config.get_settings", return_value=SimpleNamespace(SQLITE_PATH=sqlite_path)):
            before_scheduled = load_financials_asof("PITPROBE.SH", date(2024, 3, 31))
            between_dates = load_financials_asof("PITPROBE.SH", date(2024, 4, 10))
            after_actual = load_financials_asof("PITPROBE.SH", date(2024, 4, 21))
        return {
            "scheduled_date": "2024-04-01", "actual_date": "2024-04-20",
            "asof_between": "2024-04-10",
            "rows_before_scheduled": before_scheduled.height,
            "rows_before_actual": between_dates.height,
            "visible_before_actual": between_dates.to_dicts(),
            "visible_after_actual": after_actual.to_dicts(),
            "expected_before_actual": "No numeric financial values should be visible before actual disclosure",
            "premature_numeric_visibility": bool(between_dates.height),
            "note": "Synthetic late-disclosure scenario; actual application SQL executed against temporary SQLite",
        }


if __name__ == "__main__":
    report = {
        "audit_date": "2026-10-27",
        "cli_prediction_metadata": probe_cli_metadata(),
        "hk_empty_quote_response": probe_hk_no_quotes(),
        "financial_scheduled_pit": probe_financial_scheduled_visibility(),
        "scope": "Temporary data and mocks only; no production database or dataset changes",
    }
    target = Path(__file__).with_suffix(".json")
    target.write_text(json.dumps(report, ensure_ascii=False, indent=2, default=str), encoding="utf-8")
    print(str(target), flush=True)
