"""单元测试：全时段异动与数据源能力路由矩阵 API 端点测试。"""
from __future__ import annotations

import pytest
from httpx import ASGITransport, AsyncClient

from app.main import app
from app.core.auth import create_jwt_token


@pytest.mark.asyncio
async def test_capabilities_api() -> None:
    """测试 GET /api/v1/datacenter/capabilities 端点。"""
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        resp = await client.get("/api/v1/datacenter/capabilities")
        assert resp.status_code == 200
        data = resp.json()
        assert data["code"] == 0
        payload = data["data"]
        assert "capabilities" in payload
        assert "usable_count" in payload
        assert payload["usable_count"] >= 4
        assert len(payload["capabilities"]) == 5


@pytest.mark.asyncio
async def test_market_abnormal_api() -> None:
    """测试 GET /api/v1/market/abnormal 端点。"""
    token, _ = create_jwt_token("viewer_user", "viewer")
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        headers = {"Authorization": f"Bearer {token}"}
        resp = await client.get("/api/v1/market/abnormal?timeline=all&limit=10", headers=headers)
        assert resp.status_code == 200
        data = resp.json()
        assert data["code"] == 0
        payload = data["data"]
        assert "auction" in payload
        assert "intraday" in payload
        assert "deviation" in payload
        assert "total_signals" in payload
