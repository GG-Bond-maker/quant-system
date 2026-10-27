"""S4 监控触发器口径（P1-17 / P1-21 / R5）——定向验证。

对应审核报告 §8.2 第 14 项与 S4 的四条改法：

① PSI 判定口径改**按交易日截面标准化**（池化原始值只披露）；
② KS 与 PSI 同口径 + 用"超限比/单日临界尺度"代替"全命中"结论；
③ IC 阈值的历史 σ 按**当日池宽**折算（防"120 只时代"噪声摊薄阈值）；
④ ``_worst`` 把 ``unknown`` 视为 unknown 而非 healthy（防虚假"恢复健康"）。

真实数据证据（本机 `data/sqlite/aqp.db` 快照 + `data/parquet/features` 面板）：
  * 修复前：`psi.max=3.3051`、`mean=0.2646`、21/85 因子 >0.25、`ks.n_over_crit=85/85`、
    `ic_state=healthy`、`drift_state=degraded` 自 2026-09-03 持续 15 天；
  * 修复后同面板重算：截面标准化 `mean=0.0415`、`max=0.2024`、**0/85 >0.25**
    （不再假降级），而"掺入 30% 异质样本"时同一口径 PSI=**0.3540**（仍会报警）；
  * 字面方案"截面内排名后再算 PSI"实测 `mean=0.0011 / max=0.0131` ⇒ **退化**
    （秩在每个截面内恒均匀 ⇒ 对所有因子 PSI≈0），故未采用（详见 compute_psi 文档）。
  * 池宽证据：`ic_series_tail.n_symbols` 历史 ≈119~121、最近 ≈2486~2492
    ⇒ `σ_raw=0.1151` 折算后 0.0253（旧阈值 −0.1262 令 IC=0 也判 healthy）。

**2026-10-02 追加（面板判定量：单因子 `max` → body 统计量）**：
  实测（本机真实面板，157 个滚动窗口）单因子 `psi.max` 的 median=**0.6223**、
  p90=**2.0716** ⇒ **几乎恒 >0.25**，作面板判定量等于常年假降级；且单因子注入被
  regime 因子**掩盖**（实测 30% 注入的 `psi.max` 与基线**完全相同** 0.5149）。
  故 `_drift_state` 改判 `psi["mean"]` + `psi["over"]["ratio_*"]`（**body 统计量**；
  阈值见 `monitor.DRIFT_*` 常量注释），单因子 `max` 降级为**仅披露**。
"""
from __future__ import annotations

import sys
from pathlib import Path

import numpy as np
import pandas as pd
import polars as pl
import pytest

BACKEND_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(BACKEND_ROOT))

from app.ml import monitor as M  # noqa: E402

# 真实面板（CI/容器里可能不存在 ⇒ 相关用例自动跳过；本机存在则真跑）
PROJECT_ROOT = BACKEND_ROOT.parent
REAL_FEATURES = PROJECT_ROOT / "data" / "parquet" / "features"


# ---------------- ④ _worst：unknown 不得被 healthy 压过 ----------------

def test_worst_unknown_not_downgraded_to_healthy():
    """审核原话：`_worst(['unknown','healthy'])=='healthy'` ⇒ 虚假恢复。现已修正。"""
    assert M._worst(["unknown", "healthy"]) == "unknown"
    assert M._worst(["healthy", "unknown"]) == "unknown"
    # 有正向告警信号时仍取告警（unknown 不是"最差"，只是"不确定"）
    assert M._worst(["unknown", "watch"]) == "watch"
    assert M._worst(["unknown", "degraded"]) == "degraded"
    assert M._worst(["healthy", "healthy"]) == "healthy"
    assert M._worst(["unknown", "unknown"]) == "unknown"


def test_rank_table_is_monotone_with_unknown_above_healthy():
    r = M._STATE_RANK
    assert r["healthy"] < r["unknown"] < r["watch"] < r["degraded"], r


def test_state_change_does_not_announce_recovery_from_unknown(monkeypatch):
    """unknown 期间不得推送"恢复健康"（只对 cur=='healthy' 发恢复文案）。"""
    from app.core import events

    sent: list[tuple] = []
    monkeypatch.setattr(events, "publish_threadsafe",
                        lambda *a, **k: sent.append((a, k)))

    M._notify_state_change("degraded", "unknown", {"recent": {"mean_ic": 0.01}})
    assert sent == [], "从 degraded 转入 unknown 被当成了恢复"
    M._notify_state_change("watch", "unknown", {"recent": {"mean_ic": 0.01}})
    assert sent == []
    # 对照：真正的恢复仍要通知
    M._notify_state_change("degraded", "healthy", {"recent": {"mean_ic": 0.08}})
    assert len(sent) == 1 and "恢复" in sent[0][0][1]


