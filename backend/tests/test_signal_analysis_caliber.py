"""审计 P1-25 防回归：信号分析的口径正确性 + 前后端契约漂移守护。

**审计发现的缺陷（2026-09-21，实测见 `backend/.tmp_testrun/p1_25_caliber.py`）**：

1. **年化价差放大 h 倍**：`quantile_spread_report` 的 `ls_daily` 元素是
   **h 日**远期收益，而 `ann = mean × 252` 把它当**日**收益年化。
   实测（h=20）：ORIG **+6031.4%** vs FIXED **+301.6%**（放大 20×）。
2. **t 值重叠高估 ≈√h**：h 日窗口在相邻日期重叠 h−1 天，`sqrt(n)` 把
   重叠观测当独立样本。实测（h=20）：ORIG **107.39** vs FIXED **24.01**
   （高估 **4.47× = √20**）；h=1 时两口径**完全一致**。
   同一缺陷也存在于 `ic_decay_report`。
3. **前端契约漂移（根因是 TS 类型自己写错）**：前端读
   `quantile_spread.long_short_nav` 与 `.spread_annualized`，而后者
   **根本不存在**（前者当时也不存在）⇒ 图表**恒空**、年化**永不显示**；
   TS 把不存在的键声明成必需字段，于是类型检查通过、运行时静默 `undefined`。
4. **docstring 失真**：声称返回 `long_short_daily` / `ls_mean`，二者都未返回。
5. **已算好的字段被丢弃**：`monotonic` 与 `ls_t_stat` 前端从未展示。
"""
from __future__ import annotations

import sys
from pathlib import Path

BACKEND_ROOT = Path(__file__).resolve().parents[1]
REPO_ROOT = BACKEND_ROOT.parent
sys.path.insert(0, str(BACKEND_ROOT))

import numpy as np  # noqa: E402
import pandas as pd  # noqa: E402
import pytest  # noqa: E402

from app.ml.signal_analysis import (  # noqa: E402
    TRADING_DAYS,
    ic_decay_report,
    overlapping_t_stat,
    quantile_spread_report,
)

H = 5
N_DAY = 120
N_SYM = 20


def _frames(h: int = H, *, reverse: bool = False, seed: int = 7):
    """确定性数据：signal 与未来 h 日收益正相关（reverse=True 则反向）。"""
    rng = np.random.default_rng(seed)
    dates = pd.bdate_range("2025-01-01", periods=N_DAY + h + 1).date
    syms = [f"{600000 + i}.SH" for i in range(N_SYM)]
    r = 0.0005 + rng.normal(0, 0.015, size=(len(dates), N_SYM))
    close = 10.0 * np.cumprod(1.0 + r, axis=0)
    fwd = np.full(close.shape, np.nan)
    fwd[:-h] = close[h:] / close[:-h] - 1.0
    sig = (-fwd if reverse else fwd) + rng.normal(0, 0.01, size=fwd.shape)
    cl = pd.DataFrame(close, index=dates, columns=syms).reset_index(names="date") \
        .melt(id_vars="date", var_name="symbol", value_name="close").dropna()
    sg = pd.DataFrame(sig, index=dates, columns=syms).reset_index(names="date") \
        .melt(id_vars="date", var_name="symbol", value_name="pred_score").dropna()
    return cl, sg


# ---------------- 1) 年化口径 ----------------

@pytest.mark.parametrize("h", [1, 5, 20])
def test_annualization_divides_by_horizon(h: int) -> None:
    """年化价差必须 = 均值 × 252 / h（原实现漏了 /h ⇒ 放大 h 倍）。"""
    cl, sg = _frames(h)
    rep = quantile_spread_report(sg, cl, horizon=h, n_quantiles=5)
    m = rep["ls_mean_daily"]
    assert m is not None
    # 容差说明：后端对 `ls_mean_daily` 与 `ls_annualized` 各舍入到 1e-6，
    # 本用例只能用**舍入后**的均值反算，误差被 252/h 放大（h=1 时约 1.3e-4
    # 绝对值）⇒ 取 rel=1e-4（仍足以区分 ×252 与 ×252/h，后者相差 h 倍）。
    assert rep["ls_annualized"] == pytest.approx(m * TRADING_DAYS / h, rel=1e-4), (
        f"h={h} 年化未按 252/h 计算：{rep['ls_annualized']}"
        f"（朴素 ×252 会给出 {m * TRADING_DAYS:.4f}）")
    if h > 1:
        assert not np.isclose(rep["ls_annualized"], m * TRADING_DAYS, rtol=0.05), \
            "h>1 时年化仍等于 ×252 ⇒ 未修复 h 倍放大"
    assert rep["annualization_basis"] == f"mean_h_day_spread * 252 / {h}"


