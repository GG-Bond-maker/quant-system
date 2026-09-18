"""推荐与选股的数据新鲜度、空壳过滤和异常脱敏最小回归。"""
from __future__ import annotations

from datetime import date

import polars as pl
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.api.v1 import market, screener
from app.core.config import get_settings
from app.core.errors import register_error_handlers


def _seed_prediction(tmp_path, day: date = date(2026, 9, 11)) -> pl.DataFrame:
    """写入一条预测分区并返回相同 DataFrame。"""
    pred = pl.DataFrame({
        "date": [day],
        "symbol": ["600001.SH"],
        "pred_score": [0.42],
    })
    pred_dir = tmp_path / "predictions"
    pred_dir.mkdir(parents=True)
    pred.write_parquet(pred_dir / f"date={day.strftime('%Y%m%d')}.parquet")
    return pred


def test_recommend_filters_shell_item(monkeypatch, tmp_path):
    """关键行情全空时不返回伪推荐，并给出稳定不可用原因。"""
    pred = _seed_prediction(tmp_path)
    monkeypatch.setattr(get_settings(), "DATA_ROOT", tmp_path)
    monkeypatch.setattr(market, "filter_universe", lambda *_args, **_kwargs: (pred, 1))
    monkeypatch.setattr(screener, "_load_instrument_info", lambda: {})
    monkeypatch.setattr(market, "_latest_announcements", lambda: {})
    monkeypatch.setattr(market, "read_symbol_dataset", lambda *_args: pl.DataFrame())

    result = market._build_recommend(5)

    assert result["status"] == "unavailable"
    assert result["reason"] == "market_data_missing"
    assert result["items"] == []
    assert result["coverage"] == {"available": 0, "total": 1, "ratio": 0.0}


def test_recommend_model_not_ready(monkeypatch, tmp_path):
    """模型未产出时返回空数组状态，而不是抛业务异常。"""
    monkeypatch.setattr(get_settings(), "DATA_ROOT", tmp_path)

    result = market._build_recommend(5)

    assert result["status"] == "unavailable"
    assert result["reason"] == "model_not_ready"
    assert result["items"] == []
    assert "模型" in result["message"]


def test_recommend_stale_but_preserves_valid_items(monkeypatch, tmp_path):
    """行情滞后时保留有效项，同时明确标记 degraded/data_stale。"""
    pred = _seed_prediction(tmp_path)
    bars = pl.DataFrame({
        "date": [date(2026, 9, 10), date(2026, 9, 11)],
        "open": [10.0, 10.5],
        "high": [10.5, 11.2],
        "low": [9.8, 10.3],
        "close": [10.0, 11.0],
        "amount": [1000.0, 2000.0],
    })
    monkeypatch.setattr(get_settings(), "DATA_ROOT", tmp_path)
    monkeypatch.setattr(market, "filter_universe", lambda *_args, **_kwargs: (pred, 1))
    monkeypatch.setattr(screener, "_load_instrument_info", lambda: {"600001.SH": ("测试股", "测试")})
    monkeypatch.setattr(screener, "_freshness", lambda _as_of: {
        "as_of": "2026-09-11", "expected": "2026-09-14",
        "lag_trading_days": 1, "is_stale": True,
        "note": "数据落后最近已收盘交易日 1 个交易日",
    })
    monkeypatch.setattr(market, "_latest_announcements", lambda: {})
    monkeypatch.setattr(market, "read_symbol_dataset", lambda *_args: bars)

    result = market._build_recommend(5)

    assert result["status"] == "degraded"
    assert result["reason"] == "data_stale"
    assert len(result["items"]) == 1
    assert result["items"][0]["close"] == 11.0


