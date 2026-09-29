"""审计 2026-09-18 §8.2 第 8 项定向用例。

覆盖四条：
    - **P1-7** ``promote_model`` 对已 promote 版本（``model_path`` 已在 ``prod/``）
      的 ``shutil.copy2(src, dst)``（src == dst）抛**未捕获** ``SameFileError``；
    - **P1-6** 回滚通道缺失（``rank_ic_tolerance=0`` ⇒ "IC 更差的旧版本"恒被拒）；
    - **R14** 门禁不校验 ``feature_version/dataset_version/验证区间`` 可比性；
    - **T8** 验证窗口 regime 化（多窗口切分 + 跨窗口符号一致性 + ``max(0.002, 2·SE)``）。

用例不依赖 LightGBM 训练（promote 只做文件复制 + 注册表指针切换），
唯一例外是 T8 的"落盘闭环"用例（真训一个微型模型，验证训练侧披露）。
"""
from __future__ import annotations

import json
import shutil
import sys
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

BACKEND_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(BACKEND_ROOT))

from app.ml.registry import (  # noqa: E402
    DEFAULT_PROMOTE_POLICY, ModelRegistryError, PromotePolicy,
    candidate_regime_windows, clear_registry, count_production, evaluate_candidate,
    exp_root, get_model, get_production, lineage_comparability, promote_model,
    prod_root, regime_check, regime_windows, register_candidate, rollback_model,
)

PERMISSIVE = dict(min_valid_rank_ic=-1.0, min_valid_icir=-1.0,
                  rank_ic_tolerance=1e9, icir_tolerance=1e9,
                  max_rmse_worsen_ratio=1e9)


@pytest.fixture(autouse=True)
def _clean_registry():
    clear_registry()
    yield


# ------------------------------------------------------------------ 造数据
def _basis(feature_version: str = "alpha_basic_v1",
           dataset_version: str = "ds_a") -> dict:
    return {"target": "absolute_forward_return", "xsec_demean": False, "horizon": 5,
            "label_mode": "winsorize", "max_abs_label_return": 0.5,
            "dataset_version": dataset_version, "feature_version": feature_version}


def _make_candidate(version: str, *, rank_ic: float = 0.05, icir: float = 0.30,
                    rmse: float = 0.06, ic: float = 0.04,
                    feature_version: str = "alpha_basic_v1",
                    dataset_version: str = "ds_a",
                    valid: tuple[str, str] = ("2024-01-01", "2024-06-30"),
                    regime: dict | None = None) -> dict:
    """在 ``exp/`` 造一个自洽的候选产物并登记（promote 不解析 booster，故用桩文件）。"""
    d = exp_root() / f"lgbm_v1_{version}"
    d.mkdir(parents=True, exist_ok=True)
    (d / "model.lgbm").write_text("stub-booster", encoding="utf-8")
    (d / "features.json").write_text(json.dumps(["f1", "f2"]), encoding="utf-8")
    (d / "params.json").write_text("{}", encoding="utf-8")
    (d / "metrics.json").write_text("{}", encoding="utf-8")
    metrics: dict = {
        "split": {"train_start": "2022-01-04", "train_end": "2023-12-29",
                  "valid_start": valid[0], "valid_end": valid[1],
                  "test_start": "2024-07-01"},
        "valid_rank_ic": rank_ic, "valid_icir": icir, "valid_rmse": rmse,
        "valid_ic": ic, "kept_features": ["f1", "f2"],
        "train_basis": _basis(feature_version, dataset_version),
    }
    if regime is not None:
        metrics["valid_regime_windows"] = regime
    register_candidate("lgbm_v1", version, metrics, d,
                       feature_version=feature_version,
                       dataset_version=dataset_version)
    rec = get_model("lgbm_v1", version)
    assert rec is not None
    return rec


def _row(rank_ic: float = 0.12, icir: float = 0.5, rmse: float = 0.06,
         feature_version: str | None = "alpha_basic_v1",
         dataset_version: str | None = "ds_a",
         valid: tuple[str, str] | None = ("2024-01-01", "2024-06-30"),
         regime: dict | None = None) -> dict:
    """门禁入参形态（注册表行 / dict 均可）——只放本用例关心的字段。"""
    row: dict = {"valid_rank_ic": rank_ic, "valid_icir": icir, "valid_rmse": rmse,
                 "valid_ic": 0.05}
    if feature_version is not None:
        row["feature_version"] = feature_version
    if dataset_version is not None:
        row["dataset_version"] = dataset_version
    if valid is not None:
        row["valid_start"], row["valid_end"] = valid
    if regime is not None:
        row["valid_regime_windows"] = regime
    return row


