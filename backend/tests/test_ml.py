"""ml 模块测试：因子提取 + 合成数据端到端训练 + TreeSHAP 贡献度。"""
from __future__ import annotations

import sys
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

BACKEND_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(BACKEND_ROOT))

from app.ml.features import build_factors, factor_columns
from app.ml.infer import (
    global_feature_importance,
    load_prod_model,
    predict_with_contrib,
    top_factor_contributions,
)
from app.ml.registry import PromotePolicy, promote_model
from app.ml.train_lgbm import rank_ic, train_lgbm


N_SYMBOLS = 20
N_DAYS = 420


def _synthetic_ohlcv() -> pd.DataFrame:
    """带动量效应的合成行情：r_t = 0.25*r_{t-1} + 0.02*eps，保证可学习信号。"""
    rng = np.random.default_rng(42)
    dates = pd.bdate_range("2023-01-02", periods=N_DAYS)
    frames = []
    for i in range(N_SYMBOLS):
        rets = np.zeros(N_DAYS)
        for t in range(1, N_DAYS):
            rets[t] = 0.25 * rets[t - 1] + 0.02 * rng.standard_normal()
        close = 10.0 * np.exp(np.cumsum(rets))
        g = pd.DataFrame({
            "symbol": f"S{i:03d}.SZ",
            "date": dates,
            "open": close * (1 + 0.005 * rng.standard_normal(N_DAYS)),
            "high": close * (1 + 0.01 * np.abs(rng.standard_normal(N_DAYS))),
            "low": close * (1 - 0.01 * np.abs(rng.standard_normal(N_DAYS))),
            "close": close,
            "volume": rng.integers(1_000, 100_000, N_DAYS).astype(float),
            "amount": close * rng.integers(1_000, 100_000, N_DAYS),
        })
        frames.append(g)
    return pd.concat(frames, ignore_index=True)


@pytest.fixture(scope="module")
def factors() -> pd.DataFrame:
    return build_factors(_synthetic_ohlcv())


class TestFeatures:
    def test_shape_and_columns(self, factors: pd.DataFrame):
        assert factors.height if hasattr(factors, "height") else len(factors) > 0
        cols = factor_columns(factors)
        assert len(cols) >= 40, f"factor cols too few: {len(cols)}"
        for must in ("ret_5", "ma_gap_20", "vol_20", "macd_bar", "rsi_14",
                     "boll_pos", "atr_14", "rank_close_60"):
            assert must in cols

    def test_no_inf(self, factors: pd.DataFrame):
        cols = factor_columns(factors)
        arr = factors[cols].to_numpy(dtype=np.float64)
        assert np.isinf(arr).sum() == 0

    def test_rsi_range(self, factors: pd.DataFrame):
        rsi = factors["rsi_14"].dropna()
        assert float(rsi.min()) >= 0.0 and float(rsi.max()) <= 1.0

    def test_no_lookahead(self, factors: pd.DataFrame):
        """PIT 校验：首行除 0 窗口因子外，不应有由未来数据产生的非 NaN 值。"""
        first = factors.iloc[0]
        assert np.isnan(first["ret_5"]) and np.isnan(first["ma_gap_20"])


