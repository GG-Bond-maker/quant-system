"""港股通扩展与 AH 溢价看板接口回归测试。"""
from __future__ import annotations

import pytest
from datetime import date
from fastapi.testclient import TestClient

from app.main import app
from app.domain.hk_rules import AH_PAIR_MAPPINGS
from app.data.hk_instruments import SOUTHBOUND_INSTRUMENTS

_ADMIN = {"Authorization": "Bearer aqp-dev-token-change-me"}


def test_screener_stocks_market_filter(monkeypatch):
    """测试 /api/v1/screener/stocks 支持 market 参数（cn/hk/southbound/all）与每手股数、币种。"""
    client = TestClient(app)

    # 1. 验证非法 market 返回 40000 业务码
    r_invalid = client.get("/api/v1/screener/stocks?market=us", headers=_ADMIN)
    assert r_invalid.status_code == 200
    assert r_invalid.json()["code"] == 40000

    # 2. 查询港股通 (market=southbound)
    r_hk = client.get("/api/v1/screener/stocks?market=southbound&page=1&page_size=20", headers=_ADMIN)
    assert r_hk.status_code == 200
    res_hk = r_hk.json()
    assert res_hk["code"] == 0
    data_hk = res_hk["data"]
    items_hk = data_hk["items"]
    assert len(items_hk) > 0
    # 验证字段齐全性
    sample = items_hk[0]
    assert "symbol" in sample
    assert "lot_size" in sample
    assert "currency" in sample
    assert "is_southbound" in sample
    assert "market" in sample
    # 验证港股特征
    for it in items_hk:
        assert it["symbol"].endswith(".HK")
        assert it["market"] == "HK"
        assert it["is_southbound"] is True
        assert it["currency"] in ("HKD", "CNY")
        assert it["lot_size"] is not None and it["lot_size"] > 0

    # 3. 查询 A 股 (market=cn)
    r_cn = client.get("/api/v1/screener/stocks?market=cn&page=1&page_size=10", headers=_ADMIN)
    assert r_cn.status_code == 200
    items_cn = r_cn.json()["data"]["items"]
    for it in items_cn:
        assert not it["symbol"].endswith(".HK")
        assert it["market"] == "CN"


def test_market_ah_spread_endpoint(monkeypatch):
    """测试 GET /api/v1/market/ah-spread 接口信封、排序与套利指标计算。"""
    client = TestClient(app)

    # 模拟外部实时行情批量返回
    def mock_fetch_quotes(symbols):
        quotes = []
        for s in symbols:
            if s == "601318.SH":  # 中国平安 A
                quotes.append({"symbol": s, "price": 50.0, "pct": 1.2, "amount": 1.5e9, "as_of": "2026-10-09 14:30:00"})
            elif s == "02318.HK": # 中国平安 H
                quotes.append({"symbol": s, "price": 45.0, "pct": -0.5, "amount": 8.0e8, "as_of": "2026-10-09 14:30:00"})
            elif s == "002594.SZ": # 比亚迪 A
                quotes.append({"symbol": s, "price": 280.0, "pct": 2.0, "amount": 3.0e9, "as_of": "2026-10-09 14:30:00"})
            elif s == "01211.HK": # 比亚迪 H
                quotes.append({"symbol": s, "price": 240.0, "pct": 1.5, "amount": 1.2e9, "as_of": "2026-10-09 14:30:00"})
            else:
                quotes.append({"symbol": s, "price": 10.0, "pct": 0.0, "amount": 1.0e8, "as_of": "2026-10-09 14:30:00"})
        return quotes, "tencent"

    monkeypatch.setattr("app.data.realtime.fetch_quotes_batch", mock_fetch_quotes)

    r = client.get("/api/v1/market/ah-spread?refresh=1&limit=20&sort=premium_desc", headers=_ADMIN)
    assert r.status_code == 200
    res = r.json()
    assert res["code"] == 0
    data = res["data"]
    assert "items" in data
    assert "summary" in data
    assert "hkd_cny_fx" in data
    assert data["source"] == "tencent"

    items = data["items"]
    assert len(items) > 0
    first = items[0]
    assert "a_symbol" in first
    assert "h_symbol" in first
    assert "a_price" in first
    assert "h_price" in first
    assert "ah_premium_rate" in first
    assert "arbitrage_signal" in first
    assert "signal_desc" in first
    assert first["arbitrage_signal"] in (
        "a_extreme_premium", "a_moderate_premium", "ah_near_parity", "h_premium", "unavailable"
    )

    summary = data["summary"]
    assert summary["total_pairs"] >= 20
    assert summary["valid_pairs"] > 0