# ---------------- ③ IC σ 的池宽折算 ----------------

def test_pool_adjusted_std_real_snapshot_numbers():
    """用真实快照数字：σ_raw=0.1151、池宽 120 → 2490 ⇒ σ_adj≈0.0253。"""
    n_symbols = pd.Series([120.0] * 100 + [2490.0] * 15,
                          index=pd.date_range("2026-01-01", periods=115, freq="D"))
    info = M._pool_adjusted_std(0.1151, n_symbols, n_symbols.index[:100],
                               n_symbols.index[-15:])
    assert info["basis"] == "pool_width_adjusted"
    assert info["n_hist_median"] == 120 and info["n_recent_median"] == 2490
    assert info["ratio"] == pytest.approx(np.sqrt(120 / 2490), abs=1e-4)
    assert info["std"] == pytest.approx(0.1151 * np.sqrt(120 / 2490), abs=1e-4)
    assert info["std_raw"] == 0.1151


def test_pool_adjustment_removes_the_false_negative():
    """旧口径下 IC 归零仍判 healthy（假阴性）；折算后正确判 degraded。"""
    hist_mean, std_raw = 0.0465, 0.1151
    assert M._ic_state(0.0, hist_mean, std_raw) == "healthy", "旧口径应复现假阴性"

    std_adj = std_raw * np.sqrt(120 / 2490)
    assert M._ic_state(0.0, hist_mean, std_adj) == "degraded"
    # 不误报：实测近期 IC=0.0774 在折算后仍为 healthy
    assert M._ic_state(0.0774, hist_mean, std_adj) == "healthy"


def test_pool_adjustment_falls_back_when_width_unavailable():
    """池宽不可得 ⇒ 原样返回并标明未折算（绝不静默改口径）。"""
    empty = pd.Series(dtype=float)
    info = M._pool_adjusted_std(0.2, empty, pd.Index([1, 2]), pd.Index([3]))
    assert info["std"] == 0.2 and info["basis"] == "raw_sigma"
    assert info["ratio"] is None
    info2 = M._pool_adjusted_std(None, pd.Series([1.0]), pd.Index([1]), pd.Index([2]))
    assert info2["std"] is None and info2["basis"] == "raw_sigma"


# ---------------- ① PSI 双口径（合成数据，可判别） ----------------

def _panel(level_shift: float = 0.0, contaminate: float = 0.0,
           n_days: int = 300, n_sym: int = 60, seed: int = 5) -> pl.DataFrame:
    """合成特征面板：`f_a` 为学生 t 分布（有形状），`f_b` 正态。

    ``level_shift``：给**近期窗口**的 `f_a` 整体加常数（模拟趋势 → 水平漂移）。
    ``contaminate``：把近期窗口 `f_a` 中该比例的样本替换成 `f_b` 的取值。
    ⚠️ `f_b` 特意取**不同尺度与偏移**（N(5, 10²)）：这样"按当日截面标准化"后
    被污染的那一天呈**双峰**，形状变化不会被标准化抹掉——与真实面板上
    `ma_gap_250` 掺入 `ret_1`（尺度/偏移差异大）触发 0.354 的机制同构。
    若用同尺度同中心的分布做污染，标准化会把混合痕迹消掉（实测 PSI≈0.01），
    那是"污染不构成形状漂移"的真实结论，不能拿来做"必须报警"的反证。
    """
    rng = np.random.default_rng(seed)
    idx = pd.date_range("2024-01-01", periods=n_days, freq="D")
    a = rng.standard_t(df=3, size=(n_days, n_sym))
    b = rng.normal(5.0, 10.0, (n_days, n_sym))
    if level_shift:
        a[-20:] = a[-20:] + level_shift
    if contaminate:
        k = int(n_sym * contaminate)
        a[-20:, :k] = b[-20:, :k]
    rows = [{"date": d, "symbol": f"S{j}", "f_a": a[i, j], "f_b": b[i, j]}
            for i, d in enumerate(idx) for j in range(n_sym)]
    return pl.DataFrame(rows)