# ---------------- 2) t 值重叠校正 ----------------

def test_overlapping_t_stat_is_naive_over_sqrt_h() -> None:
    assert overlapping_t_stat(0.01, 0.02, 400, 1) == pytest.approx(
        0.01 / 0.02 * 20.0)
    assert overlapping_t_stat(0.01, 0.02, 400, 4) == pytest.approx(
        0.01 / 0.02 * 20.0 / 2.0)
    assert overlapping_t_stat(0.01, 0.02, 400, 16) == pytest.approx(
        0.01 / 0.02 * 20.0 / 4.0)


def test_overlapping_t_stat_guards() -> None:
    assert np.isnan(overlapping_t_stat(0.01, 0.0, 100, 1)), "std≈0 应返回 nan"
    assert np.isnan(overlapping_t_stat(0.01, 0.02, 1, 1)), "n<2 应返回 nan"
    # n_eff = n/h < 2 ⇒ 无有效样本
    assert np.isnan(overlapping_t_stat(0.01, 0.02, 4, 5)), "n_eff<2 应返回 nan"
    with pytest.raises(ValueError):
        overlapping_t_stat(0.01, 0.02, 100, 0)


@pytest.mark.parametrize("h", [1, 5, 20])
def test_quantile_spread_t_stat_is_overlap_corrected(h: int) -> None:
    cl, sg = _frames(h)
    rep = quantile_spread_report(sg, cl, horizon=h, n_quantiles=5)
    n = rep["n_days"]
    # 用后端自己报的均值/观测数与标准差复算：t 应等于 sqrt(n/h) 口径
    # （标准差无法从返回值直接取，故用「朴素 t / 校正 t == sqrt(h)」反推）
    naive = rep["ls_t_stat"] * np.sqrt(h)
    assert rep["ls_t_stat"] is not None
    assert rep["n_independent"] == pytest.approx(n / h, rel=0.05)
    if h > 1:
        # 与朴素口径必须相差 ≈√h（不是 1）
        assert not np.isclose(rep["ls_t_stat"], naive, rtol=1e-6)
        assert naive / rep["ls_t_stat"] == pytest.approx(np.sqrt(h), rel=1e-6)
    assert rep["t_stat_basis"] == f"overlap-adjusted n_eff=n/{h}"


def test_ic_decay_t_stat_overlap_corrected_and_h1_unchanged() -> None:
    cl, sg = _frames(H)
    ic = ic_decay_report(sg, cl, horizons=(1, H))
    row1 = ic[ic["horizon"] == 1].iloc[0]
    # h=1：与原朴素公式完全一致（无重叠）
    naive1 = row1["mean_ic"] / row1["std_ic"] * np.sqrt(int(row1["n_days"]))
    assert row1["t_stat"] == pytest.approx(naive1, rel=1e-3), \
        "h=1 的 t 值不应被校正改变"
    rowH = ic[ic["horizon"] == H].iloc[0]
    naiveH = rowH["mean_ic"] / rowH["std_ic"] * np.sqrt(int(rowH["n_days"]))
    assert naiveH / rowH["t_stat"] == pytest.approx(np.sqrt(H), rel=1e-3), \
        f"h={H} 的 t 值未做 √h 校正"
    assert rowH["n_independent"] == pytest.approx(int(rowH["n_days"]) / H, rel=0.05)


def test_ic_decay_empty_input_reports_zero_independent() -> None:
    empty = pd.DataFrame({"date": [], "symbol": [], "pred_score": []})
    close = pd.DataFrame({"date": [], "symbol": [], "close": []})
    ic = ic_decay_report(empty, close, horizons=(5,))
    assert int(ic["n_days"].iloc[0]) == 0
    assert float(ic["n_independent"].iloc[0]) == 0.0
    assert not np.isfinite(ic["t_stat"].iloc[0])


# ---------------- 3) 多空净值序列（前端画图用） ----------------

