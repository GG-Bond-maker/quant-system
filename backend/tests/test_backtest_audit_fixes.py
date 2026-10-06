"""Regression test suite for quant audit fixes:
1. CRIT-1: Physical turnover amount dimension guard in broker impact cost
2. CRIT-2: StrategyBase T+1 state comparison (locked holdings guard)
3. CRIT-3: MaCrossStrategy target rebalance with multi-symbol capacity re-allocation
4. HIGH-2: Holiday-aware weekly rebalancing boundary detection
5. HIGH-3: Dynamic ATR chandelier stop loss in MaCrossStrategy
6. Walk-forward embargo & chained OOS equity calculation
"""
from __future__ import annotations

from datetime import date, timedelta
import numpy as np
import pandas as pd
import pytest

from app.backtest.broker import Broker, BrokerConfig
from app.backtest.engine import run_backtest
from app.backtest.strategy_base import MaCrossStrategy, StrategyContext, run_strategy
from app.backtest.ma_cross import MaCrossParams, run_ma_cross
from app.backtest.param_search import walk_forward_search


def test_crit1_daily_amount_hfq_not_inflated_by_factor():
    """Verify CRIT-1: _daily_amount_hfq converts physical turnover to HFQ domain to match order amount."""
    cfg = BrokerConfig(
        impact_model="sqrt",
        impact_pct=0.01,
        impact_sqrt_coef_bps=30.0,
    )
    broker = Broker(init_cash=100_000, config=cfg)

    # Bar with raw amount 10,000,000 and factor 5.0 -> HFQ amount 50,000,000
    bar = {
        "open": 50.0,
        "high": 52.0,
        "low": 49.0,
        "close": 50.0,
        "volume": 200_000,
        "amount": 10_000_000.0,
        "factor": 5.0,
    }
    
    amount = broker._daily_amount_hfq(bar)
    assert amount == 50_000_000.0, f"Expected HFQ amount 50M, got {amount}"


def test_crit2_strategy_base_locked_today_t1_no_redundant_pending():
    """Verify CRIT-2: run_strategy considers broker._locked_today when setting pending."""
    # Create 10 days of flat prices, golden cross on day 5
    d0 = date(2024, 1, 2)
    closes = [10.0] * 5 + [10.5, 11.0, 11.5, 12.0, 12.5]
    rows = []
    for i, c in enumerate(closes):
        d = d0 + timedelta(days=i)
        rows.append({
            "date": d, "open": c, "high": c * 1.01, "low": c * 0.99, "close": c,
            "volume": 1_000_000, "amount": c * 1_000_000, "is_halted": False,
            "limit_up": c * 1.10, "limit_down": c * 0.90, "factor": 1.0,
        })
    df = pd.DataFrame(rows)
    bars = {"600000.SH": df}

    res = run_strategy(bars, MaCrossStrategy(short_ma=2, long_ma=4), init_cash=100_000)
    # The stock should be bought once upon golden cross, not repeatedly queued
    buys = [t for t in res.trades if t["side"] == "buy"]
    assert len(buys) == 1, f"Expected exactly 1 buy order without duplicate triggers, got {len(buys)}"


def test_crit3_macross_execute_target_rebalances_multi_symbols():
    """Verify CRIT-3: run_ma_cross_backtest trims first position to fund second position."""
    d0 = date(2024, 1, 2)
    # A crosses early (day 3), B crosses later (day 6)
    # Total 12 days
    closes_a = [10.0] * 3 + [10.5, 11.0, 11.2, 11.3, 11.4, 11.5, 11.5, 11.5, 11.5]
    closes_b = [10.0] * 6 + [10.5, 11.0, 11.2, 11.3, 11.4, 11.5]

    rows_a = [{"date": d0 + timedelta(days=i), "open": c, "high": c, "low": c, "close": c, "volume": 1e6}
              for i, c in enumerate(closes_a)]
    rows_b = [{"date": d0 + timedelta(days=i), "open": c, "high": c, "low": c, "close": c, "volume": 1e6}
              for i, c in enumerate(closes_b)]

    df_a = pd.DataFrame(rows_a)
    df_b = pd.DataFrame(rows_b)
    bench_df = pd.DataFrame({"date": [d0 + timedelta(days=i) for i in range(12)], "close": [3000.0] * 12})

    params = MaCrossParams(short_ma=2, long_ma=3, trailing_stop_pct=10.0, init_cash=100_000.0)
    res = run_ma_cross({"SYM_A": df_a, "SYM_B": df_b}, bench_df, params)

    # In the old code, SYM_B would never be bought because SYM_A occupied 100% and cash was 0.
    # In the fixed code, SYM_A is partially trimmed and SYM_B is bought!
    symbols_bought = {t["symbol"] for t in res.trades if t["side"] == "buy"}
    assert "SYM_A" in symbols_bought
    assert "SYM_B" in symbols_bought, "SYM_B must be bought after rebalancing cash from SYM_A"


