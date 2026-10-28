"""Point-in-time and inference contracts, including real feature and CLI wiring."""
from datetime import date
import importlib.util
import json
from pathlib import Path
import sqlite3
import sys
from types import SimpleNamespace

import numpy as np
import pandas as pd
import pytest

from app.ml import signal_contract
from app.ml.calibration import fit_calibration, calibrated_values, evaluate_calibration
from app.ml.features import FEATURE_VERSION


def model_artifact(tmp_path, *, available="2024-01-10", horizon=7):
    root = tmp_path / "model"
    root.mkdir(exist_ok=True)
    metrics = {"horizon": horizon, "model_available_on": available,
               "split": {"train_end": "2023-11-01", "valid_end": "2023-12-20", "test_start": "2024-01-01"},
               "train_basis": {"feature_version": FEATURE_VERSION, "target": "absolute_forward_return"}}
    (root / "metrics.json").write_text(json.dumps(metrics), encoding="utf-8")
    return root


def test_model_time_and_horizon_metadata_are_binding(tmp_path, monkeypatch):
    model = model_artifact(tmp_path)
    monkeypatch.setattr(signal_contract, "predict_with_contrib", lambda *args: (np.array([.02]), None))
    panel = pd.DataFrame({"date": [date(2024, 1, 9)], "symbol": ["A.SZ"]})
    with pytest.raises(ValueError, match="前视"):
        signal_contract.predict_panel(panel, None, [], model)
    panel["date"] = date(2024, 1, 11)
    out = signal_contract.predict_panel(panel, None, [], model)
    assert out["label_horizon"].iloc[0] == 7
    assert out["model_available_on"].iloc[0] == "2024-01-10"
    assert out["signal_id"].notna().all() and out["run_id"].notna().all()
    assert out["calibrated_probability"].isna().all()


def test_signals_without_provenance_cannot_be_backtested():
    with pytest.raises(ValueError, match="元数据"):
        signal_contract.validate_signals(pd.DataFrame({"date": ["2024-01-01"], "symbol": ["A"]}))


def test_inference_cli_writes_the_contract_consumed_by_backtest(tmp_path, monkeypatch):
    source = Path(__file__).resolve().parents[1] / "scripts/infer.py"
    spec = importlib.util.spec_from_file_location("closed_loop_infer", source)
    cli = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(cli)
    model = model_artifact(tmp_path)
    features = tmp_path / "features" / f"version={FEATURE_VERSION}"
    features.mkdir(parents=True)
    pd.DataFrame({"date": [date(2024, 1, 11)], "symbol": ["A.SZ"]}).to_parquet(features / "year=2024.parquet")
    monkeypatch.setattr(cli, "get_settings", lambda: SimpleNamespace(DATA_ROOT=tmp_path))
    monkeypatch.setattr(cli, "setup_logging", lambda: None)
    monkeypatch.setattr(cli, "load_prod_model", lambda: (None, [], model))
    monkeypatch.setattr(signal_contract, "predict_with_contrib", lambda *args: (np.array([.02]), None))
    monkeypatch.setattr(sys, "argv", ["infer.py"])
    cli.main()
    out = pd.read_parquet(tmp_path / "predictions/date=20240111.parquet")
    signal_contract.validate_signals(out)
    assert out["label_horizon"].iloc[0] == 7


def test_old_signals_are_not_silently_overwritten(tmp_path, monkeypatch):
    model = model_artifact(tmp_path)
    monkeypatch.setattr(signal_contract, "predict_with_contrib", lambda *args: (np.array([.02]), None))
    out = signal_contract.predict_panel(pd.DataFrame({"date": [date(2024, 1, 11)], "symbol": ["A.SZ"]}), None, [], model)
    signal_contract.write_predictions(out, tmp_path / "predictions")
    out["pred_score"] = 99
    with pytest.raises(ValueError, match="独立run"):
        signal_contract.write_predictions(out, tmp_path / "predictions")
    assert pd.read_parquet(tmp_path / "predictions/date=20240111.parquet")["pred_score"].iloc[0] == .02


def test_heldout_calibrator_has_probabilities_intervals_and_independent_metrics():
    pred = np.linspace(-.1, .1, 300)
    truth = pred + np.random.default_rng(5).normal(0, .025, 300)
    artifact = fit_calibration(pred[:200], truth[:200])
    values = calibrated_values(pred[200:], artifact)
    assert artifact["available"] and values is not None
    probability, lower, upper = values
    assert ((probability >= 0) & (probability <= 1)).all()
    assert (lower < upper).all()
    report = evaluate_calibration(pred[200:], truth[200:], artifact)
    assert 0 <= report["brier"] <= 1 and 0 <= report["ece"] <= 1
    assert 0 <= report["interval_coverage"] <= 1
    assert not fit_calibration(np.array([.01]), np.array([.02]))["available"]


