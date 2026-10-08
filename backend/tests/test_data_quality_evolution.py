"""测试数据质量中心 P0/P1/P2 进化端点：五维雷达、截面有效性、血缘受损扩散与板块自适应涨跌幅。"""
from datetime import date
import pytest
from fastapi.testclient import TestClient
from app.main import app
from app.core.auth import create_jwt_token
from app.data.quality import QCThresholds, get_board_return_limit


@pytest.fixture
def client():
    c = TestClient(app)
    token, _ = create_jwt_token("test_researcher", "researcher")
    c.headers.update({"Authorization": f"Bearer {token}"})
    return c


def test_board_return_limit_adaptive_rules():
    th = QCThresholds()
    # 主板 (600 / 000) 阈值 12% (10% + 缓冲)
    assert get_board_return_limit("600519.SH", is_st=False, th=th) == 0.12
    assert get_board_return_limit("000001.SZ", is_st=False, th=th) == 0.12

    # 创业板 (300) / 科创板 (688) 阈值 22% (20% + 缓冲)
    assert get_board_return_limit("300750.SZ", is_st=False, th=th) == 0.22
    assert get_board_return_limit("688981.SH", is_st=False, th=th) == 0.22

    # 北交所 (8 / 4 / 920) 阈值 33% (30% + 缓冲)
    assert get_board_return_limit("832000.BJ", is_st=False, th=th) == 0.33
    assert get_board_return_limit("920001.BJ", is_st=False, th=th) == 0.33

    # ST 标的严格 12% 限制
    assert get_board_return_limit("600000.SH", is_st=True, th=th) == 0.12


def test_quality_radar_endpoint(client):
    res = client.get("/api/v1/ops/quality/radar?dataset=daily_bar")
    assert res.status_code == 200
    data = res.json()
    assert data["code"] == 0
    assert "composite_score" in data["data"]
    assert data["data"]["composite_score"] >= 90.0
    assert "dimensions" in data["data"]
    assert len(data["data"]["dimensions"]) == 5
    dim_keys = [d["key"] for d in data["data"]["dimensions"]]
    assert "completeness" in dim_keys
    assert "accuracy" in dim_keys
    assert "consistency" in dim_keys
    assert "stability" in dim_keys
    assert "timeliness" in dim_keys


def test_cross_sectional_coverage_endpoint(client):
    res = client.get("/api/v1/ops/quality/cross-sectional-coverage?days=15")
    assert res.status_code == 200
    data = res.json()
    assert data["code"] == 0
    assert data["data"]["total_dates"] == 15
    assert "mean_coverage_pct" in data["data"]
    assert "heatmap_data" in data["data"]
    assert len(data["data"]["heatmap_data"]) > 0


def test_blast_radius_analysis_endpoint(client):
    res = client.get("/api/v1/ops/quality/blast-radius?node_id=daily_bar")
    assert res.status_code == 200
    data = res.json()
    assert data["code"] == 0
    assert data["data"]["source_node"]["id"] == "daily_bar"
    assert data["data"]["total_impacted"] >= 5
    impacted_ids = [n["id"] for n in data["data"]["impacted_nodes"]]
    assert "features" in impacted_ids
    assert "model" in impacted_ids
    assert len(data["data"]["affected_alpha_factors"]) > 0
