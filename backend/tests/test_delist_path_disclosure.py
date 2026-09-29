"""审计 P1-4 / S2 防回归：退市路径的可达性与披露真实性。

**对报告的更正（2026-09-21 实测）**：报告称「退市过滤/强平/haircut **三条路径
全部空转**」——**部分不成立**。实测（生产库只读 + 面板 schema 只读）：

| 路径 | 驱动条件 | 当前数据下是否生效 |
|---|---|---|
| ① 建库期「按 `delist_date` 剔除」 | `instrument.delist_date` | **空转**（非空 **0/5552**） |
| ② `universe_daily[_bt]` 面板是否含该列 | 面板 schema | **不含** `delist_date` 列（两套面板均无）⇒ ①在**结构上**不可能发生 |
| ③ 持仓强平 + haircut | **面板缺席**（连续 N 日不在 `uni_d`） | **生效**，与 `delist_date` **无关** |

因此真实缺陷是两条：**(a) 披露不实**（原 note 恒定宣称"按 delist_date 剔除、
超期强平减记"）；**(b) 折价损失不可见**（不进 `friction_costs`，用户无从知道
haircut 吃掉多少钱）。本文件对这两条与③的可达性都加防回归。
"""
from __future__ import annotations

import sys
from datetime import date, timedelta
from pathlib import Path

BACKEND_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(BACKEND_ROOT))

import pandas as pd  # noqa: E402
import pytest  # noqa: E402

from app.backtest.broker import Broker, BrokerConfig  # noqa: E402


def _day(i: int) -> date:
    d = date(2026, 3, 2)
    n = 0
    while n < i:
        d += timedelta(days=1)
        if d.weekday() < 5:
            n += 1
    return d


# ---------------- ③ 强平路径可达（不依赖 delist_date） ----------------

def test_delist_liquidation_reachable_without_delist_date() -> None:
    """持仓连续缺席超过宽限期 ⇒ 强平，且**不需要任何 delist_date**。"""
    b = Broker(init_cash=1_000_000.0, config=BrokerConfig(enabled=False))
    b.buy(_day(0), "600000.SH", cash_amount=100_000.0,
          row=pd.Series({"open": 10.0, "close": 10.0, "volume": 1e9, "amount": 1e9,
                         "limit_up": 11.0, "limit_down": 9.0}))
    b.mark_to_market(_day(0), pd.DataFrame({"close": {"600000.SH": 10.0}}))
    assert b.holdings["600000.SH"] > 0

    t = b.liquidate(_day(5), "600000.SH", haircut=0.5)
    assert t is not None, "强平路径不可达 ⇒ 报告的『三条路径全空转』需要修正"
    assert t.reason == "delisted_liquidation"
    assert t.qty > 0 and t.amount > 0
    assert "600000.SH" not in b.holdings
    assert "600000.SH" not in b._prev_close


def test_liquidation_records_haircut_loss_as_friction_cost() -> None:
    """折价损失必须进 `friction_costs["delist_loss"]`（S2 要求；此前完全不可见）。"""
    b = Broker(init_cash=1_000_000.0, config=BrokerConfig(enabled=False))
    b.buy(_day(0), "600000.SH", cash_amount=100_000.0,
          row=pd.Series({"open": 10.0, "close": 10.0, "volume": 1e9, "amount": 1e9,
                         "limit_up": 11.0, "limit_down": 9.0}))
    b.mark_to_market(_day(0), pd.DataFrame({"close": {"600000.SH": 10.0}}))
    qty = b.holdings["600000.SH"]
    assert b.friction_costs["delist_loss"] == 0.0, "强平前不应有退市损失"

    t = b.liquidate(_day(5), "600000.SH", haircut=0.5)
    assert t is not None
    # 损失 = 最后有效收盘估值 − 折价回收额 = 10*qty*0.5
    expect = 10.0 * qty - t.amount
    assert b.friction_costs["delist_loss"] == pytest.approx(expect, rel=1e-9)
    assert expect > 0


def test_liquidation_loss_scale_with_haircut() -> None:
    """haircut 越小损失越大（口径自洽）。"""
    def _loss(h: float) -> float:
        b = Broker(init_cash=1_000_000.0, config=BrokerConfig(enabled=False))
        b.buy(_day(0), "600000.SH", cash_amount=100_000.0,
              row=pd.Series({"open": 10.0, "close": 10.0, "volume": 1e9,
                             "amount": 1e9, "limit_up": 11.0, "limit_down": 9.0}))
        b.mark_to_market(_day(0), pd.DataFrame({"close": {"600000.SH": 10.0}}))
        b.liquidate(_day(5), "600000.SH", haircut=h)
        return b.friction_costs["delist_loss"]

    assert _loss(0.1) > _loss(0.5) > _loss(0.9)
    assert _loss(1.0) == pytest.approx(0.0, abs=1e-9), "无折价则无损失"


