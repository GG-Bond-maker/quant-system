import pytest
from app.data.screening import compute_quintile_spreads


def test_compute_quintile_spreads():
    res = compute_quintile_spreads(days=30, board="all")
    assert "status" in res
    if res["status"] == "ok":
        assert "dates" in res
        assert "q1" in res and "q5" in res
        assert "long_short" in res
        assert len(res["q1"]) == len(res["dates"])
        assert len(res["long_short"]) == len(res["dates"])
        assert "metrics" in res
        m = res["metrics"]
        assert "q1_total_ret" in m
        assert "spread_total_ret" in m
        assert "spread_sharpe" in m
        assert "monotonicity_score" in m
    else:
        assert res["status"] == "unavailable"
        assert "reason" in res


def test_api_screener_quintile_spreads():
    from fastapi.testclient import TestClient
    from app.main import app
    from app.core.auth import create_jwt_token

    token, _ = create_jwt_token("test_user", "viewer")
    client = TestClient(app)
    resp = client.get(
        "/api/v1/screener/quintile-spreads?days=30&board=all",
        headers={"Authorization": f"Bearer {token}"},
    )
    assert resp.status_code == 200
    data = resp.json()
    assert data["code"] == 0
    assert "status" in data["data"]
