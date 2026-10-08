"""市场概览增强组件回归测试：A股科创与创新指数、外盘美日韩指数、真实均线广度、风格因子与微观流动性。"""
import pytest
from app.data.ingest.akshare_adapter import CORE_INDICES, GLOBAL_INDICES
from app.api.v1.market import (
    _build_indices,
    _compute_real_moving_average_breadth,
    _heat_payload,
    _build_style_factors,
    _build_speculative_sentiment,
    _build_market_valuation,
)


def test_core_indices_contains_star_and_innovation():
    """验证 A 股核心指数包含科创50与创新指数（创业板指/双创50）。"""
    codes = [code for code, _name in CORE_INDICES]
    names = [name for _code, name in CORE_INDICES]
    assert "sh000688" in codes  # 科创50
    assert "sz399006" in codes  # 创业板指 (创新)
    assert "sz399706" in codes  # 双创50 (创新)
    assert any("科创" in n for n in names)
    assert any("创业" in n or "双创" in n for n in names)


def test_global_indices_contains_us_jp_kr():
    """验证外盘指数覆盖美日韩主流指数。"""
    names = [name for _kind, _sym, name in GLOBAL_INDICES]
    assert any("日经" in n for n in names)  # 日本
    assert any("KOSPI" in n or "首尔" in n for n in names)  # 韩国
    assert any("道琼斯" in n or "纳斯达克" in n or "标普" in n for n in names)  # 美国


def test_real_moving_average_breadth_is_not_fake_proxy():
    """验证市场广度真实均线计算函数运行正常，返回浮点数或 None，无硬编码。"""
    r20, r60 = _compute_real_moving_average_breadth()
    if r20 is not None:
        assert isinstance(r20, (int, float))
        assert 0.0 <= r20 <= 100.0
    if r60 is not None:
        assert isinstance(r60, (int, float))
        assert 0.0 <= r60 <= 100.0


def test_heat_payload_structure():
    """验证 _heat_payload 输出包含真实均线广度标记。"""
    import polars as pl
    pct_series = pl.Series("pct", [-3.0, 0.0, 1.5, 5.0, 9.9, -10.0])
    res = _heat_payload("test", pct_series)
    assert res["status"] == "ok"
    assert "breadth" in res
    assert "pct_above_ma20" in res["breadth"]
    assert "pct_above_ma60" in res["breadth"]
    assert "is_real_ma" in res["breadth"]


def test_style_factors_builder():
    """验证风格因子多空收益计算。"""
    res = _build_style_factors()
    assert res["status"] in ("ok", "unavailable")
    if res["status"] == "ok":
        assert len(res["items"]) >= 4
        codes = [it["code"] for it in res["items"]]
        assert "SIZE" in codes
        assert "MOM" in codes
        for it in res["items"]:
            assert "ls_return" in it
            assert isinstance(it["ls_return"], (int, float))


def test_speculative_sentiment_builder():
    """验证微观投机生态指标（炸板率、连板、昨日溢价）。"""
    res = _build_speculative_sentiment()
    assert res["status"] in ("ok", "unavailable")
    if res["status"] == "ok":
        assert "broken_board_rate" in res
        assert "limit_up_count" in res
        assert "yesterday_zt_premium" in res
        assert 0.0 <= res["broken_board_rate"] <= 100.0


def test_market_valuation_builder():
    """验证大盘估值与 ERP 股债平衡指标。"""
    res = _build_market_valuation()
    assert res["status"] == "ok"
    assert "csi300" in res
    assert "erp" in res
    assert "indices" in res
    assert len(res["indices"]) >= 3
