"""Task 5（整改计划 A-P1-3）：冲击成本/参与率端到端生效 + 流动性如实披露。

背景：主回测 /run 曾因 universe_daily 无 amount 列，冲击成本恒 0、
参与率上限无操作、按开盘价无限量成交且响应无提示（收益虚高）。
T1 已让 universe_daily_bt 携带 amount（raw 口径）+ factor 列；本任务
以测试固化端到端行为，并在响应中如实披露流动性口径。
"""
from __future__ import annotations

import sys
from datetime import date
from pathlib import Path

BACKEND_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(BACKEND_ROOT))

import pandas as pd  # noqa: E402

from app.backtest.broker import Broker, BrokerConfig  # noqa: E402

SYM = "600001.SH"


def _row(amount: float) -> pd.Series:
    return pd.Series({"close": 10.0, "open": 10.0, "volume": 1e6,
                      "amount": amount, "is_halted": False,
                      "limit_up": 11.0, "limit_down": 9.0, "factor": 1.0})


def test_broker_impact_fires_with_amount():
    """sqrt 冲击模型 + 真实日成交额：大单成本 > 0 且从现金扣减。"""
    b = Broker(init_cash=10_000_000, config=BrokerConfig(
        enabled=True, impact_model="sqrt", impact_sqrt_coef_bps=10.0))
    t = b.buy(date(2024, 1, 2), SYM, 5_000_000.0, _row(amount=1e7))
    assert t.reason == "filled"
    assert b.friction_costs["impact"] > 0


def test_impact_zero_when_amount_missing():
    """无成交额（amount=0）：冲击成本按 0 处理，不虚构（宁可低估）。"""
    b = Broker(init_cash=10_000_000, config=BrokerConfig(
        enabled=True, impact_model="sqrt", impact_sqrt_coef_bps=10.0))
    t = b.buy(date(2024, 1, 2), SYM, 5_000_000.0, _row(amount=0.0))
    assert t.reason == "filled"
    assert b.friction_costs["impact"] == 0.0


def test_participation_cap_limits_buy():
    """参与率上限：买入金额截断到日成交额 × max_participation（hfq 域一致）。"""
    b = Broker(init_cash=10_000_000, config=BrokerConfig(
        enabled=True, max_participation=0.05))
    t = b.buy(date(2024, 1, 2), SYM, 5_000_000.0, _row(amount=1e7))
    assert t.reason == "filled"
    assert t.amount <= 1e7 * 0.05 * 1.01  # 1% 容差覆盖滑点


def test_participation_cap_scales_with_factor():
    """factor≠1 时，参与率分母换算到 hfq 域（raw amount × f），不虚增参与率。"""
    row = _row(amount=1e7)
    row["factor"] = 3.0
    b = Broker(init_cash=10_000_000, config=BrokerConfig(
        enabled=True, max_participation=0.05))
    t = b.buy(date(2024, 1, 2), SYM, 2_000_000.0, row)
    assert t.reason == "filled"
    # 上限 = 1e7 × 3.0 × 0.05 = 1.5e6（hfq 域）；预算 2e6 被截断
    assert t.amount <= 1.5e6 * 1.01