# ================================================================== P1-7
def test_samefileerror_is_reproduced_by_the_old_copy_loop():
    """**修前行为复现**：产物已在 prod/ 时，旧循环 ``copy2(src, dst)`` 的 src==dst
    ⇒ ``shutil.SameFileError``（该异常在 promote_model 里**未捕获**，CLI 直接崩）。
    """
    _make_candidate("v1")
    res = promote_model("lgbm_v1", "v1", by="pytest")
    assert res["promoted"], res["reason"]

    dst_dir = prod_root() / "lgbm_v1_v1"
    assert dst_dir.exists()
    # 旧实现（修复前 registry.py:384-387）对每个在 prod/ 里已存在的产物都会走到这里：
    for name in ("model.lgbm", "features.json", "params.json", "metrics.json"):
        with pytest.raises(shutil.SameFileError):
            shutil.copy2(dst_dir / name, dst_dir / name)


def test_same_file_helper_and_copy_guard():
    """P1-7 的最小单元：equal-path 判定必须为真，且同路径复制不抛异常。"""
    from app.ml.registry import _copy_artifacts, _same_file

    d = exp_root() / "lgbm_v1_samefile"
    d.mkdir(parents=True, exist_ok=True)
    f = d / "model.lgbm"
    f.write_text("stub-booster", encoding="utf-8")
    assert _same_file(f, f) is True
    assert _same_file(f, d / "other.lgbm") is False
    assert _copy_artifacts(f, d) == f  # 同目录 ⇒ 幂等跳过，不抛 SameFileError


def test_promote_current_production_version_is_idempotent_after_fix():
    """修后：对**当前生产版本**再 promote 一次必须幂等成功（不再 SameFileError），
    且注册表指针/产物不变、生产模型仍唯一。
    """
    _make_candidate("v1")
    assert promote_model("lgbm_v1", "v1", by="pytest")["promoted"]
    rec = get_production()
    path_before = rec["model_path"]
    # 此刻 src（注册表 model_path）== dst（prod/<name>_<ver>/model.lgbm）
    assert Path(path_before) == prod_root() / "lgbm_v1_v1" / "model.lgbm"

    res = promote_model("lgbm_v1", "v1", by="pytest", reason="重复 promote（幂等）")
    assert res["promoted"] is True, f"修前此处抛 SameFileError：{res}"
    assert count_production() == 1
    assert get_production()["model_path"] == path_before
    assert Path(path_before).exists()
    assert get_production()["promote_reason"] == "重复 promote（幂等）"


def test_repromote_archived_version_whose_artifacts_already_in_prod():
    """a → b → 再 promote a：a 的产物已在 prod/（src==dst）也必须幂等成功。"""
    _make_candidate("a", rank_ic=0.05)
    _make_candidate("b", rank_ic=0.06)
    assert promote_model("lgbm_v1", "a", by="pytest")["promoted"]
    assert promote_model("lgbm_v1", "b", by="pytest")["promoted"]
    assert get_production()["version"] == "b"

    res = promote_model("lgbm_v1", "a", by="pytest",
                        policy=PromotePolicy(**PERMISSIVE))
    assert res["promoted"], res["reason"]
    assert get_production()["version"] == "a"
    assert count_production() == 1


# ================================================================== P1-6
def test_p1_6_relative_gate_really_refuses_rollback():
    """P1-6 现象确认：默认门禁下"IC 更差的旧版本"**恒被拒**（无回滚通道）。"""
    _make_candidate("old", rank_ic=0.0920, icir=0.30)
    _make_candidate("new", rank_ic=0.1020, icir=0.45)
    assert promote_model("lgbm_v1", "old", by="pytest")["promoted"]
    assert promote_model("lgbm_v1", "new", by="pytest")["promoted"]

    d = evaluate_candidate(get_model("lgbm_v1", "old"), get_production(),
                           DEFAULT_PROMOTE_POLICY)
    assert d.promote is False
    assert "valid_rank_ic 劣于生产" in d.reason, d.reason
    # 默认容忍度必须仍是 0（不得为了让门禁通过而放松阈值）
    assert DEFAULT_PROMOTE_POLICY.rank_ic_tolerance == 0.0
    assert DEFAULT_PROMOTE_POLICY.icir_tolerance == 0.0


