"""审计 B5-20 防回归：逐笔 pnl 的成本基准必须含**买入**费用。

**缺陷（2026-09-21 实测，见 `backend/.tmp_testrun/b520_pnl_basis.py`）**：
两处实现的移动平均成本都把**买入费用排除在基准之外**——
- `strategy_base.py:410-411`：`avg_cost = (avg_cost*old_q + t.amount) / new_q`
- `ma_cross.py:214-215`：`avg_cost = (prev_cost + amount) / holdings`
而卖出侧 pnl 是 `(price - base) * qty - 卖出cost` ⇒ **只减了卖出腿的费用**，
往返成本少算买入腿 ⇒ `win_rate` / `avg_pnl_ratio` **系统性偏乐观**。

**实测（engineered 往返：买 9200@10.300 费 29.38，卖 9200@10.310 费 76.83）**：
    ORIG  pnl = **+15.17 ⇒ 记盈利**（win_rate 100%）
    FIXED pnl = **−14.21 ⇒ 记亏损**（win_rate 0%）
恒等式 `FIXED_pnl == ORIG_pnl − 买入fee` 精确成立（15.17 − 29.38 = −14.21）。

口径说明：本修复让逐笔 pnl 变为**双边净额**（毛盈亏 − 买入费用 − 卖出费用），
与 `nav`/`sharpe` 的口径（已扣全部费用）一致；此前两者不一致。
"""
from __future__ import annotations

import sys
from pathlib import Path

BACKEND_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(BACKEND_ROOT))

import numpy as np  # noqa: E402
import pandas as pd  # noqa: E402
import pytest  # noqa: E402

# 「毛赚、净亏」的临界行情：金叉买入后微涨即死叉。
# 卖出价 10.310 使毛盈利(≈92.0 元) 落在「卖出费用(≈76.8)」与
# 「卖出费用 + 买入费用(≈106.2)」之间 ⇒ ORIG 记盈利、FIXED 记亏损。
PATH = [10.0, 10.1, 10.2, 10.3, 10.31, 10.305, 10.309, 10.310, 10.29, 10.28]


def _frame() -> pd.DataFrame:
    close = np.array(PATH)
    return pd.DataFrame({
        "date": pd.bdate_range("2026-01-05", periods=len(close)).date,
        "symbol": "600000.SH",
        "open": close, "high": close * 1.01, "low": close * 0.99,
        "close": close, "volume": 1e7, "amount": 1e8,
        "limit_up": close * 1.1, "limit_down": close * 0.9,
    })


def _round_trip(trades: list[dict]) -> tuple[dict, dict]:
    """取首笔买入与其后首笔卖出。"""
    buy = next(t for t in trades if t["side"] == "buy")
    sell = next(t for t in trades if t["side"] == "sell")
    return buy, sell


# ---------------- 1) 框架路径 `run_strategy` ----------------

@pytest.fixture(scope="module")
def strategy_run():
    from app.backtest.strategy_base import STRATEGIES, run_strategy

    tmpl = STRATEGIES["ma_cross"](short_ma=2, long_ma=3, trailing_stop_pct=99.0)
    res = run_strategy({"600000.SH": _frame()}, tmpl, init_cash=100_000.0,
                       commission_rate=0.0003, slippage_bps=0.0)
    return res


def test_strategy_pnl_is_net_of_both_legs(strategy_run) -> None:
    """pnl 必须等于「毛盈亏 − 买入费 − 卖出费」。"""
    buy, sell = _round_trip(strategy_run.trades)
    gross = (sell["price"] - buy["price"]) * sell["qty"]
    assert sell["pnl"] == pytest.approx(gross - buy["fee"] - sell["fee"], abs=0.02), (
        f"pnl={sell['pnl']} 应为毛盈亏 {gross:.2f} 减去双边费用 "
        f"{buy['fee']:.2f}+{sell['fee']:.2f}（只减卖出费 = B5-20 缺陷）")


def test_strategy_gross_profitable_trip_is_recorded_as_loss(strategy_run) -> None:
    """本用例是缺陷的**决定性**判据：毛盈利的往返必须被记成亏损。"""
    buy, sell = _round_trip(strategy_run.trades)
    gross = (sell["price"] - buy["price"]) * sell["qty"]
    assert gross > 0, "前置条件：该往返毛盈利"
    assert sell["pnl"] < 0, (
        f"毛盈利 {gross:.2f} 元但净额 {sell['pnl']:.2f} 应 < 0"
        f"（若 > 0 则说明买入费用未进成本基准 ⇒ win_rate 偏乐观）")


