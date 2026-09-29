"""组合层约束：单资产上限可行性（P1-13）+ 权重归一（B2-12）——定向验证。

**修复前的缺陷本体**（审核 §8.2 第 15 项 / S6）：

* **P1-13**：``apply_weight_cap`` 在 ``n·cap<1``（N=10、cap=5%）时**静默**返回
  Σw=0.5 的权重（一半现金），API 仍报 ``status=ok``、``fallback=false``
  ⇒ 用户把"半仓回测"读成正常结果；
* **B2-12**：``run_portfolio_backtest`` 接受 Σw∈[0.99,1.01] 却**不归一**
  ⇒ Σw=0.995 时 0.5% 永久留作现金，且响应回显的权重与实际执行口径不一致。

本文件同时固化两条**行为边界**（修复不改行为，只做披露 —— 见 §S6 引入风险）：
① 权重仍按原样返回（不改历史净值）；② ``equal`` 方案下 cap **始终未被执行**
（若强行执行，n·cap<1 时会退化成"半仓"，比超限更糟）。
"""
from __future__ import annotations

import sys
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

BACKEND_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(BACKEND_ROOT))

from app.backtest.engine import _summarize_cap_infos, run_backtest  # noqa: E402
from app.domain import portfolio as portfolio_mod  # noqa: E402
from app.domain.optimizer import (  # noqa: E402
    apply_weight_cap,
    cap_feasibility,
    cap_info_for,
)

SYMS = ["A.SZ", "B.SZ", "C.SZ", "D.SZ"]
PRICE = 10.0


# ---------------- ① 纯函数：可行性判据 ----------------

def test_cap_feasibility_truth_table():
    """``n·cap≥1`` 才可行；``min_feasible_cap=1/n``；cap≤0/≥1 视为不设限。"""
    assert cap_feasibility(10, 0.0)["feasible"] is True
    assert cap_feasibility(10, 1.0)["feasible"] is True
    assert cap_feasibility(10, 0.1)["feasible"] is True           # 恰好可行（边界）
    bad = cap_feasibility(10, 0.05)
    assert bad["feasible"] is False
    assert bad["max_invested_ratio"] == pytest.approx(0.5)
    assert bad["min_feasible_cap"] == pytest.approx(0.1)
    assert cap_feasibility(3, 0.4)["feasible"] is True            # 3×0.4=1.2
    assert cap_feasibility(3, 0.3)["feasible"] is False           # 3×0.3=0.9
    # 浮点边界：1/3 这类不能被二进制精确表示的值不得误判
    assert cap_feasibility(3, 1.0 / 3.0)["feasible"] is True


def test_apply_weight_cap_behavior_unchanged_but_disclosed():
    """**缺陷本体反证**：行为不变（仍返回 Σw=0.5），但必须能取出可行性标志。"""
    w = np.full(10, 0.1)
    capped = apply_weight_cap(w, 0.05)
    assert float(capped.sum()) == pytest.approx(0.5, abs=1e-12), \
        "行为必须保持原样（改行为会改历史净值）"
    assert capped.max() <= 0.05 + 1e-12
    info = cap_info_for(capped, 0.05)
    assert info["feasible"] is False
    assert info["cap_enforced"] is True          # 上限确实执行了，代价是半仓
    assert info["invested_ratio"] == pytest.approx(0.5)
    assert "现金" in info["note"] and "0.1000" in info["note"]
    # 可行时：满仓且上限生效
    ok = cap_info_for(np.full(10, 0.1), 0.1)
    assert ok["feasible"] is True and ok["cap_enforced"] is True
    assert ok["invested_ratio"] == pytest.approx(1.0)
    # 不设限：不报"生效/未生效"，如实说未设
    none = cap_info_for(np.full(4, 0.25), 0.0)
    assert none["note"] == "未设单资产上限" and none["feasible"] is True


