"""审计 B5-16 防回归：被拒绝/无法撮合的订单必须留下可观测记录。

模块承诺（`broker.py` 开头）："被拒绝的订单以 qty=0 的 Trade 记录返回
（reason 标注原因），保证可观测性。"

缺陷（2026-09-21 审计）：`match()` 对**不在 `uni_d`** 的订单、以及**未带
`cash` 的买单**直接 `continue` 静默丢弃 —— 既无 Trade 也无 reason。
后果：`/backtest/run` 的 `rejected_trades` 汇总（`backtest.py:164-167`）
看不到这类订单，调用方无法回答"为什么某些目标持仓当天没有建仓"。
"""
from __future__ import annotations

import sys
from datetime import date
from pathlib import Path

BACKEND_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(BACKEND_ROOT))

import pandas as pd  # noqa: E402
import pytest  # noqa: E402

from app.backtest.broker import (  # noqa: E402
    Broker,
    BrokerConfig,
    Order,
    OrderSide,
)

D = date(2026, 9, 1)


def _uni(symbols: list[str]) -> pd.DataFrame:
    return pd.DataFrame({
        "open": {s: 10.0 for s in symbols},
        "close": {s: 10.0 for s in symbols},
        "volume": {s: 1e9 for s in symbols},
        "amount": {s: 1e9 for s in symbols},
        "limit_up": {s: 11.0 for s in symbols},
        "limit_down": {s: 9.0 for s in symbols},
    })


def test_order_outside_universe_is_recorded_not_dropped() -> None:
    """不在 `uni_d` 的订单必须返回 reason=`no_bar` 的记录（原实现静默丢弃）。"""
    b = Broker(init_cash=1_000_000.0,
               config=BrokerConfig(enabled=False))
    uni = _uni(["600000.SH"])

    trades = b.match(D, [Order(symbol="999999.SH", side=OrderSide.BUY,
                               cash=100_000.0)], uni)
    assert len(trades) == 1, f"订单被静默丢弃（返回 {len(trades)} 条记录）"
    t = trades[0]
    assert t.reason == "no_bar", f"拒绝原因应可区分，实际 {t.reason!r}"
    assert t.qty == 0 and t.symbol == "999999.SH"


def test_sell_outside_universe_is_recorded_too() -> None:
    """卖单同理（原实现与买单一样静默跳过）。"""
    b = Broker(init_cash=1_000_000.0, config=BrokerConfig(enabled=False))
    b.holdings["999999.SH"] = 1000
    trades = b.match(D, [Order(symbol="999999.SH", side=OrderSide.SELL, qty=1000)],
                     _uni(["600000.SH"]))
    assert len(trades) == 1 and trades[0].reason == "no_bar"
    assert b.holdings["999999.SH"] == 1000, "被拒卖单不得改变持仓"


def test_buy_without_cash_is_recorded() -> None:
    """买单未给 `cash` 金额必须留下 `no_cash` 记录（原实现静默跳过）。"""
    b = Broker(init_cash=1_000_000.0, config=BrokerConfig(enabled=False))
    trades = b.match(D, [Order(symbol="600000.SH", side=OrderSide.BUY)], _uni(["600000.SH"]))
    assert len(trades) == 1 and trades[0].reason == "no_cash"


def test_no_bar_reject_price_is_nan_not_fabricated() -> None:
    """`no_bar` 的 price 必须是 NaN —— 当日无行情，编造价格会污染成交价聚合。"""
    b = Broker(init_cash=1_000_000.0, config=BrokerConfig(enabled=False))
    t = b.match(D, [Order(symbol="999999.SH", side=OrderSide.BUY, cash=1.0)],
                _uni(["600000.SH"]))[0]
    assert t.price != t.price, f"no_bar 的 price 应为 NaN，实际 {t.price}"


def test_normal_orders_still_fill_alongside_rejects() -> None:
    """拒绝记录不得影响正常订单成交（同批混合）。"""
    b = Broker(init_cash=1_000_000.0, config=BrokerConfig(enabled=False))
    trades = b.match(D, [
        Order(symbol="600000.SH", side=OrderSide.BUY, cash=100_000.0),
        Order(symbol="999999.SH", side=OrderSide.BUY, cash=100_000.0),
    ], _uni(["600000.SH"]))
    by_reason = {t.reason for t in trades}
    assert "filled" in by_reason and "no_bar" in by_reason, by_reason
    filled = [t for t in trades if t.reason == "filled"]
    assert len(filled) == 1 and filled[0].qty > 0
    # T+1：当日买入落在 `_locked_today`（`holdings` 要等 mark_to_market 解冻）
    held = b.holdings.get("600000.SH", 0) + b._locked_today.get("600000.SH", 0)
    assert held > 0, "正常订单未成交"


def test_no_bar_trades_do_not_affect_turnover() -> None:
    """被拒订单不得计入当日换手（否则会凭空增加 decay 成本）。"""
    b = Broker(init_cash=1_000_000.0,
               config=BrokerConfig(enabled=True, slippage_bps=0.0))
    b.match(D, [
        Order(symbol="600000.SH", side=OrderSide.BUY, cash=100_000.0),
        Order(symbol="999999.SH", side=OrderSide.BUY, cash=100_000.0),
        Order(symbol="888888.SH", side=OrderSide.SELL, qty=100),
    ], _uni(["600000.SH"]))
    assert b._day_buy_amount == pytest.approx(100_000.0, rel=0.05), (
        f"当日买腿成交额被拒绝订单污染：{b._day_buy_amount}")
    assert b._day_sell_amount == 0.0, "被拒卖单不得计入卖腿"
    expected = b._day_buy_amount / 2.0 / max(b.total_equity, 1.0)
    assert b.last_day_turnover == pytest.approx(expected, rel=1e-9)