"""domain 纯函数单元测试：adjust / limit / metrics / calendar。"""
from __future__ import annotations

import sys
from datetime import date, datetime, timedelta
from decimal import Decimal
from pathlib import Path

import polars as pl
import pytest

BACKEND_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(BACKEND_ROOT))

from app.domain.adjust import apply_adjust_df
from app.domain.calendar import (
    build_calendar,
    is_trade_day,
    last_completed_trade_day,
    next_trade_day,
    prev_trade_day,
)
from app.domain.limit import InstrumentAttrs, calc_limit_prices, determine_board
from app.domain.metrics import (
    all_metrics,
    max_drawdown,
    profit_loss_ratio,
    sharpe_ratio,
    win_rate,
)


# ---------------- adjust ----------------
class TestAdjust:
    def _bar(self) -> pl.DataFrame:
        return pl.DataFrame([
            {"symbol": "A", "date": date(2024, 1, 1), "open": 10, "high": 11, "low": 9, "close": 10},
            {"symbol": "A", "date": date(2024, 1, 2), "open": 10, "high": 11, "low": 9, "close": 11},
        ])

    def _fac(self) -> pl.DataFrame:
        return pl.DataFrame([
            {"symbol": "A", "date": date(2024, 1, 1), "adj_factor": 1.5},
            {"symbol": "A", "date": date(2024, 1, 2), "adj_factor": 1.65},
        ])

    def test_none_passthrough(self):
        out = apply_adjust_df(self._bar(), self._fac(), "none")
        assert out["close"].to_list() == [10.0, 11.0]

    def test_hfq(self):
        out = apply_adjust_df(self._bar(), self._fac(), "hfq")
        assert out["close"].to_list() == pytest.approx([15.0, 18.15], rel=1e-6)

    def test_qfq_last_day_equals_raw(self):
        """关键不变量：qfq 最后一日价格必须等于原始价（今日不被再修正）。"""
        out = apply_adjust_df(self._bar(), self._fac(), "qfq")
        assert out["close"].to_list()[-1] == pytest.approx(11.0, rel=1e-9)
        # 历史（除权前）价格被向下调整：10 * 1.5/1.65
        assert out["close"].to_list()[0] == pytest.approx(10 * 1.5 / 1.65, rel=1e-9)

    def test_invalid_mode(self):
        with pytest.raises(ValueError):
            apply_adjust_df(self._bar(), self._fac(), "xxx")


# ---------------- limit ----------------
class TestLimit:
    def test_main_10pct(self):
        a = InstrumentAttrs("600519.SH", "600519")
        up, dn, pct = calc_limit_prices(100.0, a, date(2024, 1, 2))
        assert up == Decimal("110.00") and dn == Decimal("90.00")
        assert pct == Decimal("0.10")

    def test_main_st_5pct(self):
        a = InstrumentAttrs("600000.SH", "600000", is_st=True)
        up, dn, _ = calc_limit_prices(10.0, a, date(2024, 1, 2))
        assert up == Decimal("10.50") and dn == Decimal("9.50")

    def test_chinext_20pct_even_if_st(self):
        """创业板 ST 仍为 ±20%（交易所实际规则）。"""
        a = InstrumentAttrs("300750.SZ", "300750", is_st=True)
        up, dn, pct = calc_limit_prices(100.0, a, date(2024, 1, 2))
        assert up == Decimal("120.00") and dn == Decimal("80.00")
        assert pct == Decimal("0.20")

    def test_star_20pct(self):
        a = InstrumentAttrs("688981.SH", "688981")
        assert determine_board(a) == "chinext_star"
        up, _, _ = calc_limit_prices(50.0, a, date(2024, 1, 2))
        assert up == Decimal("60.00")

    def test_bse_30pct(self):
        a = InstrumentAttrs("831010.BJ", "831010")
        assert determine_board(a) == "bse"
        up, dn, _ = calc_limit_prices(10.0, a, date(2024, 1, 2))
        assert up == Decimal("13.00") and dn == Decimal("7.00")

    def test_chinext_new_issue_no_limit(self):
        a = InstrumentAttrs("301000.SZ", "301000", list_date=date(2024, 1, 2))
        up, dn, _ = calc_limit_prices(30.0, a, date(2024, 1, 2))
        assert up > Decimal("100000") and dn == Decimal("0.00")

    def test_round_half_up_not_bankers(self):
        """10.55 * 1.1 = 11.605 -> 四舍五入 11.61（银行家舍入会错成 11.60）。"""
        a = InstrumentAttrs("600000.SH", "600000")
        up, _, _ = calc_limit_prices(10.55, a, date(2024, 1, 2))
        assert up == Decimal("11.61")

    def test_prev_close_validation(self):
        a = InstrumentAttrs("600519.SH", "600519")
        with pytest.raises(ValueError):
            calc_limit_prices(None, a, date(2024, 1, 2))
        with pytest.raises(ValueError):
            calc_limit_prices(0, a, date(2024, 1, 2))


