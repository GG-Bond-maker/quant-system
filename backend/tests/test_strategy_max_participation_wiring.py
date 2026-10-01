"""2026-10-01 代码审查 #1 修复验证：框架策略回测的流动性闸门**真的接上了**。

背景（代码审查发现，`_inbox_codereview-2026-10-01.md` Top3 #1）：
    `StrategyBacktestRequest` **根本没有** `max_participation` 字段，
    `_run_single` 调 `run_strategy` 也**不传**该参数。而 `run_strategy` 的
    形参默认值是 `0.0`（=不限制），所以框架策略路径虽然构造了
    `BrokerConfig(enabled=True)`，流动性闸门实际恒不生效 ⇒ **完全无流动性闸门**。
    更糟的是 `run_strategy` docstring 声称"策略回测路径已走 5% 默认"——
    **注释 ≠ 代码**。

修复：`StrategyBacktestRequest` 增字段（默认 0.05）、`_run_single` 两处
`run_strategy` 调用均显式透传 `req.max_participation`。

本文件是**证伪性**测试（依据 silent-degradation-audit 纪律：
"「测试通过」不是证据，「注入 bug 后测试失败」才是"）。
断言分两层：
    1. 契约层：字段存在且默认 0.05（删掉字段即 FAIL）；
    2. 行为层：**捕获 `_run_single` 实际传给 `run_strategy` 的
       `max_participation` 值**，断言 == req 的值（把透传改回不传即 FAIL）。
若有人回退修复（删字段 / 删透传），本文件必须失败。
"""
from __future__ import annotations

import sys
from pathlib import Path

BACKEND_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(BACKEND_ROOT))

import pandas as pd  # noqa: E402
import pytest  # noqa: E402

from app.api.v1.backtest import (  # noqa: E402
    StrategyBacktestRequest,
    _run_single,
)


def _req(**kw) -> StrategyBacktestRequest:
    base = dict(start="2025-08-01", end="2025-09-30", symbols=["600519"])
    base.update(kw)
    return StrategyBacktestRequest(**base)


def _bars(days: int = 60) -> dict[str, pd.DataFrame]:
    """构造最小可用日线：monotone 上涨，保证策略会产生买入 target。"""
    dates = pd.bdate_range("2025-08-01", periods=days).date
    px = [10.0 + i * 0.1 for i in range(days)]
    df = pd.DataFrame({
        "date": list(dates),
        "open": px,
        "close": px,
        "high": [p * 1.01 for p in px],
        "low": [p * 0.99 for p in px],
        "volume": [1e6] * days,
        "amount": [1e7] * days,
        "limit_up": [p * 1.1 for p in px],
        "limit_down": [p * 0.9 for p in px],
    })
    return {"600519.SH": df}


def _bench(days: int = 60) -> pd.DataFrame:
    dates = pd.bdate_range("2025-08-01", periods=days).date
    px = [3000.0 + i for i in range(days)]
    return pd.DataFrame({"date": list(dates), "close": px})


# ---------------------------------------------------------------- 1. 字段契约

def test_strategy_request_has_max_participation_default_5pct():
    """删掉该字段即 FAIL —— 这正是修复前的状态。"""
    assert "max_participation" in StrategyBacktestRequest.model_fields, (
        "StrategyBacktestRequest 必须声明 max_participation"
        "（修复前缺失 ⇒ 框架策略路径完全无流动性闸门）")
    assert (StrategyBacktestRequest.model_fields["max_participation"].default
            == 0.05)


def test_strategy_request_accepts_overrides():
    assert _req(max_participation=0.10).max_participation == 0.10
    assert _req(max_participation=0.0).max_participation == 0.0


# ---------------------------------------------------------------- 2. 行为层（核心）

@pytest.mark.parametrize("value", [0.05, 0.123, 0.0])
def test_run_single_forwards_max_participation_to_run_strategy(value, monkeypatch):
    """**证伪性核心**：捕获 `_run_single` 实传给 `run_strategy` 的闸门值。

    把 `_run_single` 里的 `max_participation=req.max_participation`
    改回"不传"（或删掉）后，捕获到的值会变成 `run_strategy` 的形参默认
    `0.0`，本用例立即 FAIL。
    """
    captured: dict[str, float] = {}

    import app.backtest.strategy_base as sb

    real_run_strategy = sb.run_strategy

    def spy(*args, **kwargs):
        captured["max_participation"] = kwargs.get("max_participation", 0.0)
        return real_run_strategy(*args, **kwargs)

    # `_run_single` 在函数体内 `from ...backtest.strategy_base import
    # MaCrossStrategy, run_strategy`，是**运行时局部导入** ⇒ 直接 patch
    # 模块属性即可拦截。
    monkeypatch.setattr(sb, "run_strategy", spy, raising=True)

    _run_single("ma_cross", _bars(), _bench(), _req(max_participation=value),
                overrides={})

    assert "max_participation" in captured, (
        "run_strategy 根本没有收到 max_participation（透传缺失 ⇒ 闸门恒不生效）")
    assert captured["max_participation"] == value, (
        f"透传值不符：期望 {value}，实得 {captured['max_participation']}"
        f"（若为 0.0 说明透传被回退，闸门静默失效）")


def test_run_single_framework_branch_uses_broker_gates(monkeypatch):
    """配套：确认走的是框架引擎分支（engine 标记），而非 legacy。"""
    out = _run_single("ma_cross", _bars(), _bench(), _req(), overrides={})
    assert out["engine"] == "framework(broker_gates)"


def test_run_single_legacy_branch_has_no_claim_of_gates(monkeypatch):
    """legacy 分支照旧（不受本修复影响），engine 标记必须诚实。"""
    out = _run_single("ma_cross", _bars(), _bench(),
                      _req(use_legacy_engine=True), overrides={})
    assert out["engine"] == "legacy_no_gates"
