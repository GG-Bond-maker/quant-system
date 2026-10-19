"""单元测试：真实分时行情与连板天梯 API 端点测试。"""
from __future__ import annotations

import pytest
from httpx import ASGITransport, AsyncClient

from app.main import app
from app.core.auth import create_jwt_token
from app.data.realtime import fetch_intraday_trends


def test_fetch_intraday_trends_unit() -> None:
    """测试真实分时抓取函数（纯单元测试，验证数据结构与杜绝假数据）。"""
    res = fetch_intraday_trends("000001.SZ", ndays=1)
    assert "symbol" in res
    assert "rows" in res
    assert "status" in res
    assert "prev_close" in res
    if res["status"] == "ok":
        assert len(res["rows"]) > 0
        r0 = res["rows"][0]
        assert "time" in r0
        assert "price" in r0
        assert "vwap" in r0
        assert "volume" in r0
        assert "pct" in r0


@pytest.mark.asyncio
async def test_stock_intraday_api() -> None:
    """测试 GET /api/v1/stock/{symbol}/intraday 接口。"""
    token, _ = create_jwt_token("viewer_user", "viewer")
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        headers = {"Authorization": f"Bearer {token}"}
        resp = await client.get("/api/v1/stock/000001.SZ/intraday?days=1", headers=headers)
        assert resp.status_code == 200
        data = resp.json()
        assert data["code"] == 0
        payload = data["data"]
        assert payload["symbol"] == "000001.SZ"
        assert "rows" in payload
        assert "status" in payload


@pytest.mark.asyncio
async def test_market_limit_ladder_api() -> None:
    """测试 GET /api/v1/market/limit-ladder 接口。"""
    token, _ = create_jwt_token("viewer_user", "viewer")
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        headers = {"Authorization": f"Bearer {token}"}
        resp = await client.get("/api/v1/market/limit-ladder", headers=headers)
        assert resp.status_code == 200
        data = resp.json()
        assert data["code"] == 0
        payload = data["data"]
        assert "tiers" in payload
        assert "total_limit_up" in payload
        assert "total_broken" in payload
        assert "broken_rate" in payload
        assert len(payload["tiers"]) >= 5
