"""生产端模块测试：GP 挖掘引擎 / Brinson 归因 / 风格回归 / 容量 / 模拟盘全链路。"""
from __future__ import annotations

import sys
from datetime import date, timedelta
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

BACKEND_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(BACKEND_ROOT))


# ==================== GP 挖掘引擎 ====================
def _features_pdf(n_days=260, n_sym=25, seed=9) -> pd.DataFrame:
    rng = np.random.default_rng(seed)
    dates = pd.bdate_range("2024-01-02", periods=n_days)
    rows = []
    for i in range(n_sym):
        close = 10 * np.exp(np.cumsum(rng.normal(0, 0.015, n_days)))
        ret = np.concatenate([[0.0], close[1:] / close[:-1] - 1])
        for j, d in enumerate(dates):
            rows.append({"symbol": f"S{i:02d}", "date": d,
                         "close": close[j], "ret_5": ret[j],
                         "vol_20": pd.Series(ret[:j + 1]).tail(20).std()})
    return pd.DataFrame(rows)


class TestGpMiner:
    def test_generate_and_evaluate(self):
        from app.ml.gp_miner import _gen_expr
        rng = __import__("random").Random(1)
        fields = ["ret_5", "vol_20"]
        pdf = _features_pdf()
        # 随机生成 5 条表达式全部可求值/淘汰（不抛异常）
        for _ in range(5):
            expr = _gen_expr(rng, fields, 2)
            from app.ml.alpha_expr import eval_expr
            g = pdf[pdf["symbol"] == "S00"].reset_index(drop=True)
            eval_expr(expr, g, extra_fields=set(fields))   # 不应抛出

    def test_full_evolution_produces_best(self):
        from app.ml.gp_miner import GpTaskState, _run_task
        pdf = _features_pdf()
        task = GpTaskState(task_id="t1",
                           params={"fields": ["ret_5", "vol_20"],
                                   "population": 10, "generations": 3,
                                   "horizon": 5, "seed": 7})
        _run_task(task, pdf)
        assert task.status == "DONE"
        assert task.generation == 3
        assert len(task.fitness_curve) == 3
        assert task.best is not None
        assert "expr" in task.best and "mean_ic" in task.best
        # fitness 非全 -1（真实数据上应有可评估表达式）
        assert max(task.fitness_curve) > -1.0

    def test_task_registry_lifecycle(self):
        from app.ml import gp_miner
        pdf = _features_pdf(n_days=120, n_sym=8)
        tid = gp_miner.start_task(
            {"fields": ["ret_5"], "population": 6, "generations": 2,
             "horizon": 5, "seed": 3}, pdf)
        st = gp_miner.get_task(tid)
        assert st is not None and st.status in ("PENDING", "RUNNING", "DONE")


# ==================== 归因与容量 ====================
class TestAttribution:
    def test_brinson_adds_up(self):
        from app.domain.attribution import brindon_attribution
        rng = np.random.default_rng(2)
        syms = [f"S{i}" for i in range(6)]
        industry = pd.Series(["银行", "银行", "医药", "医药", "科技", "科技"],
                             index=syms)
        rets = pd.Series(rng.normal(0.02, 0.05, 6), index=syms)
        port_w = {s: 1 / 6 for s in syms}
        bench_w = {syms[0]: 0.4, syms[2]: 0.3, syms[4]: 0.3}
        out = brindon_attribution(port_w, bench_w, rets, industry)
        s = out["summary"]
        # 恒等式：excess = allocation + selection + interaction + residual
        total = s["allocation"] + s["selection"] + s["interaction"] + s["residual_alpha"]
        assert abs(total - s["excess_return"]) < 1e-4
        assert len(out["sectors"]) == 3

    def test_style_regression_recovers_beta(self):
        from app.domain.attribution import style_regression_attribution
        rng = np.random.default_rng(4)
        n = 300
        f = pd.Series(rng.normal(0, 0.01, n),
                      index=pd.bdate_range("2024-01-02", periods=n))
        port = 0.8 * f + rng.normal(0.0002, 0.002, n)
        out = style_regression_attribution(pd.Series(port, index=f.index),
                                           {"Momentum": f})
        assert abs(out["betas"]["Momentum"] - 0.8) < 0.1
        assert out["n_days"] == n

    def test_capacity_formula(self):
        from app.domain.attribution import strategy_capacity
        out = strategy_capacity(adv_median=1e8, participation_cap=0.01,
                                holdings=20, rebalance_per_year=12)
        assert out["aum_threshold"] is not None
        assert out["aum_yi"] > 0