def test_recommend_normal_path(monkeypatch, tmp_path):
    """预测和行情均正常时返回 ok 与完整覆盖率。"""
    pred = _seed_prediction(tmp_path)
    bars = pl.DataFrame({
        "date": [date(2026, 9, 11)], "open": [10.0], "high": [11.0],
        "low": [9.8], "close": [10.5], "amount": [1000.0],
    })
    monkeypatch.setattr(get_settings(), "DATA_ROOT", tmp_path)
    monkeypatch.setattr(market, "filter_universe", lambda *_args, **_kwargs: (pred, 1))
    monkeypatch.setattr(screener, "_load_instrument_info", lambda: {})
    monkeypatch.setattr(screener, "_freshness", lambda as_of: {
        "as_of": as_of, "expected": as_of, "lag_trading_days": 0,
        "is_stale": False, "note": "数据为最近已收盘交易日产物",
    })
    monkeypatch.setattr(market, "_latest_announcements", lambda: {})
    monkeypatch.setattr(market, "read_symbol_dataset", lambda *_args: bars)

    result = market._build_recommend(5)

    assert result["status"] == "ok"
    assert result["reason"] is None
    assert result["coverage"] == {"available": 1, "total": 1, "ratio": 1.0}


def test_screener_distinguishes_no_signal_and_missing_market_data(monkeypatch):
    """选股空信号与行情缺失是两个稳定机器码。"""
    monkeypatch.setattr(screener, "_freshness", lambda as_of: {
        "as_of": as_of, "expected": as_of, "lag_trading_days": 0,
        "is_stale": False, "note": "数据正常",
    })
    base = {"date": "2026-09-11", "items": [], "count": 0}
    no_signal = screener._finalize_screener_payload(dict(base))
    assert no_signal["status"] == "ok"
    assert no_signal["reason"] == "no_matching_signals"

    missing = screener._finalize_screener_payload({
        "date": "2026-09-11", "items": [{
            "symbol": "600001.SH", "close": None, "pct": None, "amount": None,
        }],
    })
    assert missing["status"] == "unavailable"
    assert missing["reason"] == "market_data_missing"
    assert missing["items"] == []


def test_screener_missing_market_data_priority_over_stale(monkeypatch):
    """P0 回归：候选全部缺行情（available==0）且数据陈旧时，终态必须是
    unavailable/market_data_missing；stale 分支不得覆盖（否则空榜被谎报成 degraded
    并走短 TTL 缓存，即本轮在治的降级态固化问题）。"""
    monkeypatch.setattr(screener, "_freshness", lambda as_of: {
        "as_of": as_of, "expected": "2026-09-14", "lag_trading_days": 3,
        "is_stale": True, "note": "数据落后最近已收盘交易日 3 个交易日",
    })
    res = screener._finalize_screener_payload({
        "date": "2026-09-11", "items": [{
            "symbol": "600001.SH", "close": None, "pct": None, "amount": None,
        }],
    })
    assert res["status"] == "unavailable"
    assert res["reason"] == "market_data_missing"
    assert res["items"] == []


def test_screener_stale_preserved_when_items_available(monkeypatch):
    """反向不误伤：available > 0 且陈旧时仍标记 degraded/data_stale。"""
    monkeypatch.setattr(screener, "_freshness", lambda as_of: {
        "as_of": as_of, "expected": "2026-09-14", "lag_trading_days": 3,
        "is_stale": True, "note": "数据落后最近已收盘交易日 3 个交易日",
    })
    res = screener._finalize_screener_payload({
        "date": "2026-09-11", "items": [{
            "symbol": "600001.SH", "name": "测试股", "industry": "测试",
            "close": 10.5, "pct": 1.0, "amount": 1000.0,
            "score": 0.42, "signal_strength": "strong",
        }],
    })
    assert res["status"] == "degraded"
    assert res["reason"] == "data_stale"


def test_unhandled_exception_message_is_sanitized():
    """未知异常响应只给友好文案，不暴露 Python 异常类名。"""
    app = FastAPI()
    register_error_handlers(app)

    @app.get("/boom")
    async def boom():
        raise ValueError("secret implementation detail")

    with TestClient(app, raise_server_exceptions=False) as client:
        body = client.get("/boom").json()
    assert body["code"] == 50000
    assert body["message"] == "系统暂不可用，请稍后重试"
    assert "ValueError" not in body["message"]
