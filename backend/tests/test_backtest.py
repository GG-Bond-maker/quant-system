"""回测撮合引擎测试（P0-Critical #1）。

覆盖 TC-T1 / TC-LIMIT-UP / TC-LIMIT-DOWN / TC-HALTED / TC-LOT-100 /
TC-COMMISSION / TC-STAMP-DUTY / TC-ENGINE-T1 / TC-ENGINE-NO-LOOKAHEAD。
"""
from __future__ import annotations

import sys
from datetime import date, timedelta
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

BACKEND_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(BACKEND_ROOT))

from app.backtest.broker import Broker, Order, OrderSide  # noqa: E402
from app.backtest.engine import run_backtest  # noqa: E402

D = date(2024, 6, 3)


def row(**kw: object) -> pd.Series:
    """构造一天的单标的市场行（默认：可交易、无涨跌停、open=close=10）。"""
    base: dict[str, object] = {
        "open": 10.0, "high": 10.2, "low": 9.8, "close": 10.0,
        "volume": 1_000_000.0, "limit_up": 11.0, "limit_down": 9.0,
        "is_halted": False,
    }
    base.update(kw)
    return pd.Series(base)


class TestBrokerRules:
    def test_tc_t1_buy_locked_then_sellable(self):
        """TC-T1：T 日买入进入锁定，当日卖 0 股；mark_to_market 后 T+1 可卖。"""
        b = Broker(init_cash=100_000)
        t_buy = b.buy(D, "600519.SH", cash_amount=10_000, row=row())
        assert t_buy.reason == "filled" and t_buy.qty == 1000  # 10000/10=1000 整手
        # 当日卖出：T+1 锁定 -> 0 股
        t_sell_same_day = b.sell(D, "600519.SH", qty=t_buy.qty, row=row())
        assert t_sell_same_day.qty == 0 and t_sell_same_day.reason == "t1"
        # T 日收盘估值：解冻
        b.mark_to_market(D, pd.DataFrame({"close": [10.0]}, index=["600519.SH"]))
        t_sell_next = b.sell(date(2024, 6, 4), "600519.SH", qty=t_buy.qty, row=row())
        assert t_sell_next.qty == t_buy.qty and t_sell_next.reason == "filled"

    def test_tc_limit_up_blocks_buy(self):
        """TC-LIMIT-UP：open >= limit_up*0.9999 -> BUY 拒绝 reason=limit_up。"""
        b = Broker(init_cash=100_000)
        t = b.buy(D, "600519.SH", cash_amount=10_000,
                  row=row(open=11.0, limit_up=11.0))
        assert t.qty == 0 and t.reason == "limit_up"

    def test_tc_limit_down_blocks_sell(self):
        """TC-LIMIT-DOWN：open <= limit_down*1.0001 -> SELL 拒绝 reason=limit_down。"""
        b = Broker(init_cash=100_000)
        # 先在正常日建仓并解冻
        b.buy(D, "600519.SH", cash_amount=10_000, row=row())
        b.mark_to_market(D, pd.DataFrame({"close": [10.0]}, index=["600519.SH"]))
        t = b.sell(D, "600519.SH", qty=100,
                   row=row(open=9.0, limit_down=9.0))
        assert t.qty == 0 and t.reason == "limit_down"

    def test_tc_halted_blocks_both(self):
        """TC-HALTED：is_halted / volume=0 时买卖都拒绝 reason=halted。"""
        b = Broker(init_cash=100_000)
        t1 = b.buy(D, "600519.SH", cash_amount=10_000, row=row(is_halted=True))
        t2 = b.buy(D, "600519.SH", cash_amount=10_000, row=row(volume=0))
        assert t1.reason == "halted" and t2.reason == "halted"
        b.buy(D, "600519.SH", cash_amount=10_000, row=row())
        b.mark_to_market(D, pd.DataFrame({"close": [10.0]}, index=["600519.SH"]))
        t3 = b.sell(D, "600519.SH", qty=100, row=row(is_halted=True))
        assert t3.reason == "halted" and t3.qty == 0

    def test_tc_lot_100(self):
        """TC-LOT-100：买入数量向下取整到 100 股（1570 -> 1500）。"""
        b = Broker(init_cash=100_000)
        # 20000 元 / 12.74 元 ≈ 1570 股 -> 应成交 1500 股
        # 注意显式给定涨跌停价（默认按 close=10 推导会误触发涨停闸门）
        t = b.buy(D, "600519.SH", cash_amount=20_000,
                  row=row(open=12.74, limit_up=14.5, limit_down=11.0))
        assert t.qty == 1500 and t.reason == "filled"

    def test_tc_commission_min_5(self):
        """TC-COMMISSION：小额交易佣金不低于 5 元。

        ⚠️ 2026-09-21（审计 B5-10）口径变更：`cost` 现**包含双边过户费**
        （0.01‰，A 股股票买入也收）。1000 元买入 ⇒ 过户费 0.01 元，
        故 5.0 → 5.01。过户费与佣金是两笔独立费用，不是佣金的一部分。
        """
        b = Broker(init_cash=100_000)
        t = b.buy(D, "600519.SH", cash_amount=1_000, row=row())  # 100 股 * 10 元
        assert t.qty == 100 and t.amount == 1000.0
        assert t.cost == pytest.approx(5.0 + 1000.0 * 0.00001), \
            f"佣金应为最低 5 元 + 过户费，实际 {t.cost}"

    def test_tc_stamp_duty_sell_only(self):
        """TC-STAMP-DUTY：买入无印花税，卖出计提 amount*印花税率。

        ⚠️ 2026-09-21（审计 B5-10 / S1-T2）口径变更两处：
            ① 印花税改按**法定分段**（2023-08-28 前 1‰、之后 0.5‰）——
               本用例的 2024-06-04 属新税率区间，故仍是 0.5‰；
            ② 卖出费用新增**过户费**（0.01‰，股票双边）。
        """
        b = Broker(init_cash=100_000)
        t_buy = b.buy(D, "600519.SH", cash_amount=10_000, row=row())
        assert t_buy.cost == pytest.approx(5.0 + 10_000.0 * 0.00001)  # 佣金 + 过户费
        b.mark_to_market(D, pd.DataFrame({"close": [10.0]}, index=["600519.SH"]))
        t_sell = b.sell(date(2024, 6, 4), "600519.SH", qty=t_buy.qty, row=row())
        expected_stamp = t_sell.amount * 0.0005
        expected_transfer = t_sell.amount * 0.00001
        assert t_sell.cost == pytest.approx(5.0 + expected_stamp + expected_transfer,
                                            rel=1e-9)

    def test_locked_not_sellable_via_match(self):
        """match 路径同样遵守 T+1：当日卖出订单被拒（reason=t1）。"""
        b = Broker(init_cash=100_000)
        b.buy(D, "600519.SH", cash_amount=10_000, row=row())
        # 当日 via match 卖出：持仓在锁定区 -> reason=t1
        trades = b.match(D, [Order(symbol="600519.SH", side=OrderSide.SELL, qty=900)],
                        pd.DataFrame({"close": [10.0], "open": [10.0],
                                      "volume": [1e6], "limit_up": [11.0],
                                      "limit_down": [9.0]}, index=["600519.SH"]))
        sell_trade = next(t for t in trades if t.side == OrderSide.SELL)
        assert sell_trade.qty == 0 and sell_trade.reason == "t1"


