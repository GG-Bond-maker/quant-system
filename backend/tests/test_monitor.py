"""前沿演进 Phase 1/2 测试：因子监控（IC/半衰期/PSI/状态机）、AI 日报、NL-to-Factor。

- 纯函数用合成数据精确断言（完美信号 IC=±1、指数衰减半衰期、分布漂移 PSI）；
- API 用 TestClient + conftest 隔离环境（无数据 → 51001 信封）；
- LLM 未配置（LLM_PROVIDER=none 默认）→ nl-to-factor 53000。
"""
from __future__ import annotations

import sys
from datetime import date
from pathlib import Path

import numpy as np
import pandas as pd
import polars as pl
import pytest
from fastapi.testclient import TestClient

BACKEND_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(BACKEND_ROOT))

from app.main import app  # noqa: E402
from app.ml import monitor  # noqa: E402

_ADMIN = {"Authorization": "Bearer aqp-dev-token-change-me"}


@pytest.fixture(scope="module")
def client():
    with TestClient(app) as c:
        yield c


# ---------------- 纯函数：半衰期拟合 ----------------
def test_fit_half_life_exponential():
    """精确指数衰减（半衰期=1）拟合回 1 附近。"""
    ic = {h: 0.05 / 2 ** h for h in (1, 2, 3, 5, 10)}
    hl, note = monitor.fit_half_life(ic)
    assert hl is not None and abs(hl - 1.0) < 0.05
    assert "交易日" in note


def test_fit_half_life_rejects_rising_or_thin():
    """IC 随期数上升 / 有效点不足 → None + 说明。"""
    assert monitor.fit_half_life({1: 0.01, 3: 0.05, 5: 0.08})[0] is None
    assert monitor.fit_half_life({1: 0.05, 2: 0.03})[0] is None
    assert monitor.fit_half_life({1: 0.05, 3: -0.01, 5: -0.02})[0] is None


# ---------------- 纯函数：逐日 RankIC ----------------
def _synthetic_close(n_days=30, n_sym=40, seed=7):
    rng = np.random.default_rng(seed)
    idx = pd.date_range("2024-01-01", periods=n_days, freq="D")
    close = pd.DataFrame(
        np.cumprod(1 + rng.normal(0, 0.01, (n_days, n_sym)), axis=0) * 10,
        index=idx, columns=[f"S{i}" for i in range(n_sym)])
    return close


def test_compute_ic_series_perfect_signal():
    """pred = 未来 5 日收益本身 → RankIC ≈ +1；取反 → ≈ -1。"""
    close = _synthetic_close()
    fwd5 = close.shift(-5) / close - 1.0
    rows = []
    for d in fwd5.index[:20]:
        for sym in close.columns:
            rows.append({"date": d, "symbol": sym, "pred_score": fwd5.loc[d, sym],
                         "model_version": "m1"})
    preds = pl.DataFrame(rows)
    ic = monitor.compute_ic_series(preds, close, horizon=5)
    good = ic["ic"].dropna()
    assert len(good) == 20 and (good > 0.999).all()

    rows_neg = [{**r, "pred_score": -r["pred_score"]} for r in rows]
    ic_neg = monitor.compute_ic_series(pl.DataFrame(rows_neg), close, horizon=5)
    bad = ic_neg["ic"].dropna()
    assert (bad < -0.999).all()


def test_compute_ic_series_skips_narrow_cross_section():
    """截面标的数 < MIN_SYMBOLS_PER_DAY 的日期记 NaN（不输出假 IC）。"""
    close = _synthetic_close()
    fwd5 = close.shift(-5) / close - 1.0
    rows = [{"date": d, "symbol": sym, "pred_score": fwd5.loc[d, sym],
             "model_version": "m1"}
            for d in fwd5.index[:10] for sym in close.columns[:5]]  # 只有 5 只
    ic = monitor.compute_ic_series(pl.DataFrame(rows), close, horizon=5)
    assert ic["ic"].isna().all()


# ---------------- 纯函数：PSI ----------------
def _synthetic_features(mean_recent: float, n_days=300, n_sym=5, seed=11):
    rng = np.random.default_rng(seed)
    idx = pd.date_range("2023-01-01", periods=n_days, freq="D")
    vals = rng.normal(0, 1, (n_days, n_sym))
    vals[-20:] = rng.normal(mean_recent, 1, (20, n_sym))  # 最近 20 日漂移
    rows = [{"date": d, "symbol": f"S{j}", "ret_5": vals[i, j]}
            for i, d in enumerate(idx) for j in range(n_sym)]
    return pl.DataFrame(rows)


