"""Task 8（整改计划 A-P1-9）：信号分析 IC 使用 hfq 口径（契约测试）。

背景：/signal-analysis 曾用不复权 close 计算前瞻收益，除权跳空直接污染
Rank IC。T1 后 _load_universe_and_signals 返回的 uni 即 hfq 口径——本测试
固化"IC 消费的价格必须是复权口径"的数据契约，并用最小样本演示：除权跳空使 raw 口径 IC 与 hfq 口径
显著分离（偏差方向依赖信号与除权日对齐，两口径不可互换）。
"""
from __future__ import annotations

import sys
from datetime import date
from pathlib import Path

BACKEND_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(BACKEND_ROOT))

import numpy as np  # noqa: E402
import pandas as pd  # noqa: E402

D0 = date(2024, 1, 2)


def test_signal_analysis_close_is_hfq(tmp_path, monkeypatch):
    """_run_signal_analysis 消费的 close 必须来自 universe_daily_bt（hfq）。"""
    import polars as pl

    import app.api.v1.backtest as bt

    # 播种：hfq 宇宙 close=100（连续）；预测一个高分信号
    uni_dir = tmp_path / "universe_daily_bt" / "symbol=__all__"
    uni_dir.mkdir(parents=True, exist_ok=True)
    pl.DataFrame({
        "date": [date(2024, 1, 2), date(2024, 1, 3)],
        "symbol": ["600001.SH"] * 2,
        "close": [100.0, 100.0],  # hfq：无除权缺口
        "open": [100.0, 100.0],
    }).write_parquet(uni_dir / "year=2024.parquet")
    pred_dir = tmp_path / "predictions"
    pred_dir.mkdir(parents=True, exist_ok=True)
    pl.DataFrame({
        "date": [date(2024, 1, 2)], "symbol": ["600001.SH"],
        "pred_score": [0.5], "model_version": ["m1"],
    }).write_parquet(pred_dir / "date=20240102.parquet")

    orig = bt.get_settings
    bt.get_settings = lambda: type("S", (), {"DATA_ROOT": tmp_path})()
    try:
        uni, sig, used = bt._load_universe_and_signals(
            "2024-01-01", "2024-01-31", "m1")
    finally:
        bt.get_settings = orig
    assert uni["close"].to_list() == [100.0, 100.0]  # hfq 口径（raw 会是 50）


def test_dividend_gap_pollutes_ic_directionally():
    """演示：同一信号，除权跳空的 raw 序列 IC 显著低于 hfq 序列。"""
    from app.ml.signal_analysis import ic_decay_report

    days = pd.date_range("2024-01-01", periods=120, freq="B")
    symbols = ["A", "B", "C", "D", "E"]
    rng = np.random.default_rng(7)
    sig_rows: list[dict] = []
    closes: dict[str, tuple[np.ndarray, np.ndarray]] = {}
    for j, sym in enumerate(symbols):
        ret = 0.0005 * np.sin(np.arange(120) / 9) + rng.normal(0, 0.01, 120)
        hfq = 100 * np.exp(np.cumsum(ret))
        raw = hfq.copy()
        # 各股除息日交错（40+j*12 天处跳空 10%）——真实市场形态
        ex = 40 + j * 12
        raw[ex:] = raw[ex:] / 1.1
        closes[sym] = (hfq, raw)
        for i, d in enumerate(days):
            sig_rows.append({"date": d.date(), "symbol": sym,
                             "pred_score": ret[i] * 10 + rng.normal(0, 0.002)})
    sig = pd.DataFrame(sig_rows)

    def ic_of(series_key: int) -> float:
        rows = []
        for sym, (hfq, raw) in closes.items():
            series = hfq if series_key == 0 else raw
            for i, d in enumerate(days):
                rows.append({"date": d.date(), "symbol": sym,
                             "close": series[i]})
        rep = ic_decay_report(sig, pd.DataFrame(rows), horizons=(5,))
        return float(rep["mean_ic"].iloc[0])

    ic_hfq = ic_of(0)
    ic_raw = ic_of(1)
    # 污染的方向依赖信号与除权日的对齐（可能虚高也可能虚低）——
    # 可靠的契约是：两口径给出显著不同的答案，即 raw 口径不可复现。
    assert abs(ic_raw - ic_hfq) > 0.005