def _make_universe(dates: list[date], symbols: list[str], price: float = 10.0,
                   limit_ratio: float = 0.1) -> pd.DataFrame:
    rows = []
    for d in dates:
        for s in symbols:
            rows.append({
                "date": d, "symbol": s, "open": price, "high": price, "low": price,
                "close": price, "volume": 1e6,
                "limit_up": price * (1 + limit_ratio),
                "limit_down": price * (1 - limit_ratio),
                "is_halted": False,
            })
    return pd.DataFrame(rows)


class TestEngine:
    def test_engine_runs_and_metrics_finite(self):
        dates = [D + timedelta(days=i) for i in range(10)]
        dates = [d for d in dates if d.weekday() < 5]
        uni = _make_universe(dates, ["A.SH", "B.SH", "C.SH"])
        rng = np.random.default_rng(0)
        sig = uni[["date", "symbol"]].copy()
        sig["pred_score"] = rng.random(len(sig))
        res = run_backtest(uni, sig, init_cash=100_000, top_k=2)
        assert list(res.nav_df.columns) == ["date", "cash", "equity", "nav", "turnover"]
        assert len(res.nav_df) == len(dates)
        assert np.isfinite(res.metrics["sharpe"])
        assert set(res.metrics) >= {"annual_return", "sharpe", "max_drawdown",
                                    "win_rate", "profit_loss_ratio"}

    def test_tc_engine_t1(self):
        """TC-ENGINE-T1：T 日买入的股票 T 日不在 holdings（在锁定区），T+1 才出现。"""
        dates = [D + timedelta(days=i) for i in range(4)]
        uni = _make_universe(dates, ["A.SH"])
        sig = pd.DataFrame({"date": [dates[0]], "symbol": ["A.SH"], "pred_score": [1.0]})
        res = run_backtest(uni, sig, init_cash=100_000, top_k=1)
        h0 = res.holdings_history[0]["holdings"]   # T 日（信号滞后 1 日 -> T 无信号）
        h1 = res.holdings_history[1]["holdings"]   # T+1（执行 T 日信号买入）
        assert h0 == {} and "A.SH" in h1 and h1["A.SH"] % 100 == 0

    def test_tc_engine_no_lookahead(self):
        """防未来信号：第 5 日才出现的信号，最早第 6 日才能建仓。"""
        dates = [D + timedelta(days=i) for i in range(8)]
        uni = _make_universe(dates, ["X.SH"])
        sig = pd.DataFrame({"date": [dates[4]], "symbol": ["X.SH"], "pred_score": [9.9]})
        res = run_backtest(uni, sig, init_cash=100_000, top_k=1)
        for h in res.holdings_history[:5]:
            assert h["holdings"] == {}, f"信号日 {dates[4]} 之前不得建仓: {h}"
        assert res.holdings_history[5]["holdings"].get("X.SH", 0) > 0

    def test_tc_engine_respects_limit_up(self):
        """防纸面成交：目标股全期一字涨停 -> 全程 0 成交、净值恒为 1。"""
        dates = [D + timedelta(days=i) for i in range(5)]
        uni = _make_universe(dates, ["L.SH"], price=11.0, limit_ratio=0.0)  # open==limit_up
        sig = pd.DataFrame({"date": [dates[0]], "symbol": ["L.SH"], "pred_score": [1.0]})
        res = run_backtest(uni, sig, init_cash=100_000, top_k=1)
        buys = [t for t in res.trades if t["side"] == "buy"]
        assert all(t["qty"] == 0 and t["reason"] == "limit_up" for t in buys)
        assert res.nav_df["nav"].nunique() == 1  # 无成交 -> 净值不变

    def test_invalid_rebalance_freq(self):
        uni = _make_universe([D], ["A.SH"])
        with pytest.raises(ValueError):
            run_backtest(uni, uni.assign(pred_score=1.0), rebalance_freq="monthly")
