"""Task 7（整改计划 A-P1-8）：ma_cross 统一到 broker 闸门 + 框架策略补涨跌停列。

背景：
1. 旧 ma_cross 引擎自带结算（execute_target），无停牌/涨跌停/T+1 闸门，
   可在涨停价成交 → 与同页 donchian/rsi（走 broker）口径分裂，结果偏乐观；
2. 更深的发现：framework 策略虽走 broker，但 run_strategy 构造的 uni_d
   从未携带 limit_up/limit_down 列 → broker 退化为 ±20% 默认涨跌停，
   涨停闸门对所有框架策略同样失效。

修复：run_strategy 携带 limit 列；API 的 ma_cross 分支迁移到框架版
MaCrossStrategy（保留 use_legacy_engine 开关可回退旧引擎）。
"""
from __future__ import annotations

import sys
from datetime import date, timedelta
from pathlib import Path

BACKEND_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(BACKEND_ROOT))

import pandas as pd  # noqa: E402
import pytest  # noqa: E402

from app.backtest.strategy_base import (  # noqa: E402
    MaCrossStrategy,
    run_strategy,
)

SYM = "600001.SH"
D0 = date(2024, 1, 2)


def _make_bars(limit_up_on_exec_day: bool) -> dict[str, pd.DataFrame]:
    """构造金叉场景：8 天横盘 10 元后连续上攻，短均线(3)上穿长均线(5)。

    执行日（金叉次日）开盘设为涨停价（昨收 × 1.10）：
    limit_up_on_exec_day=True 时 limit_up 列 = 该开盘价 -> 应拒买。
    closes: 10×8, 10.3, 10.9, 11.6, 12.3, 13.0（第 9~13 天）
    """
    closes = [10.0] * 8 + [10.3, 10.9, 11.6, 12.3, 13.0]
    rows = []
    for i, c in enumerate(closes):
        d = D0 + timedelta(days=i)
        rows.append({"date": d, "open": c, "close": c, "volume": 1e6,
                     "amount": c * 1e6, "is_halted": False,
                     "limit_up": c * 1.10, "limit_down": c * 0.90})
    df = pd.DataFrame(rows)
    # 金叉判定发生在第 9 天（index 8，close=10.3）：ma3=10.1 > ma5=10.06
    # -> 目标于次日（index 9）开盘执行：开盘设涨停（10.3×1.1=11.33）
    if limit_up_on_exec_day:
        exec_i = 9
        df.loc[exec_i, "open"] = round(df.loc[exec_i - 1, "close"] * 1.10, 2)
        df.loc[exec_i, "limit_up"] = df.loc[exec_i, "open"]
    return {SYM: df}


def _days(bars):
    return sorted(set(bars[SYM]["date"]))


def test_framework_macross_respects_limit_up():
    """执行日开盘涨停（limit_up 列 = open）：当日拒买，次日合法价才成交。

    （策略被拒后会重试——闸门的语义是"涨停价不可成交"，不是"永不入场"。）
    """
    bars = _make_bars(limit_up_on_exec_day=True)
    res = run_strategy(bars, MaCrossStrategy(short_ma=3, long_ma=5), init_cash=1_000_000)
    exec_day = D0 + timedelta(days=9)
    buys_on_exec_day = [t for t in res.trades
                        if t["side"] == "buy" and t["date"] == exec_day]
    assert buys_on_exec_day == [], "涨停日买入应被拒绝"
    later = [t for t in res.trades if t["side"] == "buy" and t["date"] > exec_day]
    # 次日（index 10）open=11.6，limit_up=11.6×1.1=12.76：合法价成交
    assert len(later) == 1 and later[0]["price"] == pytest.approx(11.606, abs=0.01)


def test_framework_macross_buys_when_not_limit():
    """同场景但执行日开盘未涨停：正常买入（闸门只在涨停时拦截）。"""
    bars = _make_bars(limit_up_on_exec_day=False)
    res = run_strategy(bars, MaCrossStrategy(short_ma=3, long_ma=5), init_cash=1_000_000)
    buys = [t for t in res.trades if t["side"] == "buy"]
    assert len(buys) == 1
    assert buys[0]["qty"] > 0


def test_limit_columns_absent_keeps_legacy_default():
    """无 limit 列时 broker 退化为 ±20% 默认——旧行为兼容（不误伤既有调用方）。"""
    bars = _make_bars(limit_up_on_exec_day=False)
    df = bars[SYM].drop(columns=["limit_up", "limit_down"])
    # 执行日开盘价设为 +13%（<20% 默认涨停）：无 limit 列 -> 仍成交
    exec_i = 9
    df.loc[exec_i, "open"] = round(df.loc[exec_i - 1, "close"] * 1.13, 2)
    res = run_strategy({SYM: df}, MaCrossStrategy(short_ma=3, long_ma=5),
                       init_cash=1_000_000)
    buys = [t for t in res.trades if t["side"] == "buy"]
    assert len(buys) == 1
