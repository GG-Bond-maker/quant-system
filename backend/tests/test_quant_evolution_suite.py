"""7 大投研与执行板块演进落地特性单元测试套件：
1. 分层风险平价聚类算法 (HRP) 与欧拉齐次风险分解 (%RC / MRC)
2. 截面多元 OLS 残差正交纯化与多重检验校正 (FDR & Deflated IC)
3. 组合净化交叉验证 (CPCV) 与过拟合概率 (PBO)
4. 收益率时间置换蒙特卡洛检验与运气显著性检验 (Luck p-value & Fan Chart)
5. 平方根冲击定律容量衰减曲面与黄金 AUM 测算
"""
import numpy as np
import pandas as pd
import pytest

from app.domain.optimizer import hrp_weights, euler_risk_contributions
from app.ml.gp_miner import orthogonalize_factor_detail
from app.ml.purged_cv import cpcv_pbo_analysis
from app.domain.monte_carlo import run_monte_carlo_permutation
from app.domain.attribution import capacity_decay_curve


def test_hrp_weights_and_euler_risk_contributions():
    # 构造 4 只资产历史收益协方差矩阵（其中两只高度相关）
    np.random.seed(42)
    n = 100
    r1 = np.random.normal(0, 0.02, n)
    r2 = r1 + np.random.normal(0, 0.005, n)  # 高度相关
    r3 = np.random.normal(0, 0.04, n)       # 高波动
    r4 = np.random.normal(0, 0.01, n)       # 低波动
    returns = np.column_stack([r1, r2, r3, r4])

    w = hrp_weights(returns)
    assert len(w) == 4
    assert np.isclose(np.sum(w), 1.0, atol=1e-5)
    assert np.all(w >= 0.0)

    # 欧拉齐次分解
    cov = np.cov(returns, rowvar=False)
    rc, pct_rc = euler_risk_contributions(w, cov)
    assert len(pct_rc) == 4
    assert np.isclose(np.sum(pct_rc), 1.0, atol=1e-4)


def test_orthogonalize_factor_detail():
    # 构造包含 25 只标的截面 (以满足截面回归至少 15 只要求) 的 DataFrame
    np.random.seed(42)
    n_days = 35
    symbols = [f"00000{i:02d}.SZ" for i in range(25)]
    dates = pd.date_range("2025-01-01", periods=n_days, freq="B").strftime("%Y-%m-%d")

    records = []
    for dt in dates:
        for sym in symbols:
            c = float(np.random.uniform(10, 30))
            records.append({
                "date": dt,
                "symbol": sym,
                "open": c * 0.99,
                "high": c * 1.02,
                "low": c * 0.98,
                "close": c,
                "volume": 100000.0,
                "amount": 2000000.0,
                "ret_20": float(np.random.normal(0, 0.05)),
                "vol_20": float(np.random.uniform(0.01, 0.03)),
                "ma_gap_20": float(np.random.normal(0, 0.02)),
                "v_rank_20": float(np.random.uniform(0.1, 0.9)),
            })
    pdf = pd.DataFrame(records)

    fields = {"open", "high", "low", "close", "volume", "amount"}
    res = orthogonalize_factor_detail(
        expr="close / open",
        pdf=pdf,
        fields=fields,
        horizon=5,
        min_days=10,
    )
    assert res is not None
    assert "pure_mean_ic" in res
    assert "raw_mean_ic" in res
    assert "p_value_fdr" in res
    assert "deflated_ic" in res
    assert "base_correlations" in res
    assert len(res["base_correlations"]) > 0


def test_cpcv_pbo_analysis():
    # 构造包含 date, symbol, close 与多个因子列的数据集
    np.random.seed(42)
    n_days = 60
    symbols = ["000001.SZ", "000002.SZ", "000003.SZ"]
    dates = pd.date_range("2025-01-01", periods=n_days, freq="B").strftime("%Y-%m-%d")

    records = []
    for dt in dates:
        for sym in symbols:
            c = float(np.random.uniform(10, 30))
            records.append({
                "date": dt,
                "symbol": sym,
                "close": c,
                "factor_a": float(np.random.randn()),
                "factor_b": float(np.random.randn()),
                "factor_c": float(np.random.randn()),
            })
    df = pd.DataFrame(records)

    res = cpcv_pbo_analysis(
        df=df,
        n_groups=5,
        k_test=2,
        purge_window=2,
        embargo_window=2,
        feature_cols=["factor_a", "factor_b", "factor_c"],
    )
    assert res["has_data"] is True
    assert "pbo" in res
    assert 0.0 <= res["pbo"] <= 1.0
    assert "is_robust" in res
    assert res["n_paths"] > 0
    assert "rank_distribution" in res


def test_monte_carlo_permutation():
    # 模拟 150 天每日收益序列
    np.random.seed(42)
    daily_returns = list(np.random.normal(0.0008, 0.012, 150))
    res = run_monte_carlo_permutation(
        returns=daily_returns,
        n_sims=300,
        target_dd=0.20,
    )
    assert res["has_data"] is True
    assert "luck_test" in res
    assert "p_value" in res["luck_test"]
    assert 0.0 <= res["luck_test"]["p_value"] <= 1.0
    assert "drawdown_risk" in res
    assert "ruin_probability" in res["drawdown_risk"]
    assert "fan_chart" in res
    fan = res["fan_chart"]
    assert "p5" in fan and "p25" in fan and "p50" in fan and "p75" in fan and "p95" in fan
    for i in range(len(fan["p5"])):
        assert fan["p5"][i] <= fan["p25"][i] <= fan["p50"][i] <= fan["p75"][i] <= fan["p95"][i]


def test_capacity_decay_curve():
    res = capacity_decay_curve(
        adv_median=2.5e8,  # 2.5 亿元
        holdings=20,
        rebalance_per_year=12,
        gross_return=0.18,
        gross_vol=0.22,
    )
    assert res["has_data"] is True
    assert res["optimal_aum_yi"] > 0
    assert res["optimal_sharpe"] > 0
    assert len(res["curve"]) > 5
    impacts = [pt["impact_bps"] for pt in res["curve"]]
    for i in range(1, len(impacts)):
        assert impacts[i] >= impacts[i - 1]