def test_rollback_channel_restores_known_good_version_and_discloses_delta():
    _make_candidate("old", rank_ic=0.0920, icir=0.30)
    _make_candidate("new", rank_ic=0.1020, icir=0.45)
    assert promote_model("lgbm_v1", "old", by="pytest")["promoted"]
    assert promote_model("lgbm_v1", "new", by="pytest")["promoted"]

    res = rollback_model("lgbm_v1", "old", by="pytest", reason="新版线上 IC 劣化")
    assert res["promoted"] is True and res["rollback"] is True
    assert res["previous_production"] == "new"
    assert get_production()["version"] == "old"
    assert count_production() == 1
    assert get_production()["promote_reason"].startswith("rollback:")
    assert "新版线上 IC 劣化" in get_production()["promote_reason"]
    assert get_production()["promoted_by"] == "pytest"
    # 文件仍在 prod/（幂等路径），且注册表指向真实产物
    assert Path(get_production()["model_path"]).exists()

    rb = res["checks"]["rollback"]
    assert rb["gate"] == "skipped_by_rollback_channel"
    assert rb["previous_production"] == "new"
    assert rb["delta_valid_rank_ic"] == pytest.approx(0.0920 - 0.1020, abs=1e-6)
    # 回滚"如果走普通门禁会被拒"这一事实必须被记录下来（可审计）
    assert rb["gate_decision_if_promoted"]["promote"] is False


def test_rollback_is_idempotent_and_requires_reason():
    _make_candidate("old", rank_ic=0.0920)
    _make_candidate("new", rank_ic=0.1020)
    assert promote_model("lgbm_v1", "old", by="pytest")["promoted"]
    assert promote_model("lgbm_v1", "new", by="pytest")["promoted"]
    assert rollback_model("lgbm_v1", "old", by="pytest", reason="第一次")["promoted"]

    again = rollback_model("lgbm_v1", "old", by="pytest", reason="第二次")
    assert again["promoted"] is True
    assert again["checks"]["rollback"]["delta_valid_rank_ic"] == 0.0
    assert count_production() == 1

    with pytest.raises(ModelRegistryError) as ei:
        rollback_model("lgbm_v1", "old", by="pytest", reason="   ")
    assert "reason" in str(ei.value)


def test_rollback_rejects_never_promoted_candidate_and_missing_production():
    """从未过门禁的候选不得借回滚通道上线（那才是放松阈值）。"""
    _make_candidate("never", rank_ic=0.20)
    _make_candidate("prod", rank_ic=0.10)
    assert promote_model("lgbm_v1", "prod", by="pytest")["promoted"]

    with pytest.raises(ModelRegistryError) as ei:
        rollback_model("lgbm_v1", "never", by="pytest", reason="绕过门禁")
    assert "从未被提升" in str(ei.value)

    clear_registry()
    _make_candidate("only", rank_ic=0.10)
    with pytest.raises(ModelRegistryError) as ei2:
        rollback_model("lgbm_v1", "only", by="pytest", reason="无生产模型")
    assert "没有生产模型" in str(ei2.value)


def test_cli_exposes_rollback_channel():
    """入口可达性：回滚通道必须能从 CLI 走到（否则等于没修）。"""
    src = (BACKEND_ROOT / "scripts" / "promote_model.py").read_text(encoding="utf-8")
    assert '"--rollback"' in src
    assert "rollback_model(" in src


# ================================================================== R14
def test_dataset_version_mismatch_refuses_direct_comparison():
    """R14 核心：生产 ds_1133s…r2022 vs 候选 ds_1729s…r2018 不得直接比 IC。"""
    cand = _row(rank_ic=0.12, dataset_version="ds_1729s_r2018")
    prod = _row(rank_ic=0.09, dataset_version="ds_1133s_r2022")
    d = evaluate_candidate(cand, prod, DEFAULT_PROMOTE_POLICY)

    assert d.promote is False
    assert "不可比" in d.reason and "dataset_version" in d.reason, d.reason
    assert "rank_ic_vs_prod" not in d.checks, "不可比时不得再产出直接比较结果"
    cmp_info = d.checks["lineage_comparability"]
    assert cmp_info["pass"] is False
    assert "dataset_version" in cmp_info["version_mismatch"]


