"""TC-EQUAL-WEIGHT：验证 Top-K 等权再平衡的仓位正确性（第八阶段 CRIT-001）。

修复前的实测缺陷：
    engine.py 用 ``broker.cash * 0.95 / N`` 计算买入预算，而该 cash 是**卖出前**余额。
    broker.match() 先卖后买，卖出回笼资金没进入当日建仓 ->
    稳态仓位利用率仅 58.7%，两只持仓 38,800 : 19,400 股（2:1 而非 1:1）。

本测试用"价格恒定"的受控场景把价格因素完全剔除，
使得任何仓位偏差都只能归因于资金分配逻辑。
"""
from __future__ import annotations

import sys
from pathlib import Path

import pandas as pd
import pytest

BACKEND_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(BACKEND_ROOT))

from app.backtest.engine import run_backtest  # noqa: E402

SYMS = ["A.SZ", "B.SZ", "C.SZ", "D.SZ"]
PRICE = 10.0


def _flat_universe(n_days: int, start: str = "2024-01-01") -> pd.DataFrame:
    """价格恒定、无涨跌停、无停牌的受控行情。"""
    dates = pd.bdate_range(start, periods=n_days).date
    rows = []
    for d in dates:
        for s in SYMS:
            rows.append({
                "date": d, "symbol": s,
                "open": PRICE, "high": PRICE * 1.05, "low": PRICE * 0.95,
                "close": PRICE, "volume": 1_000_000.0, "amount": 10_000_000.0,
                "limit_up": PRICE * 1.10, "limit_down": PRICE * 0.90,
                "is_halted": False,
            })
    return pd.DataFrame(rows)


def _rotating_signal(universe: pd.DataFrame, top_k: int, shift_every: int = 1) -> pd.DataFrame:
    """每 shift_every 天把 Top-K 窗口向后滚动一格，制造持续换仓。"""
    dates = sorted(universe["date"].unique())
    rows = []
    for i, d in enumerate(dates):
        start = (i // shift_every) % len(SYMS)
        top = {SYMS[(start + j) % len(SYMS)] for j in range(top_k)}
        for s in SYMS:
            rows.append({"date": d, "symbol": s,
                         "pred_score": 100.0 if s in top else 0.0})
    return pd.DataFrame(rows)


def _utilization(res) -> list[float]:
    nav = res.nav_df
    return [float((e - c) / e) if e else 0.0
            for c, e in zip(nav["cash"], nav["equity"])]


def test_equal_weight_two_positions_are_1_to_1():
    """核心断言：top_k=2 时两个持仓必须等权。

    修复前为 38,800 : 19,400（2:1，系统性错误）。

    为什么允许 1 手（100 股）残差而不是要求精确相等：
        A 股买入必须取整到 100 股，且买入预算受现金余额约束，
        因此"精确相等"在物理上不可能；强行补齐会产生额外佣金。
        判据因此取"残差 <= 1 手 且 相对偏差 < 1%"。
    """
    uni = _flat_universe(40)
    sig = _rotating_signal(uni, top_k=2, shift_every=3)
    res = run_backtest(uni, sig, init_cash=1_000_000, top_k=2)

    # 跳过前 3 天建仓期，检查稳态
    holdings = [h["holdings"] for h in res.holdings_history[3:]]
    assert holdings, "没有任何持仓记录"
    checked = 0
    for h in holdings:
        if len(h) == 2:
            qty = sorted(h.values())
            diff = qty[1] - qty[0]
            rel = diff / qty[1] if qty[1] else 0.0
            assert diff <= 100, (
                f"等权持仓残差 {diff} 股超过 1 手：{h}（修复前为 19,400 股差异）。"
                f"这说明买入预算未按 target_weight 分配。")
            assert rel < 0.01, f"等权持仓相对偏差 {rel:.2%} 超限：{h}"
            checked += 1
    assert checked > 0, "没有任何一天形成 2 个持仓"


def test_equal_weight_utilization_near_95pct():
    """仓位利用率应稳定在 ~95%（预留 5% 费用缓冲），而不是 58.7%。"""
    uni = _flat_universe(40)
    sig = _rotating_signal(uni, top_k=2, shift_every=3)
    res = run_backtest(uni, sig, init_cash=1_000_000, top_k=2)

    util = _utilization(res)[3:]
    assert util, "没有净值记录"
    avg = sum(util) / len(util)
    low = min(util)
    # 建仓首日之后，任何一天的利用率都不应低于 85%
    assert low > 0.85, (
        f"最低仓位利用率 {low:.1%} 过低（修复前为 52%），"
        f"全部: {[round(u, 3) for u in util]}")
    assert avg > 0.90, f"平均仓位利用率 {avg:.1%} 过低（修复前 57.1%）"


def test_equal_weight_survives_100pct_turnover():
    """每天 100% 换仓（top_k=1 轮转）时，仓位不应塌缩。"""
    uni = _flat_universe(30)
    sig = _rotating_signal(uni, top_k=1, shift_every=1)
    res = run_backtest(uni, sig, init_cash=1_000_000, top_k=1)

    util = _utilization(res)[3:]
    low = min(util)
    assert low > 0.85, (
        f"100% 换仓下最低利用率 {low:.1%}；修复前该场景会退化到 ~4.7%。"
        f" 全部: {[round(u, 3) for u in util]}")


def test_equal_weight_three_positions():
    """top_k=3 时三个持仓股数应相等。"""
    uni = _flat_universe(30)
    sig = _rotating_signal(uni, top_k=3, shift_every=2)
    res = run_backtest(uni, sig, init_cash=1_000_000, top_k=3)

    for h in [x["holdings"] for x in res.holdings_history[4:]]:
        if len(h) == 3:
            qty = sorted(h.values())
            assert max(qty) - min(qty) <= 100, (
                f"三等权持仓差异超过 1 手：{h}")


def test_no_position_when_no_signal():
    """无信号时不应建仓（避免用假信号交易）。"""
    uni = _flat_universe(10)
    sig = pd.DataFrame(columns=["date", "symbol", "pred_score"])
    res = run_backtest(uni, sig, init_cash=1_000_000, top_k=2)
    filled = [t for t in res.trades if t["reason"] == "filled"]
    assert not filled, f"无信号却发生了成交：{filled[:3]}"


def test_signal_lag_is_enforced():
    """signal_lag=1：T 日只能用 T-1 日信号，不得用当日。"""
    uni = _flat_universe(6)
    dates = sorted(uni["date"].unique())
    # 只在最后一个交易日给出信号 -> 因为 lag，回测期内不应成交
    sig = pd.DataFrame([{"date": dates[-1], "symbol": "A.SZ", "pred_score": 99.0},
                        {"date": dates[-1], "symbol": "B.SZ", "pred_score": 1.0}])
    res = run_backtest(uni, sig, init_cash=1_000_000, top_k=1)
    filled = [t for t in res.trades if t["reason"] == "filled"]
    assert not filled, "使用了当日信号成交（未来函数）"


def test_signal_lag_zero_rejected():
    with pytest.raises(ValueError):
        run_backtest(_flat_universe(5), _rotating_signal(_flat_universe(5), 1),
                     signal_lag=0)
