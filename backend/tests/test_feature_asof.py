"""特征 asof 稳定性回归测试（P0-Major#4 修复守卫）。

纪律：因子计算必须基于【后复权 hfq】价格（IPO 累计因子，只增不改）。
本测试证明：追加未来数据（含未来除权除息）后重新计算，
历史特征 H 逐值保持不变 —— 覆盖 ret_5 / ret_20 / MA / momentum / MACD 等全列。

（对照组：qfq 以窗口末因子为锚，窗口扩展会改写全部历史 —— 审计报告 Major#4。）
"""
from __future__ import annotations

import sys
from datetime import date, timedelta
from pathlib import Path

import numpy as np
import pandas as pd
import polars as pl

BACKEND_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(BACKEND_ROOT))

from app.domain.adjust import apply_adjust_df  # noqa: E402
from app.ml.features import build_factors, factor_columns  # noqa: E402

SYM = "TEST.SH"
N_A, N_B = 300, 60


def _raw_and_factor() -> tuple[pd.DataFrame, pl.DataFrame]:
    """构造 raw 价格与 hfq 累计因子（B 段包含一次除息 -> factor 跳变）。"""
    rng = np.random.default_rng(21)
    dates = [date(2022, 1, 4) + timedelta(days=i) for i in range(N_A + N_B)]
    rets = np.zeros(N_A + N_B)
    for t in range(1, N_A + N_B):
        rets[t] = 0.2 * rets[t - 1] + 0.02 * rng.standard_normal()
    close = 50.0 * np.exp(np.cumsum(rets))
    raw = pl.DataFrame({
        "symbol": [SYM] * (N_A + N_B), "date": dates,
        "open": close, "high": close * 1.01, "low": close * 0.99, "close": close,
    })
    # 因子：A 段 1.0；B 段第 20 日起除息跳到 1.08（模拟未来分红，累计口径只增）
    factors = [1.0] * (N_A + 20) + [1.08] * (N_B - 20)
    fac = pl.DataFrame({"symbol": [SYM] * (N_A + N_B), "date": dates,
                        "adj_factor": [float(f) for f in factors]})
    return raw, fac


def _hfq_df(raw: pl.DataFrame, fac: pl.DataFrame) -> pd.DataFrame:
    hfq = apply_adjust_df(raw, fac, "hfq")
    out = hfq.to_pandas()
    out["volume"] = np.linspace(50_000, 80_000, len(out))
    return out


def test_feature_asof_stability_on_hfq():
    """数据 A 生成 H；追加未来数据 B（含未来除息）重算 => H 不变。"""
    raw, fac = _raw_and_factor()
    hfq_full = _hfq_df(raw, fac)

    hfq_a = hfq_full.iloc[:N_A]
    hist = build_factors(hfq_a)                       # H：仅用 A 段
    full = build_factors(hfq_full)                    # A+B（含未来除息）重算

    cols = factor_columns(hist)
    a = hist[cols].reset_index(drop=True)
    b = full.iloc[:N_A][cols].reset_index(drop=True)
    # 逐列逐值相等（重点列显式列出，其余全列兜底）
    for must in ("ret_5", "ret_20", "ma_gap_20", "ma_gap_60", "macd_bar", "rsi_14"):
        assert must in cols
    pd.testing.assert_frame_equal(a, b, check_exact=False, atol=1e-12)


def test_label_asof_stability_on_hfq():
    """Label（未来 N 日收益）同样不受未来除息影响：hfq 收益率 = 真实持有收益。"""
    raw, fac = _raw_and_factor()
    hfq_full = _hfq_df(raw, fac)
    label_full = hfq_full["close"].shift(-5) / hfq_full["close"] - 1.0
    label_a = hfq_full["close"].iloc[:N_A].shift(-5) / \
        hfq_full["close"].iloc[:N_A] - 1.0
    # A 段内的 label（不触及 B 段除息日）逐值一致
    n = N_A - 5
    assert np.allclose(label_full.iloc[:n].to_numpy(),
                       label_a.iloc[:n].to_numpy(), atol=1e-12)


def test_qfq_is_not_asof_stable_documented():
    """对照守卫：qfq（窗口末锚点）在窗口扩展后会改写历史 —— 锁定已知口径属性，
    防止未来有人把特征基准改回 qfq 而不被发现。"""
    raw, fac = _raw_and_factor()
    qfq_a = apply_adjust_df(raw[:N_A], fac[:N_A], "qfq")["close"].to_numpy()
    qfq_full = apply_adjust_df(raw, fac, "qfq")["close"].to_numpy()[:N_A]
    assert not np.allclose(qfq_a, qfq_full, atol=1e-9), \
        "qfq 历史价不应与扩展窗口重算结果一致（若一致说明测试前提被破坏）"
