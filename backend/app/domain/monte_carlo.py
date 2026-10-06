"""蒙特卡洛置换检验与运气检验模型（纯函数，无 IO）。

量化金融理论依据：
- Bailey, Borwein, López de Prado, Zhu (2014): 伪真实收益与多重检验过拟合；
- Aronson (2006) Evidence-Based Technical Analysis: 白噪音置换检验（Permutation Test）；
- 核心功能：
  1. 交易/日收益序列置换（Reshuffling）：打乱时间顺序，评估最大回撤的路径依赖与破产概率；
  2. 零假设符号翻转/重采样（Luck vs Alpha Test）：构造期望为 0 的纯运气对照组，计算 Luck p-value；
  3. 扇形分位数净值路径（Fan Chart: p5, p25, p50, p75, p95）。
"""
from __future__ import annotations

import numpy as np


def run_monte_carlo_permutation(
    returns: list[float] | np.ndarray,
    n_sims: int = 1000,
    target_dd: float = 0.20,
    seed: int = 42,
) -> dict:
    """对收益序列进行蒙特卡洛置换与运气检验。

    :param returns: 日收益率序列 (如 [0.01, -0.005, ...])
    :param n_sims: 模拟仿真次数 (100 ~ 5000)
    :param target_dd: 目标破产/容忍最大回撤阈值 (如 0.20 代表 20%)
    :param seed: 随机数种子 (保证结果可复现)
    :return: 包含观测指标、运气 p 值、破产概率、分位数净值与回撤分布的字典
    """
    arr = np.asarray(returns, dtype=np.float64)
    # 过滤 NaN / Inf
    arr = arr[np.isfinite(arr)]
    n_days = len(arr)
    if n_days < 10:
        return {
            "error": "有效收益样本不足 10 天，无法进行蒙特卡洛检验",
            "has_data": False,
        }

    rng = np.random.default_rng(seed)

    # 1. 真实观测指标
    mean_ret = float(np.mean(arr))
    std_ret = float(np.std(arr, ddof=1)) if n_days > 1 else 1e-6
    obs_sharpe = float((mean_ret / std_ret) * np.sqrt(252)) if std_ret > 1e-12 else 0.0
    cum_nav = np.cumprod(1.0 + arr)
    running_max = np.maximum.accumulate(cum_nav)
    dds = (cum_nav - running_max) / np.maximum(running_max, 1e-12)
    obs_max_dd = float(abs(np.min(dds)))

    # 2. 模拟置换 (Permutation for Drawdown & Fan Chart)
    # 每一行是一次置换
    sim_max_dds = np.empty(n_sims, dtype=np.float64)
    # 采样 100 个时间步长用于绘制扇形图，避免返回过大数据
    sample_steps = min(n_days, 100)
    step_indices = np.linspace(0, n_days - 1, sample_steps, dtype=int)
    sim_nav_samples = np.empty((n_sims, sample_steps), dtype=np.float64)

    # 3. 零假设检验 (Sign Flipping for Luck p-value: H0: mean return == 0)
    # 消除真实均值，中心化为 0
    centered_arr = arr - mean_ret
    sim_null_sharpes = np.empty(n_sims, dtype=np.float64)

    for i in range(n_sims):
        # 置换时间序列
        perm_rets = rng.permutation(arr)
        p_nav = np.cumprod(1.0 + perm_rets)
        p_rmax = np.maximum.accumulate(p_nav)
        p_dds = (p_nav - p_rmax) / np.maximum(p_rmax, 1e-12)
        sim_max_dds[i] = abs(np.min(p_dds))
        sim_nav_samples[i, :] = p_nav[step_indices]

        # 零假设：符号随机反转 (Rademacher 扰动)
        signs = rng.choice([-1.0, 1.0], size=n_days)
        null_rets = centered_arr * signs
        n_m = np.mean(null_rets)
        n_s = np.std(null_rets, ddof=1)
        sim_null_sharpes[i] = (n_m / n_s) * np.sqrt(252) if n_s > 1e-12 else 0.0

    # 4. 指标统计
    # 运气 p 值：在零假设纯运气下，产生 >= 真实夏普的概率
    luck_p_value = float(np.mean(sim_null_sharpes >= obs_sharpe))

    # 破产/超额回撤概率
    ruin_prob = float(np.mean(sim_max_dds >= target_dd))

    # 扇形分位数路径 (p5, p25, p50, p75, p95)
    fan_p5 = np.percentile(sim_nav_samples, 5, axis=0).tolist()
    fan_p25 = np.percentile(sim_nav_samples, 25, axis=0).tolist()
    fan_p50 = np.percentile(sim_nav_samples, 50, axis=0).tolist()
    fan_p75 = np.percentile(sim_nav_samples, 75, axis=0).tolist()
    fan_p95 = np.percentile(sim_nav_samples, 95, axis=0).tolist()

    # 最大回撤分布百分位数
    dd_p50 = float(np.percentile(sim_max_dds, 50))
    dd_p95 = float(np.percentile(sim_max_dds, 95))
    dd_p99 = float(np.percentile(sim_max_dds, 99))
    dd_worst = float(np.max(sim_max_dds))

    # 夏普零分布直方图 bins (12 个桶)
    hist_counts, bin_edges = np.histogram(sim_null_sharpes, bins=12)
    sharpe_hist = [
        {
            "bin_start": round(float(bin_edges[j]), 3),
            "bin_end": round(float(bin_edges[j + 1]), 3),
            "count": int(hist_counts[j]),
        }
        for j in range(len(hist_counts))
    ]

    is_significant = luck_p_value < 0.05
    if is_significant:
        verdict = f"策略通过统计置换检验 (p={luck_p_value:.3f} < 0.05)，具有显著超额 Alpha，拒绝纯随机运气假设。"
    else:
        verdict = f"策略未通过置换检验 (p={luck_p_value:.3f} >= 0.05)，收益特征与白噪音随机波动无显著统计差异，存在运气驱动成分。"

    return {
        "has_data": True,
        "n_samples": n_days,
        "n_sims": n_sims,
        "observed_metrics": {
            "sharpe": round(obs_sharpe, 3),
            "max_drawdown": round(obs_max_dd, 4),
            "annual_return": round(float(mean_ret * 252), 4),
            "annual_vol": round(float(std_ret * np.sqrt(252)), 4),
        },
        "luck_test": {
            "p_value": round(luck_p_value, 4),
            "is_significant": is_significant,
            "null_sharpe_mean": round(float(np.mean(sim_null_sharpes)), 3),
            "null_sharpe_std": round(float(np.std(sim_null_sharpes)), 3),
            "sharpe_histogram": sharpe_hist,
        },
        "drawdown_risk": {
            "target_drawdown": target_dd,
            "ruin_probability": round(ruin_prob, 4),
            "p50_drawdown": round(dd_p50, 4),
            "p95_drawdown": round(dd_p95, 4),
            "p99_drawdown": round(dd_p99, 4),
            "worst_case_drawdown": round(dd_worst, 4),
        },
        "fan_chart": {
            "steps": [int(x) for x in step_indices],
            "p5": [round(x, 4) for x in fan_p5],
            "p25": [round(x, 4) for x in fan_p25],
            "p50": [round(x, 4) for x in fan_p50],
            "p75": [round(x, 4) for x in fan_p75],
            "p95": [round(x, 4) for x in fan_p95],
        },
        "summary": verdict,
    }