def test_feature_version_mismatch_refuses_direct_comparison():
    cand = _row(rank_ic=0.12, feature_version="alpha_basic_v2g")
    prod = _row(rank_ic=0.09, feature_version="alpha_basic_v1")
    d = evaluate_candidate(cand, prod, DEFAULT_PROMOTE_POLICY)
    assert d.promote is False and "feature_version" in d.reason
    assert "rank_ic_vs_prod" not in d.checks


def test_incomparable_lineage_can_only_be_skipped_explicitly_and_is_disclosed():
    cand = _row(rank_ic=0.12, dataset_version="ds_new")
    prod = _row(rank_ic=0.09, dataset_version="ds_old")
    pol = PromotePolicy(allow_incomparable_lineage=True)
    d = evaluate_candidate(cand, prod, pol)

    assert d.promote is True, d.reason
    assert d.checks["relative_gate"]["skipped"] is True
    assert d.checks["relative_gate"]["pass"] is None
    assert d.checks["relative_gate"]["keys"] == ["dataset_version"]
    assert "rank_ic_vs_prod" not in d.checks  # 跳过 ≠ 偷偷比了一个不可比的数


def test_incomparable_with_regime_evidence_switches_criterion_not_hard_reject():
    """R14 × T8 闭环：不可比时**换判据**（跨 regime），既不比不可比的数也不硬冻结。"""
    good = regime_windows(_pairs([0.08, 0.09, 0.07, 0.10, 0.06, 0.11, 0.09, 0.12]),
                          n_windows=4)
    cand = _row(rank_ic=0.0871, dataset_version="ds_new_r2026", regime=good)
    prod = _row(rank_ic=0.1020, dataset_version="ds_old_r2022")

    d = evaluate_candidate(cand, prod, DEFAULT_PROMOTE_POLICY)
    assert d.promote is True, d.reason
    assert "rank_ic_vs_prod" not in d.checks          # 不可比的数一个都没比
    assert d.checks["relative_gate"]["substituted_by"] == "regime_windows"
    assert d.checks["regime_windows"]["enforced"] is True   # 替代判据强制生效
    assert d.checks["regime_windows"]["pass"] is True

    bad = regime_windows(_pairs([0.30, 0.25, -0.05, -0.03, -0.02, 0.10, -0.04, -0.01]),
                         n_windows=4)
    refused = evaluate_candidate(
        _row(rank_ic=0.0871, dataset_version="ds_new_r2026", regime=bad), prod,
        DEFAULT_PROMOTE_POLICY)
    assert refused.promote is False, "替代判据不通过就必须拒绝（跳过 ≠ 放行）"
    assert "跨 regime 判据" in refused.reason
    assert "符号一致性不足" in refused.reason


def test_comparable_lineage_keeps_the_relative_gate_unchanged():
    """不放松：同口径下相对门禁行为与既有断言一致。"""
    prod = _row(rank_ic=0.10, icir=0.40, dataset_version="ds_a")
    worse = evaluate_candidate(_row(rank_ic=0.05, icir=0.50), prod,
                               DEFAULT_PROMOTE_POLICY)
    assert worse.promote is False and "valid_rank_ic 劣于生产" in worse.reason
    assert worse.checks["rank_ic_vs_prod"]["pass"] is False

    worse_icir = evaluate_candidate(_row(rank_ic=0.12, icir=0.20), prod,
                                    DEFAULT_PROMOTE_POLICY)
    assert worse_icir.promote is False and "valid_icir 劣于生产" in worse_icir.reason

    better = evaluate_candidate(_row(rank_ic=0.12, icir=0.50), prod,
                                DEFAULT_PROMOTE_POLICY)
    assert better.promote is True, better.reason
    assert better.checks["rank_ic_vs_prod"]["pass"] is True
    assert better.checks["lineage_comparability"]["pass"] is True


def test_legacy_rows_without_lineage_keep_historical_behavior():
    """两边都没记（旧产物）⇒ 保持历史行为（仍比），但要披露为"不可证明"。"""
    cand = _row(rank_ic=0.12, feature_version=None, dataset_version=None, valid=None)
    prod = _row(rank_ic=0.09, feature_version=None, dataset_version=None, valid=None)
    d = evaluate_candidate(cand, prod, DEFAULT_PROMOTE_POLICY)
    assert d.promote is True, d.reason
    assert "rank_ic_vs_prod" in d.checks

    one_sided = evaluate_candidate(
        _row(rank_ic=0.12, dataset_version="ds_new"), _row(rank_ic=0.09,
                                                           dataset_version=None),
        DEFAULT_PROMOTE_POLICY)
    assert one_sided.checks["lineage_comparability"]["pass"] is None
    assert "dataset_version" in one_sided.checks["lineage_comparability"]["unproven"]
    assert "rank_ic_vs_prod" in one_sided.checks, "单向缺记不改变历史行为"