def test_compute_psi_detects_drift_and_stability():
    """分布一致 → PSI 接近 0；均值漂移 3σ → PSI 超过降级线。"""
    stable = monitor.compute_psi(_synthetic_features(0.0))
    assert stable["ok"] and stable["max"] < 0.10
    drifted = monitor.compute_psi(_synthetic_features(3.0))
    assert drifted["ok"] and drifted["max"] > monitor.PSI_DEGRADED
    assert drifted["top"][0]["factor"] == "ret_5"


# ---------------- 纯函数：KS（与 PSI 同切分互验） ----------------
def _ref_ks(b: np.ndarray, r: np.ndarray) -> float:
    """暴力参考实现：逐观测点比较两侧经验 CDF（与 ks_two_sample 独立实现）。"""
    bs, rs = np.sort(b), np.sort(r)
    d = 0.0
    for x in np.concatenate([bs, rs]):
        d = max(d, abs((bs <= x).mean() - (rs <= x).mean()))
    return d


def test_ks_two_sample_matches_reference():
    """ks_two_sample 与独立暴力实现逐位一致；同分布 D≈0、完全分离 D=1。"""
    rng = np.random.default_rng(3)
    b = rng.normal(0, 1, 500)
    r = rng.normal(0.1, 1, 300)
    assert abs(monitor.ks_two_sample(b, r) - _ref_ks(b, r)) < 1e-12
    # 同分布：D 趋近 0
    same = rng.normal(0, 1, 400)
    assert monitor.ks_two_sample(same, same) == 0.0
    # 完全分离：D = 1
    assert monitor.ks_two_sample(np.array([1.0, 2.0, 3.0]), np.array([10.0, 11.0])) == 1.0


def test_compute_ks_dual_basis_with_psi():
    """KS 与 PSI 同切分：稳定样本 KS 低；均值漂移 3σ 时 KS 超临界因子计数 ≥1。"""
    stable = monitor.compute_ks(_synthetic_features(0.0))
    assert stable["ok"] and stable["max"] < 0.3
    assert "PSI" in stable["note"]
    drifted = monitor.compute_ks(_synthetic_features(3.0))
    assert drifted["ok"] and drifted["max"] > 0.5
    assert drifted["n_over_crit"] >= 1
    assert drifted["top"][0]["factor"] == "ret_5"


# ---------------- 纯函数：状态机 ----------------
def test_state_machine_thresholds():
    assert monitor._ic_state(-0.01, 0.03, 0.01) == "degraded"       # 反向
    assert monitor._ic_state(0.014, 0.03, 0.01) == "degraded"       # <1.5σ
    assert monitor._ic_state(0.018, 0.03, 0.01) == "watch"          # <1σ
    assert monitor._ic_state(0.025, 0.03, 0.01) == "healthy"
    assert monitor._ic_state(-0.01, None, None) == "degraded"       # 无历史仅看正负
    assert monitor._drift_state(0.30) == "degraded"
    assert monitor._drift_state(0.15) == "watch"
    assert monitor._drift_state(0.05) == "healthy"
    assert monitor._drift_state(None) == "unknown"
    assert monitor._worst(["healthy", "degraded"]) == "degraded"
    assert monitor._worst(["unknown", "healthy"]) == "healthy"


# ---------------- 纯函数：date 列口径归一化（异构 predictions 回归） ----------------
def test_normalize_date_col_variants():
    """Date 原样；Datetime -> Date；Utf8 毫秒串解析；无法解析置空；非法 dtype 报错。"""
    # Date：原样返回
    d0 = pl.DataFrame({"date": [date(2024, 6, 5)]})
    assert monitor._normalize_date_col(d0).schema["date"] == pl.Date
    # Datetime（任意时间单位）-> Date
    for unit in ("ms", "us", "ns"):
        d1 = pl.DataFrame({"date": [date(2024, 6, 5)]}).with_columns(
            pl.col("date").cast(pl.Datetime(unit)))
        assert monitor._normalize_date_col(d1).schema["date"] == pl.Date
    # Utf8 毫秒串 -> Date（取前 10 位）
    d2 = pl.DataFrame({"date": ["2024-06-05 00:00:00.000"]})
    assert monitor._normalize_date_col(d2)["date"].to_list() == [date(2024, 6, 5)]
    # 无法解析 -> 置空（不抛），且不伪造
    d3 = pl.DataFrame({"date": ["2024-06-05", "not-a-date", None]})
    assert monitor._normalize_date_col(d3)["date"].to_list() == [date(2024, 6, 5), None, None]
    # 其它 dtype -> 明确报错（fail loudly，不猜测）
    d4 = pl.DataFrame({"date": [12345]})
    with pytest.raises(TypeError):
        monitor._normalize_date_col(d4)