def test_cap_info_reports_unenforced_cap_before_infeasibility():
    """上限**根本没被执行**时必须先报违反（否则会把"满仓且超限"说成"只能投 80%"）。"""
    info = cap_info_for(np.full(4, 0.25), 0.2)      # equal 方案的真实权重
    assert info["cap_enforced"] is False
    assert info["max_weight"] == pytest.approx(0.25)
    assert "未生效" in info["note"]
    assert info["note"].startswith("⚠️ 上限**未生效**")
    assert "0.25 > cap=0.2" in info["note"]
    # 同时说明"若强制生效会怎样"（4×0.2=0.8 ⇒ 只能投 80%）
    assert "80.0%" in info["note"]
    # 可行且被执行时不得出现任何 ⚠️
    good = cap_info_for(np.full(4, 0.25), 0.3)
    assert "⚠️" not in good["note"] and good["cap_enforced"] is True


def test_summarize_cap_infos_takes_worst_not_average():
    """汇总取**最坏**：只要有一次半仓，整段净值就被稀释，平均值会掩盖它。"""
    ok = cap_info_for(np.full(10, 0.1), 0.1)
    half = cap_info_for(np.full(10, 0.05), 0.05)
    s = _summarize_cap_infos([ok, ok, half], 0.05)
    assert s["rebalances"] == 3 and s["infeasible_rebalances"] == 1
    assert s["half_invested_rebalances"] == 1
    assert s["feasible"] is False
    assert s["invested_ratio_min"] == pytest.approx(0.5)
    assert s["min_feasible_cap"] == pytest.approx(0.1)
    assert "现金" in s["note"] and "1/3" in s["note"]
    # 未执行（超限）与半仓必须分开计数
    unenf = cap_info_for(np.full(4, 0.25), 0.2)
    s2 = _summarize_cap_infos([unenf], 0.2)
    assert s2["cap_unenforced_rebalances"] == 1
    assert s2["half_invested_rebalances"] == 0
    assert "未被执行" in s2["note"] and "超限" in s2["note"]
    # 不设上限 ⇒ 不给结论（避免"上限生效"这种无意义文案）
    assert _summarize_cap_infos([ok], 0.0) == {}
    assert _summarize_cap_infos([], 0.3)["feasible"] is None


# ---------------- ② 引擎端到端：真实 run_backtest ----------------

def _flat_universe(n_days: int = 20) -> pd.DataFrame:
    """价格恒定、无停牌/涨跌停的受控行情（沿用 test_equal_weight.py 的口径）。"""
    dates = pd.bdate_range("2024-01-01", periods=n_days).date
    rows = []
    for d in dates:
        for s in SYMS:
            rows.append({"date": d, "symbol": s, "open": PRICE, "high": PRICE * 1.05,
                         "low": PRICE * 0.95, "close": PRICE, "volume": 1_000_000.0,
                         "amount": 10_000_000.0, "limit_up": PRICE * 1.10,
                         "limit_down": PRICE * 0.90, "is_halted": False})
    return pd.DataFrame(rows)


def _signal(universe: pd.DataFrame) -> pd.DataFrame:
    rows = [{"date": d, "symbol": s, "pred_score": 1.0 - 0.1 * i}
            for d in sorted(universe["date"].unique())
            for i, s in enumerate(SYMS)]
    return pd.DataFrame(rows)


def _wavy_universe(n_days: int = 60) -> pd.DataFrame:
    """价格起伏且各标的不同波动（风险类方案需要非退化协方差，否则回退等权）。"""
    dates = pd.bdate_range("2024-01-01", periods=n_days).date
    rows = []
    for k, d in enumerate(dates):
        for i, s in enumerate(SYMS):
            px = round(10 * (1 + 0.2 * i) * (1 + 0.05 * np.sin(k * 0.3 + i)), 4)
            rows.append({"date": d, "symbol": s, "open": px, "high": px * 1.05,
                         "low": px * 0.95, "close": px, "volume": 1_000_000.0,
                         "amount": px * 1_000_000.0, "limit_up": px * 1.1,
                         "limit_down": px * 0.9, "is_halted": False})
    return pd.DataFrame(rows)