class TestTrainAndInfer:
    @pytest.fixture(scope="class")
    def train_result(self, factors: pd.DataFrame) -> dict:
        return train_lgbm(
            factors,
            horizon=5,
            holdout_days=60,
            test_days=40,
            gap_days=5,
            version_suffix="pytest",
            num_boost_round=80,
            stopping_rounds=15,
            min_abs_rank_ic=0.0,
            top_k=30,
        )

    def test_train_metrics_finite(self, train_result: dict):
        assert np.isfinite(train_result["valid_ic"])
        assert np.isfinite(train_result["valid_rank_ic"])
        assert train_result["valid_rmse"] > 0
        assert Path(train_result["model_path"]).exists()

    def test_three_way_split_present(self, train_result: dict):
        """P0-Major#3：train/valid/test 三段指标齐备，test 只做最终评估。"""
        for k in ("train_ic", "train_rmse", "valid_ic", "valid_rank_ic",
                  "valid_icir", "test_ic", "test_rank_ic", "test_icir", "test_rmse"):
            assert k in train_result, f"缺少 {k}"
        assert train_result["gap_days"] >= train_result["horizon"]
        # test 段不得参与早停：best_iteration 仅由 valid 决定（结构性由代码保证，
        # 此处断言 split 边界齐全且 test_rows > 0）
        assert train_result["test_rows"] > 0
        assert {"train_end", "valid_start", "valid_end", "test_start"}             <= set(train_result["split"])

    def test_model_dir_artifacts(self, train_result: dict):
        d = Path(train_result["model_path"]).parent
        for name in ("features.json", "params.json", "metrics.json"):
            assert (d / name).exists(), f"missing {name}"

    def test_rank_ic_sanity(self):
        rng = np.random.default_rng(0)
        y = rng.standard_normal(1000)
        assert rank_ic(y, y + 0.5 * rng.standard_normal(1000)) > 0.5
        assert rank_ic(y, -y) < -0.9

    def test_default_entry_uses_xsec_demean(self, train_result: dict):
        """**P1-37**：默认训练入口必须就是"截面去均值"口径。

        原实现 `xsec_demean` 默认 False，只有 `scripts/retrain.py` 显式打开 ⇒
        按文档跑 `scripts/train.py` / `scripts/grid_search.py` 得到的是退化模型
        （真实面板实测 best_iteration=1、valid_rank_ic 0.0144，同数据去均值后
        0.0832，5.8×）。本用例走**默认参数**的 train_lgbm（fixture 不传该 flag），
        断言落盘口径即为 xsec_demean，从而钉死"默认入口不再是退化口径"。
        """
        import inspect

        from app.ml.train_lgbm import train_lgbm

        assert inspect.signature(train_lgbm).parameters["xsec_demean"].default is True
        assert train_result["train_basis"]["xsec_demean"] is True
        assert train_result["train_basis"]["target"] == "xsec_demean"
        # P1-38：筛选口径同样必须留痕（此前无从判断用的是池化还是逐日截面 IC）
        assert train_result["train_basis"]["selection"]["ic_basis"] == "daily_xsec_rank_ic"

    def test_pred_level_disclosed_in_metrics(self, train_result: dict):
        """**P1-48**：预测水平/校准统计必须随产物落盘（门禁的第二维度）。"""
        lv = train_result["pred_level"]
        for k in ("pred_level_mean", "pred_level_std", "label_level_mean",
                  "label_level_std", "level_bias", "level_bias_se", "n"):
            assert k in lv, f"pred_level 缺 {k}"
        assert lv["n"] > 0
        assert lv["unit"] == "forward_return"
        assert abs(lv["level_bias"]
                   - (lv["pred_level_mean"] - lv["label_level_mean"])) < 1e-12
        assert train_result["pred_level_mean"] == lv["pred_level_mean"]
        assert train_result["pred_level_std"] == lv["pred_level_std"]

    def test_xsec_demean_completes_and_registers(self, factors: pd.DataFrame):
        """横截面去均值标签：训练全链路可跑通且指标有限。

        回归背景：原始 5 日收益的 L2 被市场共同波动主导，1133 只全量数据
        早停在第 2 轮；去均值后训练目标与 RankIC 的截面口径一致。
        """
        r = train_lgbm(
            factors,
            horizon=5, holdout_days=60, test_days=40, gap_days=5,
            version_suffix="pytest_demean",
            num_boost_round=80, stopping_rounds=15,
            min_abs_rank_ic=0.0, top_k=30,
            xsec_demean=True,
        )
        assert np.isfinite(r["valid_ic"]) and np.isfinite(r["valid_rank_ic"])
        assert r["valid_rmse"] > 0
        assert r["version"].endswith("pytest_demean")
        assert Path(r["model_path"]).exists()


    def test_predict_with_contrib(self, factors: pd.DataFrame, train_result: dict):
        # 第三阶段纪律：训练只产生 candidate，必须显式 promote 才能被推理加载。
        # （旧实现按目录名取"最新"，任何一次训练都会静默变成生产模型。）
        version = train_result["version"]
        # 本用例验证的是"训练->登记->promote->加载"链路本身，不是模型质量，
        # 因此放宽质量门槛（质量门槛由 test_model_registry.py 单独覆盖）。
        res = promote_model(
            "lgbm_v1", version, by="pytest", reason="测试显式提升",
            policy=PromotePolicy(min_valid_rank_ic=-1.0, min_valid_icir=-1.0,
                                 rank_ic_tolerance=1e9, icir_tolerance=1e9,
                                 max_rmse_worsen_ratio=1e9))
        assert res["promoted"], f"promote 被拒绝：{res['reason']}"
        booster, feats, model_dir = load_prod_model("lgbm_v1")
        assert model_dir.name == Path(train_result["model_path"]).parent.name

        sample = factors.tail(50)
        pred, contrib = predict_with_contrib(booster, sample, feats)
        assert pred.shape == (50,)
        assert contrib.shape == (50, len(feats) + 1)
        # 贡献守恒：sum(contrib) = pred - base_value
        recon = contrib[:, :-1].sum(axis=1) + contrib[:, -1]
        assert np.allclose(recon, pred, atol=1e-6)

        tops = top_factor_contributions(contrib[0], feats, k=5)
        assert len(tops) == 5
        assert all(set(t) == {"feature", "contribution", "direction"} for t in tops)
        # 按 |贡献| 降序
        abss = [abs(t["contribution"]) for t in tops]
        assert abss == sorted(abss, reverse=True)

        imp = global_feature_importance(booster, feats, top_k=10)
        assert 0 < len(imp) <= 10
        gains = [x["gain"] for x in imp]
        assert gains == sorted(gains, reverse=True)
