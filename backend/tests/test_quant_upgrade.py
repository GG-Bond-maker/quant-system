"""机构级回测优化模块测试。

覆盖：
- domain/factor_processing.py：MAD 去极值 / 截面标准化 / Rank 变换 / 对称正交化
- domain/optimizer.py：Ledoit-Wolf / RMT 去噪 / 风险平摊 / 最大分散度 / MVO / 权重上限
- domain/metrics.py：PSR / Deflated Sharpe
- backtest/broker.py：平方根冲击 / 参与率上限
- backtest/engine.py：risk_parity 等权重方案端到端
- domain/portfolio.py：weighting=risk_parity 端到端
- ml/purged_cv.py：Purge+Embargo 无重叠验证 / RankIC 报告 / 波动率倒数加权
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

from app.domain import factor_processing as fp  # noqa: E402
from app.domain import optimizer as opt  # noqa: E402
from app.domain.metrics import (  # noqa: E402
    all_metrics,
    deflated_sharpe_ratio,
    probabilistic_sharpe_ratio,
)
from app.ml.purged_cv import (  # noqa: E402
    PurgedGroupTimeSeriesSplit,
    verify_no_overlap,
    volatility_inverse_weights_from_close,
)

D0 = date(2024, 1, 2)


# ==================== factor_processing ====================
class TestFactorProcessing:
    def test_mad_winsorize_clips_outliers(self):
        x = np.array([1.0, 2.0, 3.0, 4.0, 5.0, 100.0])
        out = fp.mad_winsorize(x, n_mad=3.0)
        assert out.max() < 100.0
        assert out[0] == 1.0          # 正常值不动
        assert np.isnan(fp.mad_winsorize(np.array([np.nan, 1.0]))[0])

    def test_mad_constant_col_noop(self):
        x = np.array([5.0, 5.0, 5.0])
        assert np.allclose(fp.mad_winsorize(x), x)

    def test_zscore_zero_mean_unit_var(self):
        x = np.array([1.0, 2.0, 3.0, 4.0, 5.0])
        z = fp.cross_sectional_zscore(x)
        assert abs(z.mean()) < 1e-12
        assert abs(z.std(ddof=1) - 1.0) < 1e-12

    def test_rank_transform_range(self):
        x = np.array([10.0, 20.0, 30.0, 40.0, np.nan])
        r = fp.rank_transform(x)
        assert np.isnan(r[-1])
        finite = r[np.isfinite(r)]
        assert finite.min() >= -0.5 and finite.max() <= 0.5
        assert np.all(np.diff(finite) > 0)     # 单调性保留

    def test_scaler_per_date_independent(self):
        df = pd.DataFrame({
            "date": ["2024-01-01"] * 3 + ["2024-01-02"] * 3,
            "symbol": list("ABC") * 2,
            "f": [1.0, 2.0, 3.0, 100.0, 200.0, 300.0],
        })
        out = fp.CrossSectionalScaler().transform(df, feature_cols=["f"])
        g1 = out[out["date"] == "2024-01-01"]["f"]
        assert abs(g1.mean()) < 1e-12       # 每个截面均值 0

    def test_symmetric_orthogonalize(self):
        rng = np.random.default_rng(7)
        F = rng.normal(size=(200, 5)) @ np.diag([3, 2, 1, 0.5, 0.1])
        G = fp.symmetric_orthogonalize(F)
        # 列正交单位化
        assert np.allclose(G.T @ G, np.eye(5), atol=1e-10)
        # 列空间投影不变（信息子空间不丢）
        assert np.allclose(G @ np.linalg.pinv(G), F @ np.linalg.pinv(F), atol=1e-8)

    def test_orthogonalize_panel(self):
        rng = np.random.default_rng(3)
        rows = []
        for d in ("2024-01-01", "2024-01-02"):
            for i in range(30):
                rows.append({"date": d, "symbol": f"S{i:03d}",
                             "f1": rng.normal(), "f2": rng.normal()})
        df = pd.DataFrame(rows)
        df["f2"] = df["f1"] * 0.9 + df["f2"] * 0.1    # 高共线
        out = fp.orthogonalize_factor_panel(df, ["f1", "f2"])
        for _, g in out.groupby("date"):
            inner = g[["f1", "f2"]].dropna()
            corr = np.corrcoef(inner["f1"], inner["f2"])[0, 1]
            assert abs(corr) < 1e-8


# ==================== optimizer ====================
def _random_returns(n_days=300, n_assets=8, seed=42) -> np.ndarray:
    rng = np.random.default_rng(seed)
    return rng.normal(0.0005, 0.015, size=(n_days, n_assets))


class TestOptimizer:
    def test_ledoit_wolf_delta_in_range(self):
        R = _random_returns()
        cov, delta = opt.ledoit_wolf_cov(R)
        assert 0.0 <= delta <= 1.0
        assert cov.shape == (8, 8)
        assert np.allclose(cov, cov.T)

    def test_rmt_denoise_keeps_trace(self):
        R = _random_returns(n_days=100, n_assets=30)   # T/N 比小 -> 有噪声带
        S = opt.sample_cov(R)
        dn = opt.rmt_denoise(S, n_obs=100)
        assert abs(np.trace(S) - np.trace(dn)) / np.trace(S) < 1e-6

    def test_risk_parity_equal_contributions(self):
        R = _random_returns(seed=1)
        cov = opt.robust_cov(R)
        w = opt.risk_parity_weights(cov)
        rc = opt.risk_contributions(w, cov)
        assert w.min() > 0
        assert abs(w.sum() - 1.0) < 1e-9
        assert np.allclose(rc, 1.0 / len(w), atol=1e-6)

    def test_max_div_weights_valid(self):
        R = _random_returns(seed=2)
        cov = opt.robust_cov(R)
        w = opt.max_diversification_weights(cov)
        assert w.min() >= 0 and abs(w.sum() - 1.0) < 1e-9
        dr = opt.diversification_ratio(w, cov)
        we = np.full(len(w), 1.0 / len(w))
        # 最大分散度 DR 不低于等权（多数情况严格更高）
        assert dr >= opt.diversification_ratio(we, cov) - 1e-6

    def test_mvo_with_cap_and_turnover(self):
        R = _random_returns(seed=3)
        cov = opt.robust_cov(R)
        mu = R.mean(axis=0) * 252
        prev = np.full(8, 1.0 / 8)
        w = opt.mean_variance_weights(mu, cov, weight_cap=0.2,
                                      prev_weights=prev, turnover_penalty=5.0)
        assert w.min() >= 0 and abs(w.sum() - 1.0) < 1e-6
        assert w.max() <= 0.2 + 1e-9

    def test_apply_weight_cap(self):
        w = np.array([0.7, 0.2, 0.1])
        out = opt.apply_weight_cap(w, cap=0.4)
        assert out.max() <= 0.4 + 1e-12
        assert abs(out.sum() - 1.0) < 1e-9

    def test_compute_weights_dispatch(self):
        R = _random_returns(seed=4)
        for m in ("risk_parity", "max_div", "inverse_vol", "equal"):
            w = opt.compute_weights_from_returns(R, method=m)
            assert abs(w.sum() - 1.0) < 1e-6
        with pytest.raises(ValueError):
            opt.compute_weights_from_returns(R, method="nope")


# ==================== metrics: PSR / DSR ====================
def _nav_from_sharpe(sharpe: float, n: int = 500, seed: int = 0) -> np.ndarray:
    """构造指定日频 Sharpe 的净值序列（正态近似）。"""
    rng = np.random.default_rng(seed)
    r = rng.normal(sharpe / np.sqrt(252), 0.01, n)
    return np.cumprod(1.0 + r)


class TestDeflatedSharpe:
    def test_psr_high_sharpe_near_one(self):
        nav = _nav_from_sharpe(2.0)
        psr = probabilistic_sharpe_ratio(nav)
        assert psr > 0.95

    def test_psr_low_sharpe_near_zero(self):
        nav = _nav_from_sharpe(-1.0)
        assert probabilistic_sharpe_ratio(nav) < 0.05

    def test_dsr_penalizes_trials(self):
        nav = _nav_from_sharpe(1.0, seed=5)
        dsr1 = deflated_sharpe_ratio(nav, n_trials=1)
        dsr1000 = deflated_sharpe_ratio(nav, n_trials=1000)
        assert dsr1 >= dsr1000          # 试验次数越多惩罚越重
        assert dsr1000 < dsr1 + 1e-9

    def test_all_metrics_has_new_keys(self):
        m = all_metrics(_nav_from_sharpe(1.5), n_trials=10)
        assert "probabilistic_sharpe" in m and "deflated_sharpe" in m
        assert 0.0 <= m["deflated_sharpe"] <= 1.0


# ==================== broker: sqrt impact / participation cap ====================
def _mk_row(**kw: object) -> pd.Series:
    base: dict[str, object] = {
        "open": 10.0, "close": 10.0, "volume": 1_000_000.0,
        "amount": 10_000_000.0, "limit_up": 11.0, "limit_down": 9.0,
        "is_halted": False,
    }
    base.update(kw)
    return pd.Series(base)


class TestBrokerFrictionUpgrade:
    def _broker(self, **cfg) -> object:
        from app.backtest.broker import Broker, BrokerConfig
        return Broker(init_cash=1_000_000,
                      config=BrokerConfig(enabled=True, **cfg))

    def test_sqrt_impact_scales_with_participation(self):
        from app.backtest.broker import BrokerConfig
        b = self._broker(impact_model="sqrt", impact_sqrt_coef_bps=50.0)
        small = b._impact_cost(100_000.0, 10_000_000.0)   # 1%
        big = b._impact_cost(2_000_000.0, 10_000_000.0)   # 20%
        assert big > small > 0
        # 平方根口径：cost/amount ∝ sqrt(participation)
        r1 = small / 100_000.0
        r2 = big / 2_000_000.0
        assert abs(r2 / r1 - (0.20 / 0.01) ** 0.5) < 0.2
        with pytest.raises(ValueError):
            BrokerConfig(impact_model="invalid")

    def test_participation_cap_blocks_buy(self):
        b = self._broker(max_participation=0.05)
        # 预算 1,000,000 > 日成交额 10M × 5% = 500,000 -> 被截断到 50 万
        t = b.buy(D0, "600000.SH", cash_amount=1_000_000, row=_mk_row())
        assert t.reason == "filled"
        assert t.amount <= 500_000 * 1.01   # 截断生效（允许滑点）

    def test_participation_cap_partial_sell(self):
        b = self._broker(max_participation=0.05)
        # 先手动建仓 10000 股并解冻
        b.holdings["600000.SH"] = 10_000
        # 上限 50 万元 / 10 元 = 50,000 股 > 10,000 股 -> 不受限
        t = b.sell(D0, "600000.SH", qty=10_000, row=_mk_row())
        assert t.reason == "filled" and t.qty == 10_000

        # 更严格上限：0.5% -> 50,000 元 = 5,000 股 部分成交
        b2 = self._broker(max_participation=0.005)
        b2.holdings["600000.SH"] = 10_000
        t2 = b2.sell(D0, "600000.SH", qty=10_000, row=_mk_row())
        assert t2.reason == "filled" and t2.qty == 5_000   # 部分成交

    def test_no_friction_unchanged(self):
        from app.backtest.broker import Broker
        b = Broker(init_cash=100_000)   # config=None
        t = b.buy(D0, "600000.SH", cash_amount=10_000, row=_mk_row())
        assert t.reason == "filled" and t.price == 10.0


# ==================== engine: weighting 端到端 ====================
class TestEngineWeighting:
    def _make_universe(self, n_days=80, n_sym=6, seed=11) -> pd.DataFrame:
        rng = np.random.default_rng(seed)
        rows = []
        for i in range(n_sym):
            px = 10.0 + i
            for j in range(n_days):
                d = D0 + timedelta(days=j)
                ret = rng.normal(0.0008, 0.02)
                px = max(px * (1 + ret), 1.0)
                rows.append({"date": d, "symbol": f"S{i:03d}", "open": px,
                             "close": px, "volume": 1e6, "amount": px * 1e6,
                             "limit_up": px * 1.2, "limit_down": px * 0.8,
                             "is_halted": False})
        return pd.DataFrame(rows)

    def _signal(self, n_days, n_sym) -> pd.DataFrame:
        rng = np.random.default_rng(99)
        rows = []
        for j in range(n_days):
            d = D0 + timedelta(days=j)
            for i in range(n_sym):
                rows.append({"date": d, "symbol": f"S{i:03d}",
                             "pred_score": rng.normal()})
        return pd.DataFrame(rows)

    def test_risk_parity_runs_and_fills(self):
        from app.backtest.engine import run_backtest
        uni = self._make_universe()
        sig = self._signal(80, 6)
        res = run_backtest(uni, sig, top_k=3, weighting="risk_parity",
                           cov_window=40, signal_lag=1)
        assert len(res.nav_df) == 80
        assert res.metrics["sharpe"] == res.metrics["sharpe"]  # 非 NaN
        # 有成交发生（风险平摊不应导致空转）
        assert any(t["reason"] == "filled" for t in res.trades)

    def test_equal_weight_regression(self):
        from app.backtest.engine import run_backtest
        uni = self._make_universe()
        sig = self._signal(80, 6)
        res_eq = run_backtest(uni, sig, top_k=3, weighting="equal")
        res_rp = run_backtest(uni, sig, top_k=3, weighting="risk_parity")
        # 等权与风险平摊应产生不同净值路径（权重确实生效）
        assert not np.allclose(res_eq.nav_df["nav"], res_rp.nav_df["nav"])

    def test_invalid_weighting_rejected(self):
        from app.backtest.engine import run_backtest
        with pytest.raises(ValueError):
            run_backtest(self._make_universe(), self._signal(5, 6),
                         weighting="magic")


# ==================== portfolio: weighting=risk_parity 端到端 ====================
class TestPortfolioWeighting:
    def test_risk_parity_portfolio(self, monkeypatch):
        from app.domain import portfolio as pm
        dates = pd.bdate_range("2023-01-02", "2023-06-30")
        rng = np.random.default_rng(7)
        frames = []
        for i, code in enumerate(("AAA", "BBB", "CCC")):
            rets = rng.normal(0.0006, 0.008 + 0.004 * i, len(dates))
            frames.append(pd.Series(10 * np.exp(np.cumsum(rets)),
                                    index=dates.date, name=code))
        prices = pd.concat(frames, axis=1)
        bm = pd.Series(100 * np.exp(np.cumsum(rng.normal(0.0004, 0.006, len(dates)))),
                       index=dates.date)

        def fake_asset(code, typ, start, end):
            return prices[code]

        def fake_bm(code, start, end):
            return bm

        res = pm.run_portfolio_backtest(
            assets=[{"code": c, "type": "stock", "weight": 1 / 3}
                    for c in ("AAA", "BBB", "CCC")],
            start_date="2023-01-02", end_date="2023-06-30",
            rebalance="M", weighting="risk_parity", cov_window=40,
            price_loader=fake_asset, benchmark_loader=fake_bm,
        )
        assert res["weighting"] == "risk_parity"
        assert res["rebalance_log"], "应有调仓日志"
        assert res["rebalance_log"][0]["weighting"] == "risk_parity"
        w = res["rebalance_log"][0]["weights"]
        assert abs(sum(w.values()) - 1.0) < 1e-3
        assert "deflated_sharpe" in res["metrics"]

    def test_user_weighting_backward_compat(self, monkeypatch):
        from app.domain import portfolio as pm
        dates = pd.bdate_range("2023-01-02", "2023-03-31")
        rng = np.random.default_rng(8)
        p1 = pd.Series(10 * np.exp(np.cumsum(rng.normal(0.0005, 0.01, len(dates)))),
                       index=dates.date, name="X1")
        bm = pd.Series(100 + np.arange(len(dates), dtype=float), index=dates.date)
        res = pm.run_portfolio_backtest(
            assets=[{"code": "X1", "type": "etf", "weight": 1.0}],
            start_date="2023-01-02", end_date="2023-03-31",
            rebalance="none", weighting="user",
            price_loader=lambda *a: p1, benchmark_loader=lambda *a: bm,
        )
        assert res["rebalance_log"] == []     # user 模式不产生求解日志
        assert res["metrics"]["sharpe"] == res["metrics"]["sharpe"]


# ==================== purged_cv ====================
class TestPurgedCV:
    def _groups(self, n_dates=120, per_day=5) -> np.ndarray:
        days = [D0 + timedelta(days=i) for i in range(n_dates)]
        return np.array([d for d in days for _ in range(per_day)], dtype=object)

    def test_purge_embargo_gap(self):
        groups = self._groups()
        cv = PurgedGroupTimeSeriesSplit(n_splits=3, purge_window=5, embargo_window=2)
        for tr, te in cv.split(groups=groups):
            assert verify_no_overlap(tr, te, groups, horizon=7)   # 5+2 隔离带
            assert tr.max() < te.min()                            # 时序不倒置

    def test_insufficient_dates_raises(self):
        with pytest.raises(ValueError):
            list(PurgedGroupTimeSeriesSplit(n_splits=5).split(
                groups=np.array([D0] * 10, dtype=object)))

    def test_cv_rank_ic_report(self):
        from app.ml.purged_cv import cv_rank_ic_report
        rng = np.random.default_rng(13)
        rows = []
        days = [D0 + timedelta(days=i) for i in range(120)]
        for d in days:
            for i in range(10):
                x = rng.normal()
                rows.append({"date": d, "symbol": f"S{i:02d}", "f1": x,
                             "label_ret": 0.3 * x + rng.normal() * 0.5})
        df = pd.DataFrame(rows)
        rep = cv_rank_ic_report(
            df, ["f1"], "label_ret",
            # 非常数预测（直接用特征），保证 Spearman 相关可估
            fit_predict_fn=lambda Xtr, ytr, Xte: Xte["f1"].to_numpy(),
            n_splits=3, purge_window=5, embargo_window=2,
        )
        assert rep["n_folds"] == 3
        assert rep["min_gap_ok"]

    def test_volatility_inverse_weights(self):
        rng = np.random.default_rng(21)
        calm = 10 * np.exp(np.cumsum(rng.normal(0, 0.003, 60)))
        wild = 10 * np.exp(np.cumsum(rng.normal(0, 0.05, 60)))
        days = [D0 + timedelta(days=i) for i in range(60)]
        rows = []
        for j, d in enumerate(days):
            rows.append({"date": d, "symbol": "CALM", "close": calm[j]})
            rows.append({"date": d, "symbol": "WILD", "close": wild[j]})
        df = pd.DataFrame(rows)
        out = volatility_inverse_weights_from_close(df)
        w_calm = out[out["symbol"] == "CALM"]["sample_weight"].iloc[-1]
        w_wild = out[out["symbol"] == "WILD"]["sample_weight"].iloc[-1]
        assert w_calm > w_wild        # 低波动股票权重更高
