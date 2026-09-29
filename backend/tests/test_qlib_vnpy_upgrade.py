"""Qlib / vnpy 式升级模块测试。

覆盖：
- ml/alpha_expr.py：表达式求值 / 安全解析（拒绝注入与未来引用）/ Alpha158-lite PIT 验收
- ml/signal_analysis.py：IC 衰减 / 分层多空
- backtest/engine.py：TopkDropout（dropout_n=0 等价朴素 Top-K；>0 降换手）
- backtest/strategy_base.py：事件引擎 / 三策略端到端 / 与 ma_cross 引擎口径一致
- backtest/param_search.py：网格搜索 / 遗传算法 / DSR 随试验次数惩罚
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

from app.ml import alpha_expr as ae  # noqa: E402
from app.ml import signal_analysis as sa  # noqa: E402

D0 = date(2024, 1, 2)


# ==================== alpha_expr ====================
def _ohlcv(n=120, seed=5) -> pd.DataFrame:
    rng = np.random.default_rng(seed)
    close = 10 * np.exp(np.cumsum(rng.normal(0, 0.02, n)))
    o = close * (1 + rng.normal(0, 0.005, n))
    h = np.maximum(close, o) * (1 + np.abs(rng.normal(0, 0.005, n)))
    l = np.minimum(close, o) * (1 - np.abs(rng.normal(0, 0.005, n)))
    return pd.DataFrame({"open": o, "high": h, "low": l, "close": close,
                         "volume": rng.uniform(1e6, 5e6, n),
                         "amount": close * rng.uniform(1e6, 5e6, n)})


class TestAlphaExpr:
    def test_eval_matches_manual_pandas(self):
        df = _ohlcv()
        got = ae.eval_expr("Mean(close, 5)", df)
        want = df["close"].rolling(5).mean()
        assert np.allclose(got.to_numpy(), want.to_numpy(), equal_nan=True)
        got = ae.eval_expr("close / Ref(close, 5) - 1", df)
        want = df["close"] / df["close"].shift(5) - 1
        assert np.allclose(got.to_numpy(), want.to_numpy(), equal_nan=True)

    def test_corr_and_delta(self):
        df = _ohlcv()
        got = ae.eval_expr("Corr(close, Log(volume + 1), 10)", df)
        want = df["close"].rolling(10).corr(np.log(df["volume"] + 1))
        assert np.allclose(got.to_numpy(), want.to_numpy(), equal_nan=True)
        got = ae.eval_expr("Delta(close, 3)", df)
        assert np.allclose(got.to_numpy(),
                           (df["close"] - df["close"].shift(3)).to_numpy(),
                           equal_nan=True)

    def test_negative_ref_forbidden(self):
        df = _ohlcv(10)
        with pytest.raises(ValueError):
            ae.eval_expr("Ref(close, -1)", df)

    def test_security_rejects_injection(self):
        df = _ohlcv(10)
        for bad in ("__import__('os').system('ls')", "close.__class__",
                    "close[0]", "open('/etc/passwd')", "lambda x: x",
                    "f(close)", "UnknownField + 1"):
            with pytest.raises(ValueError):
                ae.eval_expr(bad, df)

    def test_if_greater_comparison(self):
        df = _ohlcv(30)
        got = ae.eval_expr("If(close > Ref(close, 1), 1, 0)", df)
        up = (df["close"] > df["close"].shift(1)).astype(float)
        assert np.allclose(got.to_numpy(), up.to_numpy(), equal_nan=True)

    def test_build_alpha158_lite_pit_safe(self):
        from app.ml.labeling import future_contamination_check
        frames = []
        for sym in ("AAA", "BBB"):
            g = _ohlcv(150, seed=hash(sym) % 1000)
            g["symbol"] = sym
            g["date"] = [D0 + timedelta(days=i) for i in range(len(g))]
            frames.append(g)
        raw = pd.concat(frames, ignore_index=True)
        built = ae.build_alpha158_lite(raw)
        assert built.shape[0] == raw.shape[0]
        factor_cols = [c for c in built.columns
                       if c not in ("symbol", "date")]
        ok, bad = future_contamination_check(
            lambda df: ae.build_alpha158_lite(df), raw,
            cutoff_date=pd.Timestamp(raw["date"].max()) - pd.Timedelta(days=20),
            feature_cols=factor_cols)
        assert ok, f"PIT 校验失败列: {bad[:5]}"

    def test_div_by_zero_becomes_nan(self):
        df = _ohlcv(10)
        df["volume"] = 0.0
        got = ae.eval_expr("close / volume", df)
        assert got.isna().all()


# ==================== signal_analysis ====================
class TestSignalAnalysis:
    def _make(self, n_days=200, n_sym=20, seed=3):
        rng = np.random.default_rng(seed)
        days = [D0 + timedelta(days=i) for i in range(n_days)]
        syms = [f"S{i:02d}" for i in range(n_sym)]
        close = pd.DataFrame(
            10 * np.exp(np.cumsum(rng.normal(0, 0.02, (n_days, n_sym)), axis=0)),
            index=days, columns=syms)
        # 信号 = 未来 5 日收益的带噪版本（应产生显著正 IC）
        fwd5 = close.shift(-5) / close - 1
        signal = (fwd5 + rng.normal(0, 0.02, fwd5.shape)).stack().rename("pred_score")
        signal = signal.reset_index().rename(columns={"level_0": "date",
                                                      "level_1": "symbol"})
        close_long = close.stack().rename("close").reset_index() \
            .rename(columns={"level_0": "date", "level_1": "symbol"})
        return signal, close_long

    def test_ic_decay_detects_signal(self):
        sig, close = self._make()
        rep = sa.ic_decay_report(sig, close, horizons=(1, 5, 10))
        assert len(rep) == 3
        row5 = rep[rep["horizon"] == 5].iloc[0]
        assert row5["mean_ic"] > 0.2        # 构造信号应有强 IC
        assert row5["t_stat"] > 5
        assert row5["positive_ratio"] > 0.8

    def test_quantile_spread_monotonic(self):
        sig, close = self._make()
        qs = sa.quantile_spread_report(sig, close, horizon=5, n_quantiles=5)
        assert qs["ls_mean_daily"] > 0      # 高分组跑赢低分组
        assert qs["monotonic"]
        assert qs["ls_t_stat"] > 3


# ==================== TopkDropout ====================
class TestTopkDropout:
    def _universe_sig(self, n_days=60, n_sym=10):
        rng = np.random.default_rng(17)
        rows, sigs = [], []
        for i in range(n_sym):
            px = 10.0 + i
            for j in range(n_days):
                d = D0 + timedelta(days=j)
                px = max(px * (1 + rng.normal(0.0005, 0.015)), 1.0)
                rows.append({"date": d, "symbol": f"S{i:02d}", "open": px,
                             "close": px, "volume": 1e6, "amount": px * 1e6,
                             "limit_up": px * 1.2, "limit_down": px * 0.8,
                             "is_halted": False})
                sigs.append({"date": d, "symbol": f"S{i:02d}",
                             "pred_score": rng.normal() + i * 0.001})
        return pd.DataFrame(rows), pd.DataFrame(sigs)

    def test_dropout_zero_equals_plain_topk(self):
        from app.backtest.engine import run_backtest
        uni, sig = self._universe_sig()
        a = run_backtest(uni, sig, top_k=3, dropout_n=0)
        b = run_backtest(uni, sig, top_k=3)      # 默认 dropout_n=0
        assert np.allclose(a.nav_df["nav"], b.nav_df["nav"])

    def test_dropout_reduces_turnover(self):
        from app.backtest.engine import run_backtest
        uni, sig = self._universe_sig(120, 15)
        plain = run_backtest(uni, sig, top_k=3, dropout_n=0)
        drop = run_backtest(uni, sig, top_k=3, dropout_n=2)
        assert drop.nav_df["turnover"].mean() <= plain.nav_df["turnover"].mean()

    def test_target_symbols_dropout_keeps_holdings(self):
        from app.backtest.engine import _target_symbols
        sig = pd.DataFrame({"symbol": list("ABCDE"),
                            "pred_score": [5.0, 4.0, 3.0, 2.0, 1.0]})
        # 持仓 D 排名第 4，top_k=3 + dropout=2 -> 保留
        got = _target_symbols(sig, top_k=3, current_holdings={"D"}, dropout_n=2)
        assert "D" in got and len(got) == 3
        # dropout=0 -> D 被换掉
        got0 = _target_symbols(sig, top_k=3, current_holdings={"D"}, dropout_n=0)
        assert "D" not in got0


# ==================== strategy_base（vnpy 式） ====================
def _bars(n_days=150, n_sym=3, seed=31) -> dict[str, pd.DataFrame]:
    rng = np.random.default_rng(seed)
    out = {}
    days = [D0 + timedelta(days=i) for i in range(n_days)]
    for i in range(n_sym):
        # 趋势 + 均值回归混合，让均线/突破/RSI 都有行情可交易
        trend = np.sin(np.arange(n_days) / 25.0 + i) * 0.004
        rets = trend + rng.normal(0, 0.015, n_days)
        close = 10 * np.exp(np.cumsum(rets))
        o = close * (1 + rng.normal(0, 0.003, n_days))
        out[f"S{i}"] = pd.DataFrame({"date": days, "open": o, "close": close})
    return out


class TestStrategyFramework:
    def test_event_engine_dispatch(self):
        from app.backtest.strategy_base import Event, EventEngine
        eng = EventEngine()
        seen: list[int] = []
        eng.register("bar", lambda ev: seen.append(ev.data["x"]))
        eng.register("bar", lambda ev: 1 / 0)   # 单 handler 失败不中断
        eng.put(Event("bar", {"x": 1}))
        eng.put(Event("bar", {"x": 2}))
        assert seen == [1, 2]

    def test_all_strategies_run(self):
        from app.backtest.strategy_base import (
            DonchianBreakoutStrategy,
            MaCrossStrategy,
            RsiMeanReversionStrategy,
            run_strategy,
        )
        bars = _bars()
        for strat in (MaCrossStrategy(short_ma=5, long_ma=20),
                      DonchianBreakoutStrategy(entry_window=20, exit_window=10),
                      RsiMeanReversionStrategy(rsi_period=14)):
            res = run_strategy(bars, strat, init_cash=1_000_000)
            assert len(res.nav_df) == 150
            assert res.risk["sharpe"] == res.risk["sharpe"]      # 非 NaN
            assert "deflated_sharpe" in res.risk
            assert res.monthly, "应有月度收益"

    def test_t1_no_lookahead(self):
        """信号 T 日产生 -> 至少 T+1 才有成交（首日不得出现交易）。"""
        from app.backtest.strategy_base import MaCrossStrategy, run_strategy
        bars = _bars()
        res = run_strategy(bars, MaCrossStrategy(short_ma=3, long_ma=10))
        if res.trades:
            day_idx = {d: i for i, d in enumerate(res.nav_df["date"])}
            first = min(day_idx[t["date"]] for t in res.trades if t["qty"] > 0)
            assert first >= 1

    def test_registry_lookup(self):
        from app.backtest.strategy_base import STRATEGIES
        assert set(STRATEGIES) == {"ma_cross", "donchian", "rsi_reversion"}


# ==================== param_search ====================
class TestParamSearch:
    def test_grid_search_finds_optimum(self):
        from app.backtest.param_search import grid_search
        rng = np.random.default_rng(2)
        nav = np.cumprod(1 + rng.normal(0.0008, 0.01, 300))   # 有波动的净值
        def evaluate(p):
            x = float(p["x"])
            return {"sharpe": -(x - 3.0) ** 2, "nav": nav}
        res = grid_search(evaluate, {"x": [0.0, 1.0, 2.0, 2.9, 3.0, 3.1, 4.0]})
        assert res.best_params["x"] == pytest.approx(3.0)
        assert res.method == "grid"
        assert len(res.trials) == 7
        assert np.isfinite(res.deflated_sharpe)

    def test_grid_over_limit_rejected(self):
        from app.backtest.param_search import grid_search
        with pytest.raises(ValueError):
            grid_search(lambda p: {"sharpe": 0.0}, {f"p{i}": [1, 2] for i in range(10)},
                        max_trials=100)

    def test_genetic_search_optimizes(self):
        from app.backtest.param_search import genetic_search
        def evaluate(p):
            x = float(p["x"])
            return {"sharpe": -(x - 7.0) ** 2, "nav": None}
        res = genetic_search(evaluate, {"x": (0.0, 15.0)},
                             population=15, generations=12, seed=1)
        assert abs(res.best_params["x"] - 7.0) < 1.5
        assert res.best_params["x"] == int(res.best_params["x"])  # 整型解码

    def test_dsr_penalizes_more_trials(self):
        """同一最优净值，试验次数越多 DSR 越低（多重试验惩罚生效）。"""
        from app.backtest.param_search import grid_search
        rng = np.random.default_rng(0)
        nav = np.cumprod(1 + rng.normal(0.0008, 0.01, 500))
        def evaluate(p):
            return {"sharpe": float(p["k"]), "nav": nav}
        small = grid_search(evaluate, {"k": [1.0]}, )
        big = grid_search(evaluate, {"k": [1.0, 0.5, 0.2, 0.1, 2.0, 3.0, 4.0, 5.0]})
        assert small.deflated_sharpe >= big.deflated_sharpe

    def test_run_search_dispatch(self):
        from app.backtest.param_search import run_search
        res = run_search(lambda p: {"sharpe": -float(p["x"]) ** 2},
                         {"x": [0.0, 1.0, 2.0]}, method="grid")
        assert res.best_params["x"] == 0.0
        with pytest.raises(ValueError):
            run_search(lambda p: {"sharpe": 0.0}, {"x": [0.0]}, method="bad")