def test_liquidation_of_absent_symbol_is_noop_when_not_held() -> None:
    b = Broker(init_cash=1_000_000.0, config=BrokerConfig(enabled=False))
    assert b.liquidate(_day(5), "999999.SH", haircut=0.5) is None
    assert b.friction_costs["delist_loss"] == 0.0


# ---------------- 全链路：缺席 → 强平 → 计入 trades 与成本 ----------------

def test_engine_liquidates_absent_holding_end_to_end(monkeypatch) -> None:
    """引擎级：持仓标的从面板消失 > grace 天后被强平，且计为 `delisted_liquidation`。

    这条用例的价值在于**证明 haircut 路径在当前 `delist_date` 全 NULL 的数据下
    依然可达**（报告称其空转）。
    """
    from app.backtest.engine import run_backtest

    days = [_day(i) for i in range(12)]
    syms = ["600000.SH", "600001.SH"]
    uni_rows, sig_rows = [], []
    for i, d in enumerate(days):
        for j, s in enumerate(syms):
            # 600001.SH 从第 3 天起彻底消失（模拟退市/长期停牌）
            if s == "600001.SH" and i >= 3:
                continue
            px = 10.0 + j
            uni_rows.append({"date": d, "symbol": s, "open": px, "high": px,
                             "low": px, "close": px, "volume": 1e9, "amount": 1e9,
                             "limit_up": px * 1.1, "limit_down": px * 0.9})
            sig_rows.append({"date": d, "symbol": s, "pred_score": float(j)})
    res = run_backtest(pd.DataFrame(uni_rows), pd.DataFrame(sig_rows),
                       init_cash=1_000_000.0, top_k=2, rebalance_freq="daily",
                       delist_haircut=0.5, delist_grace_days=2,
                       friction=BrokerConfig(enabled=True, slippage_bps=0.0,
                                             decay_bps=0.0, impact_linear_bps=0.0))
    liq = [t for t in res.trades if t["reason"] == "delisted_liquidation"]
    assert liq, ("`delist_date` 全 NULL 时强平仍应可达（面板缺席驱动）⇒ "
                 "报告『三条路径全部空转』不准确")
    assert res.friction_costs.get("delist_loss", 0.0) > 0, (
        f"强平损失未计入 friction_costs：{res.friction_costs}")


# ---------------- (a) 披露真实性 ----------------

def test_universe_note_is_truthful_when_delist_date_missing() -> None:
    """`delist_date` 覆盖为 0 时，note 必须**明说**未做剔除且含幸存者偏差。"""
    import polars as pl

    from app.api.v1.backtest import _universe_note

    uni = pl.DataFrame({"symbol": ["600000.SH", "000001.SZ"]})
    cov = {"n_instruments": 5552, "n_with_delist_date": 0, "coverage_pct": 0.0,
           "source": "instrument", "bt_panel_has_delist_column": False}
    note = _universe_note(uni, cov)
    assert "未做按 delist_date 的宇宙剔除" in note, note
    assert "幸存者偏差" in note, note
    assert "0/5552" in note, note
    # 原文案的核心虚假声称不得再出现
    assert "delist_date 之后的日期已从宇宙剔除" not in note, (
        "仍保留「按 delist_date 剔除」的虚假声称")


def test_universe_note_discloses_survivorship_when_coverage_present() -> None:
    """有覆盖时 note 应报告覆盖度（不得无脑宣称已处理）。"""
    import polars as pl

    from app.api.v1.backtest import _universe_note

    uni = pl.DataFrame({"symbol": ["600000.SH"]})
    cov = {"n_instruments": 100, "n_with_delist_date": 40, "coverage_pct": 40.0,
           "source": "instrument", "bt_panel_has_delist_column": False}
    note = _universe_note(uni, cov)
    assert "40/100" in note and "40.0%" in note, note


def test_universe_note_degrades_honestly_when_query_unavailable() -> None:
    """覆盖度查询失败时必须披露"未取到"，不得让读者以为已处理。"""
    import polars as pl

    from app.api.v1.backtest import _universe_note

    cov = {"n_instruments": None, "n_with_delist_date": None, "coverage_pct": None,
           "source": "unavailable", "bt_panel_has_delist_column": False}
    note = _universe_note(pl.DataFrame({"symbol": ["600000.SH"]}), cov)
    assert "未取到" in note, note


def test_delist_coverage_helper_never_raises() -> None:
    """披露查询绝不能因环境问题抛异常（回测本身不能被阻断）。"""
    from app.api.v1.backtest import _delist_coverage

    cov = _delist_coverage()
    assert set(cov) >= {"n_instruments", "n_with_delist_date", "source"}
    assert cov["source"] in {"instrument", "unavailable"}
    if cov["source"] == "instrument":
        assert isinstance(cov["n_instruments"], int)
        assert cov["n_with_delist_date"] <= cov["n_instruments"]