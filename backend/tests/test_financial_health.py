"""
单元测试：个股财务健康度与破产排雷诊断模型 (financial_health)。
严格验证真实数据计算、红线排雷告警、以及无数据时诚实返回 unavailable（杜绝数据造假）。
"""
import pytest
from app.domain.financial_health import compute_financial_health
from app.data.panels import build_financial_health, load_stock_financial_history


def test_load_stock_financial_history():
    """验证从本地 SQLite 正确读取历史财报。"""
    # 真实蓝筹股 600519
    hist = load_stock_financial_history("600519.SH", limit=4)
    assert len(hist) > 0
    latest = hist[0]
    assert "period" in latest
    assert "revenue_yi" in latest
    assert "net_profit_yi" in latest
    assert "roe" in latest
    assert "gross_margin" in latest
    assert latest["gross_margin"] is not None and latest["gross_margin"] > 80.0


def test_compute_financial_health_safe():
    """验证健康标的 (600519.SH) 评分为安全区且无致命红线。"""
    res = build_financial_health("600519.SH")
    assert res["status"] == "ok"
    assert res["risk_level"] == "safe"
    assert res["score"] >= 70.0
    assert res["altman_assessment"]["zone"] == "safe"
    assert len(res["positive_flags"]) >= 1
    # 贵州茅台无连续亏损或毛利倒挂红线
    danger_flags = [f for f in res["red_flags"] if f["level"] == "danger"]
    assert len(danger_flags) == 0
    # 指标完整
    assert res["metrics"]["roe"] > 20.0
    assert res["metrics"]["gross_margin"] > 80.0
    assert len(res["dimensions"]) == 5


def test_compute_financial_health_distress():
    """验证业绩亏损标的 (000002.SZ) 能有效识别连续亏损并触发排雷预警。"""
    res = build_financial_health("000002.SZ")
    assert res["status"] == "ok"
    # 万科近 4 期巨亏，应落入高危预警或中度关注区
    assert res["risk_level"] in ("distress", "moderate")
    assert any(f["code"] == "CONTINUOUS_LOSS" for f in res["red_flags"])
    assert res["metrics"]["net_profit_yi"] < 0
    assert res["metrics"]["roe"] < 0


def test_compute_financial_health_unavailable():
    """验证不存在代码诚实返回 unavailable，坚决不捏造虚假指标与平滑数据。"""
    res = build_financial_health("999999.SH")
    assert res["status"] == "unavailable"
    assert res["score"] is None
    assert res["risk_level"] == "unavailable"
    assert res["reason"] == "暂无该标的历史财报披露数据"
    assert res["red_flags"] == []
    assert res["positive_flags"] == []
    assert res["quarterly_trend"] == []


def test_build_financial_health_panel_entrypoint():
    """验证 panels 集成入口 build_financial_health。"""
    res = build_financial_health("600519.SH")
    assert res["status"] == "ok"
    assert "altman_assessment" in res
    assert "dimensions" in res