def test_run_backtest_score_weighted_discloses_half_cash():
    """端到端（真正执行 cap 的方案）：top_k=4、cap=20% ⇒ Σw=0.8（20% 现金）并披露。"""
    uni = _flat_universe()
    sig = _signal(uni)
    res = run_backtest(uni, sig, top_k=4, weighting="score_weighted", weight_cap=0.2)
    info = res.weight_cap_info
    assert info["cap"] == pytest.approx(0.2)
    assert info["feasible"] is False
    assert info["half_invested_rebalances"] > 0
    assert info["cap_unenforced_rebalances"] == 0
    assert info["invested_ratio_min"] == pytest.approx(0.8, abs=1e-6)
    assert info["min_feasible_cap"] == pytest.approx(0.25)
    assert "现金" in info["note"] and "不是「满仓策略」" in info["note"]
    # 对照：cap=25%（恰好可行）⇒ 满仓、无告警
    res_ok = run_backtest(uni, sig, top_k=4, weighting="score_weighted", weight_cap=0.25)
    assert res_ok.weight_cap_info["feasible"] is True
    assert res_ok.weight_cap_info["invested_ratio_min"] == pytest.approx(1.0, abs=1e-6)
    assert "⚠️" not in res_ok.weight_cap_info["note"]


def test_run_backtest_risk_parity_cap_path():
    """风险类方案（cap 在 optimizer 内生效）同样必须带出可行性披露。

    ⚠️ ``cov_window`` 必须 ≥20：``_trailing_returns`` 的 ``min_obs`` 默认 21，
    而切片只有 ``cov_window+1`` 行 ⇒ ``cov_window<20`` 时风险类方案**必然**
    回退等权（本文件外的一条独立观察，记在报告"残留/观察"里）。
    """
    uni = _wavy_universe()
    sig = _signal(uni)
    res = run_backtest(uni, sig, top_k=4, weighting="risk_parity",
                       weight_cap=0.2, cov_window=30)
    info = res.weight_cap_info
    assert info["rebalances"] > 0
    assert info["feasible"] is False
    assert info["infeasible_rebalances"] > 0
    assert info["half_invested_rebalances"] > 0, \
        f"风险类方案未走「执行上限」路径：{info}"
    assert info["max_invested_ratio"] == pytest.approx(0.8)
    assert info["invested_ratio_min"] == pytest.approx(0.8, abs=1e-6)
    assert info["n_assets_min"] == 4
    # 混合情形（早期历史不足回退等权 ⇒ 未执行；后期生效 ⇒ 半仓）必须两个计数都给出
    assert info["cap_unenforced_rebalances"] >= 0
    assert "⚠️" in info["note"]


def test_run_backtest_default_equal_never_enforces_cap_but_now_says_so():
    """``weighting="equal"``（**默认**）下 cap 从未被应用 ⇒ 必须披露"未被执行"。

    这是审核 P1-13 家族里未被文字点明的一条：修复前该组合**既超限又无任何字段**。
    行为**有意不改**：n·cap<1 时若强行执行上限，结果会退化成"半仓"（Σw=n·cap），
    比"超限但满仓"更糟；正确做法是让用户看到"上限没生效 + 想生效该设多大"。
    """
    uni = _flat_universe()
    sig = _signal(uni)
    res = run_backtest(uni, sig, top_k=4, weighting="equal", weight_cap=0.2)
    info = res.weight_cap_info
    assert info["cap_enforced"] is False
    assert info["cap_unenforced_rebalances"] > 0
    assert info["max_weight"] == pytest.approx(0.25)     # 1/4 > cap
    assert info["invested_ratio_min"] == pytest.approx(1.0, abs=1e-6)   # 仍是满仓
    assert "未被执行" in info["note"] and "超限" in info["note"]
    # 不传 cap ⇒ 不产生结论字段（避免给前端假信号）
    res_none = run_backtest(uni, sig, top_k=4, weighting="equal")
    assert res_none.weight_cap_info == {}


# ---------------- ③ B2-12：Σw 容差内必须归一 ----------------