# ==================== 模拟盘引擎（隔离 sqlite） ====================
@pytest.fixture
def paper_env(tmp_path, monkeypatch):
    """独立 sqlite 环境（env 键与 conftest 约定一致，不碰真实库）。"""
    monkeypatch.setenv("DATA_ROOT", str(tmp_path / "data"))
    monkeypatch.setenv("SQLITE_URL",
                       f"sqlite+aiosqlite:///{(tmp_path / 'test.db').as_posix()}")
    monkeypatch.setenv("MODEL_ROOT", str(tmp_path / "models"))
    from app.core.config import get_settings
    get_settings.cache_clear()
    from app.trading import paper as paper_mod
    monkeypatch.setattr(paper_mod, "_SYNC_ENGINE", None)   # 隔离引擎缓存
    import app.db.models  # noqa: F401  确保 ORM 注册进 metadata
    from app.db.session import Base
    import sqlalchemy as sa
    engine = sa.create_engine(f"sqlite:///{tmp_path / 'test.db'}")
    Base.metadata.create_all(engine)
    # 伪造真实日行情（parquet_store 契约：DATA_ROOT/daily_bar/symbol=X/year=Y.parquet）
    import polars as pl
    rng = np.random.default_rng(8)
    # 行情覆盖"今天 ±"，保证 D0+1（次日开盘撮合）在本地数据内
    _anchor = date.today() - timedelta(days=7)
    days = [_anchor + timedelta(days=i) for i in range(20)
            if (_anchor + timedelta(days=i)).weekday() < 5]
    px = 10 * np.exp(np.cumsum(rng.normal(0, 0.01, len(days))))
    df = pl.DataFrame({"date": days, "open": px * 0.999, "high": px * 1.01,
                       "low": px * 0.99, "close": px,
                       "volume": [1e7] * len(days), "amount": px * 1e7})
    sym_dir = tmp_path / "data" / "daily_bar" / "symbol=000001.SZ"
    sym_dir.mkdir(parents=True)
    df.write_parquet(sym_dir / "year=2026.snappy.parquet")
    get_settings.cache_clear()
    yield tmp_path


class TestPaperDesk:
    def test_order_rejected_by_kill_switch(self, paper_env):
        from app.trading import paper
        Session = paper.sync_session_factory()
        with Session() as s:
            paper.set_kill_switch(s, True)
            s.commit()
            res = paper.place_order(s, symbol="000001.SZ", side="buy",
                                    order_amount=100_000, algo="market",
                                    split_days=1, participation_cap=0.05)
            assert not res["ok"] and "kill_switch" in res["reason"]

    def test_order_rejected_by_exclusion(self, paper_env):
        from app.db.models import ExclusionItem
        from app.trading import paper
        Session = paper.sync_session_factory()
        with Session() as s:
            s.add(ExclusionItem(symbol="000001.SZ", category="manual_blacklist",
                                reason="测试黑名单"))
            s.commit()
            res = paper.place_order(s, symbol="000001.SZ", side="buy",
                                    order_amount=100_000, algo="market",
                                    split_days=1, participation_cap=0.05)
            assert not res["ok"] and "合规禁买" in res["reason"]

    def test_full_order_lifecycle_with_fills_and_basis(self, paper_env):
        from app.trading import paper
        Session = paper.sync_session_factory()
        with Session() as s:
            res = paper.place_order(s, symbol="000001.SZ", side="buy",
                                    order_amount=500_000, algo="vwap",
                                    split_days=3, participation_cap=0.05)
            assert res["ok"]
            assert res["decision_price"] > 0
            fr = paper.run_fills(s)
            s.commit()
            assert fr["fills_created"] >= 1
            acc = paper.account_summary(s)
            assert acc["positions"].get("000001.SZ", {}).get("qty", 0) > 0
            assert acc["cash"] < paper.INIT_CASH        # 买入扣现金
            fills = s.query(paper.PaperFill).all()
            for f in fills:
                assert f.qty > 0 and f.price > 0
                # D0+1 起成交（无未来函数）
                assert f.exec_date >= date.today() - timedelta(days=6)

    def test_cancel_all(self, paper_env):
        from app.trading import paper
        Session = paper.sync_session_factory()
        with Session() as s:
            paper.place_order(s, symbol="000001.SZ", side="buy",
                              order_amount=100_000, algo="market",
                              split_days=1, participation_cap=0.05)
            n = paper.cancel_all(s)
            assert n == 1
            statuses = [o.status for o in s.query(paper.PaperOrder).all()]
            assert statuses == ["CANCELLED"]
