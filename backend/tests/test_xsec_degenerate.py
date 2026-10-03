"""`_xsec_standardize` 的 MAD==0 退化修复 —— 回归测试（QA 补充）。

背景（2026-10-02）：`rank_close_60` 等"离散档"因子在全市场普跌的截面里，
当日 MAD 恰好为 0，旧实现分母只剩 `+1e-12` ⇒ `z` 冲到 ~1e12 ⇒
污染 `_psi_one` 的等频分箱边界。修复：MAD 尺度 ≤ eps ⇒ 回退当日 std；
std 亦 ≤ eps（常数截面）⇒ z 置 0；退化计数经 `compute_psi` 的 `degenerate` 披露。

⚠️ 本文件是**新增回归覆盖**：修复交付时 `tests/` 下**没有**任何针对该退化分支的
测试（既有的 `test_xsec_standardize_is_per_date_and_scale_invariant` 用的是
MAD>0 的面板，旧/新实现都通过，**不构成对本修复的判别**）。故由 QA 补齐。

断言口径均为**行为**（z 的取值 / 计数结构），不绑定实现细节。
"""
from __future__ import annotations

import sys
from pathlib import Path

import numpy as np
import pandas as pd
import polars as pl

BACKEND_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(BACKEND_ROOT))

from app.ml import monitor as M  # noqa: E402


def _day(values: list[float], date: str = "2026-01-01") -> pd.DataFrame:
    return pd.DataFrame({
        "date": pd.to_datetime([date] * len(values)),
        "symbol": [f"S{i}" for i in range(len(values))],
        "f": values,
    })


# ---------------- 正常路径：MAD>0，z 与手算一致、计数全 0 ----------------

def test_normal_path_matches_hand_calc_and_reports_zero_degenerate():
    pdf = _day([1.0, 2.0, 3.0, 4.0, 5.0])          # median=3, MAD=1
    out = M._xsec_standardize(pdf, ["f"])
    scale = 1.0 * M.XSEC_MAD_SCALE
    assert np.allclose(out["f"].to_numpy(),
                       np.array([-2, -1, 0, 1, 2]) / scale, atol=1e-12)
    d = out.attrs["xsec_degenerate"]
    assert d["basis"] == "per_(date,factor)_pair"
    assert d["n_mad_zero"] == 0 and d["n_std_fallback"] == 0 and d["n_constant"] == 0
    assert d["factors"] == []


# ---------------- 一级退化：MAD==0 ⇒ 回退当日 std ----------------

def test_mad_zero_falls_back_to_cross_section_std():
    # [0×5, 1, 2, 3] ⇒ median=0，>50% 等于中位 ⇒ MAD=0；std>0
    pdf = _day([0.0, 0.0, 0.0, 0.0, 0.0, 1.0, 2.0, 3.0])
    out = M._xsec_standardize(pdf, ["f"])
    z = out["f"].to_numpy()
    std = float(pd.Series([0.0, 0.0, 0.0, 0.0, 0.0, 1.0, 2.0, 3.0]).std())  # ddof=1
    expect = np.array([0.0, 0.0, 0.0, 0.0, 0.0, 1.0, 2.0, 3.0]) / std
    assert np.allclose(z, expect, atol=1e-9)
    d = out.attrs["xsec_degenerate"]
    assert d["n_mad_zero"] == 1 and d["n_std_fallback"] == 1 and d["n_constant"] == 0
    assert d["n_mad_zero_cells"] == 8 and d["n_std_fallback_cells"] == 8
    assert d["n_dates"] == 1 and d["factors"] == ["f"]


def test_mad_zero_no_longer_produces_pathological_z():
    """回归本体：MAD==0 时 z 必须是有界量纲，绝不再冲到 ~1e12。"""
    pdf = _day([0.0, 0.0, 0.0, 0.0, 0.0, 1.0, 2.0, 3.0])
    z = M._xsec_standardize(pdf, ["f"])["f"].to_numpy()
    assert np.abs(z).max() < 100.0, f"出现病态 z：max|z|={np.abs(z).max():.3e}"
    assert np.isfinite(z).all()


# ---------------- 二级退化：std 亦为 0（常数截面）⇒ z=0，NaN 不伪造 ----------------

