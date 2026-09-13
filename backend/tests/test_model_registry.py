"""模型治理测试（第三/四/五/六阶段：CRIT-003）。

覆盖：
    1. 无生产模型时必须明确报错，绝不回退实验模型；
    2. 训练产物落 exp/，生产模型只存在于 prod/；
    3. 训练不自动写 is_production；
    4. promote 必须显式执行，且同一时间最多一个 production；
    5. 质量门槛：candidate vs production 只比较 validation 指标。
"""
from __future__ import annotations

import sys
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

BACKEND_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(BACKEND_ROOT))

from app.ml.registry import (  # noqa: E402
    DEFAULT_PROMOTE_POLICY, ModelRegistryError, NoProductionModelError,
    clear_registry, count_production, evaluate_candidate, exp_root,
    get_production, list_models, prod_root, promote_model,
)
from app.ml.train_lgbm import train_lgbm  # noqa: E402

# ⚠️ N_SYMBOLS 必须 >= 10：daily_rank_ic 要求每日截面样本 > min_samples(10)，
# 否则所有日期被跳过 -> RankIC 为 NaN -> promote 门禁以"指标缺失"拒绝。
# （这是 daily_rank_ic 的既定行为，小样本宇宙需自行调低 min_samples。）
N_SYMBOLS, N_DAYS = 20, 320


def _ohlcv(seed: int = 3) -> pd.DataFrame:
    rng = np.random.default_rng(seed)
    dates = pd.bdate_range("2023-01-02", periods=N_DAYS)
    frames = []
    for i in range(N_SYMBOLS):
        rets = np.zeros(N_DAYS)
        for t in range(1, N_DAYS):
            rets[t] = 0.25 * rets[t - 1] + 0.02 * rng.standard_normal()
        close = 10.0 * np.exp(np.cumsum(rets))
        frames.append(pd.DataFrame({
            "symbol": f"S{i:03d}.SZ", "date": dates,
            "open": close * 1.001, "high": close * 1.01, "low": close * 0.99,
            "close": close, "volume": 1e6, "amount": 1e7,
        }))
    return pd.concat(frames, ignore_index=True)


def _train(version_suffix: str, seed: int = 3) -> dict:
    from app.ml.features import build_factors

    feats = build_factors(_ohlcv(seed))
    return train_lgbm(
        feats, horizon=5, holdout_days=40, test_days=30, gap_days=5,
        version_suffix=version_suffix, num_boost_round=30, stopping_rounds=10,
        min_abs_rank_ic=0.0, top_k=20, persist=True)


# 宽松策略：既放宽绝对门槛，也放宽与当前生产的相对比较。
# （质量门槛本身由 test_gate_* 系列用例单独覆盖。）
PERMISSIVE = dict(min_valid_rank_ic=-1.0, min_valid_icir=-1.0,
                  rank_ic_tolerance=1e9, icir_tolerance=1e9,
                  max_rmse_worsen_ratio=1e9)


@pytest.fixture(autouse=True)
def _clean_registry():
    """每个用例前清空注册表，消除对执行顺序的依赖。

    第九阶段要求：pytest 单独跑 与 全量跑 结果必须一致。
    本模块多个用例断言"无生产模型"，若不清空就会被 test_api.py
    （字母序在前，会 promote 一个模型）污染而失败。
    """
    clear_registry()
    yield


# ---------------------------------------------------------------- 第三阶段
def test_load_prod_model_raises_when_no_production():
    """无生产模型 -> 明确报错，绝不回退到"最新目录"。"""
    from app.ml.infer import load_prod_model

    assert count_production() == 0
    # 即使 exp/ 里躺着实验模型，也不得被加载
    _train("exp_only")
    assert len(list(exp_root().iterdir())) > 0, "exp/ 应已有实验产物"

    with pytest.raises(NoProductionModelError) as ei:
        load_prod_model()
    assert "promote" in str(ei.value).lower(), (
        f"错误信息应指引用户执行 promote，实际：{ei.value}")


def test_no_production_fallback_to_experiment_dir():
    """旧实现按目录名取最新；新实现必须只认注册表。"""
    from app.ml.infer import load_prod_model

    _train("aaa")   # 名字排序靠前
    _train("zzz")   # 名字排序靠后（旧实现会选它）
    with pytest.raises(NoProductionModelError):
        load_prod_model()


# ---------------------------------------------------------------- 第四阶段
def test_training_writes_to_exp_not_prod():
    """训练产物必须落在 models/exp/，且不得自动成为生产模型。"""
    r = _train("isolation")
    model_dir = Path(r["model_path"]).parent
    assert model_dir.parent == exp_root(), (
        f"产物应落在 exp/，实际 {model_dir.parent}")
    assert get_production() is None, "训练后不应出现生产模型"
    assert count_production() == 0

    recs = list_models(limit=50)
    mine = [x for x in recs if x["version"] == r["version"]]
    assert mine, "训练后应登记 candidate"
    assert int(mine[0]["is_production"]) == 0
    assert mine[0]["status"] == "candidate"


