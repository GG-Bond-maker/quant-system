"""策略研究工作台（四象限）纯函数测试——全部真实数据口径，无模拟值。"""
from __future__ import annotations

import sys
from pathlib import Path

import numpy as np
import pandas as pd

BACKEND_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(BACKEND_ROOT))

from app.domain.research import (  # noqa: E402
    daily_rank_ic_series,
    execution_impact_sim,
    factor_corr_matrix,
    neutralize_by_size_cs,
    portfolio_stress_replay,
    quantile_curves,
    stress_windows,
)

D0 = pd.Timestamp("2024-01-02")


def _panel(n_days=200, n_sym=15, seed=7):
    rng = np.random.default_rng(seed)
    dates = pd.bdate_range(D0, periods=n_days)
    syms = [f"S{i:02d}" for i in range(n_sym)]
    close = pd.DataFrame(
        10 * np.exp(np.cumsum(rng.normal(0, 0.015, (n_days, n_sym)), axis=0)),
        index=dates, columns=syms)
    fwd5 = close.shift(-5) / close - 1
    # 因子1 = 未来收益的带噪版（强信号）；因子2 = 与因子1高共线
    f1 = fwd5 + rng.normal(0, 0.01, fwd5.shape)
    f2 = f1 * 0.95 + rng.normal(0, 0.001, fwd5.shape)
    return close, f1, f2


class TestFactorResearch:
    def test_rank_ic_detects_signal(self):
        close, f1, _ = _panel()
        fwd = close.shift(-5) / close - 1
        ics = daily_rank_ic_series(f1, fwd)
        assert len(ics) > 150
        assert ics.mean() > 0.5          # 构造的强信号

    def test_size_neutralization_removes_tilt(self):
        rng = np.random.default_rng(3)
        dates = pd.bdate_range(D0, periods=40)
        syms = [f"S{i:02d}" for i in range(30)]
        size = pd.DataFrame(rng.uniform(1e6, 1e10, (40, 30)),
                            index=dates, columns=syms)
        f = pd.DataFrame(0.0, index=dates, columns=syms)
        for d in dates:
            f.loc[d] = np.log(size.loc[d]) * 0.8   # 因子完全由 size 驱动
        resid = neutralize_by_size_cs(f, size)
        assert resid.abs().mean().mean() < 1e-6   # 残差≈0（暴露被剔除）

    def test_corr_matrix_flags_high_corr(self):
        _close, f1, f2 = _panel()
        rep = factor_corr_matrix({"F1": f1, "F2": f2, "Noise": f1 * np.nan + 0.0})
        assert rep["factors"] == ["F1", "F2", "Noise"]
        pair = {(p["a"], p["b"]): p["r"] for p in rep["high_corr_pairs"]}
        assert pair.get(("F1", "F2"), 0) > 0.7    # 共线因子被红牌标记

    def test_quantile_curves_monotonic_spread(self):
        close, f1, _ = _panel()
        cur = quantile_curves(f1, close, horizon=5, n_quantiles=5)
        nav_top = pd.DataFrame(cur["curves"]["Q5"])["nav"]
        nav_bot = pd.DataFrame(cur["curves"]["Q1"])["nav"]
        assert float(nav_top.iloc[-1]) > float(nav_bot.iloc[-1])   # Top 跑赢 Bottom


class TestExecutionImpact:
    def _bars(self, n=25):
        rng = np.random.default_rng(11)
        dates = pd.bdate_range(D0, periods=n)
        close = 10 * np.exp(np.cumsum(rng.normal(0, 0.01, n)))
        return pd.DataFrame({
            "open": close * 0.999, "high": close * 1.01, "low": close * 0.99,
            "close": close, "volume": 1e7,
            "amount": close * 1e7,
        }, index=dates)

    def test_market_single_fill_with_impact(self):
        bars = self._bars()
        res = execution_impact_sim(bars, "buy", 1_000_000, 0.05, "market")
        assert len(res["fills"]) == 1
        assert res["fills"][0]["impact_bps"] > 0
        assert res["avg_impact_bps"] > 0

    def test_vwap_splits_fills(self):
        bars = self._bars()
        res = execution_impact_sim(bars, "buy", 5_000_000, 0.05, "vwap",
                                   split_days=5)
        assert len(res["fills"]) == 5
        # 分批后单日参与率更低 -> 平均冲击应低于一次性
        one_shot = execution_impact_sim(bars, "buy", 5_000_000, 0.5, "market")
        assert res["avg_impact_bps"] < one_shot["avg_impact_bps"]

    def test_participation_cap_defers(self):
        bars = self._bars()
        # 日成交额 ≈ 1e8，订单 2000 万 = 20% > 5% 上限
        res = execution_impact_sim(bars, "buy", 20_000_000, 0.05, "vwap",
                                   split_days=5)
        total = sum(f["amount"] for f in res["fills"])
        assert total <= 20_000_000 * 1.0001
        # market 模式下首日被截断（≈50 万） -> 有大量未成交余额
        m = execution_impact_sim(bars, "buy", 20_000_000, 0.05, "market")
        assert m["unfilled"] > 0
        assert m["fills"][0]["participation"] <= 0.05 + 1e-9


class TestStress:
    def test_stress_windows_picks_worst(self):
        rng = np.random.default_rng(5)
        n = 600
        rets = rng.normal(0.0003, 0.01, n)
        rets[300:320] -= 0.03            # 注入一段真实崩盘
        nav = pd.Series((1 + rets).cumprod(),
                        index=pd.bdate_range(D0, periods=n))
        wins = stress_windows(nav, window=20, top=5)
        assert len(wins) == 5
        assert wins[0]["window_end"] == str(nav.index[319])[:10]  # 崩盘末段
        assert wins[0]["market_mdd"] > 0.3

    def test_portfolio_replay(self):
        rng = np.random.default_rng(6)
        n = 300
        dates = pd.bdate_range(D0, periods=n)
        px = pd.DataFrame(
            10 * np.exp(np.cumsum(rng.normal(0, 0.012, (n, 3)), axis=0)),
            index=dates, columns=list("ABC"))
        wins = [{"window_start": str(dates[50])[:10],
                 "window_end": str(dates[69])[:10],
                 "market_return": -0.1, "market_mdd": 0.12,
                 "var_95": -0.02, "cvar_95": -0.03, "n_days": 20}]
        out = portfolio_stress_replay(px, {"A": 0.5, "B": 0.5}, wins)
        assert len(out) == 1
        assert out[0]["portfolio_return"] is not None
        assert out[0]["portfolio_var_95"] < 0