def test_predictions_frame_tolerates_heterogeneous_date_dtypes(tmp_path, monkeypatch):
    """回归：predictions 目录混有 Date 与 Utf8("2024-06-05 00:00:00.000") 不得崩。

    旧实现直接 ``.cast(pl.Date)``：``diagonal_relaxed`` 把 Date + Datetime + Utf8
    异构列抬升为 String，Datetime 毫秒值被串成 "2024-06-05 00:00:00.000"，转 Date
    即抛 ``InvalidOperationError``（全量套件 test_monitor_run_runs 的崩溃根因）。
    """
    from types import SimpleNamespace

    pred_dir = tmp_path / "predictions"
    pred_dir.mkdir(parents=True)
    # 生产口径：pl.Date
    pl.DataFrame({"date": [date(2024, 6, 5)], "symbol": ["600519.SH"],
                  "pred_score": [0.5], "model_version": ["m1"]}).write_parquet(
        pred_dir / "date=20240605.parquet")
    # 污染口径：Utf8 毫秒串（外部/旧工具直写）
    pl.DataFrame({"date": ["2024-06-06 00:00:00.000"], "symbol": ["000001.SZ"],
                  "pred_score": [0.1], "model_version": ["m1"]}).write_parquet(
        pred_dir / "date=20240606.parquet")

    monkeypatch.setattr(monitor, "get_settings",
                        lambda: SimpleNamespace(DATA_ROOT=tmp_path))
    df = monitor._predictions_frame()   # 修复前此处抛 InvalidOperationError
    assert df.schema["date"] == pl.Date
    assert set(df["date"].to_list()) == {date(2024, 6, 5), date(2024, 6, 6)}


def test_features_frame_cache_holds_normalized_frame(tmp_path, monkeypatch):
    """features 缓存只能存归一化后的 Date 帧：Utf8 命中帧不得绕过修复。"""
    from types import SimpleNamespace

    feat_dir = tmp_path / "features" / "version=test" / "symbol=__all__"
    feat_dir.mkdir(parents=True)
    pl.DataFrame({"date": ["2024-06-05 00:00:00.000"], "symbol": ["600519.SH"],
                  "ret_5": [0.1]}).write_parquet(feat_dir / "year=2024.parquet")

    monkeypatch.setattr(monitor, "get_settings",
                        lambda: SimpleNamespace(DATA_ROOT=tmp_path))
    monitor._FEATURES_CACHE.clear()
    try:
        df = monitor._features_frame(days=10)
        assert df.schema["date"] == pl.Date
        # 缓存里存的也必须是 Date 帧（否则下次命中会绕过归一化）
        assert monitor._FEATURES_CACHE["all"][1].schema["date"] == pl.Date
    finally:
        monitor._FEATURES_CACHE.clear()


# ---------------- 隔离环境：run_monitor / 日报 ----------------
def test_run_monitor_structural_contract():
    """run_monitor 不抛异常：有数据 → 合法状态机结果；无数据 → ok=False+错误。

    兼容两种会话状态（test_pipeline 可能已向共享临时 DATA_ROOT 写入数据）。
    """
    snap = monitor.run_monitor(trigger="test")
    if snap.get("ok"):
        assert snap["state"] in ("unknown", "healthy", "watch", "degraded")
        assert snap["ic_state"] in ("unknown", "healthy", "watch", "degraded")
        assert "half_life" in snap and "psi" in snap and "ks" in snap
        assert snap["thresholds"]["psi_degraded"] == 0.25
    else:
        assert "不存在" in snap["error"]


def test_build_daily_report_on_empty_env():
    """空环境日报不抛异常，结构完整、数据缺失以 '—' 呈现。"""
    from app.api.v1.report import build_daily_report

    rep = build_daily_report()
    titles = [s["title"] for s in rep["sections"]]
    assert any("数据面" in t for t in titles)
    assert any("因子健康度" in t for t in titles)
    assert any("Brinson" in t for t in titles)   # 无持仓时该节也在（说明原因）
    assert rep["markdown"].startswith("## ")


