"""审计 P0-7 防回归：因子分层/多空净值**不得有前视偏差**。

缺陷（2026-09-21 全栈审计，P0）：
    ``gp_miner.evaluate_expr_detail(with_groups=True)`` 与 ``factor_report`` 用
    ``close_w.pct_change()``（= close[d]/close[d-1]-1，即 d 日**已经走完**的收益）
    作为分组持仓收益，却用 **d 日的因子值**分组 —— 等价于"收盘后拿到信号，去持有
    当天已经实现的行情"。自反式因子（表达式直接引用 ret_1、动量列等当日收益派生量）
    会因此得到天文数字净值（审计实测 L/S 净值 1.7e10 量级），并直接展示给用户。

修复：改用**前向一日收益** ``close_w.shift(-1)/close_w - 1``（d 日收盘建仓，赚 d→d+1）。

本用例的鉴别逻辑（关键）：
    在同一份**独立同分布**收益面板上构造两个因子
      * ``same_day`` = 当日收益  —— 真实预测力为 0；
      * ``next_day`` = 次日收益  —— 完全预见（clairvoyant）。
    正确口径下：same_day 的多空净值应≈无边缘（漂在 1 附近），
                next_day 的多空净值应爆炸式增长。
    若前视偏差回归，两者的**大小关系会反转**（same_day 变成暴富的那个），
    因此 ``discriminator`` 断言是本缺陷的可靠哨兵。
"""
from __future__ import annotations

import sys
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

BACKEND_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(BACKEND_ROOT))

from app.ml.gp_miner import evaluate_expr_detail, factor_report  # noqa: E402

N_SYMBOL = 60
N_DAY = 400


@pytest.fixture(scope="module")
def panel() -> pd.DataFrame:
    """独立同分布收益面板：same_day / next_day 两个因子列。"""
    rng = np.random.default_rng(20260921)          # 固定种子，确定性
    dates = pd.bdate_range("2022-01-03", periods=N_DAY)
    rets = rng.normal(0.0, 0.02, size=(N_DAY, N_SYMBOL))
    close = 100.0 * np.cumprod(1.0 + rets, axis=0)
    frames = []
    for s in range(N_SYMBOL):
        frames.append(pd.DataFrame({
            "date": dates,
            "symbol": f"{600000 + s}.SH",
            "close": close[:, s],
            "same_day": rets[:, s],
            "next_day": np.concatenate([rets[1:, s], [np.nan]]),
        }))
    return pd.concat(frames, ignore_index=True)


def _ls_nav(panel: pd.DataFrame, expr: str) -> float:
    res = evaluate_expr_detail(expr, panel, {"close", "same_day", "next_day"},
                               5, with_groups=True, min_days=30)
    assert res is not None, f"表达式 {expr} 评估失败"
    return float(res["long_short_nav"][-1]["nav"])


def test_no_lookahead_discriminator(panel: pd.DataFrame) -> None:
    """哨兵断言：只有"次日收益"因子才允许暴富，当日收益因子必须无边缘。"""
    nav_same = _ls_nav(panel, "same_day")
    nav_next = _ls_nav(panel, "next_day")

    # 1) 无真实预测力的因子不得暴富（前视偏差会让它变成 1e9 量级）
    assert 0.2 < nav_same < 4.0, (
        f"当日收益因子的多空净值 {nav_same:.4g} 远离 1 —— 前视偏差疑似回归"
        f"（用 d 日信号赚了 d 日已实现收益）")
    # 2) 完全预见因子必须显著更优（大小关系反转即为回归）
    assert nav_next > 10.0 * nav_same, (
        f"次日收益因子({nav_next:.4g}) 未显著优于当日收益因子({nav_same:.4g})"
        f" —— 净值口径疑似退回前视偏差")
    # 3) 完全预见因子的量级确认（正确口径下确为天文数字，属预期而非缺陷）
    assert nav_next > 1e3, f"完全预见因子净值 {nav_next:.4g} 异常偏低，口径可能反向"


def test_same_day_factor_long_side_not_self_referential(
    panel: pd.DataFrame,
) -> None:
    """多头端单独复查：当日收益因子的多头不得逐日复利放大。"""
    res = evaluate_expr_detail("same_day", panel, {"close", "same_day", "next_day"},
                               5, with_groups=True, min_days=30)
    assert res is not None
    long_nav = float(res["long_nav"][-1]["nav"])
    assert 0.2 < long_nav < 4.0, (
        f"当日收益因子多头净值 {long_nav:.4g} 异常 —— 多头端前视偏差疑似回归")


def test_nav_basis_is_disclosed(panel: pd.DataFrame) -> None:
    """契约第 6 条：派生净值必须披露口径（basis + kind=platform）。"""
    res = evaluate_expr_detail("same_day", panel, {"close", "same_day", "next_day"},
                               5, with_groups=True, min_days=30)
    assert res is not None
    assert res.get("nav_kind") == "platform"
    basis = res.get("nav_basis") or ""
    assert "d+1" in basis or "次" in basis, f"净值口径未说明起算日: {basis!r}"


def test_factor_report_quintiles_have_no_lookahead(panel: pd.DataFrame) -> None:
    """factor_report 的 5 分组净值同口径：当日因子不得把 q5 复利放大。"""
    rep = factor_report("same_day", panel, {"close", "same_day", "next_day"}, 5,
                        min_days=30)
    assert rep is not None
    annual = rep["quintile_annual"]
    # 无预测力因子：5 个分组的年化都应落在可解释范围（年化 |值| < 500%）
    for q, val in annual.items():
        assert val is None or abs(val) < 5.0, (
            f"{q} 年化 {val} 超出无预测力因子的合理范围 —— 前视偏差疑似回归")
    assert rep.get("quintile_kind") == "platform"
    assert rep.get("quintile_basis")