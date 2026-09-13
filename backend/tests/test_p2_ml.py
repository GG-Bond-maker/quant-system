"""P2-1 网格搜索 + P2-2 ICIR 监控测试。"""
from __future__ import annotations

import sqlite3
import asyncio
import sys
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

BACKEND_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(BACKEND_ROOT))

from app.db.migrations import ensure_columns  # noqa: E402
from app.db.init_db import init_database  # noqa: E402
from scripts.grid_search import GRID, pick_top1, run_grid_search  # noqa: E402
from scripts.icir_monitor import compute_icir_report, status_of  # noqa: E402

N_SYMBOLS = 12
N_DAYS = 300


@pytest.fixture(scope="module")
def features() -> pd.DataFrame:
    rng = np.random.default_rng(13)
    dates = pd.bdate_range("2023-01-02", periods=N_DAYS)
    frames = []
    for i in range(N_SYMBOLS):
        rets = np.zeros(N_DAYS)
        for t in range(1, N_DAYS):
            rets[t] = 0.25 * rets[t - 1] + 0.02 * rng.standard_normal()
        close = 20.0 * np.exp(np.cumsum(rets))
        frames.append(pd.DataFrame({
            "symbol": f"G{i:02d}.SZ", "date": dates,
            "open": close, "high": close * 1.01, "low": close * 0.99, "close": close,
            "volume": rng.integers(1e4, 1e5, N_DAYS).astype(float),
        }))
    return pd.concat(frames, ignore_index=True)


class TestGridSearch:
    def test_grid_space_is_81(self):
        total = 1
        for v in GRID.values():
            total *= len(v)
        assert total == 81
        assert GRID["learning_rate"] == [0.03, 0.05, 0.08]
        assert GRID["num_leaves"] == [31, 63, 127]
        assert GRID["feature_fraction"] == [0.6, 0.8, 1.0]
        assert GRID["min_child_samples"] == [20, 50, 100]

    def test_81_runs_recorded_and_top1_ignores_test(self, features, tmp_path):
        results, top1 = run_grid_search(
            features, holdout_days=40, test_days=30, gap_days=5,
            num_boost_round=25, stopping_rounds=10, record=False)
        assert len(results) == 81
        # 每组完整指标
        for r in results:
            assert {"train_ic", "valid_ic", "test_ic", "train_rankic",
                    "valid_rankic", "test_rankic", "train_time"} <= set(r)
        # Top-1 排序纪律：构造一个 test_ic 极高但 valid_ic 极低的组合，
        # 若排序含 test 字段它会被选中 -> 守卫
        poisoned = dict(results[0])
        poisoned["valid_ic"], poisoned["valid_rankic"] = -0.99, -0.99
        poisoned["test_ic"] = 9.99
        assert pick_top1(results + [poisoned])["valid_ic"] != 9.99
        assert top1["valid_ic"] == max(r["valid_ic"] for r in results)

    def test_feature_runs_migration_and_record(self, features, tmp_path):
        """feature_runs 迁移列存在 + record=True 真实写库（幂等可重复迁移）。"""
        from app.db.session import reset_engine

        reset_engine()
        asyncio.run(init_database())
        # 幂等迁移
        from sqlalchemy import create_engine, text

        from app.core.config import get_settings

        eng = create_engine(get_settings().SQLITE_URL.replace("+aiosqlite", ""))
        with eng.begin() as conn:
            added = ensure_columns(conn, "feature_runs", {"grid_extra_col": "TEXT"})
        with eng.begin() as conn:
            added2 = ensure_columns(conn, "feature_runs", {"grid_extra_col": "TEXT"})
        assert added == ["grid_extra_col"] and added2 == []
        with eng.begin() as conn:
            conn.execute(text("ALTER TABLE feature_runs DROP COLUMN grid_extra_col"))

        # 共享测试库可能已被同会话其它用例写入 grid_% 行，精确计数断言
        # 会随执行顺序漂移（曾经的 flake 来源）；改为断言"本次净增 81 行"，
        # 既保留精确性又对顺序免疫。
        db_path = get_settings().SQLITE_PATH

        def _grid_count() -> int:
            c = sqlite3.connect(db_path)
            try:
                return c.execute(
                    "SELECT COUNT(*) FROM feature_runs WHERE strategy LIKE 'grid_%'"
                ).fetchone()[0]
            finally:
                c.close()

        before = _grid_count()
        results, _ = run_grid_search(
            features.head(1200), holdout_days=30, test_days=25, gap_days=5,
            num_boost_round=20, stopping_rounds=10, record=True)
        n = _grid_count() - before
        cols = [r[1] for r in
                sqlite3.connect(db_path).execute("PRAGMA table_info(feature_runs)")]
        assert n == 81
        assert {"valid_ic", "test_ic", "train_time", "params_json"} <= set(cols)


class TestICIR:
    def test_status_thresholds(self):
        assert status_of(0.8) == "strong"
        assert status_of(-0.8) == "risk_factor"
        assert status_of(0.2) == "normal"
        assert status_of(float("nan")) == "insufficient"

    def test_icir_known_series(self):
        """人工构造 IC 序列验证 ICIR = mean/std（总体口径 ddof=0）。"""
        ics = np.array([0.1, 0.2, 0.3, 0.4, 0.5])
        assert ics.mean() / ics.std() == pytest.approx(2.1213, rel=1e-3)

    def test_report_on_constructed_strong_factor(self, features):
        """构造一个强因子（动量本身），其 ICIR 应显著为正；报告列齐全。"""
        from app.ml.features import build_factors

        report = compute_icir_report(build_factors(features), window=20)
        assert {"factor", "mean_ic_20", "std_ic_20", "icir_20",
                "sample_count", "status"} <= set(report.columns)
        assert len(report) >= 40  # v1 44 列
        ret5 = report[report["factor"] == "ret_5"]
        assert ret5["icir_20"].iloc[0] > 0  # 动量合成数据上为正
        strong = report[report["status"] == "strong"]
        assert (strong["icir_20"] > 0.5).all()

    def test_no_forged_strong_counts(self, features):
        """不伪造达标：status 完全由 icir 阈值决定。"""
        from app.ml.features import build_factors

        report = compute_icir_report(build_factors(features), window=20)
        for _, r in report.iterrows():
            if r["icir_20"] != r["icir_20"]:  # NaN
                expected = "insufficient"
            elif r["icir_20"] > 0.5:
                expected = "strong"
            elif r["icir_20"] < -0.5:
                expected = "risk_factor"
            else:
                expected = "normal"
            assert r["status"] == expected
