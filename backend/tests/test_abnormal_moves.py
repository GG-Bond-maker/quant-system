"""单元测试：全时段异动与交易所偏离值监控算法（纯函数测试）。"""
from __future__ import annotations

import pytest

from app.domain.abnormal_moves import (
    AuctionAbnormalSignal,
    DeviationRiskItem,
    DeviationSeverity,
    IntradayAbnormalSignal,
    calculate_board_deviation_threshold,
    evaluate_auction_abnormal,
    evaluate_exchange_deviation_guard,
    evaluate_intraday_pulse,
)


def test_calculate_board_deviation_threshold() -> None:
    """测试不同板块交易所偏离值法定门槛。"""
    assert calculate_board_deviation_threshold("main") == 20.0
    assert calculate_board_deviation_threshold("Main_Board") == 20.0
    assert calculate_board_deviation_threshold("chinext") == 30.0
    assert calculate_board_deviation_threshold("star") == 30.0
    assert calculate_board_deviation_threshold("bse") == 40.0
    assert calculate_board_deviation_threshold("北交所") == 40.0


def test_evaluate_auction_abnormal() -> None:
    """测试集合竞价高开与低开异动识别。"""
    quotes = [
        {"symbol": "600519.SH", "name": "贵州茅台", "open": 1800.0, "pre_close": 1700.0, "volume": 5000, "amount": 9000000},
        {"symbol": "000001.SZ", "name": "平安银行", "open": 10.0, "pre_close": 10.0, "volume": 1000, "amount": 10000},
        {"symbol": "000002.SZ", "name": "万科A", "open": 8.0, "pre_close": 8.5, "volume": 2000, "amount": 16000},
        {"symbol": "688981.SH", "name": "中芯国际", "open": 110.0, "pre_close": 100.0, "volume": 3000, "amount": 330000},
    ]

    signals = evaluate_auction_abnormal(quotes, high_open_threshold=3.0, low_open_threshold=-3.0)
    
    # 茅台高开 (1800/1700 - 1 = +5.88%)
    # 中芯高开 (110/100 - 1 = +10.00% -> limit_up_bid)
    # 万科低开 (8.0/8.5 - 1 = -5.88%)
    # 平安银行无异动
    assert len(signals) == 3
    
    symbols = {s.symbol for s in signals}
    assert "600519.SH" in symbols
    assert "688981.SH" in symbols
    assert "000002.SZ" in symbols
    assert "000001.SZ" not in symbols

    smic = next(s for s in signals if s.symbol == "688981.SH")
    assert smic.signal_type == "limit_up_bid"
    assert smic.change_pct == 10.0


def test_evaluate_intraday_pulse() -> None:
    """测试盘中即时脉冲拉升、跳水与炸板。"""
    ticks = [
        {"symbol": "600519.SH", "name": "贵州茅台", "price": 1850.0, "pct": 3.0, "surge_pct": 2.8, "limit_up": 1900.0, "high": 1850.0},
        {"symbol": "000001.SZ", "name": "平安银行", "price": 9.5, "pct": -2.0, "surge_pct": -2.9, "limit_up": 11.0, "high": 10.0},
        {"symbol": "300750.SZ", "name": "宁德时代", "price": 240.0, "pct": 7.0, "surge_pct": 0.5, "limit_up": 250.0, "high": 250.0},
    ]

    signals = evaluate_intraday_pulse(ticks, surge_threshold=2.5, plunge_threshold=-2.5)

    # 茅台快速拉升
    # 平安快速跳水
    # 宁德时代触及涨停 250 但现价 240，为炸板
    assert len(signals) == 3

    types = {s.signal_type for s in signals}
    assert "rapid_surge" in types
    assert "rapid_plunge" in types
    assert "limit_break" in types


def test_evaluate_exchange_deviation_guard() -> None:
    """测试交易所严重异常波动偏离值风险分级。"""
    # 主板门槛 ±20%
    # 正常
    res_normal = evaluate_exchange_deviation_guard(
        symbol="600519.SH",
        name="贵州茅台",
        board="main",
        stock_3d_pct=8.0,
        benchmark_3d_pct=2.0,
    )
    assert res_normal.deviation_3d_pct == 6.0
    assert res_normal.severity == DeviationSeverity.NORMAL

    # 观察区 (≥50%, 即偏离 ≥ 10.0%)
    res_watch = evaluate_exchange_deviation_guard(
        symbol="600519.SH",
        name="贵州茅台",
        board="main",
        stock_3d_pct=13.0,
        benchmark_3d_pct=2.0,
    )
    assert res_watch.deviation_3d_pct == 11.0
    assert res_watch.severity == DeviationSeverity.WATCH

    # 预警区 (≥70%, 即偏离 ≥ 14.0%)
    res_warn = evaluate_exchange_deviation_guard(
        symbol="600519.SH",
        name="贵州茅台",
        board="main",
        stock_3d_pct=17.0,
        benchmark_3d_pct=2.0,
    )
    assert res_warn.deviation_3d_pct == 15.0
    assert res_warn.severity == DeviationSeverity.WARNING

    # 严重超标 (≥100%, 即偏离 ≥ 20.0%)
    res_crit = evaluate_exchange_deviation_guard(
        symbol="600519.SH",
        name="贵州茅台",
        board="main",
        stock_3d_pct=25.0,
        benchmark_3d_pct=2.0,
    )
    assert res_crit.deviation_3d_pct == 23.0
    assert res_crit.severity == DeviationSeverity.CRITICAL