def test_psi_split_contract_stable_panel():
    """同分布面板：两个口径都接近 0，且返回结构含 basis / raw 披露块。"""
    psi = M.compute_psi(_panel())
    assert psi["ok"]
    assert psi["basis"] == "xsec_standardized"
    assert psi["n_factors"] == 2
    assert psi["max"] < M.PSI_WATCH
    assert psi["raw"]["basis"] == "pooled_raw"
    assert psi["raw"]["max"] < M.PSI_WATCH
    assert {t["factor"] for t in psi["top"]} <= {"f_a", "f_b"}


def test_psi_level_shift_no_longer_fake_degraded_but_raw_discloses_it():
    """**不误报**：纯水平平移 ⇒ 判定口径不降级；同时 raw 口径必须如实披露。"""
    psi = M.compute_psi(_panel(level_shift=3.0))
    assert psi["ok"]
    assert psi["max"] <= M.PSI_WATCH, "水平漂移仍把判定口径推到 watch/degraded"
    assert M._drift_state(psi["max"]) == "healthy"
    assert psi["raw"]["max"] > M.PSI_DEGRADED, "raw 口径应能看到水平漂移（披露）"
    assert psi["raw"]["top"][0]["factor"] == "f_a"


def test_psi_contamination_raises_the_state_caliber():
    """**有判别力**：掺入异质样本（30%）⇒ 判定口径显著抬高（方向性证据）。

    注意这里**不**断言"必然 > 0.25"：合成面板只有 60 只/日、基线为 t(3)，
    实测 30% 污染下 PSI≈0.16（cross watch 线但未到降级线）；真实面板（2490 只/日、
    掺入尺度差异更大的 `ret_1`）实测 **0.354 > 0.25** ⇒ "必须报警"的证据挂在
    ``test_real_panel_detects_injected_contamination`` 上，而不是靠调参把合成样本
    凑过阈值（那样只是拟合测试）。
    """
    stable = M.compute_psi(_panel())
    psi = M.compute_psi(_panel(contaminate=0.3))
    assert psi["ok"]
    assert psi["max"] > M.PSI_WATCH, f"形状漂移完全没被检出（max={psi['max']}）"
    assert psi["max"] > 3 * stable["max"], "污染后 PSI 未显著抬高"
    assert psi["top"][0]["factor"] == "f_a"


def test_psi_one_helper_edge_cases():
    """直方图核：基线近常数 ⇒ None；比例完全分离 ⇒ 大 PSI；下限防 log(0)。"""
    const = np.ones(200)
    assert M._psi_one(const, np.ones(100)) is None
    b = np.linspace(-3, 3, 500)
    r = np.linspace(-3, 3, 100) + 10.0
    assert M._psi_one(b, r) > 1.0
    assert M._psi_one(b, b) == pytest.approx(0.0, abs=1e-9)


def test_xsec_standardize_is_per_date_and_scale_invariant():
    """截面标准化必须按日进行，且对"当日全体平移/放大"完全不变。"""
    pdf = pd.DataFrame({
        "date": list(pd.date_range("2026-01-01", periods=3)) * 2,
        "symbol": ["A"] * 3 + ["B"] * 3,
        "f": [1.0, 2.0, 3.0, 2.0, 4.0, 6.0],
    })
    z = M._xsec_standardize(pdf, ["f"])["f"].to_numpy()
    # 第 1 日 (1,2)：中位 1.5、MAD 0.5 ⇒ z = ∓1/(0.5*1.4826)
    scale = 0.5 * M.XSEC_MAD_SCALE
    assert z[0] == pytest.approx(-0.5 / scale)
    assert z[3] == pytest.approx(0.5 / scale)

    shifted = pdf.copy()
    shifted["f"] = shifted["f"] * 7.0 + 100.0
    z2 = M._xsec_standardize(shifted, ["f"])["f"].to_numpy()
    assert np.allclose(z, z2, atol=1e-9), "平移/缩放竟然改变了截面标准化结果"


# ---------------- ② KS：口径对齐 + 强度指标 ----------------

