"""
推理与特征贡献度解释（AQP ML）。

- load_prod_model：加载 MODEL_ROOT 下最新版本模型 + 因子清单；
- predict_with_contrib：LightGBM 原生 pred_contrib（TreeSHAP 精确值，无需额外依赖），
  返回逐行预测分与逐行逐因子贡献矩阵；
- top_factor_contributions：单标的 Top-K 因子贡献（中文名映射可选）；
- global_feature_importance：全局 gain 重要度 Top-K；
- infer_day：对某日特征分区推理并落盘 predictions Parquet。
"""
from __future__ import annotations

from pathlib import Path

import lightgbm as lgb
import numpy as np
import pandas as pd
from loguru import logger

from .features import FEATURE_VERSION


def load_prod_model(model_name: str = "lgbm_v1") -> tuple[lgb.Booster, list[str], Path]:
    """加载生产模型（booster, features, model_dir）。

    ⚠️ CRIT-003 修复（第三阶段）：
        旧实现取 MODEL_ROOT 下"目录名排序最后一个" —— 任何一次实验/测试训练
        都会静默变成线上模型（曾服务 1 棵树、test IC −0.365 的模型）。
        现在一律走 :func:`app.ml.registry.load_prod_model`，
        只读 ``model_registry.is_production = 1``，不存在就抛错，不回退实验模型。
    """
    from .registry import load_prod_model as _load

    return _load(model_name)


def predict_with_contrib(
    booster: lgb.Booster,
    X: pd.DataFrame,
    features: list[str],
) -> tuple[np.ndarray, np.ndarray]:
    """预测 + TreeSHAP 贡献矩阵。

    :return: (pred_score[n], contrib[n, n_features+1])
             contrib 最后一列为基准值（expected value），
             第 i 列为第 i 个因子对该行预测值的贡献（求和 = pred - base）。
    """
    missing = [c for c in features if c not in X.columns]
    if missing:
        raise ValueError(f"特征列缺失: {missing[:10]}{'...' if len(missing) > 10 else ''}")
    matrix = X[features]
    pred: np.ndarray = np.asarray(
        booster.predict(matrix, num_iteration=booster.best_iteration or -1)
    )
    contrib: np.ndarray = np.asarray(
        booster.predict(matrix, pred_contrib=True,
                        num_iteration=booster.best_iteration or -1)
    )
    # 单棵树时 contrib 可能是 1-D
    if contrib.ndim == 1:
        contrib = contrib.reshape(1, -1)
    return pred, contrib


def top_factor_contributions(
    contrib_row: np.ndarray,
    features: list[str],
    k: int = 5,
) -> list[dict[str, object]]:
    """把单行贡献向量转为 Top-K 因子解释列表（按 |贡献| 降序）。"""
    values = contrib_row[:-1] if contrib_row.ndim == 1 and contrib_row.size == len(features) + 1 \
        else contrib_row
    order = np.argsort(-np.abs(values))[:k]
    return [
        {"feature": features[i], "contribution": float(values[i]),
         "direction": "positive" if values[i] >= 0 else "negative"}
        for i in order
    ]


def global_feature_importance(
    booster: lgb.Booster,
    features: list[str],
    top_k: int = 20,
) -> list[dict[str, object]]:
    """全局 gain 重要度 Top-K（零额外计算开销的默认解释层）。"""
    gain = booster.feature_importance(importance_type="gain")
    order = np.argsort(-gain)[:top_k]
    return [
        {"feature": features[i], "gain": float(gain[i])}
        for i in order
        if gain[i] > 0
    ]


def infer_day(features_day_path: Path | str, output_path: Path | str) -> pd.DataFrame:
    """对某日因子分区推理，输出 [date, symbol, pred_score] Parquet。"""
    booster, feats, _ = load_prod_model()
    day_df = pd.read_parquet(features_day_path)
    pred, _ = predict_with_contrib(booster, day_df, feats)
    out = day_df[["date", "symbol"]].copy()
    out["pred_score"] = pred
    out_path = Path(output_path)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    from ..data.parquet_store import atomic_write_parquet

    atomic_write_parquet(out_path, out)
    logger.info(
        f"infer done({FEATURE_VERSION}): rows={len(out)} -> {out_path}"
    )
    return out