# ---------------------------------------------------------------- 第五阶段
def test_promote_is_explicit_and_singleton():
    """promote 后生产模型唯一；再 promote 另一个，旧的必须被归档。"""
    from app.ml.registry import PromotePolicy

    # 本用例验证"单例"语义，不是质量，故用宽松门槛
    pol = PromotePolicy(**PERMISSIVE)
    a = _train("first")
    res1 = promote_model("lgbm_v1", a["version"], by="test",
                         reason="first", policy=pol)
    assert res1["promoted"], res1["reason"]
    assert count_production() == 1
    assert get_production()["version"] == a["version"]

    b = _train("second", seed=9)
    res2 = promote_model("lgbm_v1", b["version"], by="test", policy=pol)
    assert res2["promoted"]
    assert count_production() == 1, "promote 后生产模型必须仍然唯一"
    assert get_production()["version"] == b["version"]

    rows = {r["version"]: r for r in list_models(limit=50)}
    assert int(rows[a["version"]]["is_production"]) == 0
    assert rows[a["version"]]["status"] == "archived"


def test_promote_copies_artifacts_into_prod_dir():
    from app.ml.registry import PromotePolicy

    r = _train("prodcopy")
    promote_model("lgbm_v1", r["version"], by="test",
                  policy=PromotePolicy(**PERMISSIVE))
    d = prod_root() / f"lgbm_v1_{r['version']}"
    assert d.exists(), f"prod/ 下应存在生产模型目录：{d}"
    for name in ("model.lgbm", "features.json"):
        assert (d / name).exists(), f"prod 目录缺少 {name}"


def test_promote_unknown_version_raises():
    with pytest.raises(ModelRegistryError):
        promote_model("lgbm_v1", "nonexistent_version_xyz", by="test")


def test_promoted_model_loadable_and_matches_registry():
    from app.ml.infer import load_prod_model
    from app.ml.registry import PromotePolicy

    r = _train("loadable")
    promote_model("lgbm_v1", r["version"], by="test",
                  policy=PromotePolicy(**PERMISSIVE))
    booster, feats, model_dir = load_prod_model()
    assert len(feats) > 0
    assert booster.num_trees() > 0
    assert model_dir.parent == prod_root()
    assert get_production()["model_path"] == str(model_dir / "model.lgbm")


# ---------------------------------------------------------------- 第六阶段
def _cand(rank_ic: float, icir: float, rmse: float = 0.06,
          ic: float = 0.02, test_rank_ic: float = 0.01) -> dict:
    return {"valid_rank_ic": rank_ic, "valid_icir": icir, "valid_rmse": rmse,
            "valid_ic": ic, "test_rank_ic": test_rank_ic}


def test_gate_rejects_below_absolute_threshold():
    d = evaluate_candidate(_cand(0.01, 0.5), None, DEFAULT_PROMOTE_POLICY)
    assert not d.promote and "绝对门槛" in d.reason

    d = evaluate_candidate(_cand(0.5, 0.01), None, DEFAULT_PROMOTE_POLICY)
    assert not d.promote and "绝对门槛" in d.reason


def test_gate_accepts_first_model_above_threshold():
    d = evaluate_candidate(_cand(0.05, 0.4), None, DEFAULT_PROMOTE_POLICY)
    assert d.promote, d.reason
    assert "无生产模型" in d.reason


def test_gate_compares_against_current_production():
    prod = _cand(0.04, 0.30, rmse=0.060)

    worse_rank = evaluate_candidate(_cand(0.035, 0.40), prod, DEFAULT_PROMOTE_POLICY)
    assert not worse_rank.promote and "valid_rank_ic 劣于生产" in worse_rank.reason

    worse_icir = evaluate_candidate(_cand(0.05, 0.20), prod, DEFAULT_PROMOTE_POLICY)
    assert not worse_icir.promote and "valid_icir 劣于生产" in worse_icir.reason

    worse_rmse = evaluate_candidate(_cand(0.05, 0.40, rmse=0.090), prod,
                                    DEFAULT_PROMOTE_POLICY)
    assert not worse_rmse.promote and "valid_rmse 恶化超限" in worse_rmse.reason

    better = evaluate_candidate(_cand(0.05, 0.40, rmse=0.055), prod,
                                DEFAULT_PROMOTE_POLICY)
    assert better.promote, better.reason


def test_gate_ignores_test_metrics_by_default():
    """test 指标不参与决策（避免用 test 调参造成 test contamination）。"""
    prod = _cand(0.04, 0.30)
    # test_rank_ic 很差，但仍应放行（默认 require_test_rank_ic_positive=False）
    d = evaluate_candidate(_cand(0.05, 0.40, test_rank_ic=-0.20), prod,
                           DEFAULT_PROMOTE_POLICY)
    assert d.promote, "默认情况下 test 指标不应影响 promote 决策"

    # 但策略可显式要求 test 为正
    from app.ml.registry import PromotePolicy

    strict = PromotePolicy(require_test_rank_ic_positive=True)
    d2 = evaluate_candidate(_cand(0.05, 0.40, test_rank_ic=-0.20), prod, strict)
    assert not d2.promote


def test_gate_handles_nan_metrics():
    nan = _cand(float("nan"), 0.4)
    d = evaluate_candidate(nan, None, DEFAULT_PROMOTE_POLICY)
    assert not d.promote and "NaN" in d.reason
