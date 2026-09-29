"""风险指标口径一致性：无风险利率单一来源 + 基准标识不伪造（审计 B2-16、B2-11）。

**修复前的缺陷本体**：

* **B2-16a**：``domain/risk.py`` 的 ``risk_metrics`` 默认 ``rf=0.0``，而
  ``domain/portfolio.py`` 用年化 2% ⇒ 个股风险卡与组合页的"夏普"口径不同、不可比；
  且 ``portfolio._compute_metrics`` 的形参叫 ``daily_rf`` 却**从未被使用**
  （函数体直接读模块常量）⇒ 形参与实现分裂。
* **B2-16b**：``risk_metrics`` 无论传入什么基准，返回的 ``benchmark`` 字段都硬编码
  ``"沪深300"`` ⇒ 换了基准的 Beta 仍被标成沪深300。
* **B2-11**：``mvo`` 路径收不到预期收益 ⇒ μ≡0，风险厌恶系数**完全无效**
  （独立复核：λ=8 与 λ=50 的权重 L1 距离 = 0.0），"均值-方差"名不副实。

本文件用数值断言（而非源码断言）锁定口径，另加少量源码锁防止回退。
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

from app.domain import portfolio as portfolio_mod  # noqa: E402
from app.domain.metrics import RISK_FREE_ANNUAL  # noqa: E402
from app.domain.optimizer import mean_variance_weights, robust_cov  # noqa: E402
from app.domain.research import _expected_returns_disclosure  # noqa: E402
from app.domain.risk import risk_metrics  # noqa: E402


def _bars(n: int = 300, seed: int = 5) -> pl.DataFrame:
    dates = [pd.Timestamp("2024-01-01").date() + pd.Timedelta(days=i) for i in range(n)]
    rng = np.random.default_rng(seed)
    close = 100 * np.exp(np.cumsum(rng.normal(0.0004, 0.012, n)))
    return pl.DataFrame({"date": dates, "close": close.tolist()})


# ---------------- B2-16a：rf 单一来源、年化口径 ----------------

def test_risk_free_annual_is_single_source():
    """年化 rf 只在 domain.metrics 定义一次，portfolio/risk 都引用它。"""
    assert RISK_FREE_ANNUAL == pytest.approx(0.02)
    src_root = BACKEND_ROOT / "app"
    risk_src = (src_root / "domain" / "risk.py").read_text(encoding="utf-8")
    pf_src = (src_root / "domain" / "portfolio.py").read_text(encoding="utf-8")
    assert "from .metrics import RISK_FREE_ANNUAL" in risk_src
    assert "RISK_FREE_ANNUAL" in pf_src
    # 不得再各自定义一份，也不得残留"年化常量 / 252"这种日度口径
    assert "_RISK_FREE_ANNUAL" not in pf_src, "portfolio 仍有本地第二份 rf 定义"
    assert "RISK_FREE_ANNUAL / 252" not in pf_src, "仍有日度 rf 用法"
    assert "daily_rf:" not in pf_src, "形参名仍为 daily_rf（口径含糊）"
    assert "rf_annual: float" in pf_src, "形参未改为 rf_annual"


def test_risk_metrics_default_rf_is_annual_and_disclosed():
    """个股风险卡默认 rf = 年化 2%，且披露 ``rf_annual``；rf=0 时差值 = rf/年化波动。"""
    df = _bars()
    r = risk_metrics(df, benchmark=df, window=252)
    assert r["rf_annual"] == pytest.approx(RISK_FREE_ANNUAL)
    r0 = risk_metrics(df, benchmark=df, window=252, rf=0.0)
    assert r0["rf_annual"] == 0.0
    expected = RISK_FREE_ANNUAL / r["annual_vol"]
    assert r["sharpe"] - r0["sharpe"] == pytest.approx(-expected, rel=1e-3), \
        "夏普差值应等于 rf/年化波动率（证明 rf 被年化处理）"
    # 修复前默认 rf=0 ⇒ 两者相同；现在必须不同（非空断言的反证）
    assert r["sharpe"] != r0["sharpe"]


def test_portfolio_sharpe_uses_its_parameter():
    """``_compute_metrics`` 的 rf 形参必须真正生效（修复前形参被忽略）。"""
    df = _bars(seed=9)
    nav = pd.Series(df["close"].to_numpy(),
                    index=pd.to_datetime([str(d) for d in df["date"].to_list()]))
    m2 = portfolio_mod._compute_metrics(nav, nav, RISK_FREE_ANNUAL)
    m0 = portfolio_mod._compute_metrics(nav, nav, 0.0)
    assert m2["risk_free"] == pytest.approx(RISK_FREE_ANNUAL)
    assert m2["rf_basis"] == "annual"
    expected = RISK_FREE_ANNUAL / m2["volatility"]
    assert m2["sharpe"] - m0["sharpe"] == pytest.approx(-expected, rel=1e-3)
    # 与文档公式一致（差值应为舍入误差，不是口径差）
    ret = nav.pct_change().dropna()
    manual = float((ret.mean() * 252 - RISK_FREE_ANNUAL)
                   / (ret.std() * np.sqrt(252)))
    assert m2["sharpe"] == pytest.approx(manual, abs=1e-6)


def test_portfolio_rf_value_unchanged_by_refactor():
    """口径重构不得改变组合页数字（修复前实际生效值就是 2%）。"""
    df = _bars(seed=11)
    nav = pd.Series(df["close"].to_numpy(),
                    index=pd.to_datetime([str(d) for d in df["date"].to_list()]))
    m = portfolio_mod._compute_metrics(nav, nav, RISK_FREE_ANNUAL)
    ret = nav.pct_change().dropna()
    legacy = float((ret.mean() * 252 - 0.02) / max(ret.std() * np.sqrt(252), 1e-12))
    assert m["sharpe"] == pytest.approx(legacy, abs=1e-6)


# ---------------- B2-16b：基准标识不伪造 ----------------

def test_benchmark_label_not_hardcoded():
    """不给 ``benchmark_symbol`` ⇒ 字段为 None（而不是硬编码"沪深300"）。"""
    df = _bars()
    r = risk_metrics(df, benchmark=df, window=252)
    assert r["benchmark"] is None, "仍在使用硬编码基准名"
    r2 = risk_metrics(df, benchmark=df, window=252,
                      benchmark_symbol="中证500(sh000905)")
    assert r2["benchmark"] == "中证500(sh000905)"
    assert r2["beta"] == pytest.approx(r["beta"]), "标识不应影响 Beta 数值"


def test_benchmark_none_keeps_beta_null_and_label_null():
    """没有基准数据时 Beta 与标签都为 null（不得出现"有标签无数据"的假象）。"""
    df = _bars()
    r = risk_metrics(df, window=252)
    assert r["benchmark"] is None and r["beta"] is None


def test_panels_passes_real_benchmark_label():
    """数据层必须把真实基准标识传下去（源码锁：否则标签又会消失）。"""
    src = (BACKEND_ROOT / "app" / "data" / "panels.py").read_text(encoding="utf-8")
    assert "benchmark_symbol=" in src
    assert "_BENCHMARK_CODE" in src and "sh000300" in src


# ---------------- B2-11：μ 缺失的披露与 λ 无效的独立复核 ----------------

def test_mvo_risk_aversion_is_inert_without_mu():
    """独立复核 B2-11：μ≡0 时 λ=8 与 λ=50 解完全相同（L1=0）。"""
    rng = np.random.default_rng(3)
    R = rng.normal(0.0005, 0.012, (250, 10))
    C = robust_cov(R)
    w8 = mean_variance_weights(np.zeros(10), C, risk_aversion=8.0)
    w50 = mean_variance_weights(np.zeros(10), C, risk_aversion=50.0)
    assert float(np.abs(w8 - w50).sum()) == pytest.approx(0.0, abs=1e-12)
    # 对照：μ 异质 ⇒ λ 立刻生效（说明缺陷在"调用方没给 μ"，不在优化器）
    mu = 0.0005 * np.arange(1, 11) / 10
    d = float(np.abs(mean_variance_weights(mu, C, risk_aversion=8.0)
                     - mean_variance_weights(mu, C, risk_aversion=50.0)).sum())
    assert d > 0.1, f"异质 μ 下 λ 应显著影响解，实测 L1={d}"


def test_mvo_disclosure_only_for_mvo():
    """只有 mvo 需要 μ 披露；其他方案不得出现该字段（避免无意义告警）。"""
    assert _expected_returns_disclosure("risk_parity") == {}
    info = _expected_returns_disclosure("mvo")
    assert info["basis"] == "unavailable"
    assert info["risk_aversion_effective"] is False
    assert "μ≡0" in info["note"] and "均值-方差" in info["note"]


def test_frontend_renames_mvo_and_shows_mu_notice():
    """前端不得再把 μ≡0 的解标成"Mean-Variance（均值-方差）"。"""
    src_root = BACKEND_ROOT.parent / "frontend" / "src"
    if not src_root.exists():
        pytest.skip("前端目录不在本工作区")
    page = (src_root / "pages" / "Research" / "index.tsx").read_text(encoding="utf-8")
    assert "Mean-Variance（均值-方差）" not in page, "MVO 标签仍在误导"
    assert "expected_returns" in page and "basis" in page
    ts = (src_root / "api" / "research.ts").read_text(encoding="utf-8")
    assert "expected_returns" in ts and "risk_aversion_effective" in ts
    stock_page = (src_root / "pages" / "StockDetail" / "index.tsx").read_text(encoding="utf-8")
    assert "rf_annual" in stock_page, "个股风险卡未披露夏普的 rf 口径"
    stock_types = (src_root / "types" / "stock.ts").read_text(encoding="utf-8")
    assert "rf_annual" in stock_types