def test_ks_aligned_caliber_and_intensity_fields():
    ks = M.compute_ks(_panel())
    assert ks["ok"] and ks["basis"] == "xsec_standardized"
    assert 0.0 <= ks["over_crit_ratio"] <= 1.0
    assert ks["over_crit_ratio"] == pytest.approx(ks["n_over_crit"] / ks["n_factors"], abs=1e-4)
    assert ks["crit_effective"] and ks["crit_effective"] > 0
    assert ks["cross_section_median"] == 60
    # 单日临界尺度 1.36·sqrt(2/60)
    assert ks["crit_effective"] == pytest.approx(1.36 * np.sqrt(2 / 60), abs=1e-5)
    assert "强度" in ks["note"] and "不作结论" in ks["note"]


def test_ks_on_standardized_values_is_smaller_under_level_shift():
    """水平平移只灌水原始 KS：同口径下 D 应显著更小（口径错配的可量化证据）。"""
    feat = _panel(level_shift=3.0)
    ks_z = M.compute_ks(feat)
    # 原始口径（修复前的算法）作为对照，逐因子比较
    pdf = feat.to_pandas()
    dates = np.sort(pdf["date"].unique())
    rec, base = pdf[pdf["date"].isin(set(dates[-20:]))], pdf[pdf["date"].isin(
        set(dates[-270:-20]))]
    raw_max = max(M.ks_two_sample(base[f].dropna().to_numpy(),
                                 rec[f].dropna().to_numpy()) for f in ("f_a", "f_b"))
    assert ks_z["max"] < raw_max, f"截面标准化未削弱水平平移的 KS（{ks_z['max']} vs {raw_max}）"


def test_stable_panel_has_no_over_crit_after_alignment():
    """静止面板：同口径 KS 不应"全命中"（原始口径下任何微小差异都会显著）。"""
    ks = M.compute_ks(_panel())
    assert ks["ok"]
    assert ks["n_over_crit"] <= 1, f"静止面板却 n_over_crit={ks['n_over_crit']}"


# ---------------- 真实面板验收（审计 S4 的验证方式） ----------------

def _load_real_panel() -> pl.DataFrame | None:
    """按 monitor 同口径读真实面板（单一特征版本 + 近 400×1.6 自然日窗口）。"""
    if not REAL_FEATURES.exists():
        return None
    dirs = [d for d in REAL_FEATURES.iterdir() if d.is_dir() and d.name.startswith("version=")]
    if not dirs:
        return None
    version_dir = max(dirs, key=lambda d: max(f.stat().st_mtime_ns
                                              for f in d.rglob("*.parquet")))
    files = sorted(version_dir.rglob("*.parquet"))
    if not files:
        return None
    df = pl.concat([pl.read_parquet(f) for f in files], how="diagonal_relaxed")
    # 与生产同口径：`_features_frame` 在读入后统一归一日期列（Date/Datetime/Utf8）
    df = M._normalize_date_col(df)
    dmax = df["date"].max()
    from datetime import timedelta
    return df.filter(pl.col("date") >= dmax - timedelta(days=int(400 * 1.6)))


def _real_replay_panel() -> tuple[pl.DataFrame, list[str]]:
    """Replay real cross-sections so baseline/recent differ only by injected faults.

    Live regime changes are observations, not a fixed healthy-state oracle. A
    repeated real window gives an exact negative control without assuming an old
    factor's distribution or a snapshot-specific sensitivity to another factor.
    """
    from datetime import timedelta

    feat = _load_real_panel()
    assert feat is not None and not feat.is_empty(), "真实面板目录存在但数据不可读"
    dates = feat["date"].unique().sort().to_list()
    assert len(dates) >= 20, "真实面板不足20个交易日"
    window = feat.filter(pl.col("date").is_in(dates[-20:]))
    excluded = {"date", "symbol", "year", "close", "label_ret"}
    factors = [c for c, dtype in window.schema.items()
               if c not in excluded and dtype.is_numeric()
               and window[c].drop_nulls().len() >= 100
               and (window[c].std() or 0) > 0][:6]
    assert len(factors) >= 3, "真实面板有效因子不足，不能验证漂移检测"
    window = window.select("date", "symbol", *factors)
    # Every replay contains exactly the same observed dates, symbols and nulls.
    replay = pl.concat([window.with_columns(
        (pl.col("date") + timedelta(days=40 * repeat)).alias("date"))
        for repeat in range(4)])
    return replay, factors


