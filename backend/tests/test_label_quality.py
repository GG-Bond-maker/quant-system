"""标签数据质量测试（第二阶段要求）。

覆盖：极端收益 / NaN / infinite / future contamination。
"""
from __future__ import annotations

import sys
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

BACKEND_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(BACKEND_ROOT))

from app.ml.features import build_factors, factor_columns  # noqa: E402
from app.ml.labeling import (  # noqa: E402
    DEFAULT_MAX_ABS_RETURN, LabelPolicy, build_forward_return_labels,
    future_contamination_check,
)

N_DAYS = 300


def _make_ohlcv(seed: int = 7, n_days: int = N_DAYS) -> pd.DataFrame:
    """构造可复现的多标的 OHLCV（价格随机游走，无公司行为）。"""
    rng = np.random.default_rng(seed)
    rows = []
    dates = pd.bdate_range("2023-01-02", periods=n_days)
    for sym in ("AAA.SZ", "BBB.SZ"):
        close = 100.0 * np.exp(np.cumsum(rng.normal(0, 0.01, n_days)))
        for i, d in enumerate(dates):
            c = float(close[i])
            rows.append({
                "symbol": sym, "date": d,
                "open": c * (1 + rng.normal(0, 0.002)),
                "high": c * (1 + abs(rng.normal(0, 0.005))),
                "low": c * (1 - abs(rng.normal(0, 0.005))),
                "close": c,
                "volume": float(rng.integers(1e5, 1e6)),
                "amount": float(rng.integers(1e7, 1e8)),
            })
    return pd.DataFrame(rows)


# ---------------------------------------------------------------- 极端收益
def test_label_winsorize_clips_extreme_returns():
    """winsorize 模式：极端收益被截尾，样本数不变，且 |label| 不超阈值。"""
    df = _make_ohlcv()
    feats = build_factors(df)

    # 注入一个人为的复权断裂（模拟 CRIT-002 事故）
    feats = feats.sort_values(["symbol", "date"]).reset_index(drop=True)
    mask = (feats["symbol"] == "AAA.SZ") & (feats["date"] == feats["date"].unique()[10])
    feats.loc[mask, "close"] = feats.loc[mask, "close"] * 0.0001  # 价格塌缩

    policy = LabelPolicy(horizon=5, mode="winsorize", max_abs_return=0.5)
    out, rep = build_forward_return_labels(feats, policy)

    assert rep.n_extreme >= 1, "断裂应产生极端标签"
    assert rep.n_winsorized >= 1
    assert rep.n_dropped == 0, "winsorize 模式不应剔除样本"
    assert len(out) == rep.n_raw - rep.n_missing_label
    assert out["label_ret"].abs().max() <= 0.5 + 1e-9
    assert rep.max_abs_after <= 0.5 + 1e-9
    assert rep.std_after < rep.std_before, "截尾后 std 必须下降"


def test_label_reject_drops_extreme_and_reports_counts():
    """reject 模式：极端样本被剔除，且报告里原始/异常/处理后数量自洽。"""
    df = _make_ohlcv()
    feats = build_factors(df)
    feats = feats.sort_values(["symbol", "date"]).reset_index(drop=True)
    mask = (feats["symbol"] == "BBB.SZ") & (feats["date"] == feats["date"].unique()[20])
    feats.loc[mask, "close"] = feats.loc[mask, "close"] * 500.0  # 价格暴涨

    policy = LabelPolicy(horizon=5, mode="reject", max_abs_return=0.5)
    out, rep = build_forward_return_labels(feats, policy)

    assert rep.n_extreme >= 1
    assert rep.n_winsorized == 0
    assert rep.n_dropped == rep.n_extreme
    # 数量守恒：原始 - 无标签(尾部) - 非有限 - 剔除 == 处理后
    assert (rep.n_raw - rep.n_missing_label - rep.n_non_finite - rep.n_dropped
            == rep.n_final)
    assert len(out) == rep.n_final
    assert out["label_ret"].abs().max() <= 0.5 + 1e-9


def test_label_policy_rejects_bad_config():
    with pytest.raises(ValueError):
        LabelPolicy(horizon=0)
    with pytest.raises(ValueError):
        LabelPolicy(mode="nonsense")  # type: ignore[arg-type]
    with pytest.raises(ValueError):
        LabelPolicy(max_abs_return=0)


# ---------------------------------------------------------------- NaN / inf
def test_label_handles_nan_prices():
    """价格为 NaN -> 标签 NaN -> 按策略剔除，且统计正确。"""
    df = _make_ohlcv()
    feats = build_factors(df)
    feats = feats.sort_values(["symbol", "date"]).reset_index(drop=True)
    # 在中间某日把 close 置 NaN
    mid = feats["date"].unique()[50]
    feats.loc[feats["date"] == mid, "close"] = np.nan

    policy = LabelPolicy(horizon=5, mode="winsorize", drop_non_finite=True)
    out, rep = build_forward_return_labels(feats, policy)

    assert rep.n_non_finite > 0
    assert np.isfinite(out["label_ret"].to_numpy()).all(), "处理后不得残留非有限值"
    assert len(out) == rep.n_final