def test_brinson_section_with_synthetic_positions(tmp_path, monkeypatch):
    """有持仓 + 本地 hfq 行情时：Brinson 节输出口径完整的行业分解。

    等权组合 vs 等权基准 → 配置/选股/交互/残差全 0（纯口径验证，不造收益）。
    """
    from app.api.v1 import report as rep_mod
    from app.core.config import get_settings

    monkeypatch.setenv("DATA_ROOT", str(tmp_path))
    get_settings.cache_clear()
    try:
        days = pd.date_range("2026-01-01", periods=30, freq="D").date
        from app.data.parquet_store import write_year_batch

        for sym in ("000001.SZ", "000002.SZ"):
            write_year_batch(
                "daily_bar_hfq", sym, 2026,
                pl.DataFrame([{"date": d, "symbol": sym,
                               "close": 10.0 + i * 0.1, "volume": 100}
                              for i, d in enumerate(days)]))
        fake = {"positions": {
            "000001.SZ": {"market_value": 600.0, "qty": 60, "pnl": 0.0},
            "000002.SZ": {"market_value": 400.0, "qty": 40, "pnl": 0.0}}}
        monkeypatch.setattr(rep_mod.paper, "account_summary", lambda sess: fake)
        br = rep_mod._brinson_section()
        assert br["ok"] is True and br["n_positions"] == 2
        assert br["window_days"] >= 10
        assert set(br["summary"]) >= {"portfolio_return", "benchmark_return",
                                      "excess_return", "allocation",
                                      "selection", "interaction",
                                      "residual_alpha"}
        # 等权组合（3:2 vs 1:1 基准）权重差 0.1/0.1，但两标的名义权重差异下
        # 超额 = 配置 + 选股 + 交互 + 残差（Brinson 恒等式，逐项核对）
        s = br["summary"]
        identity = s["allocation"] + s["selection"] + s["interaction"] \
            + s["residual_alpha"]
        assert abs(identity - s["excess_return"]) < 1e-6
        assert "等权基准" in br["basis"]
        assert "未分类" in {x["industry"] for x in br["top_sectors"]}
    finally:
        get_settings.cache_clear()


# ---------------- API 行为 ----------------
def test_monitor_health_envelope(client: TestClient):
    r = client.get("/api/v1/monitor/health", headers=_ADMIN)
    body = r.json()
    assert body["code"] == 0
    assert body["data"]["state"] in ("unknown", "healthy", "watch", "degraded")


def test_monitor_run_requires_auth(client: TestClient):
    r = client.post("/api/v1/monitor/run")
    assert r.json()["code"] == 40100


def test_monitor_run_admin(client: TestClient):
    """admin 触发监控：无数据 → 51001 信封；有数据 → 0 + 合法状态（均不 500）。"""
    r = client.post("/api/v1/monitor/run", headers=_ADMIN)
    body = r.json()
    assert body["code"] in (0, 51001)
    if body["code"] == 0:
        assert body["data"]["state"] in ("unknown", "healthy", "watch", "degraded")


def test_report_daily_envelope(client: TestClient):
    r = client.get("/api/v1/report/daily", headers=_ADMIN)
    body = r.json()
    assert body["code"] == 0
    assert "history" in body["data"]


def test_report_generate_requires_auth(client: TestClient):
    r = client.post("/api/v1/report/daily/generate")
    assert r.json()["code"] == 40100


def test_report_generate_admin(client: TestClient):
    r = client.post("/api/v1/report/daily/generate", headers=_ADMIN)
    body = r.json()
    assert body["code"] == 0
    assert body["data"]["date"] and body["data"]["markdown"]


# ---------------- NL-to-Factor ----------------
def test_nl_to_factor_requires_auth(client: TestClient):
    r = client.post("/api/v1/studio/nl-to-factor",
                    json={"text": "5 日动量最强的因子", "horizon": 5})
    assert r.json()["code"] == 40100


def test_nl_to_factor_provider_none(client: TestClient):
    """LLM_PROVIDER 默认 none → 53000 明确提示（不假装生成）。"""
    r = client.post("/api/v1/studio/nl-to-factor", headers=_ADMIN,
                    json={"text": "5 日动量最强的因子", "horizon": 5})
    body = r.json()
    assert body["code"] == 53000
    assert "LLM_PROVIDER" in body["message"]


def test_extract_expression_variants():
    from app.api.v1.studio import _extract_expression

    assert _extract_expression("```text\nRank(close, 5)\n```") == "Rank(close, 5)"
    assert _extract_expression("表达式：Rank(close, 5)\n说明：动量") == "Rank(close, 5)"
    assert _extract_expression("  Mean(volume, 20)  ") == "Mean(volume, 20)"