# ---------------- metrics ----------------
class TestMetrics:
    def test_straight_line(self):
        nav = [1.0, 1.01, 1.02, 1.03, 1.04, 1.05]
        m = all_metrics(nav)
        assert m["total_return"] == pytest.approx(0.05, rel=1e-3)
        assert m["max_drawdown"] == 0.0
        assert m["win_rate"] == 1.0
        assert sharpe_ratio(nav) > 0

    def test_max_drawdown_with_positions(self):
        nav = [1.0, 1.2, 0.9, 1.1]
        mdd, peak, trough = max_drawdown(nav)
        assert peak == 1 and trough == 2
        assert mdd == pytest.approx(1 - 0.9 / 1.2, rel=1e-6)

    def test_win_rate_and_pl_ratio(self):
        # 日收益: +20%, -25%, +22.2% -> 2 胜 1 负；盈亏比 = mean(正)/|mean(负)|
        nav = [1.0, 1.2, 0.9, 1.1]
        assert win_rate(nav) == pytest.approx(2 / 3)
        plr = profit_loss_ratio(nav)
        assert plr == pytest.approx(((0.2 + (1.1 / 0.9 - 1)) / 2) / 0.25, rel=1e-6)

    def test_zero_std_sharpe(self):
        assert sharpe_ratio([1.0, 1.0, 1.0]) == 0.0

    def test_validation(self):
        with pytest.raises(ValueError):
            all_metrics([1.0])            # 长度不足
        with pytest.raises(ValueError):
            all_metrics([1.0, -1.0, 2.0])  # 负净值


# ---------------- calendar（P0 修复后：纯函数 + 显式 CalendarData） ----------------
class TestCalendar:
    def test_is_trade_day_and_neighbors(self):
        base = date(2024, 1, 1)
        days = [base + timedelta(days=i) for i in range(14)]
        cal = build_calendar(d for d in days if d.weekday() < 5)  # 仅工作日
        assert is_trade_day(date(2024, 1, 2), cal) is True    # Tue
        assert is_trade_day(date(2024, 1, 6), cal) is False   # Sat
        assert is_trade_day(date(2024, 1, 2)) is False        # 空日历（无隐式全局状态）
        assert prev_trade_day(date(2024, 1, 8), cal) == date(2024, 1, 5)
        assert next_trade_day(date(2024, 1, 5), cal) == date(2024, 1, 8)

    def test_last_completed_trade_day_before_close(self):
        """盘中/盘前不能返回今天：日线的"今天"是盘中快照，入库即永久残缺。"""
        base = date(2024, 1, 1)
        days = [base + timedelta(days=i) for i in range(14)]
        cal = build_calendar(d for d in days if d.weekday() < 5)
        tue = date(2024, 1, 2)
        assert last_completed_trade_day(cal, datetime(2024, 1, 2, 10, 0)) == date(2024, 1, 1)
        # 收盘缓冲 15:29 仍算未收盘
        assert last_completed_trade_day(cal, datetime(2024, 1, 2, 15, 29)) == date(2024, 1, 1)
        # 15:30 起视为已收盘 → 目标日即今天
        assert last_completed_trade_day(cal, datetime(2024, 1, 2, 15, 30)) == tue

    def test_last_completed_trade_day_non_trade_day(self):
        """周六（非交易日）：无论几点都回退到上一个交易日。"""
        base = date(2024, 1, 1)
        days = [base + timedelta(days=i) for i in range(14)]
        cal = build_calendar(d for d in days if d.weekday() < 5)
        sat = datetime(2024, 1, 6, 20, 0)  # 2024-01-06 为周六
        assert last_completed_trade_day(cal, sat) == date(2024, 1, 5)