def test_label_handles_infinite_prices():
    """价格为 ±inf -> 标签非有限 -> 必须被清除。"""
    df = _make_ohlcv()
    feats = build_factors(df)
    feats = feats.sort_values(["symbol", "date"]).reset_index(drop=True)
    mid = feats["date"].unique()[60]
    feats.loc[feats["date"] == mid, "close"] = np.inf
    mid2 = feats["date"].unique()[61]
    feats.loc[feats["date"] == mid2, "close"] = -np.inf

    policy = LabelPolicy(horizon=5, mode="reject")
    out, rep = build_forward_return_labels(feats, policy)

    assert rep.n_non_finite > 0
    arr = out["label_ret"].to_numpy(dtype="float64")
    assert np.isfinite(arr).all(), "±inf 必须被清除"
    assert not np.isnan(arr).any()


def test_label_tail_rows_have_no_label():
    """每个 symbol 最后 horizon 行无未来价格 -> 不应产生标签（而非填 0）。"""
    df = _make_ohlcv(n_days=120)
    feats = build_factors(df)
    horizon = 5
    out, rep = build_forward_return_labels(feats, LabelPolicy(horizon=horizon))
    assert rep.n_missing_label == horizon * feats["symbol"].nunique()
    # 尾部日期不得出现在结果中
    last_date = pd.to_datetime(out["date"]).max()
    assert last_date < pd.Timestamp(feats["date"].max())


# ---------------------------------------------------------------- 防未来泄漏
def test_label_uses_future_price_only():
    """标签必须等于 close[t+N]/close[t]-1（确实使用未来价格，这是标签的本分）。"""
    df = _make_ohlcv(n_days=80)
    feats = build_factors(df).sort_values(["symbol", "date"]).reset_index(drop=True)
    h = 5
    out, _ = build_forward_return_labels(feats, LabelPolicy(horizon=h, mode="reject"))

    # 按 (symbol, date) 对齐比较，不能按位置（剔除尾部后行号会错位）
    raw = feats.copy()
    raw["_exp"] = raw.groupby("symbol")["close"].transform(
        lambda x: x.shift(-h) / x - 1.0)
    m = out.merge(raw[["symbol", "date", "_exp"]], on=["symbol", "date"],
                  how="left", validate="one_to_one")
    assert m["_exp"].notna().all(), "结果中不应残留无标签行"
    np.testing.assert_allclose(m["label_ret"].to_numpy(), m["_exp"].to_numpy(),
                               rtol=1e-9)


def test_label_is_not_leaked_into_features():
    """构建标签不得改写任何特征列（特征与标签必须隔离）。"""
    df = _make_ohlcv(n_days=120)
    feats = build_factors(df).sort_values(["symbol", "date"]).reset_index(drop=True)
    before = feats.copy()
    out, _ = build_forward_return_labels(feats, LabelPolicy(horizon=5))

    key = ["symbol", "date"]
    common = [c for c in before.columns if c in out.columns and c != "label_ret"]
    a = out[common].sort_values(key).reset_index(drop=True)
    b = before[common].sort_values(key).reset_index(drop=True)
    # 仅比较保留下来的行（标签构建只删行，不改值）
    kept = a.merge(b, on=key, how="left", suffixes=("", "_orig"))
    assert kept["close_orig"].notna().all()
    for c in common:
        if c in key:
            continue
        np.testing.assert_allclose(
            kept[c].to_numpy(dtype="float64"),
            kept[f"{c}_orig"].to_numpy(dtype="float64"),
            rtol=1e-12, equal_nan=True, err_msg=f"特征列 {c} 被标签构建改写")


def test_features_have_no_future_contamination():
    """PIT 稳定性：截断未来数据后，历史特征值必须逐列不变。

    这是最严格的未来函数检测 —— 若特征用到 shift(-N) 或全序列统计量，
    截断后历史值必然改变。
    """
    raw = _make_ohlcv(seed=11, n_days=400)
    cutoff = pd.Timestamp(raw["date"].unique()[-60])
    ok, bad = future_contamination_check(
        build_factors, raw, cutoff, factor_columns(build_factors(raw)))
    assert ok, f"检测到未来信息污染，受影响列: {bad}"


# ---------------------------------------------------------------- 报告可审计
def test_label_report_is_auditable():
    """报告必须包含原始/异常/处理后，且可序列化。"""
    df = _make_ohlcv()
    feats = build_factors(df)
    _, rep = build_forward_return_labels(feats, LabelPolicy(horizon=5))
    d = rep.as_dict()
    for k in ("n_raw", "n_extreme", "n_final", "mode", "horizon",
              "max_abs_return", "std_before", "std_after"):
        assert k in d, f"报告缺少字段 {k}"
    assert d["n_final"] <= d["n_raw"]
    assert d["max_abs_return"] == DEFAULT_MAX_ABS_RETURN