def test_constant_cross_section_maps_to_zero_but_keeps_nan():
    pdf = _day([5.0, 5.0, 5.0, float("nan")])
    out = M._xsec_standardize(pdf, ["f"])
    z = out["f"].to_numpy()
    assert z[0] == 0.0 and z[1] == 0.0 and z[2] == 0.0
    assert np.isnan(z[3]), "原值为 NaN 的观测不得被伪造成 0"
    d = out.attrs["xsec_degenerate"]
    assert d["n_mad_zero"] == 1 and d["n_constant"] == 1 and d["n_std_fallback"] == 0
    assert d["n_dates_constant"] == 1


# ---------------- 计数口径：按 (date, factor) 对 ----------------

def test_counts_are_per_date_factor_pair():
    pdf = pd.DataFrame({
        "date": pd.to_datetime(["2026-01-01"] * 4 + ["2026-01-02"] * 4),
        "symbol": list("ABCD") * 2,
        "f": [1.0, 2.0, 3.0, 4.0, 0.0, 0.0, 0.0, 1.0],   # d2: MAD==0
    })
    d = M._xsec_standardize(pdf, ["f"]).attrs["xsec_degenerate"]
    assert d["n_mad_zero"] == 1, d       # 仅 d2 的 1 个 (date,factor) 对
    assert d["n_dates"] == 1, d
    assert d["n_mad_zero_cells"] == 4, d


def test_empty_factors_returns_zero_structure():
    pdf = pd.DataFrame({"date": pd.to_datetime(["2026-01-01"])})
    out = M._xsec_standardize(pdf, [])
    assert out.attrs["xsec_degenerate"]["basis"] == "per_(date,factor)_pair"


# ---------------- 计数透传：compute_psi 返回结构 ----------------

def _feat(n_dates: int = 60, n_sym: int = 120) -> pl.DataFrame:
    """60 个交易日 × 120 只；`f_b` 在第 0、30 日制造 MAD==0（80% 同值）。"""
    rng = np.random.default_rng(0)
    dates = pd.date_range("2024-01-01", periods=n_dates, freq="B")
    rows = []
    for di, dt in enumerate(dates):
        a = rng.normal(size=n_sym)
        for si in range(n_sym):
            if di in (0, 30):
                fb = 0.0 if si < int(n_sym * 0.8) else float(si)
            else:
                fb = float(a[si])
            rows.append({"date": dt.date(), "symbol": f"S{si:03d}",
                         "f_a": float(a[si]), "f_b": fb})
    return pl.DataFrame(rows)


def test_compute_psi_passes_through_degenerate_block():
    psi = M.compute_psi(_feat())
    assert psi["ok"]
    d = psi["degenerate"]
    assert d["basis"] == "per_(date,factor)_pair"
    assert d["n_mad_zero"] >= 1, d
    assert "f_b" in d["factors"], d
    # 计数为 0 的字段也必须存在（结构完整，绝不缺失）
    for k in ("n_mad_zero", "n_std_fallback", "n_constant", "n_mad_zero_cells",
              "n_std_fallback_cells", "n_constant_cells", "n_dates",
              "n_dates_constant", "factors", "basis"):
        assert k in d, f"degenerate 缺字段 {k}"


def test_compute_psi_degenerate_zero_structure_when_no_mad_zero():
    """无退化的面板：degenerate 字段仍完整、计数为 0（不是缺失/None）。"""
    rng = np.random.default_rng(1)
    dates = pd.date_range("2024-01-01", periods=60, freq="B")
    rows = [{"date": dt.date(), "symbol": f"S{si:03d}",
             "f_a": float(rng.normal())}
            for dt in dates for si in range(120)]
    psi = M.compute_psi(pl.DataFrame(rows))
    assert psi["ok"]
    d = psi["degenerate"]
    assert d["n_mad_zero"] == 0 and d["n_constant"] == 0 and d["factors"] == []
    assert d["basis"] == "per_(date,factor)_pair"


# ---------------- 不变量：仍按日标准化 + 平移/缩放不变（防退化修复破坏原契约） ----------------

def test_scale_invariance_still_holds_after_fix():
    pdf = _day([1.0, 2.0, 3.0, 4.0, 5.0])
    z = M._xsec_standardize(pdf, ["f"])["f"].to_numpy()
    shifted = pdf.copy()
    shifted["f"] = shifted["f"] * 7.0 + 100.0
    z2 = M._xsec_standardize(shifted, ["f"])["f"].to_numpy()
    assert np.allclose(z, z2, atol=1e-9), "平移/缩放改变了截面标准化结果"