def test_valid_window_mismatch_is_disclosed_by_default_and_hard_when_required():
    """验证区间维度：默认**披露**（滚动重训必然换验证段，默认硬拒会让门禁空转），
    置 ``require_identical_valid_window=True`` 后升级为硬拒绝。
    """
    cand = _row(rank_ic=0.12, valid=("2024-07-01", "2024-12-31"))
    prod = _row(rank_ic=0.09, valid=("2024-01-01", "2024-06-30"))
    d = evaluate_candidate(cand, prod, DEFAULT_PROMOTE_POLICY)
    assert d.checks["lineage_comparability"]["valid_window_differs"] is True
    assert d.checks["lineage_comparability"]["pass"] is False
    assert d.promote is True, d.reason
    assert "rank_ic_vs_prod" in d.checks

    strict = evaluate_candidate(cand, prod,
                                PromotePolicy(require_identical_valid_window=True))
    assert strict.promote is False and "valid_start" in strict.reason


def test_lineage_comparability_helper_contract():
    same = lineage_comparability(_row(), _row())
    assert same["pass"] is True and same["reason"] is None
    assert lineage_comparability(_row(dataset_version="x"), _row(dataset_version="y"))[
        "version_mismatch"]
    assert lineage_comparability(None, None)["pass"] is True


# ================================================================== T8
def _pairs(values: list[float]) -> list[tuple[str, float]]:
    return [(f"2024-{i + 1:02d}-01", v) for i, v in enumerate(values)]


def test_regime_windows_splits_and_discloses():
    """① 窗口切分/披露：几个窗口、哪段 regime、每段均值与符号。"""
    rw = regime_windows(_pairs([0.10, 0.12, -0.04, -0.02, 0.08, 0.06, 0.09, 0.05]),
                        n_windows=4)
    assert rw["available"] is True
    assert rw["n_windows"] == 4
    assert len(rw["windows"]) == 4
    assert [w["n"] for w in rw["windows"]] == [2, 2, 2, 2]
    assert rw["windows"][0]["start"] == "2024-01-01"
    assert rw["windows"][0]["end"] == "2024-02-01"
    assert rw["windows"][0]["mean_rank_ic"] == pytest.approx(0.11)
    assert [w["sign"] for w in rw["windows"]] == [1, -1, 1, 1]
    assert rw["positive_windows"] == 3
    assert rw["positive_ratio"] == pytest.approx(0.75)
    assert rw["mean_rank_ic"] == pytest.approx(0.055)
    assert rw["se_across_windows"] > 0
    # 门槛按样本/离散度归一：max(0.002, 2·SE)
    assert rw["threshold"] == pytest.approx(max(0.002, 2 * rw["se_across_windows"]))
    assert rw["pass_level"] == (rw["mean_rank_ic"] >= rw["threshold"])
    assert json.dumps(rw)  # 必须可落盘（metrics.json / params_json）


def test_regime_windows_unavailable_cases_are_explicit():
    assert regime_windows([])["available"] is False
    assert regime_windows([0.1, 0.2, 0.3], n_windows=4)["available"] is False
    assert regime_windows([float("nan"), 0.2, 0.3, 0.4], n_windows=2)[
        "n_observations"] == 3
    single = regime_windows(_pairs([0.1, 0.2]), n_windows=1)
    assert single["available"] is True
    assert single["se_across_windows"] is None
    assert single["threshold"] == pytest.approx(0.002)


def test_regime_gate_is_disclosed_by_default_and_decisive_when_enforced():
    """② 门禁加入跨窗口符号一致性判据并**默认披露**；不得默认放水也不得默认误杀。"""
    bad = regime_windows(_pairs([0.30, 0.25, -0.05, -0.03, -0.02, 0.10, -0.04, -0.01]),
                         n_windows=4)
    assert bad["positive_windows"] == 2  # 2/4 正向 < 3/4 ⇒ 符号一致性不足

    cand = _row(rank_ic=0.12, regime=bad)
    prod = _row(rank_ic=0.09)

    disclosed = regime_check({"valid_regime_windows": bad}, DEFAULT_PROMOTE_POLICY)
    assert disclosed["available"] is True
    assert disclosed["pass_sign"] is False
    assert disclosed["pass"] is None and disclosed["enforced"] is False
    assert DEFAULT_PROMOTE_POLICY.enforce_regime_gate is False

    d = evaluate_candidate(cand, prod, DEFAULT_PROMOTE_POLICY)
    assert d.promote is True, d.reason
    assert d.checks["regime_windows"]["pass"] is None
    assert d.checks["regime_windows"]["pass_sign"] is False
    assert d.checks["regime_windows"]["positive_windows"] == 2

    strict = evaluate_candidate(cand, prod, PromotePolicy(enforce_regime_gate=True))
    assert strict.promote is False
    assert "跨 regime 判据" in strict.reason and "符号一致性不足" in strict.reason
    assert strict.checks["regime_windows"]["pass"] is False


