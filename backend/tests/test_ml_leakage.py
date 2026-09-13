"""ML 防泄漏测试（P0-Major#3 回归守卫）。

TC-LEAK-FEATURE            特征不得包含未来数据（截断不变性）
TC-LEAK-FEATURE-SELECTION  特征筛选只能使用 train 段
TC-LEAK-SCALER             无跨样本 / 跨截面 fit 状态（逐 symbol 独立、可重入）
TC-LEAK-EARLY-STOP         早停只受 valid 影响，test 数据变化不得改变 best_iteration
TC-LEAK-GAP                gap >= horizon，Label 窗口不跨越切分边界
"""
from __future__ import annotations

import sys
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

BACKEND_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(BACKEND_ROOT))

from app.ml.features import build_factors, factor_columns  # noqa: E402
from app.ml.train_lgbm import (  # noqa: E402
    select_features_by_ic,
    split_dates,
    train_lgbm,
)

N_SYMBOLS = 12
N_DAYS = 420


def _synthetic(seed: int = 11) -> pd.DataFrame:
    """带动量的合成行情（hfq 语义：append-stable 的价格序列）。"""
    rng = np.random.default_rng(seed)
    dates = pd.bdate_range("2023-01-02", periods=N_DAYS)
    frames = []
    for i in range(N_SYMBOLS):
        rets = np.zeros(N_DAYS)
        for t in range(1, N_DAYS):
            rets[t] = 0.25 * rets[t - 1] + 0.02 * rng.standard_normal()
        close = 20.0 * np.exp(np.cumsum(rets))
        frames.append(pd.DataFrame({
            "symbol": f"S{i:03d}.SZ", "date": dates,
            "open": close, "high": close * 1.01, "low": close * 0.99,
            "close": close,
            "volume": rng.integers(1_000, 100_000, N_DAYS).astype(float),
        }))
    return pd.concat(frames, ignore_index=True)


def test_tc_leak_feature_truncation_invariance():
    """TC-LEAK-FEATURE：截断数据重算，历史行特征必须逐值一致（禁止未来信息）。"""
    df = _synthetic()
    full = build_factors(df)
    cut = len(df) // 2
    trunc = build_factors(df.iloc[:cut])
    cols = factor_columns(full)
    a = full.iloc[:cut][cols].reset_index(drop=True)
    b = trunc[cols].reset_index(drop=True)
    pd.testing.assert_frame_equal(a, b, check_exact=False, atol=1e-12)


def test_tc_leak_feature_selection_train_only():
    """TC-LEAK-FEATURE-SELECTION：只在 train/valid 泄漏的因子，仅用 train 筛选时不可入选。"""
    rng = np.random.default_rng(3)
    n = 600
    y = pd.Series(rng.standard_normal(n))
    noise = pd.Series(rng.standard_normal(n))
    # f_leak 在前 400 行（"train"）与 y 无关，在最后 200 行与 y 完全一致（纯泄漏）
    f_leak = pd.concat([noise.iloc[:400], y.iloc[400:]], ignore_index=True)
    X = pd.DataFrame({"f_noise": noise, "f_leak": f_leak})
    kept = select_features_by_ic(X.iloc[:400], y.iloc[:400], min_abs_rank_ic=0.05, top_k=2)
    assert "f_leak" not in kept, "train 段无信息的因子不得因 valid 段泄漏而入选"
    # 反证：若错误地用全量数据筛选，f_leak 会被选中（证明测试有效）
    kept_wrong = select_features_by_ic(X, y, min_abs_rank_ic=0.05, top_k=2)
    assert "f_leak" in kept_wrong


def test_tc_leak_scaler_no_cross_sectional_fit():
    """TC-LEAK-SCALER：无全局/截面 fit 状态 —— 单 symbol 特征与其余标的是否在场无关。"""
    df = _synthetic()
    sym_a = df[df["symbol"] == df["symbol"].iloc[0]].copy()
    alone = build_factors(sym_a)
    both = build_factors(df)
    cols = factor_columns(alone)
    a1 = alone[cols].reset_index(drop=True)
    a2 = both[both["symbol"] == sym_a["symbol"].iloc[0]][cols].reset_index(drop=True)
    pd.testing.assert_frame_equal(a1, a2, check_exact=False, atol=1e-12)
    # 可重入：同一输入两次构建结果一致（无隐藏累积状态）
    pd.testing.assert_frame_equal(
        build_factors(df)[cols], both[cols], check_exact=False, atol=1e-12)


def test_tc_leak_early_stop_ignores_test():
    """TC-LEAK-EARLY-STOP：test 段数据变化不得改变 best_iteration。"""
    df = _synthetic(seed=5)
    common = train_lgbm(
        df.copy(), horizon=5, holdout_days=60, test_days=40, gap_days=5,
        version_suffix="leakA", num_boost_round=80, stopping_rounds=15,
        min_abs_rank_ic=0.0, top_k=30, params={"min_data_in_leaf": 20, "num_leaves": 15},
    )
    # 只篡改 test 段的 close（=> test 标签全变），train/valid/gap 段保持不变
    df_mut = df.copy()
    dates = sorted(df_mut["date"].unique())
    test_start = dates[-40]  # 与 split_dates(test_days=40) 的 test_start 完全一致
    mask = df_mut["date"] >= test_start
    df_mut.loc[mask, "close"] = df_mut.loc[mask, "close"] * 1.5
    df_mut.loc[mask, ["open", "high", "low"]] = (
        df_mut.loc[mask, ["open", "high", "low"]] * 1.5)
    mutated = train_lgbm(
        df_mut, horizon=5, holdout_days=60, test_days=40, gap_days=5,
        version_suffix="leakB", num_boost_round=80, stopping_rounds=15,
        min_abs_rank_ic=0.0, top_k=30, params={"min_data_in_leaf": 20, "num_leaves": 15},
    )
    assert common["best_iteration"] == mutated["best_iteration"], \
        "test 段变化影响了早停 => 泄漏"
    assert common["test_ic"] != pytest.approx(mutated["test_ic"], abs=1e-12) or \
        common["test_rmse"] != pytest.approx(mutated["test_rmse"], abs=1e-12), \
        "test 段已被篡改但 test 指标未变化，说明对照无效"


def test_tc_leak_gap():
    """TC-LEAK-GAP：gap >= horizon 且 Label 窗口不跨越 train/valid、valid/test 边界。"""
    dates = list(pd.bdate_range("2023-01-02", periods=500))
    bounds = split_dates(dates, holdout_days=120, test_days=100, gap_days=5)
    idx = {d: i for i, d in enumerate(dates)}
    horizon = 5
    # train 末日的 label 窗口 [train_end+1, train_end+horizon] 必须完全落在 gap1 内
    assert idx[bounds["train_end"]] + horizon < idx[bounds["valid_start"]]
    # valid 末日的 label 窗口必须完全落在 gap2 内
    assert idx[bounds["valid_end"]] + horizon < idx[bounds["test_start"]]
    # gap 长度恰为 gap_days
    assert idx[bounds["valid_start"]] - idx[bounds["train_end"]] - 1 == 5
    assert idx[bounds["test_start"]] - idx[bounds["valid_end"]] - 1 == 5
    # gap < horizon 时三段 label 必然互相污染：split_dates 不做拦截（由调用方保证
    # gap >= horizon），但 train_lgbm 默认 gap=horizon —— 验证默认行为
    assert split_dates(dates, 120, 100, 5)["gap1_start"] is not None
