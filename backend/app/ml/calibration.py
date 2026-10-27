"""Time-held-out probability calibration and empirical return intervals."""
from __future__ import annotations

from typing import Any

import numpy as np
from sklearn.isotonic import IsotonicRegression


def fit_calibration(predictions: np.ndarray, targets: np.ndarray,
                    coverage: float = .9) -> dict[str, Any]:
    """Fit only on a calibration window excluded from early stopping and testing."""
    pred, target = np.asarray(predictions, dtype=float), np.asarray(targets, dtype=float)
    good = np.isfinite(pred) & np.isfinite(target)
    pred, target = pred[good], target[good]
    if len(pred) < 30 or len(np.unique(target > 0)) < 2:
        return {"available": False, "reason": "独立校准样本不足或标签仅含单一方向", "n": len(pred)}
    calibrator = IsotonicRegression(out_of_bounds="clip", y_min=0, y_max=1)
    calibrator.fit(pred, (target > 0).astype(float))
    level = min(1.0, np.ceil((len(pred) + 1) * coverage) / len(pred))
    radius = float(np.quantile(np.abs(target - pred), level, method="higher"))
    return {"available": True, "method": "heldout_isotonic_and_absolute_residual",
            "n": len(pred), "coverage_level": coverage, "radius": radius,
            "x": calibrator.X_thresholds_.tolist(), "p": calibrator.y_thresholds_.tolist(),
            "target": "P(model_target > 0)",
            "note": "仅对应模型训练目标；截面超额收益不等于个股绝对上涨概率。区间覆盖须按样本外实测评价。"}


def calibrated_values(predictions: np.ndarray, artifact: dict[str, Any]) -> tuple[np.ndarray, np.ndarray, np.ndarray] | None:
    """Apply the frozen calibrator; never fit or update with prediction outcomes."""
    if not artifact.get("available"):
        return None
    pred = np.asarray(predictions, dtype=float)
    probability = np.interp(pred, np.asarray(artifact["x"]), np.asarray(artifact["p"]))
    radius = float(artifact["radius"])
    return probability, pred - radius, pred + radius


def evaluate_calibration(predictions: np.ndarray, targets: np.ndarray,
                         artifact: dict[str, Any]) -> dict[str, Any]:
    """Report independent test Brier/ECE and interval coverage without tuning."""
    result = calibrated_values(predictions, artifact)
    if result is None:
        return {"available": False}
    probability, lower, upper = result
    target = np.asarray(targets, dtype=float)
    good = np.isfinite(probability) & np.isfinite(target)
    probability, target = probability[good], target[good]
    if not len(target):
        return {"available": False}
    truth = (target > 0).astype(float)
    ece = 0.0
    bins = np.minimum((probability * 10).astype(int), 9)
    for index in range(10):
        selected = bins == index
        if selected.any():
            ece += float(selected.mean() * abs(probability[selected].mean() - truth[selected].mean()))
    return {"available": True, "n": len(target),
            "brier": float(np.mean((probability - truth) ** 2)), "ece": ece,
            "interval_coverage": float(np.mean((target >= lower[good]) & (target <= upper[good]))),
            "nominal_coverage": artifact["coverage_level"]}