def test_scheduled_disclosure_never_releases_numeric_reports(tmp_path, monkeypatch):
    from app.data.ingest.financials import load_financials_asof
    from app.core import config
    db = tmp_path / "financial.db"
    with sqlite3.connect(db) as conn:
        conn.execute("CREATE TABLE financial_report(symbol,period,announce_date,revenue,net_profit,roe,roa,eps,is_proxy_announce,announce_basis,gross_margin,bps)")
        conn.executemany("INSERT INTO financial_report VALUES (?,?,?,?,?,?,?,?,?,?,?,?)", [
            ("A.SZ", "2023-12-31", "2024-04-01", 999, 99, 10, 5, 1, 1, "scheduled", 30, 8),
            ("A.SZ", "2023-12-31", "2024-04-20", 999, 99, 10, 5, 1, 0, "actual", 30, 8)])
    monkeypatch.setattr(config, "get_settings", lambda: SimpleNamespace(SQLITE_PATH=db))
    assert load_financials_asof("A.SZ", date(2024, 4, 10)).is_empty()
    assert load_financials_asof("A.SZ", date(2024, 4, 21)).height == 1


def test_financial_changes_do_not_rewrite_previously_known_yoy():
    from app.ml.financial_factors import build_financial_factors
    panel = pd.DataFrame({"symbol": ["A.SZ"] * 2, "date": [date(2024, 4, 22), date(2024, 5, 22)]})
    reports = pd.DataFrame([
        {"period": "2023-03-31", "announce_date": "2023-04-20", "revenue": 100., "roe": 10.},
        {"period": "2024-03-31", "announce_date": "2024-04-20", "revenue": 120., "roe": 12.},
        {"period": "2023-03-31", "announce_date": "2024-05-20", "revenue": 200., "roe": 10.},
    ])
    out = build_financial_factors(panel, source=lambda *_: reports, enabled=True)
    assert out["f_revenue_yoy"].iloc[0] == pytest.approx(.2)
    assert out["f_revenue_yoy"].iloc[1] == pytest.approx(-.4)
    assert out["f_roe"].iloc[1] == 12


def test_hk_outage_does_not_generate_ranked_candidates(monkeypatch):
    from app.api.v1.screener import _screen_hk
    from app.data import realtime
    from app.core.errors import AQPException
    monkeypatch.setattr(realtime, "fetch_quotes_batch", lambda *_: ([], "unavailable"))
    out = _screen_hk(None, "hk", 10, "all")
    assert out["status"] == "degraded" and out["items"] == []
    assert out["coverage"]["available"] == 0 and out["coverage"]["ratio"] == 0
    with pytest.raises(AQPException):
        _screen_hk(date(2024, 1, 1), "hk", 10, "all")


def test_beta_neutral_training_does_not_use_future_test_outcomes(tmp_path):
    from app.ml.train_lgbm import train_lgbm
    import lightgbm as lgb
    dates = pd.bdate_range("2023-01-02", periods=230)
    rng = np.random.default_rng(91)
    frames = []
    market = rng.normal(.0001, .01, len(dates))
    for index in range(3):
        returns = market * (index + 1) + rng.normal(0, .005, len(dates))
        frames.append(pd.DataFrame({"date": dates, "symbol": f"B{index}",
            "close": 10 * np.exp(np.cumsum(returns)), "factor": np.roll(returns, 1)}))
    first = pd.concat(frames, ignore_index=True)
    second = first.copy()
    # Change only test outcomes; neither feature selection nor training targets may change.
    selected = (second["date"] >= dates[-25]) & (second["symbol"] == "B0")
    second.loc[selected, "close"] *= np.exp(np.linspace(0, .4, int(selected.sum())))
    options = dict(horizon=3, holdout_days=40, test_days=25, min_abs_rank_ic=0,
                   num_boost_round=8, stopping_rounds=3, market_neutral_mode="beta_neutral")
    a = train_lgbm(first, version_suffix="beta_a", **options)
    b = train_lgbm(second, version_suffix="beta_b", **options)
    model_a = lgb.Booster(model_file=a["model_path"])
    model_b = lgb.Booster(model_file=b["model_path"])
    assert a["kept_features"] == b["kept_features"]
    assert model_a.dump_model()["tree_info"] == model_b.dump_model()["tree_info"]


def test_calibration_artifact_is_preserved_when_model_is_promoted(tmp_path):
    from app.ml.registry import _copy_artifacts
    source = tmp_path / "candidate"
    source.mkdir()
    model = source / "model.lgbm"
    model.write_text("model fixture", encoding="utf-8")
    (source / "calibration.json").write_text('{"available":true}', encoding="utf-8")
    destination = tmp_path / "production"
    _copy_artifacts(model, destination)
    assert (destination / "calibration.json").read_text(encoding="utf-8") == '{"available":true}'
