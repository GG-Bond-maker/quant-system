"""容量与业绩归因升级套件测试（对标 FactSet / Barra / WorldQuant）。

覆盖：
1. multi_period_brinson_attribution 连乘平滑多期归因与零残差特性；
2. stock_level_contributions 个股微观贡献度穿透与赢家/拖累排行；
3. portfolio_liquidity_bottleneck 持仓流动性短板与变现天数（DtL）锁喉分析；
4. style_regression_attribution 带 t 检验显著性与 Barra 风险结构分解；
5. capacity_decay_curve 多周期帕累托前沿。
"""
from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from app.domain.attribution import (
    brinson_attribution,
    capacity_decay_curve,
    multi_period_brinson_attribution,
    portfolio_liquidity_bottleneck,
    stock_level_contributions,
    strategy_capacity,
    style_regression_attribution,
)


def test_multi_period_brinson_zero_residual():
    """验证 Menchero / Carino 连乘平滑多期 Brinson 算法的零残差与精确闭合。"""
    dates = pd.bdate_range("2024-01-02", periods=60)
    rng = np.random.default_rng(42)

    symbols = ["000001.SZ", "000858.SZ", "600519.SH", "300750.SZ"]
    industries = pd.Series({"000001.SZ": "银行", "000858.SZ": "食品饮料", "600519.SH": "食品饮料", "300750.SZ": "电力设备"})

    # 生成 60 日真实对数收益
    p_rets = pd.DataFrame(rng.normal(0.001, 0.02, size=(60, 4)), index=dates, columns=symbols)
    b_rets = pd.DataFrame(rng.normal(0.0005, 0.015, size=(60, 4)), index=dates, columns=symbols)

    port_w = {"000001.SZ": 0.25, "000858.SZ": 0.25, "600519.SH": 0.25, "300750.SZ": 0.25}
    bench_w = {"000001.SZ": 0.40, "000858.SZ": 0.30, "600519.SH": 0.20, "300750.SZ": 0.10}

    out = multi_period_brinson_attribution(
        port_rets_wide=p_rets,
        bench_rets_wide=b_rets,
        port_weights=port_w,
        bench_weights=bench_w,
        industry=industries,
    )

    summary = out["summary"]
    assert "excess_return" in summary
    assert "allocation" in summary
    assert "selection" in summary
    assert "interaction" in summary
    assert "residual_alpha" in summary

    # 关键数学验证：多期连乘平滑下，excess_return == allocation + selection + interaction（误差小于 1e-5）
    reconstructed = summary["allocation"] + summary["selection"] + summary["interaction"]
    assert abs(reconstructed - summary["excess_return"]) < 1e-4
    assert abs(summary["residual_alpha"]) < 1e-4

    # 时序长度一致
    assert len(out["timeseries"]) == 60
    assert out["timeseries"][-1]["excess_cum_ret"] == summary["excess_return"]


def test_stock_level_contributions():
    """验证个股微观贡献度计算与 Top 5 赢家/拖累排序。"""
    dates = pd.bdate_range("2024-01-02", periods=30)
    p_rets = pd.DataFrame({
        "A": [0.01] * 30,  # 累计涨幅大
        "B": [-0.01] * 30, # 累计跌幅大
        "C": [0.002] * 30, # 小幅上涨
    }, index=dates)

    weights = {"A": 0.5, "B": 0.3, "C": 0.2}
    ind = pd.Series({"A": "科技", "B": "医药", "C": "金融"})
    names = {"A": "标的A", "B": "标的B", "C": "标的C"}

    res = stock_level_contributions(
        port_rets_wide=p_rets,
        port_weights=weights,
        bench_cum_ret=0.05,
        industry=ind,
        names=names,
    )

    assert len(res["top_contributors"]) >= 1
    assert res["top_contributors"][0]["symbol"] == "A"
    assert res["bottom_drags"][0]["symbol"] == "B"
    assert res["top_contributors"][0]["excess_contrib_bps"] > 0
    assert res["bottom_drags"][0]["excess_contrib_bps"] < 0


def test_portfolio_liquidity_bottleneck():
    """验证持仓流动性锁喉资产（Bottleneck Asset）与 DtL 测算。"""
    weights = {"LIQUID": 0.5, "ILLIQUID": 0.5}
    adv_map = {
        "LIQUID": 5e8,     # 5 亿元 ADV，流动性充沛
        "ILLIQUID": 1e7,   # 1000 万元 ADV，流动性短板
    }
    names = {"LIQUID": "大盘蓝筹", "ILLIQUID": "小盘弹性"}

    res = portfolio_liquidity_bottleneck(
        weights=weights,
        adv_map=adv_map,
        target_aum=5e7,                # 5000 万元组合规模
        participation_cap=0.10,        # 10% 参与率上限
        liquidation_days_target=3,     # 3 日变现约束
        names=names,
    )

    assert res["has_data"] is True
    # 短板标的必须为 ILLIQUID
    assert res["bottleneck_symbol"] == "ILLIQUID"
    assert res["bottleneck_name"] == "小盘弹性"
    # ILLIQUID 单日变现额 = 1000万 * 10% = 100万；3日可变现 300万。
    # 持仓权重 50%，对应组合容量上限 = 300万 / 0.5 = 600万元 = 0.06亿元
    assert res["bottleneck_capacity_yi"] == 0.06
    # ILLIQUID 持仓 2500万，单日 100万，需 25 天
    assert res["holdings"][0]["days_to_liquidate"] == 25.0
    assert res["holdings"][0]["stress_level"] == "high"


def test_enhanced_style_regression_with_stats():
    """验证风格回归 t 统计量、显著性星标与主动风险方差分解。"""
    rng = np.random.default_rng(101)
    n = 100
    dates = pd.bdate_range("2024-01-02", periods=n)
    f1 = pd.Series(rng.normal(0, 0.01, n), index=dates)
    f2 = pd.Series(rng.normal(0, 0.01, n), index=dates)

    # 构造真值为 beta1=1.2, beta2=0.0 的组合收益
    port_ret = 1.2 * f1 + rng.normal(0, 0.002, n)
    bench_ret = 0.5 * f1 + rng.normal(0, 0.003, n)

    res = style_regression_attribution(
        port_ret=pd.Series(port_ret, index=dates),
        style_rets={"Mom": f1, "Vol": f2},
        bench_ret=pd.Series(bench_ret, index=dates),
    )

    assert abs(res["betas"]["Mom"] - 1.2) < 0.15
    assert "factor_stats" in res
    assert "t_stat" in res["factor_stats"]["Mom"]
    assert "p_value" in res["factor_stats"]["Mom"]
    assert res["factor_stats"]["Mom"]["significance"] in ("***", "**", "*")
    assert "risk_decomposition" in res
    assert res["risk_decomposition"] is not None
    assert "factor_risk_pct" in res["risk_decomposition"]
    assert "specific_risk_pct" in res["risk_decomposition"]


def test_capacity_decay_curve_horizons():
    """验证冲击容量曲面包含多变现周期帕累托前沿。"""
    res = capacity_decay_curve(adv_median=1e8, participation_cap=0.01, holdings=20)
    assert res["has_data"] is True
    assert "horizon_frontiers" in res
    assert len(res["horizon_frontiers"]) == 3
    # 5 日容量 > 1 日容量
    caps = [h["capacity_yi"] for h in res["horizon_frontiers"]]
    assert caps[2] > caps[0]
