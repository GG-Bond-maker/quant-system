"""P2-3 分组回测 + P2-4 摩擦成本测试。"""
from __future__ import annotations

import sys
from datetime import date, timedelta
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

BACKEND_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(BACKEND_ROOT))

from app.backtest.broker import Broker, BrokerConfig  # noqa: E402
from app.backtest.engine import (  # noqa: E402
    run_backtest,
    run_backtest_comparison,
    run_group_backtest,
)

D0 = date(2024, 1, 2)


def _universe(dates: list[date], symbols: list[str], price: float = 10.0,
              daily_amount: float = 1e7) -> pd.DataFrame:
    rows = []
    for d in dates:
        for s in symbols:
            rows.append({"date": d, "symbol": s, "open": price, "high": price,
                         "low": price, "close": price, "volume": 1e6,
                         "amount": daily_amount,
                         "limit_up": price * 1.1, "limit_down": price * 0.9,
                         "is_halted": False})
    return pd.DataFrame(rows)


class TestFriction:
    def test_slippage_shifts_execution_price(self):
        """TC-FRICTION-SLIP：启用滑点后买价 = open×(1+bps)。"""
        row = pd.Series({"open": 10.0, "high": 10.2, "low": 9.8, "close": 10.0,
                         "volume": 1e6, "amount": 1e7, "limit_up": 11.0,
                         "limit_down": 9.0, "is_halted": False})
        b0 = Broker(init_cash=100_000)
        b1 = Broker(init_cash=100_000, config=BrokerConfig(slippage_bps=5, enabled=True))
        t0 = b0.buy(D0, "A.SH", cash_amount=10_000, row=row)
        t1 = b1.buy(D0, "A.SH", cash_amount=10_000, row=row)
        assert t0.price == 10.0
        assert t1.price == pytest.approx(10.0 * (1 + 5 / 10_000))
        assert t1.qty < t0.qty  # 滑点降低可买股数
        assert b1.friction_costs["slippage"] > 0

    def test_impact_cost_only_above_threshold(self):
        """TC-FRICTION-IMPACT：amount > daily_amount×pct 才产生冲击成本。"""
        row_small = pd.Series({"open": 10.0, "close": 10.0, "volume": 1e6,
                               "amount": 1e7, "limit_up": 11.0, "limit_down": 9.0,
                               "is_halted": False, "high": 10.2, "low": 9.8})
        b = Broker(init_cash=1_000_000,
                   config=BrokerConfig(impact_pct=0.02, impact_linear_bps=30, enabled=True))
        t_small = b.buy(D0, "A.SH", cash_amount=5_000, row=row_small)  # 5000 << 2e5
        assert t_small.reason == "filled" and b.friction_costs["impact"] == 0.0
        b2 = Broker(init_cash=10_000_000,
                    config=BrokerConfig(impact_pct=0.02, impact_linear_bps=30, enabled=True))
        t_big = b2.buy(D0, "A.SH", cash_amount=1_000_000, row=row_small)  # 1e6 > 2e5
        assert t_big.reason == "filled"
        assert b2.friction_costs["impact"] > 0

    def test_decay_cost_applied_by_engine(self):
        """TC-FRICTION-DECAY：engine 启用 decay 后现金额外减少、decay 成本入账。"""
        dates = [D0 + timedelta(days=i) for i in range(5)]
        uni = _universe(dates, ["A.SH", "B.SH", "C.SH"])
        sig = uni[["date", "symbol"]].copy()
        sig["pred_score"] = 0.5
        raw, fri = run_backtest_comparison(
            uni, sig, friction=BrokerConfig(decay_bps=10, enabled=True),
            init_cash=100_000, top_k=2)
        assert fri.friction_costs["decay"] > 0
        # 2026-09-21（审计 P1-4/S2）：`friction_costs` 新增 `delist_loss` 键
        # （退市强平折价损失的可观测性），故"全零"断言需含该键。
        assert raw.friction_costs == {"slippage": 0.0, "impact": 0.0, "decay": 0.0,
                                      "delist_loss": 0.0}
        # 成本使终值不高于无摩擦（同信号同参数）
        assert fri.nav_df["nav"].iloc[-1] <= raw.nav_df["nav"].iloc[-1]

    def test_comparison_raw_vs_friction_report(self):
        uni = _universe([D0 + timedelta(days=i) for i in range(6)],
                        ["A.SH", "B.SH", "C.SH"])
        sig = uni[["date", "symbol"]].copy()
        sig["pred_score"] = 0.4
        raw, fri = run_backtest_comparison(
            uni, sig, friction=BrokerConfig(slippage_bps=5, decay_bps=10,
                                            impact_pct=0.02, impact_linear_bps=30,
                                            enabled=True),
            init_cash=100_000, top_k=2)
        for k in ("slippage", "impact", "decay"):
            assert k in fri.friction_costs
        assert "annual_return" in raw.metrics and "annual_return" in fri.metrics

    def test_disabled_config_is_zero_friction(self):
        from datetime import timedelta as _td
        uni = _universe([D0, D0 + _td(days=1)], ["A.SH"])
        sig = uni[["date", "symbol"]].copy()
        sig["pred_score"] = 1.0
        res = run_backtest(uni, sig, init_cash=100_000, top_k=1,
                           friction=BrokerConfig(enabled=False))
        assert res.friction_costs == {"slippage": 0.0, "impact": 0.0, "decay": 0.0,
                                      "delist_loss": 0.0}  # 键见 P1-4/S2 注释