def _make_prices(start: str, end: str, n_assets: int = 3):
    dates = pd.bdate_range(start, end)
    rng = np.random.default_rng(7)
    assets = []
    for i in range(n_assets):
        rets = rng.normal(0.0008, 0.012, len(dates))
        assets.append(pd.Series(10 * (1 + i * 0.2) * np.exp(np.cumsum(rets)),
                                index=dates.date, name=f"CODE{i}"))
    bm = pd.Series(100 * np.exp(np.cumsum(rng.normal(0.0005, 0.010, len(dates)))),
                   index=dates.date, name="benchmark")
    return assets, bm


def _run_portfolio(weights: list[float]):
    assets, bm = _make_prices("2023-01-01", "2023-06-30", n_assets=3)

    def fake_asset(code, asset_type, start, end):
        return assets[[s.name for s in assets].index(code)]

    return portfolio_mod.run_portfolio_backtest(
        price_loader=fake_asset, benchmark_loader=lambda c, s, e: bm,
        assets=[{"code": f"CODE{i}", "type": "stock", "weight": w}
                for i, w in enumerate(weights)],
        start_date="2023-01-01", end_date="2023-06-30", rebalance="M",
        initial_cash=1_000_000)


def test_portfolio_weights_are_normalized_within_tolerance():
    """Σw=0.995（容差内）⇒ 必须归一到 1 并披露因子；回显权重之和为 1。"""
    res = _run_portfolio([0.3, 0.395, 0.3])          # Σ=0.995
    norm = res["weights_normalization"]
    assert norm is not None
    assert norm["input_sum"] == pytest.approx(0.995)
    assert norm["factor"] == pytest.approx(1 / 0.995, rel=1e-6)
    assert "现金" in norm["note"]
    assert sum(a["weight"] for a in res["assets"]) == pytest.approx(1.0, abs=1e-9)
    # drift 首日权重之和也应 ≈1（此前会恒差 0.5%）
    assert sum(res["holdings_drift"][0]["weights"].values()) > 0.9


def test_portfolio_weights_exact_one_needs_no_normalization():
    """Σw=1 ⇒ 不产生归一披露（避免"无意义字段"）。"""
    res = _run_portfolio([0.3, 0.4, 0.3])
    assert res["weights_normalization"] is None
    assert sum(a["weight"] for a in res["assets"]) == pytest.approx(1.0)


def test_portfolio_weights_outside_tolerance_still_rejected():
    """容差外仍然拒绝（归一不是"来者不拒"）—— Σw=0.98 必须报错。"""
    with pytest.raises(ValueError, match="权重之和应为 1"):
        _run_portfolio([0.3, 0.38, 0.3])


# ---------------- ④ 潜在耦合守卫（非缺陷：API 已用 ge=20 守住） ----------------

def test_risk_scheme_silently_degrades_below_cov_window_20():
    """``cov_window<20`` ⇒ 风险类方案**必然**回退等权（守卫这条隐藏耦合）。

    机理：``_trailing_returns`` 的 ``min_obs`` 默认 **21**，而它只切片
    ``closes_hist[-(cov_window+1):]`` ⇒ 需要 ``cov_window+1 >= 21``。
    当前两个 HTTP API 都用 ``Field(..., ge=20)`` 恰好守住（``/backtest/run`` 与
    ``/portfolio/backtest``），所以**不是活缺陷**；但一旦有人放宽该下界或改动
    ``min_obs`` 默认值，`risk_parity`/`max_div`/`inverse_vol` 会**静默**退化成等权
    （只留一条 warning 日志）⇒ 本用例把这条耦合钉住。
    """
    from app.backtest.engine import _trailing_returns
    closes = [{s: 10.0 + k * 0.01 for s in SYMS} for k in range(40)]
    assert _trailing_returns(closes, SYMS, 19) is None, "cov_window=19 应立即判定观测不足"
    assert _trailing_returns(closes, SYMS, 20) is not None, "cov_window=20 是可用下界"
    # API 下界必须与上面的可用下界一致
    src = (BACKEND_ROOT / "app" / "api" / "v1" / "backtest.py").read_text(encoding="utf-8")
    assert "cov_window: int = Field(60, ge=20" in src, "API 的 cov_window 下界被放宽/改动"
    pf = (BACKEND_ROOT / "app" / "api" / "v1" / "portfolio.py").read_text(encoding="utf-8")
    assert "cov_window: int = Field(60, ge=20" in pf, "portfolio API 的 cov_window 下界被改动"


