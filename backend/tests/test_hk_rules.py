"""港股（港股通）交易规则、每手股数与 AH 溢价核算单测。"""
import pytest
from datetime import date, datetime
from app.domain.hk_rules import (
    is_hk_symbol,
    normalize_hk_symbol,
    hk_symbol_to_code,
    get_hk_lot_size,
    round_to_lot,
    calculate_hk_trading_costs,
    calculate_ah_spread,
    get_ah_counterpart,
    HK_STAMP_DUTY_RATE,
    HK_STAMP_DUTY_RATE_LEGACY,
)
from app.domain.market_rules import (
    detect_market,
    get_market_lot_size,
    round_order_quantity,
    calculate_trading_friction,
)


def test_hk_symbol_normalization():
    assert is_hk_symbol("00700.HK") is True
    assert is_hk_symbol("00700") is True
    assert is_hk_symbol("700") is False  # 裸码非5位数字
    assert is_hk_symbol("600519.SH") is False

    assert normalize_hk_symbol("700") == "00700.HK"
    assert normalize_hk_symbol("0700") == "00700.HK"
    assert normalize_hk_symbol("00700") == "00700.HK"
    assert normalize_hk_symbol("00700.hk") == "00700.HK"
    assert normalize_hk_symbol("9988.HK") == "09988.HK"
    assert normalize_hk_symbol("09988.HK") == "09988.HK"

    assert hk_symbol_to_code("00700.HK") == "00700"
    assert hk_symbol_to_code("700") == "00700"

    with pytest.raises(ValueError):
        normalize_hk_symbol("INVALID")
    with pytest.raises(ValueError):
        normalize_hk_symbol("123456.HK")


def test_hk_lot_size_and_rounding():
    assert get_hk_lot_size("00700.HK") == 100
    assert get_hk_lot_size("01211.HK") == 500  # 比亚迪
    assert get_hk_lot_size("00005.HK") == 400  # 汇丰
    assert get_hk_lot_size("00939.HK") == 1000 # 建设银行
    assert get_hk_lot_size("99999.HK") == 100  # 默认 100

    # 规整委托股数
    assert round_to_lot(350, 100, mode="floor") == 300
    assert round_to_lot(350, 100, mode="ceil") == 400
    assert round_to_lot(350, 100, mode="round") == 400
    assert round_to_lot(1200, 500, mode="floor") == 1000
    assert round_to_lot(1200, 500, mode="ceil") == 1500


def test_hk_trading_costs_and_history_cut():
    # 2024 年买入 100,000 港元
    costs_2024 = calculate_hk_trading_costs(100_000.0, direction="buy", on_date=date(2024, 1, 15))
    # 印花税 0.1% -> 100 元
    assert costs_2024["stamp_duty"] == 100.0
    # 券商佣金 万 3 -> 30 元（高于最低 15 元）
    assert costs_2024["commission"] == 30.0
    # 规费: 2.7 + 0.15 + 5.65 = 8.5
    assert costs_2024["sfc_levy"] == 2.7
    assert costs_2024["afrc_levy"] == 0.15
    assert costs_2024["hkex_fee"] == 5.65
    assert costs_2024["total_cost"] == 138.5

    # 历史印花税测试：2023-11-17 前为 0.13%
    costs_2023 = calculate_hk_trading_costs(100_000.0, direction="sell", on_date=date(2023, 10, 1))
    assert costs_2023["stamp_duty"] == 130.0

    # 最低佣金保障（小单 5,000 元，万 3 = 1.5 < 15）
    small_order = calculate_hk_trading_costs(5_000.0, direction="buy")
    assert small_order["commission"] == 15.0


def test_ah_spread_and_arbitrage_signals():
    # 测试中国平安：A股 50.0 元，H股 45.0 港币，汇率 0.92
    # H股等值人民币 = 45 * 0.92 = 41.4 元
    # AH 溢价率 = (50 - 41.4) / 41.4 = 20.77%
    res = calculate_ah_spread(50.0, 45.0, hkd_cny_fx=0.92)
    assert res["h_price_cny"] == 41.4
    assert res["ah_premium_rate"] == 20.77
    assert res["arbitrage_signal"] == "a_moderate_premium"

    # 测试极度溢价标的（A股 100 元，H股 50 港币，汇率 0.90 -> H股 45 元 -> 溢价 122.22%）
    res_extreme = calculate_ah_spread(100.0, 50.0, hkd_cny_fx=0.90)
    assert res_extreme["ah_premium_rate"] == 122.22
    assert res_extreme["arbitrage_signal"] == "a_extreme_premium"

    # 测试缺损数据
    res_none = calculate_ah_spread(None, 45.0)
    assert res_none["arbitrage_signal"] == "unavailable"

    # 测试代码映射
    assert get_ah_counterpart("601318.SH") == "02318.HK"
    assert get_ah_counterpart("02318.HK") == "601318.SH"
    assert get_ah_counterpart("600519.SH") is None


def test_market_rules_dispatcher():
    assert detect_market("600519.SH") == "CN"
    assert detect_market("000001.SZ") == "CN"
    assert detect_market("00700.HK") == "HK"
    assert detect_market("00700") == "HK"

    assert get_market_lot_size("600519.SH") == 100
    assert get_market_lot_size("00700.HK") == 100
    assert get_market_lot_size("01211.HK") == 500

    # 规整下单股数
    assert round_order_quantity("600519.SH", 250) == 200
    assert round_order_quantity("01211.HK", 750) == 500

    # 统一计算摩擦
    friction_cn = calculate_trading_friction("600519.SH", 100_000.0, direction="sell")
    assert friction_cn["stamp_duty"] == 50.0  # A股印花税 0.05%
    assert friction_cn["transfer_fee"] == 1.0  # 过户费 0.01‰

    friction_hk = calculate_trading_friction("00700.HK", 100_000.0, direction="buy")
    assert friction_hk["stamp_duty"] == 100.0 # 港股印花税 0.1%
    assert friction_hk["other_fees"] == 8.5