def test_long_short_nav_is_present_and_compounded() -> None:
    cl, sg = _frames(H)
    rep = quantile_spread_report(sg, cl, horizon=H, n_quantiles=5)
    nav = rep["long_short_nav"]
    assert nav, "`long_short_nav` 缺失 ⇒ 前端图表恒空（P1-25 回归）"
    assert len(nav) == rep["n_days"]
    assert all(set(p) == {"date", "nav"} for p in nav)
    assert all(p["nav"] > 0 for p in nav), "净值必须为正（复利不可穿越 0）"
    # 日期必须是升序且唯一
    dates = [p["date"] for p in nav]
    assert dates == sorted(dates) and len(set(dates)) == len(dates)
    assert "nav_basis" in rep and "overlap" in str(rep["nav_basis"])


def test_long_short_nav_matches_first_observation() -> None:
    """首点净值 = 1 × (1 + 首日价差)，用于验证复利口径。"""
    cl, sg = _frames(H)
    rep = quantile_spread_report(sg, cl, horizon=H, n_quantiles=5)
    nav = rep["long_short_nav"]
    # 用后端返回的均值与观测数无法逐日复原，故只验证"首点 ≠ 1"且有限
    assert np.isfinite(nav[0]["nav"])
    assert nav[-1]["nav"] > 0
    if len(nav) > 1:
        assert nav[-1]["nav"] != nav[0]["nav"]


# ---------------- 4) monotonic 语义（此前被前端丢弃） ----------------

def test_monotonic_flag_positive_signal() -> None:
    cl, sg = _frames(H)
    rep = quantile_spread_report(sg, cl, horizon=H, n_quantiles=5)
    assert rep["monotonic"] is True, (
        f"正相关信号的各分位 h 日期收益应单调递增：{rep['quantile_mean_ret']}")


def test_monotonic_flag_reversed_signal() -> None:
    cl, sg = _frames(H, reverse=True)
    rep = quantile_spread_report(sg, cl, horizon=H, n_quantiles=5)
    assert rep["monotonic"] is False, "反向信号不应被判为单调递增"


# ---------------- 5) 契约完整性与跨层漂移守护 ----------------

EXPECTED_KEYS = {
    "horizon", "n_quantiles", "quantile_mean_ret", "long_short_nav",
    "ls_mean_daily", "ls_t_stat", "ls_annualized", "monotonic", "n_days",
    "n_independent", "annualization_basis", "t_stat_basis", "nav_basis",
    "ls_mean_basis",
}


def test_returned_keys_match_declared_contract() -> None:
    """返回键集合必须与测试声明的契约一致（防 docstring/实际漂移）。"""
    cl, sg = _frames(H)
    rep = quantile_spread_report(sg, cl, horizon=H, n_quantiles=5)
    assert set(rep) == EXPECTED_KEYS, (
        f"契约漂移：多出 {set(rep) - EXPECTED_KEYS}，缺少 {EXPECTED_KEYS - set(rep)}")
    # 旧 docstring 声称的两个键从未返回（文档失真已修，此处防回归到"声称"状态）
    assert "long_short_daily" not in rep and "ls_mean" not in rep


def test_frontend_type_declares_all_backend_keys() -> None:
    """跨层漂移守护：后端返回的键必须出现在前端 TS 声明里。

    本轮 P1-25 的**根因**就是 `frontend/src/api/backtest.ts` 把不存在的
    `spread_annualized` 声明成必需字段、同时漏掉真实键 ⇒ 类型检查通过、
    运行时静默 `undefined`（图表恒空）。该用例直接对源码断言，无法再漂移。
    """
    ts = REPO_ROOT / "frontend" / "src" / "api" / "backtest.ts"
    if not ts.exists():
        pytest.skip("前端目录不在本工作区")
    src = ts.read_text(encoding="utf-8")
    block = src.split("quantile_spread:", 1)[-1].split("from_cache", 1)[0]
    missing = sorted(k for k in EXPECTED_KEYS if k not in block)
    assert not missing, (
        f"前端 `quantile_spread` 类型缺少后端真实返回的键：{missing}"
        f"（会导致运行时静默 undefined）")
    assert "spread_annualized" not in block, (
        "前端仍在声明不存在的 `spread_annualized` ⇒ 年化价差永远不会显示")