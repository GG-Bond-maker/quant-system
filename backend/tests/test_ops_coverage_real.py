"""Coverage diagnostics use observed universe rows and feature nulls only."""
from datetime import date

import polars as pl
import pytest
from fastapi.testclient import TestClient

from app.core.auth import create_jwt_token
from app.core.config import get_settings
from app.main import app


@pytest.fixture
def client(monkeypatch, tmp_path):
    monkeypatch.setattr(get_settings(), "DATA_ROOT", tmp_path)
    token, _ = create_jwt_token("coverage_viewer", "viewer")
    with TestClient(app) as client:
        client.headers.update({"Authorization": f"Bearer {token}"})
        yield client


def _write(root, dataset, frame):
    path = root / dataset / "symbol=__all__" / "year=2026.parquet"
    path.parent.mkdir(parents=True, exist_ok=True)
    frame.write_parquet(path)


def test_coverage_uses_actual_daily_rows_nulls_and_feature_cells(client, tmp_path):
    d1, d2 = date(2026, 1, 2), date(2026, 1, 5)
    _write(tmp_path, "universe_daily", pl.DataFrame({
        "date": [d1, d1, d1, d2, d2], "symbol": ["A", "B", "C", "A", "B"],
        "close": [10.0, None, 30.0, 20.0, 40.0],
        "volume": [100.0, 200.0, None, 100.0, 0.0],
    }))
    _write(tmp_path, "features/version=test_v1", pl.DataFrame({
        "date": [d1, d1, d2, d2], "symbol": ["A", "B", "A", "B"],
        "ret_1": [0.1, None, 0.2, 0.3], "roe": [None, 2.0, 3.0, 4.0],
    }))
    body = client.get("/api/v1/ops/quality/cross-sectional-coverage?days=15").json()
    assert body["code"] == 0
    data = body["data"]
    assert data["status"] == "ok"
    assert data["dates"] == [d1.isoformat(), d2.isoformat()]
    assert data["total_dates"] == 2
    first, last = data["series"]
    assert (first["expected_count"], first["covered_count"]) == (3, 1)
    assert (first["null_close_count"], first["null_volume_count"]) == (1, 1)
    assert first["coverage_pct"] == 33.33
    assert first["feature_null_count"] == 4  # missing C contributes two missing cells
    assert first["feature_coverage_pct"] == 33.33
    assert (last["expected_count"], last["covered_count"]) == (2, 2)
    assert last["coverage_pct"] == 100.0  # observed zero volume is not a missing cell
    assert last["feature_coverage_pct"] == 100.0
    assert data["mean_coverage_pct"] == pytest.approx(66.67)
    assert data["min_coverage_pct"] == 33.33
    assert data["source"]["feature_version"] == "test_v1"
    assert data["source"]["universe_files"] == 1
    assert data["source"]["feature_columns"] == ["ret_1", "roe"]
    heat = {(x, y): value for x, y, value in data["heatmap_data"]}
    assert heat[(0, data["categories"].index("收盘价"))] == 66.67
    assert heat[(0, data["categories"].index("成交量"))] == 66.67
    assert heat[(0, data["categories"].index("因子非空"))] == 33.33


def test_coverage_missing_source_is_unavailable_without_fabricated_dates(client):
    data = client.get("/api/v1/ops/quality/cross-sectional-coverage?days=15").json()["data"]
    assert data["status"] == "unavailable"
    assert data["total_dates"] == 0
    assert data["dates"] == data["series"] == data["heatmap_data"] == []
    assert data["mean_coverage_pct"] is None
    assert data["min_coverage_pct"] is None
    assert data["expected_universe_size"] is None


def test_coverage_missing_features_keeps_only_observed_price_volume(client, tmp_path):
    _write(tmp_path, "universe_daily", pl.DataFrame({
        "date": [date(2026, 1, 2)], "symbol": ["A"],
        "close": [10.0], "volume": [100.0],
    }))
    data = client.get("/api/v1/ops/quality/cross-sectional-coverage?days=15").json()["data"]
    assert data["status"] == "ok"
    assert data["feature_status"] == "unavailable"
    assert data["categories"] == ["收盘价", "成交量"]
    assert data["series"][0]["feature_coverage_pct"] is None


def test_radar_observed_scores_do_not_fill_unverified_dimensions(client, tmp_path):
    _write(tmp_path, "daily_bar", pl.DataFrame({
        "date": [date(2026, 1, i) for i in (2, 5, 6, 7)], "symbol": ["A"] * 4,
        "open": [10.0] * 4, "high": [11.0, 8.0, 11.0, 11.0],
        "low": [9.0] * 4, "close": [10.0, 10.0, None, 10.0],
        "volume": [100.0, 100.0, 100.0, float("inf")],
    }))
    data = client.get("/api/v1/ops/quality/radar?dataset=daily_bar").json()["data"]
    assert data["status"] == "partial"
    dims = {d["key"]: d for d in data["dimensions"]}
    assert dims["completeness"]["score"] == 90.0  # 18 finite cells / 20 observed cells
    assert dims["accuracy"]["score"] == 50.0  # one valid row / two complete rows
    assert dims["completeness"]["status"] == dims["accuracy"]["status"] == "FAIL"
    for key in ("consistency", "stability", "timeliness"):
        assert dims[key]["score"] is None
        assert dims[key]["status"] == "UNAVAILABLE"
    assert data["composite_score"] is None
    assert data["grade"] is None
    assert data["source"]["rows_scanned"] == 4
    assert data["source"]["files_scanned"] == 1
    assert not data["source"]["truncated"]


def test_radar_missing_source_is_unknown_instead_of_aaa(client):
    data = client.get("/api/v1/ops/quality/radar?dataset=daily_bar").json()["data"]
    assert data["status"] == "unavailable"
    assert data["composite_score"] is None and data["grade"] is None
    assert all(d["score"] is None and d["status"] == "UNAVAILABLE"
               for d in data["dimensions"])
    assert data["source"]["rows_scanned"] == 0