def test_high2_weekly_rebalance_with_holiday_boundary():
    """Verify HIGH-2: engine triggers weekly rebalance on Thursday when Friday is holiday."""
    # Week 1: Mon 01-08 to Thu 01-11 (Friday 01-12 is holiday!)
    # Week 2: Mon 01-15 to Fri 01-19
    dates = [
        date(2024, 1, 8), date(2024, 1, 9), date(2024, 1, 10), date(2024, 1, 11),  # Thu is last day of week 1
        date(2024, 1, 15), date(2024, 1, 16), date(2024, 1, 17), date(2024, 1, 18), date(2024, 1, 19)
    ]
    symbols = ["A.SH", "B.SH"]
    rows = []
    for d in dates:
        for s in symbols:
            rows.append({
                "date": d, "symbol": s, "open": 10.0, "high": 10.0, "low": 10.0,
                "close": 10.0, "volume": 1e6, "limit_up": 11.0, "limit_down": 9.0,
                "is_halted": False,
            })
    uni = pd.DataFrame(rows)

    # Initial signal prefers A.SH. On 2024-01-10 (Wed), signal shifts to B.SH.
    # With signal_lag=1, on 2024-01-11 (Thu, last trading day of week 1),
    # the engine should recognize week boundary and rebalance to B.SH!
    sig_rows = []
    for d in dates:
        sig_rows.append({"date": d, "symbol": "A.SH", "pred_score": 10.0 if d < date(2024, 1, 10) else 1.0})
        sig_rows.append({"date": d, "symbol": "B.SH", "pred_score": 1.0 if d < date(2024, 1, 10) else 10.0})
    sig = pd.DataFrame(sig_rows)

    res = run_backtest(uni, sig, init_cash=100_000, top_k=1, rebalance_freq="weekly")
    # Check that a trade happened on 2024-01-11 (Thursday)
    trades_on_thu = [t for t in res.trades if t["date"] == date(2024, 1, 11)]
    assert len(trades_on_thu) > 0, "Engine should rebalance on Thursday before the holiday weekend"


def test_high3_dynamic_atr_stop_loss_in_macross():
    """Verify HIGH-3: MaCrossStrategy with ATR stop loss triggers exit when price drops by k*ATR."""
    strat = MaCrossStrategy(
        short_ma=2,
        long_ma=3,
        use_atr_stop=True,
        atr_period=5,
        atr_multiplier=1.5,
    )
    ctx = StrategyContext(date=date(2024, 1, 10), targets=set(), bars={})
    strat.on_init(ctx)

    # 1. Provide initial bars to form golden cross and calculate TR
    # Prices: 10, 10, 11 -> golden cross
    strat.on_bar(ctx, "000001.SZ", {"open": 10.0, "high": 10.2, "low": 9.8, "close": 10.0})
    strat.on_bar(ctx, "000001.SZ", {"open": 10.0, "high": 10.2, "low": 9.8, "close": 10.0})
    strat.on_bar(ctx, "000001.SZ", {"open": 10.5, "high": 11.2, "low": 10.4, "close": 11.0})
    assert "000001.SZ" in ctx.targets, "Golden cross should enter target"

    # Fill more bars so len(tr_hist) >= 5
    strat.on_bar(ctx, "000001.SZ", {"open": 11.0, "high": 11.5, "low": 10.8, "close": 11.2})
    strat.on_bar(ctx, "000001.SZ", {"open": 11.2, "high": 12.0, "low": 11.0, "close": 12.0})

    # High water is 12.0. Average TR is around 0.8-1.0. 1.5 * ATR ~ 1.2 - 1.5.
    # Now price sharply drops to 10.0:
    strat.on_bar(ctx, "000001.SZ", {"open": 10.5, "high": 10.6, "low": 9.9, "close": 10.0})

    # The position should be stopped out
    assert "000001.SZ" not in ctx.targets, "ATR chandelier stop should exit position"
    assert ctx.signals[-1]["action"] in ("stop", "stopped", "dead")


def test_walk_forward_chained_oos_metrics():
    """Verify walk_forward_search chains OOS NAV and computes out-of-sample metrics."""
    def evaluate_window(ws, we):
        def ev(params: dict) -> dict:
            return {
                "sharpe": 1.5,
                "nav": np.array([1.0, 1.02, 1.05]),
            }
        return ev

    windows = [(0, 10, 10, 20), (0, 20, 20, 30)]
    wf = walk_forward_search(
        evaluate_window, windows,
        {"x": [1, 2]},
        method="grid",
    )
    assert wf.chained_oos_nav is not None
    assert len(wf.chained_oos_nav) == 5
    assert "sharpe" in wf.chained_oos_metrics
    assert "max_drawdown" in wf.chained_oos_metrics
    assert "annual_return" in wf.chained_oos_metrics
    assert wf.chained_oos_metrics["max_drawdown"] >= 0.0
