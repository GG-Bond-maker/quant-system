"""审计 P1-39 防回归：promote 门禁**不得跨训练口径比 RMSE**。

缺陷（2026-09-21 全栈审计，P1）：
    ``train_lgbm`` 的 ``xsec_demean`` 既没进 ``metrics.json``、也没进 ``params.json``，
    注册表里更是零命中 ⇒ ``evaluate_candidate`` 会拿**目标函数不同**的两个模型直接
    比 validation RMSE。真实库一对同 split、同 dataset、只差这一个 flag 的产物：
        ``181337``（绝对收益目标）valid_rmse=0.07411
        ``181713``（截面去均值目标）valid_rmse=0.05998
    比值 1.25×，远超 ``max_rmse_worsen_ratio=0.05`` ⇒ **更优的候选被判"恶化超限"**。
    这正是 P1-16「自动重训候选恒被拒」的真实根因（不是单靠放宽阈值能解决的）。

修法：口径随训练产物落盘（``train_basis`` / ``params_json.basis``），门禁先判
可比性——不一致就**跳过 RMSE**并记 ``basis_mismatch``，RankIC/ICIR 维度照常把关。
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

BACKEND_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(BACKEND_ROOT))

from app.ml.registry import (  # noqa: E402
    DEFAULT_PROMOTE_POLICY,
    PromotePolicy,
    basis_mismatch_reason,
    evaluate_candidate,
    prediction_level,
    training_basis,
)

# 真实库那一对产物的口径（只差 xsec_demean）
BASIS_ABS = {
    "target": "absolute_forward_return", "xsec_demean": False, "horizon": 5,
    "label_mode": "winsorize", "max_abs_label_return": 0.5,
    "dataset_version": "ds_20260830", "feature_version": "alpha_basic_v1",
}
BASIS_DEMEAN = {**BASIS_ABS, "target": "xsec_demean", "xsec_demean": True}


def _candidate(rmse: float, basis: dict | None) -> dict:
    """构造一个 RankIC/ICIR 都达标的候选，只让 RMSE 成为争议点。"""
    c: dict = {
        "valid_rank_ic": 0.12, "valid_icir": 0.5, "valid_ic": 0.10,
        "valid_rmse": rmse,
    }
    if basis is not None:
        c["train_basis"] = basis
    return c


def _prod_row(rmse: float, basis: dict | None) -> dict:
    """生产模型在门禁里的形态 = 注册表行（口径在 params_json 字符串里）。"""
    row: dict = {
        "valid_rank_ic": 0.10, "valid_icir": 0.4, "valid_ic": 0.08,
        "valid_rmse": rmse,
    }
    if basis is not None:
        row["params_json"] = json.dumps({"kept_features": [], "split": {}, "basis": basis})
    return row


# ---------------- training_basis 提取 ----------------

def test_training_basis_from_candidate_dict() -> None:
    assert training_basis(_candidate(0.06, BASIS_DEMEAN)) == BASIS_DEMEAN


def test_training_basis_from_registry_row_params_json() -> None:
    assert training_basis(_prod_row(0.07, BASIS_ABS)) == BASIS_ABS


def test_training_basis_returns_none_for_legacy_artifacts() -> None:
    """旧产物没有口径 ⇒ 返回 None（调用方按"不可比"处理）。"""
    assert training_basis({"valid_rmse": 0.07}) is None
    assert training_basis({"params_json": json.dumps({"kept_features": []})}) is None
    assert training_basis({"params_json": "not-json"}) is None
    assert training_basis(None) is None


# ---------------- 可比性判定 ----------------

def test_same_basis_is_comparable() -> None:
    assert basis_mismatch_reason(BASIS_ABS, BASIS_ABS) is None
    assert basis_mismatch_reason(BASIS_DEMEAN, BASIS_DEMEAN) is None


def test_xsec_demean_difference_is_incomparable() -> None:
    why = basis_mismatch_reason(BASIS_DEMEAN, BASIS_ABS)
    assert why is not None and "xsec_demean" in why, why
    assert basis_mismatch_reason(BASIS_ABS, BASIS_DEMEAN) is not None


def test_other_basis_differences_are_incomparable() -> None:
    """horizon / label_mode / dataset_version 不同同样不可比。"""
    for key, val in (("horizon", 10), ("label_mode", "clip"),
                     ("dataset_version", "ds_other")):
        assert basis_mismatch_reason({**BASIS_ABS, key: val}, BASIS_ABS) is not None, key


def test_missing_basis_on_one_side_is_incomparable() -> None:
    """只有一边记了口径 ⇒ 不能证明可比 ⇒ 不可比（诚实优先）。"""
    assert basis_mismatch_reason(BASIS_ABS, None) is not None
    assert basis_mismatch_reason(None, BASIS_ABS) is not None
    # 两边都没有（全是旧产物）：保持历史行为，不额外拦
    assert basis_mismatch_reason(None, None) is None


# ---------------- 门禁行为 ----------------

def test_gate_skips_rmse_when_basis_differs() -> None:
    """核心断言：口径不同时，RMSE 恶化 1.25× **不再**导致拒绝。"""
    cand = _candidate(0.05998, BASIS_DEMEAN)      # 更优的目标函数
    prod = _prod_row(0.07411, BASIS_ABS)          # 绝对收益目标
    d = evaluate_candidate(cand, prod, DEFAULT_PROMOTE_POLICY)

    assert d.checks["rmse_vs_prod"]["skipped"] is True, d.checks
    assert d.checks["rmse_vs_prod"]["pass"] is None
    assert d.checks["basis_mismatch"]["pass"] is None
    assert "xsec_demean" in d.checks["basis_mismatch"]["reason"]
    assert d.promote is True, (
        f"口径不同时不应因 RMSE 判负；实际 reason={d.reason}，checks={d.checks}")


def test_gate_still_rejects_rmse_worsen_same_basis() -> None:
    """不放松：口径相同时 RMSE 超限仍必须拒绝。"""
    cand = _candidate(0.07411 * 1.25, BASIS_ABS)
    prod = _prod_row(0.07411, BASIS_ABS)
    d = evaluate_candidate(cand, prod, DEFAULT_PROMOTE_POLICY)
    assert d.promote is False
    assert "恶化超限" in d.reason
    assert d.checks["rmse_vs_prod"]["pass"] is False
    assert d.checks["basis_mismatch"]["pass"] is True


def test_gate_skips_rmse_for_legacy_prod_with_recorded_candidate() -> None:
    """生产是旧产物（无口径）、候选已记口径 ⇒ 跳过 RMSE（无法证明可比）。"""
    d = evaluate_candidate(_candidate(0.20, BASIS_ABS), _prod_row(0.07, None),
                           DEFAULT_PROMOTE_POLICY)
    assert d.checks["rmse_vs_prod"]["skipped"] is True
    assert d.promote is True, d.reason


def test_gate_legacy_both_sides_keeps_historical_behavior() -> None:
    """两边都无口径：维持历史行为（仍比 RMSE），避免既有链路行为突变。"""
    d = evaluate_candidate(_candidate(0.20, None), _prod_row(0.07, None),
                           DEFAULT_PROMOTE_POLICY)
    assert d.promote is False and "恶化超限" in d.reason


def test_gate_rank_ic_still_guards_when_basis_differs() -> None:
    """跳过 RMSE ≠ 放行：RankIC 劣于生产仍必须拒绝。"""
    cand = _candidate(0.05, BASIS_DEMEAN)
    cand["valid_rank_ic"] = 0.05                  # 低于生产 0.10
    d = evaluate_candidate(cand, _prod_row(0.07411, BASIS_ABS), DEFAULT_PROMOTE_POLICY)
    assert d.promote is False and "valid_rank_ic 劣于生产" in d.reason


def test_train_lgbm_persists_basis() -> None:
    """源码级钉死：训练侧必须写出 train_basis（否则门禁永远拿不到口径）。"""
    src = (BACKEND_ROOT / "app" / "ml" / "train_lgbm.py").read_text(encoding="utf-8")
    assert '"train_basis": train_basis' in src, "metrics.json 未落盘 train_basis"
    assert 'merged_params["train_basis"]' in src, "params.json 未落盘 train_basis"
    reg = (BACKEND_ROOT / "app" / "ml" / "registry.py").read_text(encoding="utf-8")
    assert '"basis": metrics.get("train_basis")' in reg, "注册表未落盘口径"


# ---------------- P1-48：水平偏置维度 ----------------

def _level(mean: float, *, std: float = 0.006, n: int = 250,
           label_mean: float = 0.0, se: float = 4e-4) -> dict:
    return {"n": n, "pred_level_mean": mean, "pred_level_std": std,
            "label_level_mean": label_mean, "label_level_std": 0.05,
            "level_bias": mean - label_mean, "level_bias_se": se,
            "unit": "forward_return"}


def _cand_with_level(level: dict | None) -> dict:
    c = _candidate(0.06, None)
    if level is not None:
        c["pred_level"] = level
    return c


def _prod_with_level(level: dict | None) -> dict:
    row = _prod_row(0.07, None)
    if level is not None:
        row["params_json"] = json.dumps({"kept_features": [], "split": {},
                                         "pred_level": level})
    return row


def test_prediction_level_from_both_shapes() -> None:
    """候选（metrics 形态）与注册表行（params_json 形态）都能取到水平统计。"""
    lv = _level(-0.0016)
    assert prediction_level(_cand_with_level(lv)) == lv
    assert prediction_level(_prod_with_level(lv)) == lv
    # 旧产物：如实返回 None（"无法判定"），不猜
    assert prediction_level(_cand_with_level(None)) is None
    assert prediction_level({"params_json": json.dumps({"kept_features": []})}) is None


def test_pred_level_check_detects_bias_and_shift_but_only_discloses() -> None:
    """**P1-48 本体**：水平偏置被看见（pass=False），但默认策略只披露不拒绝。"""
    from app.ml.registry import pred_level_check

    cand = _cand_with_level(_level(-0.02))          # 水平整体下移 2%
    prod = _prod_with_level(_level(-0.0016))
    lv = pred_level_check(cand, prod, DEFAULT_PROMOTE_POLICY)
    assert lv["available"] is True
    assert lv["enforced"] is False
    assert lv["calibration"]["pass"] is False, lv["calibration"]
    assert lv["shift_vs_prod"]["pass"] is False, lv["shift_vs_prod"]
    # 默认只披露：不下硬判决
    assert lv["pass"] is None
    assert "校准偏差显著" in lv["fail_reasons"]

    # 端到端：默认策略仍 promote（RankIC/ICIR/RMSE 都达标），但 checks 里看得见
    d = evaluate_candidate(cand, prod, DEFAULT_PROMOTE_POLICY)
    assert d.promote is True, d.reason
    assert d.checks["pred_level_bias"]["calibration"]["pass"] is False


def test_pred_level_gate_enforced_rejects_level_shift() -> None:
    """置 `enforce_pred_level_gate=True` 后，水平漂移显著的候选必须被拒。"""
    from app.ml.registry import pred_level_check

    pol = PromotePolicy(min_valid_rank_ic=-1.0, min_valid_icir=-1.0,
                        rank_ic_tolerance=1e9, icir_tolerance=1e9,
                        max_rmse_worsen_ratio=1e9, enforce_pred_level_gate=True)
    cand = _cand_with_level(_level(-0.02))
    prod = _prod_with_level(_level(-0.0016))
    lv = pred_level_check(cand, prod, pol)
    assert lv["enforced"] is True and lv["pass"] is False

    d = evaluate_candidate(cand, prod, pol)
    assert d.promote is False
    assert "未通过预测水平门禁" in d.reason


def test_pred_level_gate_enforced_passes_calibrated_candidate() -> None:
    """不放松也不误杀：水平校准良好的候选在强制档下仍必须通过。"""
    from app.ml.registry import pred_level_check

    pol = PromotePolicy(min_valid_rank_ic=-1.0, min_valid_icir=-1.0,
                        rank_ic_tolerance=1e9, icir_tolerance=1e9,
                        max_rmse_worsen_ratio=1e9, enforce_pred_level_gate=True)
    lv = _level(-0.0002, se=4e-4)                    # 偏差 0.5×SE，远小于 2×SE
    assert pred_level_check(_cand_with_level(lv), _prod_with_level(lv), pol)["pass"] is True
    assert evaluate_candidate(_cand_with_level(lv), _prod_with_level(lv), pol).promote is True


def test_pred_level_gate_legacy_artifacts_are_not_rejected() -> None:
    """旧产物（未落盘水平统计）⇒ 无法判定 ⇒ 既不拒绝也不冒充通过。"""
    from app.ml.registry import pred_level_check

    pol = PromotePolicy(min_valid_rank_ic=-1.0, min_valid_icir=-1.0,
                        rank_ic_tolerance=1e9, icir_tolerance=1e9,
                        max_rmse_worsen_ratio=1e9, enforce_pred_level_gate=True)
    lv = pred_level_check(_cand_with_level(None), _prod_with_level(None), pol)
    assert lv["available"] is False and lv["pass"] is None
    assert "未披露预测水平" in lv["reason"]
    d = evaluate_candidate(_cand_with_level(None), _prod_with_level(None), pol)
    assert d.promote is True, d.reason


def test_train_lgbm_persists_pred_level() -> None:
    """源码级钉死：预测水平统计必须落 metrics 且随注册表行落盘（P1-48）。"""
    src = (BACKEND_ROOT / "app" / "ml" / "train_lgbm.py").read_text(encoding="utf-8")
    assert '"pred_level": pred_level' in src, "metrics.json 未落盘 pred_level"
    assert '"pred_level": metrics.get("pred_level")' in (
        BACKEND_ROOT / "app" / "ml" / "registry.py").read_text(encoding="utf-8"), \
        "注册表未落盘 pred_level"
    infer_src = (BACKEND_ROOT / "app" / "ml" / "infer.py").read_text(encoding="utf-8")
    assert "def prediction_return_basis" in infer_src, "infer 未提供 return_basis 披露"
    pred_src = (BACKEND_ROOT / "app" / "ml" / "predict.py").read_text(encoding="utf-8")
    assert '"return_basis": _return_basis(model_dir)' in pred_src, \
        "predict 响应未披露 return_basis"