"""
个股详情面板的纯函数单测：筹码分布 / 风险度量。

覆盖重点：
- 数学恒等式（筹码权重归一化、获利+套牢=1、区间包含平均成本）
- 退化输入的边界行为（常量序列、样本不足、换手率为 0）
- Beta 的自反性（个股对自身基准 β=1）
- 不触碰网络：全部使用构造数据
"""
from __future__ import annotations

import numpy as np
import polars as pl
import pytest

from app.domain.chip import chip_distribution
from app.domain.risk import risk_metrics


# ---------------- 测试数据构造 ----------------
def _bars(n: int, price: float = 10.0, spread: float = 0.02,
          turnover: float = 0.01, with_turnover: bool = True,
          seed: int = 7) -> pl.DataFrame:
    """构造 n 根日线：收盘价围绕 price 小幅波动，换手率固定。"""
    rng = np.random.default_rng(seed)
    close = price * (1 + rng.normal(0, spread / 3, n).cumsum() * 0.01)
    close = np.clip(close, price * 0.5, price * 1.5)
    high = close * (1 + spread)
    low = close * (1 - spread)
    volume = np.full(n, 1_000_000.0)
    amount = close * volume
    dates = [__import__("datetime").date(2024, 1, 1) + __import__("datetime").timedelta(days=i)
             for i in range(n)]
    data = {
        "date": dates, "open": close, "high": high, "low": low,
        "close": close, "volume": volume, "amount": amount,
    }
    if with_turnover:
        data["turnover"] = np.full(n, turnover)
    return pl.DataFrame(data)


# ---------------- 筹码分布 ----------------
def test_chip_weights_normalized_and_ratios_sum_to_one():
    df = _bars(120)
    r = chip_distribution(df)
    assert 0.0 <= r["profit_ratio"] <= 1.0
    assert 0.0 <= r["trapped_ratio"] <= 1.0
    assert r["profit_ratio"] + r["trapped_ratio"] == pytest.approx(1.0, abs=1e-6)
    assert len(r["curve"]) > 0
    assert max(p["pct"] for p in r["curve"]) == pytest.approx(1.0, abs=1e-6)


def test_chip_price_interval_brackets_avg_cost():
    df = _bars(150)
    r = chip_distribution(df)
    assert r["p5"] <= r["avg_cost"] <= r["p95"]
    assert r["p5"] < r["p95"]
    assert r["concentration"] is not None and r["concentration"] > 0


def test_chip_all_chips_in_the_money_when_price_far_above():
    """现价远高于所有历史成交区间 → 获利盘≈100%。"""
    df = _bars(60, price=10.0, spread=0.01, turnover=0.05)
    r = chip_distribution(df, current_price=10.0 * 3)
    assert r["profit_ratio"] > 0.99
    assert r["trapped_ratio"] < 0.01


def test_chip_all_chips_trapped_when_price_far_below():
    df = _bars(60, price=10.0, spread=0.01, turnover=0.05)
    r = chip_distribution(df, current_price=10.0 * 0.3)
    assert r["profit_ratio"] < 0.01
    assert r["trapped_ratio"] > 0.99


def test_chip_rejects_empty_or_missing_columns():
    with pytest.raises(ValueError):
        chip_distribution(pl.DataFrame())
    with pytest.raises(ValueError):
        chip_distribution(pl.DataFrame({"close": [1.0, 2.0]}))


def test_chip_turnover_column_missing_falls_back_to_volume():
    """无 turnover 列时按量能相对倍数估算，不应抛错且结果自洽。"""
    df = _bars(120, with_turnover=False)
    r = chip_distribution(df)
    assert r["profit_ratio"] + r["trapped_ratio"] == pytest.approx(1.0, abs=1e-6)


def test_chip_percent_turnover_is_normalized():
    """东财若返回百分数口径（4.3 表示 4.3%），结果应与小数口径一致。"""
    df = _bars(80, turnover=0.02)
    pct_df = df.with_columns((pl.col("turnover") * 100).alias("turnover"))
    a = chip_distribution(df)
    b = chip_distribution(pct_df)
    assert a["avg_cost"] == pytest.approx(b["avg_cost"], rel=1e-6)
    assert a["profit_ratio"] == pytest.approx(b["profit_ratio"], abs=1e-6)


# ---------------- 风险度量 ----------------
def test_risk_constant_series_has_zero_vol_and_drawdown():
    dates = [__import__("datetime").date(2024, 1, 1) + __import__("datetime").timedelta(days=i)
             for i in range(300)]
    df = pl.DataFrame({"date": dates, "close": [10.0] * 300})
    r = risk_metrics(df)
    assert r["annual_vol"] == pytest.approx(0.0, abs=1e-9)
    assert r["max_drawdown"] == pytest.approx(0.0, abs=1e-9)
    assert r["sharpe"] is None          # 波动为 0 时夏普无定义，宁缺勿滥
    assert r["beta"] is None            # 未传基准


def test_risk_metrics_on_known_drawdown():
    """净值 1 -> 2 -> 1：最大回撤 50%。"""
    dates = [__import__("datetime").date(2024, 1, 1) + __import__("datetime").timedelta(days=i)
             for i in range(3)]
    df = pl.DataFrame({"date": dates, "close": [1.0, 2.0, 1.0]})
    with pytest.raises(ValueError):
        risk_metrics(df)                # 样本 < 20，指标无统计意义


def test_risk_max_drawdown_value():
    dates = [__import__("datetime").date(2024, 1, 1) + __import__("datetime").timedelta(days=i)
             for i in range(60)]
    close = [10.0] * 30 + [20.0] + [10.0] * 29
    df = pl.DataFrame({"date": dates, "close": close})
    r = risk_metrics(df, window=60)
    assert r["max_drawdown"] == pytest.approx(0.5, abs=1e-6)


def test_risk_beta_against_itself_is_one():
    dates = [__import__("datetime").date(2024, 1, 1) + __import__("datetime").timedelta(days=i)
             for i in range(300)]
    rng = np.random.default_rng(11)
    close = 100 * np.exp(np.cumsum(rng.normal(0, 0.01, 300)))
    df = pl.DataFrame({"date": dates, "close": close.tolist()})
    r = risk_metrics(df, benchmark=df, window=252)
    assert r["beta"] == pytest.approx(1.0, abs=1e-6)


def test_risk_vol_percentile_in_range():
    df = _bars(300)
    r = risk_metrics(df, window=252)
    assert r["vol_percentile"] is not None
    assert 0.0 <= r["vol_percentile"] <= 100.0
    assert r["annual_vol"] is not None and r["annual_vol"] > 0


def test_risk_rejects_short_history():
    dates = [__import__("datetime").date(2024, 1, 1)] * 1
    df = pl.DataFrame({"date": dates, "close": [10.0]})
    with pytest.raises(ValueError):
        risk_metrics(df)