# ---------------- ⑤ 披露字段的序列化与接线（端到端） ----------------

def test_disclosure_payloads_are_json_safe_and_wired_into_responses():
    """披露字段必须 JSON 可序列化且**真的进了响应 dict**。

    既有 API 用例（`test_api.py`）调用 `/backtest/run` 时都不传 `weight_cap`
    （默认 0 ⇒ 披露为空），因此"非空披露"的序列化路径没人覆盖过：
    若里面混入 numpy 标量，`JSONResponse` 会在**生产**才炸。这里用引擎真实产物
    + 端口函数产物双重断言，并锁住响应接线（防止重构时字段被丢掉）。
    """
    import json

    uni = _flat_universe()
    sig = _signal(uni)
    res = run_backtest(uni, sig, top_k=4, weighting="score_weighted", weight_cap=0.2)
    assert res.weight_cap_info, "非空披露缺失"
    raw = json.dumps(res.weight_cap_info, ensure_ascii=False)
    assert json.loads(raw)["cap"] == pytest.approx(0.2)
    # numpy 标量会被 json 直接拒绝 ⇒ 这一步即可证明全部是 Python 原生类型
    assert "0.8" in raw

    norm = _run_portfolio([0.3, 0.395, 0.3])["weights_normalization"]
    assert json.loads(json.dumps(norm, ensure_ascii=False))["factor"] > 1.0

    api_src = (BACKEND_ROOT / "app" / "api" / "v1" / "backtest.py").read_text(encoding="utf-8")
    assert '"weight_cap_info": res.weight_cap_info' in api_src, "回测响应未接线披露字段"
    res_src = (BACKEND_ROOT / "app" / "api" / "v1" / "research.py").read_text(encoding="utf-8")
    assert '"weight_cap_info": sol.get("weight_cap_info", {})' in res_src
    assert '"expected_returns": sol.get("expected_returns", {})' in res_src
    pf_src = (BACKEND_ROOT / "app" / "domain" / "portfolio.py").read_text(encoding="utf-8")
    assert '"weights_normalization": weights_normalization' in pf_src


# ---------------- ⑥ 前端披露锁（无前端测试框架 ⇒ 源码断言） ----------------

def test_frontend_discloses_infeasible_weight_cap():
    """上限不可行/未生效必须在控制台可见（否则 P1-13 只修了 API，用户照样误读）。

    前端无测试框架（项目既有做法见
    `test_signal_analysis_caliber.py::test_frontend_type_declares_all_backend_keys`），
    故对源码断言，防止后续改版把告警行静默删掉。
    """
    src_root = BACKEND_ROOT.parent / "frontend" / "src"
    if not src_root.exists():
        pytest.skip("前端目录不在本工作区")
    ts = (src_root / "api" / "research.ts").read_text(encoding="utf-8")
    assert "weight_cap_info" in ts, "research.ts 未声明后端返回的上限披露字段"
    for key in ("feasible", "cap_enforced", "invested_ratio", "max_weight",
                "max_invested_ratio", "min_feasible_cap", "note"):
        assert key in ts, f"research.ts 的 weight_cap_info 缺字段 {key}"
    page = (src_root / "pages" / "Research" / "index.tsx").read_text(encoding="utf-8")
    assert "weight_cap_info" in page, "Research 页未消费 weight_cap_info"
    assert "role=\"alert\"" in page and "note" in page, "未渲染上限告警文案"
    assert "invested_ratio" in page, "未显示权重合计（半仓时用户无从判断）"