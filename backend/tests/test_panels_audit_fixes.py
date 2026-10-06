"""测试针对审计报告的 P0/P1 缺陷修复：
1. Barra Size (规模因子) 基于对数市值真实映射，而非错误使用 pb；
2. build_fundamentals 在外部接口失败时通过本地 SQLite financial_report 兜底容灾。
"""
import pytest
from app.data import panels


def test_barra_size_score_uses_market_cap(monkeypatch):
    """验证 Barra 风格因子 Size 正确反映市值规模，且单调递增。"""
    # 模拟大盘股 (市值 5000 亿，低 PB 0.8)
    def fake_quote_large(symbol: str):
        return {
            "price": 10.0,
            "pe_ttm": 6.5,
            "pb": 0.8,
            "total_cap_yi": 5000.0,
            "float_cap_yi": 4500.0,
        }

    monkeypatch.setattr(panels, "fetch_quote", fake_quote_large)
    monkeypatch.setattr(panels, "fetch_financial_indicators", lambda s: {"roe": 12.0, "net_margin": 15.0})
    monkeypatch.setattr(panels, "read_symbol_dataset", lambda *args, **kwargs: __import__("polars").DataFrame())

    res_large = panels.build_barra("601398.SH")
    size_large = res_large["factors"]["size"]["score"]
    assert size_large > 70.0, f"大盘股 Size 得分应显著偏高，实际为 {size_large}"

    # 模拟微盘成长股 (市值 15 亿，高 PB 8.0)
    def fake_quote_small(symbol: str):
        return {
            "price": 25.0,
            "pe_ttm": 45.0,
            "pb": 8.0,
            "total_cap_yi": 15.0,
            "float_cap_yi": 12.0,
        }

    monkeypatch.setattr(panels, "fetch_quote", fake_quote_small)
    res_small = panels.build_barra("300999.SZ")
    size_small = res_small["factors"]["size"]["score"]
    assert size_small < 30.0, f"微盘股 Size 得分应显著偏低，实际为 {size_small}"
    assert size_large > size_small, "大盘股市值得分必须严格大于微盘股"


def test_fundamentals_fallback_to_local_sqlite(monkeypatch):
    """验证当外部财务指标接口报错时，build_fundamentals 自动切换至本地 SQLite 财报。"""
    def broken_fetch(symbol: str):
        raise RuntimeError("网络超时 / 接口被阻断")

    monkeypatch.setattr(panels, "fetch_quote", lambda s: {"pe_ttm": 8.0, "pb": 0.9})
    monkeypatch.setattr(panels, "fetch_financial_indicators", broken_fetch)
    monkeypatch.setattr(
        panels,
        "_fundamentals_from_local",
        lambda s: {
            "report_date": "20241231",
            "roe": 11.2,
            "gross_margin": 25.0,
            "net_margin": 14.5,
            "eps": 1.25,
            "bps": 12.0,
            "revenue_yi": 500.0,
            "net_profit_yi": 80.0,
            "source": "local-sqlite-pit",
        },
    )

    fund = panels.build_fundamentals("000001.SZ")
    assert fund["status"] == "ok"
    assert fund["roe"] == 11.2
    assert fund["f_score"]["score"] >= 6
    assert fund["report_date"] == "20241231"
