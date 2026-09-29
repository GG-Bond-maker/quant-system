"""
推理与特征贡献度解释（AQP ML）。

- load_prod_model：加载 MODEL_ROOT 下最新版本模型 + 因子清单；
- predict_with_contrib：LightGBM 原生 pred_contrib（TreeSHAP 精确值，无需额外依赖），
  返回逐行预测分与逐行逐因子贡献矩阵；
- top_factor_contributions：单标的 Top-K 因子贡献（中文名映射可选）；
- global_feature_importance：全局 gain 重要度 Top-K。
"""
from __future__ import annotations

from pathlib import Path

import lightgbm as lgb
import numpy as np
import pandas as pd


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


def prediction_return_basis(model_dir: Path | str) -> dict[str, object]:
    """模型产物的**预测口径**披露（审计 P1-48，T3 的 "return_basis"）。

    为什么必须有：`pred_score` 这一列混装两种互不相容的语义——绝对收益口径
    （实测 mean=+0.7077%/5d）与截面去均值口径（mean=−0.0138%/5d），此前只能靠
    预测分布反推；且报告与图表把 pred_score 当"预期收益"解读时，必须知道它
    究竟是"绝对收益"还是"截面相对收益"。

    :return: ``{"available": bool, "target":..., "xsec_demean":..., "horizon":...,
        "pred_level_mean":..., "pred_level_std":..., "label_level_mean":...,
        "level_bias":..., "level_bias_se":..., "note":...}``；
        ``metrics.json`` 缺失或为旧产物（未落盘这些字段）时 ``available=False``
        并给出 reason —— **不猜、不填 0**。
    """
    import json

    try:
        metrics_path = Path(model_dir) / "metrics.json"
    except TypeError:
        return {"available": False, "reason": "模型目录不可解析，无法读取训练口径"}
    if not metrics_path.exists():
        return {"available": False, "reason": "模型 metrics.json 不存在（无法披露训练口径）"}
    try:
        metrics = json.loads(metrics_path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {"available": False, "reason": "模型 metrics.json 不可解析"}
    basis = metrics.get("train_basis") or {}
    level = metrics.get("pred_level") or {}
    if not basis and not level:
        return {"available": False,
                "reason": "旧产物：metrics.json 未落盘 train_basis/pred_level"}
    out: dict[str, object] = {"available": True}
    for k in ("target", "xsec_demean", "horizon", "label_mode",
              "max_abs_label_return", "dataset_version", "feature_version"):
        if k in basis:
            out[k] = basis[k]
    for k in ("pred_level_mean", "pred_level_std", "label_level_mean",
              "label_level_std", "level_bias", "level_bias_se", "n"):
        if k in level:
            out[k] = level[k]
    out["note"] = (
        "pred_score 的口径由 target/xsec_demean 决定：target=xsec_demean 时为"
        "**截面相对收益**（同日全市场去均值），pred_score 的绝对水平不可当"
        "预期收益解读；target=absolute_forward_return 时为绝对收益口径。"
        "pred_level_* 为 valid 段的预测水平统计，level_bias = mean(pred) − mean(label)")
    return out


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
