"""测试数据中心 P0/P1/P2 进化端点：Schema抽样预览、微观质检雷达、幸存者偏差审计、PIT时滞分布、坏数据隔离区。"""
import pytest
from fastapi.testclient import TestClient
from app.main import app
from app.core.auth import create_jwt_token


@pytest.fixture
def client():
    c = TestClient(app)
    token, _ = create_jwt_token("test_researcher", "researcher")
    c.headers.update({"Authorization": f"Bearer {token}"})
    return c


def test_dataset_preview_endpoint(client):
    res = client.get("/api/v1/datacenter/dataset-preview?dataset=daily_bar&limit=5")
    assert res.status_code == 200
    data = res.json()
    assert data["code"] == 0
    assert "dataset" in data["data"]
    assert "schema" in data["data"]
    assert "records" in data["data"]


def test_dataset_preview_financial_report(client):
    res = client.get("/api/v1/datacenter/dataset-preview?dataset=financial_report&limit=5")
    assert res.status_code == 200
    data = res.json()
    assert data["code"] == 0
    assert data["data"]["table"] == "financial_report"


def test_micro_qc_radar_endpoint(client):
    res = client.get("/api/v1/datacenter/micro-qc-radar?limit=10")
    assert res.status_code == 200
    data = res.json()
    assert data["code"] == 0
    assert "total_audited_records" in data["data"]
    assert "health_score" in data["data"]
    assert "anomalies" in data["data"]


def test_survivorship_bias_audit_endpoint(client):
    res = client.get("/api/v1/datacenter/survivorship-bias-audit")
    assert res.status_code == 200
    data = res.json()
    assert data["code"] == 0
    assert data["data"]["active_stocks_count"] > 5000
    assert data["data"]["delisted_stocks_count"] > 200
    assert len(data["data"]["delisted_stocks"]) >= 5
    assert "bias_impact_assessment" in data["data"]


def test_pit_lag_distribution_endpoint(client):
    res = client.get("/api/v1/datacenter/pit-lag-distribution")
    assert res.status_code == 200
    data = res.json()
    assert data["code"] == 0
    assert "median_lag_days" in data["data"]
    assert "histogram" in data["data"]
    assert len(data["data"]["histogram"]) == 5


def test_quarantine_vault_endpoint(client):
    res = client.get("/api/v1/datacenter/quarantine-vault")
    assert res.status_code == 200
    data = res.json()
    assert data["code"] == 0
    assert data["data"]["vault_status"] == "CLEAN"
    assert "quarantine_policy" in data["data"]
