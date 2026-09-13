"""§4.4 回测寻优测试（Sprint4-遗留项闭环）：Optuna TPE / walk-forward / 报告落盘。

合成目标函数离线验证（不跑真实回测——回测链路已有既有测试覆盖）。
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

BACKEND_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(BACKEND_ROOT))

from app.backtest.param_search import (  # noqa: E402
    optuna_search, run_search, walk_forward_search,
)


def _unimodal(params: dict) -> dict:
    """单峰目标：真值 x=7.3（sharpe 越接近 0 越大）。"""
    x = float(params["x"])
    return {"sharpe": -(x - 7.3) ** 2 * 0.1, "nav": None}


def test_optuna_tpe_converges():
    """TPE 以少量试验逼近单峰最优（真值 7.3，候选 [1..10]）。"""
    res = optuna_search(_unimodal, {"x": list(range(1, 11))},
                        n_trials=25, seed=42)
    assert 6 <= res.best_params["x"] <= 9
    assert res.best_objective == max(t.objective for t in res.trials)
    assert res.method == "optuna"
    # DSR：nav=None 时 NaN（不造数）
    assert res.deflated_sharpe != res.deflated_sharpe  # NaN


def test_run_search_dispatch_optuna():
    """run_search 统一入口 optuna 分支 + optuna_trials 透传。"""
    res = run_search(_unimodal, {"x": [3, 7, 10]}, method="optuna",
                     optuna_trials=8)
    assert res.method == "optuna" and len(res.trials) == 8
    assert res.best_params["x"] == 7  # 候选中 7 距 7.3 最近


def test_walk_forward_folds_and_overfit():
    """walk-forward：折数/窗口结构 + OOS 均值与过拟合比口径。"""
    calls: list[tuple] = []

    def evaluate_window(ws, we):
        calls.append((ws, we))

        def ev(params: dict) -> dict:
            # IS 窗口内 x 越大越好（制造寻优偏置）；OOS 期望用同一最优参数评估
            return {"sharpe": -abs(float(params["x"]) - 7.3) * 0.1, "nav": None}

        return ev

    windows = [(0, 10, 10, 20), (0, 20, 20, 30), (0, 30, 30, 40)]
    wf = walk_forward_search(evaluate_window, windows,
                             {"x": [3, 5, 7, 9, 10]},
                             method="optuna", n_trials_per_fold=10)
    assert len(wf.folds) == 3
    assert [f.fold for f in wf.folds] == [1, 2, 3]
    # 每折都调用了 IS 与 OOS 两个窗口
    assert len(calls) == 6
    # 真值恒定 → OOS ≈ IS ≈ 0（x=7）→ 过拟合比 ≈ 1
    assert wf.overfit_ratio == wf.overfit_ratio
    assert 0.5 <= wf.overfit_ratio <= 2.0


def test_walk_forward_all_fail_raises():
    """整折试验全失败：grid_search 明确抛错（宁可报错不给假净值）。"""
    def evaluate_window(ws, we):
        def ev(params: dict) -> dict:
            return {"sharpe": float("nan"), "nav": None}
        return ev

    with pytest.raises(ValueError):
        walk_forward_search(evaluate_window, [(0, 1, 1, 2)],
                            {"x": [1, 2]}, method="grid", n_trials_per_fold=2)


def test_opt_report_persist(tmp_path, monkeypatch):
    """寻优报告落盘 MODEL_ROOT/exp/backtest_opt/（时间戳命名 + JSON 可读）。"""
    from app.api.v1.backtest import _persist_opt_report

    from app.core.config import get_settings

    monkeypatch.setattr(get_settings(), "MODEL_ROOT", tmp_path)
    class _Req:  # 最小鸭子类型
        strategy_type = "ma_cross"
        symbols = ["600519.SH"]
        start = "2025-01-01"
        end = "2026-01-01"
    opt = {"method": "walk_forward", "n_folds": 2,
           "mean_oos_sharpe": 0.12, "overfit_ratio": 1.1}
    _persist_opt_report(_Req(), opt)
    files = list((tmp_path / "exp" / "backtest_opt").glob("*.json"))
    assert len(files) == 1
    payload = json.loads(files[0].read_text(encoding="utf-8"))
    assert payload["strategy"] == "ma_cross"
    assert payload["optimization"]["mean_oos_sharpe"] == 0.12