def test_strategy_win_rate_not_optimistic(strategy_run) -> None:
    """该单笔往返在旧口径下会被算作 100% 胜率，现必须为 0%。"""
    assert strategy_run.risk["win_rate"] == pytest.approx(0.0), (
        f"win_rate={strategy_run.risk['win_rate']} 应为 0（旧口径给出 1.0）")


def test_strategy_buy_trade_has_no_pnl_and_fee_recorded(strategy_run) -> None:
    buy, _ = _round_trip(strategy_run.trades)
    assert buy["pnl"] is None, "买入笔不记 pnl（与既有口径一致）"
    assert buy["fee"] > 0, "买入费用必须落账，否则无法验证基准"


# ---------------- 2) 原引擎路径 `run_ma_cross` ----------------

def test_ma_cross_pnl_is_net_of_both_legs() -> None:
    """原引擎路径（`ma_cross.py:214-215`）同修，判据相同。"""
    from app.backtest.ma_cross import MaCrossParams, run_ma_cross

    df = _frame()
    bench = pd.DataFrame({"date": df["date"], "close": df["close"]})
    res = run_ma_cross({"600000.SH": df}, bench,
                       MaCrossParams(short_ma=2, long_ma=3, trailing_stop_pct=99.0,
                                     commission_rate=0.0003, slippage_bps=0.0,
                                     init_cash=100_000.0))
    buy, sell = _round_trip(res.trades)
    gross = (sell["price"] - buy["price"]) * sell["qty"]
    assert gross > 0, "前置条件：该往返毛盈利"
    assert sell["pnl"] == pytest.approx(gross - buy["fee"] - sell["fee"], abs=0.05), (
        f"原引擎 pnl={sell['pnl']} 未减双边费用（毛 {gross:.2f}）")
    assert sell["pnl"] < 0, (
        f"毛盈利 {gross:.2f} 元的往返应记亏损，实际 {sell['pnl']:.2f} ⇒ 成本基准漏买入费")


def test_ma_cross_cost_basis_includes_buy_fee() -> None:
    """直接验证成本基准公式：买入后 avg_cost ≈ 买入价 + 单位费用。"""
    from app.backtest.ma_cross import MaCrossParams, run_ma_cross

    df = _frame()
    bench = pd.DataFrame({"date": df["date"], "close": df["close"]})
    res = run_ma_cross({"600000.SH": df}, bench,
                       MaCrossParams(short_ma=2, long_ma=3, trailing_stop_pct=99.0,
                                     commission_rate=0.0003, slippage_bps=0.0,
                                     init_cash=100_000.0))
    buy, sell = _round_trip(res.trades)
    unit_fee = buy["fee"] / buy["qty"]
    # 由卖出 pnl 反推基准：base = price - (pnl + sell_fee)/qty
    base = sell["price"] - (sell["pnl"] + sell["fee"]) / sell["qty"]
    assert base == pytest.approx(buy["price"] + unit_fee, abs=1e-4), (
        f"成本基准 {base:.6f} 应 ≈ 买入价 {buy['price']} + 单位费用 {unit_fee:.6f}"
        f"（旧口径基准 = 买入价，漏费用）")


# ---------------- 3) 与 nav 口径一致性 ----------------

def test_pnl_basis_is_consistent_with_nav_direction() -> None:
    """若逐笔 pnl 是双边净额，则「单笔往返亏损」与「净值下降」必须同向。"""
    from app.backtest.strategy_base import STRATEGIES, run_strategy

    tmpl = STRATEGIES["ma_cross"](short_ma=2, long_ma=3, trailing_stop_pct=99.0)
    res = run_strategy({"600000.SH": _frame()}, tmpl, init_cash=100_000.0,
                       commission_rate=0.0003, slippage_bps=0.0)
    _, sell = _round_trip(res.trades)
    # 注意 `StrategyRunResult.nav_df` 的列名是 `equity`（非 `nav`）
    equity_change = float(res.nav_df["equity"].iloc[-1] - res.nav_df["equity"].iloc[0])
    assert (sell["pnl"] < 0) == (equity_change < 0), (
        f"逐笔 pnl={sell['pnl']:.2f} 与权益变化 {equity_change:+.2f} 方向不一致 ⇒ 口径打架")