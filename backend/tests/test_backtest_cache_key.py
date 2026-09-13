"""Task 4（整改计划 A-P0-1）：回测缓存键全参数入键。

背景：/run 键漏 init_cash、/strategy-run 键漏 walk_forward/wf_folds——
TTL 600s 内不同参数的请求命中同一键，返回"别人的"回测结果
（docs/audit/2026-09-05-代码审核报告.md P0-1；同类前科见 cache/keys.py
recommend_k 警告注释）。
"""
from __future__ import annotations

import sys
from pathlib import Path

BACKEND_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(BACKEND_ROOT))

from app.api.v1.backtest import (  # noqa: E402
    BacktestRequest,
    StrategyBacktestRequest,
    _run_cache_key,
    _strategy_cache_key,
)


def _run_req(**kw) -> BacktestRequest:
    base = dict(start="2025-08-08", end="2025-09-30")
    base.update(kw)
    return BacktestRequest(**base)


def _strategy_req(**kw) -> StrategyBacktestRequest:
    base = dict(start="2025-08-08", end="2025-09-30", symbols=["600519"])
    base.update(kw)
    return StrategyBacktestRequest(**base)


def test_run_key_differs_by_init_cash():
    assert _run_cache_key(_run_req()) != _run_cache_key(_run_req(init_cash=10_000_000.0))


def test_run_key_differs_by_each_friction_param():
    base = _run_cache_key(_run_req(enable_friction=True))
    for field, value in (("slippage_bps", 9.0), ("decay_bps", 20.0),
                         ("impact_model", "sqrt"), ("max_participation", 0.05),
                         ("weighting", "risk_parity"), ("dropout_n", 5),
                         ("cov_window", 120), ("weight_cap", 0.3),
                         ("n_trials", 10), ("model_version", "v2")):
        assert base != _run_cache_key(_run_req(enable_friction=True, **{field: value})), field


def test_strategy_key_differs_by_walk_forward_and_folds():
    a = _strategy_cache_key(_strategy_req())
    b = _strategy_cache_key(_strategy_req(walk_forward=True))
    c = _strategy_cache_key(_strategy_req(walk_forward=True, wf_folds=5))
    assert a != b != c


def test_strategy_key_differs_by_init_cash():
    assert _strategy_cache_key(_strategy_req()) != _strategy_cache_key(
        _strategy_req(init_cash=5_000_000.0))
