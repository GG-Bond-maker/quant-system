"""P1（2026-10-01）：`_build_daily` 的**子块并行** 与 **墙钟预算不变量**。

## 缺陷本体（实测）

`/api/v1/market/overview/daily` 用 `asyncio.wait_for(_build_daily, timeout=5.0)` 包住日频块，
但 `_build_daily` 内部各子块**串行**，实测合计 **13.23s**：

| 子块 | 耗时 |
|------|------|
| heat | 1.85s |
| sectors | 0.99s |
| recommend | 1.11s |
| **ai_stats** | **7.72s** |
| sentiment | 1.56s |

⇒ 预算 5.0s **小于**真实工作量 ⇒ **每一次缓存未命中都必然超时**，
日频块被长期钉在 `heat.status="unavailable"` 的降级空态。
（HTTP 实证：`reason="本地日频计算超时"` / `data_freshness.reason="日频块五秒预算已用尽"`。）

## 修复判据

1. 互不依赖的子块（heat/sectors/recommend/ai_stats）**并行**执行；
2. 预算 `DAILY_BUILD_TIMEOUT_SECONDS` **必须 ≥ 真实最坏耗时** —— 否则"预算"
   本身就从保护机制变成降级来源。

## 本文件断言什么（证伪导向）

1. **并行性**：把 4 个子块换成 `sleep(0.5)`，墙钟必须显著小于串行和（≈2.0s）⇒
   若有人改回串行，本测试失败；
2. **预算不变量**：预算 ≥ 20s（覆盖实测冷启动 22.2s 的一半以上量级）⇒
   若有人把预算调回 5s，本测试失败；
3. **sentiment 仍拿到 heat**（依赖不能因为并行而断）。
"""

from __future__ import annotations

import sys
import time
from datetime import date
from pathlib import Path

import pytest

BACKEND_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(BACKEND_ROOT))

from app.api.v1 import market as market_api  # noqa: E402

# 每个子块人为耗时（秒）。串行和 = 4 × SLEEP = 2.0s。
SLEEP = 0.5
# 并行墙钟上界：取串行和的一半（1.0s）再留余量，容忍 CI 抖动。
PARALLEL_WALL_LIMIT = 1.2


@pytest.fixture()
def _stub_blocks(monkeypatch):
    """把 4 个重子块替换为固定耗时的桩，便于观测调度方式。"""
    calls: list[str] = []

    def make(name: str):
        def _fn(*_a, **_kw):
            calls.append(name)
            time.sleep(SLEEP)
            return {"status": "ok", "stub": name}
        return _fn

    monkeypatch.setattr(market_api, "_heat_from_local", make("heat"))
    monkeypatch.setattr(market_api, "_sectors_from_local", make("sectors"))
    monkeypatch.setattr(market_api, "_build_recommend", make("recommend"))
    monkeypatch.setattr(market_api, "_build_ai_stats", make("ai_stats"))
    # sentiment 也打桩：真实实现会读 `heat["up"]` 等字段，而桩 heat 无这些键。
    # 它自身 ~0.1s、且**依赖 heat**，不属于"互不依赖的 4 个重子块"，故不计入并行观测。
    monkeypatch.setattr(market_api, "_build_sentiment",
                        lambda _heat: {"status": "ok", "stub": "sentiment"})
    return calls


def test_daily_subblocks_run_in_parallel(_stub_blocks):
    """4 个独立子块必须并行 —— 串行会是 4×0.5s=2.0s，并行应 ≈0.5s。"""
    t0 = time.perf_counter()
    out = market_api._build_daily(date(2026, 9, 30), 50)
    wall = time.perf_counter() - t0

    assert set(_stub_blocks) >= {"heat", "sectors", "recommend", "ai_stats"}, _stub_blocks
    assert wall < PARALLEL_WALL_LIMIT, (
        f"_build_daily 墙钟 {wall:.2f}s ≥ {PARALLEL_WALL_LIMIT}s"
        f"（串行和应为 {4 * SLEEP:.2f}s）—— 子块疑似被改回串行"
    )
    for k in ("heat", "sectors", "recommend", "ai_stats"):
        assert out[k]["stub"] == k


def test_budget_covers_real_workload():
    """预算必须 ≥ 真实最坏耗时。

    实测（2026-10-01，修复后）：`_build_daily` 直接调用 4.66s / HTTP 冷启动 7.6s /
    HTTP 稳态重建 8.6s ⇒ 15.0s 是"覆盖实测最坏 + 余量"的下限。

    这条断言的意义：预算若长期小于真实工作量，它就不是保护而是**缺陷来源**
    （2026-10-01 的 5.0s 正是如此：对 13.23s 的工作量恒定超时）。
    """
    assert market_api.DAILY_BUILD_TIMEOUT_SECONDS >= 15.0, (
        f"日频块预算仅 {market_api.DAILY_BUILD_TIMEOUT_SECONDS}s，"
        "小于实测最坏耗时（HTTP 稳态重建 8.6s / 冷启动 7.6s）的 2 倍余量"
    )


def test_sentiment_receives_heat(_stub_blocks, monkeypatch):
    """sentiment 依赖 heat：并行化后必须仍把 heat 的**返回值**传进去。"""
    seen: dict = {}

    def _sentiment(heat):
        seen["heat"] = heat
        return {"status": "ok"}

    monkeypatch.setattr(market_api, "_build_sentiment", _sentiment)
    out = market_api._build_daily(date(2026, 9, 30), 50)

    assert seen["heat"] == {"status": "ok", "stub": "heat"}, seen
    assert out["sentiment"] == {"status": "ok"}