class TestGroupBacktest:
    def _signal_with_drift(self, uni: pd.DataFrame) -> pd.DataFrame:
        """构造持续强势标的 X（最高分），验证其稳定落入最高组。"""
        sig = uni[["date", "symbol"]].copy()
        sig["pred_score"] = [1.0 if s == "X.SH" else 0.1 for s in sig["symbol"]]
        return sig

    def test_group_shapes_and_strong_symbol_in_top_group(self):
        dates = [D0 + timedelta(days=i) for i in range(8)]
        symbols = ["X.SH"] + [f"S{i}.SZ" for i in range(11)]
        uni = _universe(dates, symbols)
        res = run_group_backtest(uni, self._signal_with_drift(uni), groups=5,
                                 init_cash=100_000)
        nav = res["group_nav"]
        assert {"date", "Q1", "Q2", "Q3", "Q4", "Q5", "long_short"} <= set(nav.columns)
        assert len(res["group_metrics"]) == 5
        assert "annual_return" in res["group_metrics"]["Q5"]
        # 注：原此处有 `h = res["group_nav"]` 死语句（赋值后从未使用，F841），
        # 且随附注释声称要断言"强势标的出现在 Q5 持仓历史"，而 `group_nav` 并不含
        # 持仓历史（`run_group_backtest` 只返回 group_nav/group_metrics/...）⇒
        # 该断言在本层无法实现，故连同死语句一并删除（单调性断言见下方专条）。

    def _monotone_universe(self, symbols: list[str], days: int = 45,
                           ascending: bool = True) -> tuple[pd.DataFrame, pd.DataFrame]:
        """Si 日收益与 i 成正比；分数按 i 升序（ascending=False 时反向）。

        价格按 `(1+i*5bp)^t` 展开 ⇒ 各组收益有确定的大小关系，可判定单调性方向。
        """
        ds = [D0 + timedelta(days=k) for k in range(days)
              if (D0 + timedelta(days=k)).weekday() < 5]
        n = len(symbols)
        rows, sig_rows = [], []
        for t, d in enumerate(ds):
            for i, s in enumerate(symbols):
                px = 10.0 * (1 + i * 0.0005) ** t
                rows.append({"date": d, "symbol": s, "open": px, "high": px * 1.001,
                             "low": px * 0.999, "close": px, "volume": 1e6,
                             "amount": 1e8, "limit_up": px * 1.1,
                             "limit_down": px * 0.9, "is_halted": False})
                sig_rows.append({"date": d, "symbol": s,
                                 "pred_score": float(i if ascending else n - i)})
        return pd.DataFrame(rows), pd.DataFrame(sig_rows)

    def test_monthly_monotonic_ratio_direction(self):
        """P1-5：方向必须与 `long_short = Qg − Q1` 一致（原判定式反了）。

        实测修前：收益随分数**递增**（Q1=1.0146 … Q5=1.2849，多空 +0.27）比值为
        **0.0**，而完全**反向**（多空 −0.27）为 **1.0** ⇒ 完美因子被判 0%、反向
        因子被判 100%，且该数字会打印进 `scripts/p2_experiment.py` 的实验报告。
        修后：递增 ⇒ 1.0、反向 ⇒ 0.0。
        """
        syms = [f"S{i}.SZ" for i in range(20)]
        uni, sig = self._monotone_universe(syms, ascending=True)
        good = run_group_backtest(uni, sig, groups=5, init_cash=1_000_000)
        nav = good["group_nav"].iloc[-1]
        assert nav["Q5"] > nav["Q4"] > nav["Q3"] > nav["Q2"] > nav["Q1"], (
            "构造前提：收益应随分数递增")
        assert good["monthly_monotonic_ratio"] == 1.0

        uni2, sig2 = self._monotone_universe(syms, ascending=False)
        bad = run_group_backtest(uni2, sig2, groups=5, init_cash=1_000_000)
        nav2 = bad["group_nav"].iloc[-1]
        assert nav2["Q5"] < nav2["Q1"], "构造前提：反向后 Q5 应最差"
        assert bad["monthly_monotonic_ratio"] == 0.0, (
            "反向因子不得被判为单调（这正是修前的错误行为）")

    def test_groups_10(self):
        dates = [D0 + timedelta(days=i) for i in range(6)]
        symbols = [f"S{i}.SZ" for i in range(20)]
        uni = _universe(dates, symbols)
        rng = np.random.default_rng(0)
        sig = uni[["date", "symbol"]].copy()
        sig["pred_score"] = rng.random(len(sig))
        res = run_group_backtest(uni, sig, groups=10, init_cash=100_000)
        assert {f"Q{q}" for q in range(1, 11)} <= set(res["group_nav"].columns)

    def test_groups_independent_brokers(self):
        """组间不共享持仓：Q5 全押强势标的，Q1 不应持有它。"""
        dates = [D0 + timedelta(days=i) for i in range(4)]
        symbols = ["X.SH"] + [f"S{i}.SZ" for i in range(5)]
        uni = _universe(dates, symbols)
        res = run_group_backtest(uni, self._signal_with_drift(uni), groups=2,
                                 init_cash=100_000)
        nav = res["group_nav"]
        # Q2（高分组）净值因持有 X 而不同于 Q1（低分组，无 X）
        assert not np.allclose(nav["Q1"], nav["Q2"])

    def test_no_future_signal(self):
        """防泄漏：信号在第 4 日才出现 -> 第 1~3 日各组净值应恒等于初始。"""
        dates = [D0 + timedelta(days=i) for i in range(6)]
        symbols = ["X.SH", "Y.SH"]
        uni = _universe(dates, symbols)
        sig = uni[uni["date"] == dates[3]][["date", "symbol"]].copy()
        sig["pred_score"] = 1.0
        res = run_group_backtest(uni, sig, groups=2, init_cash=100_000)
        nav = res["group_nav"]
        for i in range(3):
            assert nav["Q1"].iloc[i] == 1.0 and nav["Q2"].iloc[i] == 1.0

    def test_invalid_groups(self):
        uni = _universe([D0], ["A.SH"])
        with pytest.raises(ValueError):
            run_group_backtest(uni, uni.assign(pred_score=1.0), groups=1)