def test_enforced_regime_gate_passes_consistent_candidate():
    good = regime_windows(_pairs([0.08, 0.09, 0.07, 0.10, 0.06, 0.11, 0.09, 0.12]),
                          n_windows=4)
    assert good["positive_windows"] == 4
    d = evaluate_candidate(_row(rank_ic=0.12, regime=good), _row(rank_ic=0.09),
                           PromotePolicy(enforce_regime_gate=True))
    assert d.promote is True, d.reason
    assert d.checks["regime_windows"]["pass"] is True


def test_require_regime_windows_blocks_undisclosed_candidate():
    d = evaluate_candidate(_row(rank_ic=0.12), _row(rank_ic=0.09),
                           PromotePolicy(require_regime_windows=True))
    assert d.promote is False
    assert "未披露逐窗口 RankIC" in d.reason
    assert d.checks["regime_windows"]["available"] is False


def test_regime_windows_resolves_from_registry_row_params_json():
    """注册表行形态（params_json 字符串）也必须能取到 regime 披露。"""
    rw = regime_windows(_pairs([0.1, 0.2, 0.3, 0.4, 0.5, 0.6, 0.7, 0.8]), n_windows=4)
    row = {"params_json": json.dumps({"kept_features": [], "split": {},
                                      "basis": _basis(), "regime_windows": rw})}
    assert candidate_regime_windows(row) == rw
    assert candidate_regime_windows({"params_json": json.dumps({"kept_features": []})}) is None
    assert candidate_regime_windows(None) is None


# ---------------- T8 落盘闭环（真训一个微型模型） ----------------
def _ohlcv(seed: int = 3, n_symbols: int = 20, n_days: int = 320) -> pd.DataFrame:
    rng = np.random.default_rng(seed)
    dates = pd.bdate_range("2023-01-02", periods=n_days)
    frames = []
    for i in range(n_symbols):
        rets = np.zeros(n_days)
        for t in range(1, n_days):
            rets[t] = 0.25 * rets[t - 1] + 0.02 * rng.standard_normal()
        close = 10.0 * np.exp(np.cumsum(rets))
        frames.append(pd.DataFrame({
            "symbol": f"S{i:03d}.SZ", "date": dates,
            "open": close * 1.001, "high": close * 1.01, "low": close * 0.99,
            "close": close, "volume": 1e6, "amount": 1e7,
        }))
    return pd.concat(frames, ignore_index=True)


def test_train_lgbm_persists_regime_windows_end_to_end():
    """① 训练侧切分/披露 → 注册表落盘 → 门禁可取用（闭环，不靠人肉补字段）。"""
    from app.ml.features import build_factors
    from app.ml.train_lgbm import train_lgbm

    r = train_lgbm(build_factors(_ohlcv()), horizon=5, holdout_days=40, test_days=30,
                   gap_days=5, version_suffix="regime", num_boost_round=30,
                   stopping_rounds=10, min_abs_rank_ic=0.0, top_k=20, persist=True)

    rw = r["valid_regime_windows"]
    assert rw["available"] is True
    assert rw["n_windows"] == 4
    assert len(rw["windows"]) == 4
    assert all(w["start"] and w["end"] for w in rw["windows"])
    assert sum(w["n"] for w in rw["windows"]) == rw["n_observations"]

    metrics_file = Path(r["model_path"]).parent / "metrics.json"
    assert json.loads(metrics_file.read_text(encoding="utf-8"))["valid_regime_windows"] == rw

    rec = get_model("lgbm_v1", r["version"])
    assert candidate_regime_windows(rec) == rw, "注册表行必须能取到 regime 披露"
    assert rec["feature_version"] and rec["valid_start"] and rec["valid_end"], (
        "R14 的可比性需要 feature_version/验证区间落库")