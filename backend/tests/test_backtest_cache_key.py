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
    # 注意 max_participation 的取值需**不同于当前默认值**才能验证「入键」；
    # 2026-10-01 默认值由 0.0 改为 0.05，故此处改用 0.10。
    for field, value in (("slippage_bps", 9.0), ("decay_bps", 20.0),
                         ("impact_model", "sqrt"), ("max_participation", 0.10),
                         ("weighting", "risk_parity"), ("dropout_n", 5),
                         ("cov_window", 120), ("weight_cap", 0.3),
                         ("n_trials", 10), ("model_version", "v2")):
        assert base != _run_cache_key(_run_req(enable_friction=True, **{field: value})), field


def test_strategy_key_differs_by_walk_forward_and_folds():
    a = _strategy_cache_key(_strategy_req())
    b = _strategy_cache_key(_strategy_req(walk_forward=True))
    c = _strategy_cache_key(_strategy_req(walk_forward=True, wf_folds=5))
    assert a != b != c


def test_max_participation_default_is_five_percent():
    """流动性闸门默认 5%（2026-10-01 由 0.0 改为 0.05）。

    背景：0.0 表示「不限制」，会让大资金回测把远超市场承接量的订单
    当作全部成交，系统性高估收益。清单要求「单笔不超成交量 5%-10%」。
    同时 BrokerConfig（引擎侧）默认值必须与 API 侧一致，否则两处漂移。
    """
    from app.backtest.broker import BrokerConfig

    assert BacktestRequest.model_fields["max_participation"].default == 0.05
    assert BrokerConfig().max_participation == 0.05


def test_strategy_key_differs_by_init_cash():
    assert _strategy_cache_key(_strategy_req()) != _strategy_cache_key(
        _strategy_req(init_cash=5_000_000.0))


def test_strategy_key_differs_by_use_legacy_engine():
    """P1-2：`use_legacy_engine` 必须入键（两套引擎的闸门口径完全不同）。

    `/strategy-run` 是**先查缓存再执行**，而 `use_legacy_engine=true` 走旧引擎
    （无停牌/涨跌停/T+1 闸门，结果偏乐观）、`false` 走真实闸门。该 flag 此前不在
    键里 ⇒ 600s 内两者互取缓存，返回"另一套引擎"的结果，连载荷里的
    `liquidity.engine="legacy_no_gates"` 披露都与实际所用引擎不符。
    最小验证即本用例（键必须不同）。
    """
    legacy = _strategy_cache_key(_strategy_req(use_legacy_engine=True))
    gated = _strategy_cache_key(_strategy_req(use_legacy_engine=False))
    default = _strategy_cache_key(_strategy_req())
    assert legacy != gated, "use_legacy_engine 必须区分缓存键"
    assert "legacy1" in legacy and "legacy0" in gated, (
        f"键内应显式带引擎标记：{legacy} / {gated}")
    assert default == gated, "默认（False）应与显式 False 同键"
    # 另一条独立性：改 flag 不影响其它字段仍入键
    assert legacy != _strategy_cache_key(
        _strategy_req(use_legacy_engine=True, wf_folds=5))


def test_strategy_key_differs_by_max_participation():
    """2026-10-01（代码审查 #1）：`max_participation` 必须入策略回测缓存键。

    同一根因链：该字段此前**根本不存在于** `StrategyBacktestRequest`，本次修复
    才第一次生效。若不入键，则默认 0.05（有流动性闸门）与显式 0.0（无闸门）
    在 600s TTL 内互取缓存，返回另一套流动性约束下的收益/成交结构，而载荷里的
    `liquidity.participation_cap` 披露会与实际不符。
    """
    assert _strategy_cache_key(_strategy_req()) != \
        _strategy_cache_key(_strategy_req(max_participation=0.0))
    assert _strategy_cache_key(_strategy_req(max_participation=0.05)) != \
        _strategy_cache_key(_strategy_req(max_participation=0.10))
    # 键内应可读地带上该值（与 legacy 标记同风格）
    assert "mp0.05" in _strategy_cache_key(_strategy_req())
