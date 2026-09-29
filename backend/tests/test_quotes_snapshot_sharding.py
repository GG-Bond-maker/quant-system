"""P1-15 防回归：`quotes_snapshot` 不得**静默**丢弃第 201 只起的标的。

## 缺陷

`app/data/quotes_hub.py:68-69` 原实现：

```python
if len(syms) > QUOTES_MAX_SYMBOLS:      # 200
    syms = syms[:QUOTES_MAX_SYMBOLS]    # 无日志、无字段
```

而 `api/v1/alerts.py:520` 把**全部**报价类预警规则（price_pct / price_cross /
volume_spike）的标的合并成**一次**快照 ⇒ watchlist 类规则标的无上限时，
**第 201 只起永不触发，且响应里没有任何字段能看出这件事**（`确定`，报告 P1-15）。

## 修法

1. **分片**：按 `QUOTES_SHARD_SIZE=200` 逐片抓取并合并 —— 不再丢标的（`source`
   在多片不一致时为 `"mixed"`，API 侧恒单片、契约不变）；
2. **有界但绝不静默**：超过 `QUOTES_MAX_SYMBOLS_TOTAL=800` 才截断，且必然带
   `truncated` / `dropped_count` / `limit` / `requested` / `returned` + WARNING
   （与 `alerts.py` 的 `truncated`+`*_limit` 同形，§4.13.3 截断契约）。
"""
from __future__ import annotations

import asyncio
import sys
from pathlib import Path

import pytest

BACKEND_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(BACKEND_ROOT))

import app.data.quotes_hub as qh  # noqa: E402
import app.data.realtime as rt  # noqa: E402


class _WarnSpy:
    """替掉模块 logger，收集 WARNING（loguru 的 sink 不便在测试中断言）。"""

    def __init__(self) -> None:
        self.warnings: list[str] = []

    def warning(self, msg: str, *a, **k) -> None:  # noqa: ANN002, ANN003
        self.warnings.append(str(msg))

    def info(self, *a, **k) -> None:  # noqa: ANN002, ANN003
        pass


@pytest.fixture()
def fake_source(monkeypatch):
    """假的 `fetch_quotes_batch`：记录每次调用的分片长度，按 1 只 1 条回报。"""
    calls: list[list[str]] = []

    def _fake(syms: list[str], *a, **k):  # noqa: ANN002, ANN003
        calls.append(list(syms))
        quotes = [{"symbol": s, "price": 10.0, "as_of": "2026-09-21 15:00:00"}
                  for s in syms]
        return quotes, "tencent"

    monkeypatch.setattr(rt, "fetch_quotes_batch", _fake)
    monkeypatch.setattr(qh, "_quotes_cache", {})       # 隔离进程级缓存
    spy = _WarnSpy()
    monkeypatch.setattr(qh, "logger", spy)
    return calls, spy


def test_over_200_symbols_are_sharded_not_dropped(fake_source):
    """**缺陷本体**：250 只必须全部返回，且按 200 一片抓取。"""
    calls, _ = fake_source
    syms = [f"60{i:04d}.SH" for i in range(250)]
    out = asyncio.run(qh.quotes_snapshot(syms))
    assert len(out["quotes"]) == 250, "超过 200 只不得丢标的（原实现只回 200）"
    assert out["requested"] == 250 and out["returned"] == 250
    assert out["truncated"] is False and out["dropped_count"] == 0
    assert out["n_shards"] == 2 and out["source"] == "tencent"
    assert [len(c) for c in calls] == [200, 50], f"分片长度应为 200+50：{calls}"


def test_total_cap_is_disclosed_and_warned(fake_source, monkeypatch):
    """超过分片总上限时：截断必须**可见**（字段 + WARNING）。"""
    calls, spy = fake_source
    monkeypatch.setattr(qh, "QUOTES_MAX_SYMBOLS_TOTAL", 250)   # 测试用小上限
    syms = [f"60{i:04d}.SH" for i in range(300)]
    out = asyncio.run(qh.quotes_snapshot(syms))
    assert out["truncated"] is True, "截断必须披露"
    assert out["requested"] == 300 and out["returned"] == 250
    assert out["dropped_count"] == 50 and out["limit"] == 250
    assert len(calls) == 2, f"250 只 = 2 片：{calls}"
    assert spy.warnings, "截断必须有 WARNING（原实现连日志都没有）"
    assert "300" in spy.warnings[0] and "50" in spy.warnings[0], spy.warnings[0]


def test_under_200_stays_single_request(fake_source):
    """反向断言：≤200 只仍**单片**（API 侧 `source` 契约不变，不引入多请求开销）。"""
    calls, _ = fake_source
    out = asyncio.run(qh.quotes_snapshot(["600519.SH", "000001.SZ"]))
    assert out["n_shards"] == 1 and out["source"] == "tencent"
    assert len(calls) == 1 and len(calls[0]) == 2
    assert out["truncated"] is False and out["requested"] == 2


def test_cache_hit_between_calls_still_reports_this_call(fake_source, monkeypatch):
    """缓存命中必须按**本次**请求披露（同键不同 requested 不得串味）。"""
    calls, _ = fake_source
    monkeypatch.setattr(qh, "QUOTES_MAX_SYMBOLS_TOTAL", 200)
    big = [f"60{i:04d}.SH" for i in range(250)]      # 截断到 200
    out1 = asyncio.run(qh.quotes_snapshot(big))
    assert out1["truncated"] is True and out1["requested"] == 250
    n_calls = len(calls)
    out2 = asyncio.run(qh.quotes_snapshot(big[:200]))   # 同缓存键（截断后同集合）
    assert len(calls) == n_calls, "同集合应命中缓存"
    assert out2["truncated"] is False and out2["requested"] == 200, (
        "命中缓存时仍须反映本次请求口径，否则披露字段会串味")
    assert out2["returned"] == 200


def test_mixed_sources_across_shards_are_labeled(fake_source):
    """多片源不一致 ⇒ `source="mixed"`（如实标注，不冒充单一源）。"""
    n = {"i": 0}

    def _flaky(syms, *a, **k):  # noqa: ANN002, ANN003
        n["i"] += 1
        return ([{"symbol": s, "price": 1.0, "as_of": None} for s in syms],
                "tencent" if n["i"] == 1 else "sina")

    rt.fetch_quotes_batch = _flaky
    syms = [f"60{i:04d}.SH" for i in range(250)]
    out = asyncio.run(qh.quotes_snapshot(syms))
    assert out["n_shards"] == 2, out
    assert out["source"] == "mixed", f"两片源不同应标 mixed，实为 {out['source']}"
    assert len(out["quotes"]) == 250


def test_empty_symbols_contract(fake_source):
    """空输入：仍是 degraded 空载荷，且截断字段齐备（契约形状稳定）。"""
    out = asyncio.run(qh.quotes_snapshot([]))
    assert out["quotes"] == [] and out["source"] == "degraded"
    for k in ("requested", "returned", "truncated", "dropped_count", "limit"):
        assert k in out, f"缺披露字段 {k}"