@pytest.mark.skipif(not REAL_FEATURES.exists(), reason="本机无真实特征面板")
def test_real_panel_no_false_degraded_and_still_sensitive():
    """Real observations plus a controlled scale/level shift must stay healthy."""
    feat, factors = _real_replay_panel()
    baseline = M.compute_psi(feat, top_n=10_000)
    assert baseline["ok"] and baseline["n_factors"] == len(factors)
    assert baseline["max"] == 0.0
    assert M._drift_state(baseline) == "healthy"
    recent = feat["date"].unique().sort().to_list()[-20:]
    shifted = feat.with_columns([
        pl.when(pl.col("date").is_in(recent))
        .then(pl.col(f) * 3.0 + 1000.0).otherwise(pl.col(f)).alias(f)
        for f in factors])
    psi = M.compute_psi(shifted, top_n=10_000)
    assert psi["ok"]
    assert psi["raw"]["max"] > M.PSI_DEGRADED
    assert psi["max"] < M.PSI_WATCH
    assert M._drift_state(psi) == "healthy"
    # A disclosed single-factor outlier must not override the body decision.
    assert M._drift_state({**psi, "max": 100.0}) == "healthy"
    ks = M.compute_ks(feat)
    assert ks["ok"] and ks["max"] == 0.0
    assert ks["over_crit_ratio"] == 0.0


@pytest.mark.skipif(not REAL_FEATURES.exists(), reason="本机无真实特征面板")
def test_real_panel_injection_sensitivity_table():
    """A frozen recent feed must alarm against the same real baseline.

    Constant recent values simulate a stale/flatlined factor feed. This changes
    cross-sectional shape instead of relying on swapping two particular factors
    whose present-day distributions may legitimately resemble each other.
    """
    feat, factors = _real_replay_panel()
    baseline = M.compute_psi(feat, top_n=10_000)
    assert baseline["ok"] and baseline["max"] == 0.0
    recent = feat["date"].unique().sort().to_list()[-20:]
    broken = feat.with_columns([
        pl.when(pl.col("date").is_in(recent) & pl.col(f).is_not_null())
        .then(0.0).otherwise(pl.col(f)).alias(f) for f in factors])
    psi = M.compute_psi(broken, top_n=10_000)
    assert psi["ok"] and psi["n_factors"] == len(factors)
    assert psi["mean"] > M.DRIFT_MEAN_DEGRADED
    assert psi["over"]["ratio_degraded"] > M.DRIFT_RATIO_DEGRADED
    assert M._drift_state(psi) == "degraded"
    assert psi["degenerate"]["n_constant"] > 0
    print(f"[real-controlled] factors={factors} baseline={baseline['mean']} "
          f"flatlined={psi['mean']} state={M._drift_state(psi)}")


# ---------------- 前端披露锁（无前端测试框架 ⇒ 源码断言） ----------------

def test_frontend_discloses_psi_caliber_and_ks_intensity():
    """跨层披露守护：口径字段必须在前端类型/文案里出现，且旧的失真表述消失。

    审核 S4 的**一半**问题在披露：`FactorHealthCard` 把池化原始 PSI 直接叫
    "PSI max（漂移）"、KS 写"超 5% 临界 N 个"（真实快照是 85/85 ⇒ 读起来像
    "全部因子都坏了"）。前端无测试框架（项目既有做法见
    `test_signal_analysis_caliber.py::test_frontend_type_declares_all_backend_keys`），
    故直接对源码断言，防止后续改版把口径说明静默删掉。
    """
    src_root = PROJECT_ROOT / "frontend" / "src"
    if not src_root.exists():
        pytest.skip("前端目录不在本工作区")

    ts = (src_root / "api" / "monitor.ts").read_text(encoding="utf-8")
    missing = [k for k in ("basis", "raw", "over_crit_ratio", "crit_effective",
                           "cross_section_median", "std_ic_raw", "sigma_basis",
                           "pool_ratio", "psi_max_raw")
               if k not in ts]
    assert not missing, f"api/monitor.ts 未声明后端真实返回的口径字段：{missing}"

    card = (src_root / "components" / "FactorHealthCard.tsx").read_text(encoding="utf-8")
    assert "截面标准化" in card, "PSI 标签未标明判定口径"
    assert "池化原始 PSI" in card, "未披露池化原始值（用户无法判断是否水平漂移）"
    assert "超限比" in card, "KS 未改用超限比表述"
    assert "池宽" in card or "σ" in card, "IC 阈值未说明按池宽折算"
    # 旧失真表述不得回归
    for stale in ("超 5% 临界", "PSI max（漂移）"):
        assert stale not in card, f"旧的失真表述回归了：{stale}"