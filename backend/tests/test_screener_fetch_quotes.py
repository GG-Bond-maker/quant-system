"""选股中心 · _fetch_quotes_sharded 专项单测（分片扩容、硬预算守卫、容错隔离）。

验证：
1. 黄金批次 750 分片：多标的按 750 正确切片，单批次直接调用外部批处理接口；
2. 硬预算守卫：耗时达到 16s 时安全跳出，返回已获取的分片 quotes，不挂死；
3. 单片异常隔离：单片网络故障时记录 warning，其余分片正常获取并合并；
4. 空标的与时间戳聚合：空列表快速返回 degraded；最新 as_of 正确向后聚合。
"""
from __future__ import annotations

import sys
import time
from pathlib import Path

import pytest

BACKEND_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(BACKEND_ROOT))

from app.api.v1 import screener as screener_api  # noqa: E402
from app.data import realtime as rt  # noqa: E402


@pytest.mark.asyncio
async def test_fetch_quotes_sharded_splits_by_750(monkeypatch):
    """1800 只标的按 750 黄金批次切分，应刚好生成 3 个分片（750 + 750 + 300）。"""
    shards_received: list[list[str]] = []

    def _fake_batch(syms: list[str]):
        shards_received.append(list(syms))
        return [{"symbol": s, "price": 10.0, "as_of": "2026-10-07 14:30:00"} for s in syms], "tencent"

    monkeypatch.setattr(rt, "fetch_quotes_batch", _fake_batch)

    test_syms = [f"{i:06d}.SH" for i in range(1800)]
    quotes, source, as_of = await screener_api._fetch_quotes_sharded(test_syms)

    assert len(shards_received) == 3
    assert len(shards_received[0]) == 750
    assert len(shards_received[1]) == 750
    assert len(shards_received[2]) == 300
    assert len(quotes) == 1800
    assert source == "tencent"
    assert as_of == "2026-10-07 14:30:00"


@pytest.mark.asyncio
async def test_fetch_quotes_sharded_budget_break(monkeypatch):
    """当累计耗时触发硬预算守卫（16s）时，必须安全提前跳出，绝不超时挂死。"""
    call_count = 0

    def _fake_batch(syms: list[str]):
        nonlocal call_count
        call_count += 1
        return [{"symbol": s, "price": 10.0, "as_of": "2026-10-07 14:30:00"} for s in syms], "tencent"

    monkeypatch.setattr(rt, "fetch_quotes_batch", _fake_batch)

    # 模拟时钟：第一次调用在 0s，第二次调用前时钟跳到 17s（超出 16s 预算）
    clock_values = [0.0, 0.0, 17.0, 17.0, 17.0]
    monkeypatch.setattr(time, "monotonic", lambda: clock_values.pop(0) if clock_values else 20.0)

    test_syms = [f"{i:06d}.SH" for i in range(2000)]  # 原本应分 3 片
    quotes, source, as_of = await screener_api._fetch_quotes_sharded(test_syms)

    # 在第 1 片执行完后，下一轮检测到耗时 17s >= 16s 触发 break，故仅执行了 1 次
    assert call_count == 1
    assert len(quotes) == 750
    assert source == "tencent"


@pytest.mark.asyncio
async def test_fetch_quotes_sharded_single_shard_failure_continues(monkeypatch):
    """单片外部网络异常不应中断其余分片，剩余分片继续合并。"""
    shards_received: list[list[str]] = []

    def _fake_batch(syms: list[str]):
        shards_received.append(list(syms))
        if len(shards_received) == 1:
            raise ConnectionResetError("模拟腾讯接口重置")
        return [{"symbol": s, "price": 10.0, "as_of": "2026-10-07 14:30:00"} for s in syms], "sina"

    monkeypatch.setattr(rt, "fetch_quotes_batch", _fake_batch)

    test_syms = [f"{i:06d}.SH" for i in range(1000)]  # 2 个分片：750 + 250
    quotes, source, as_of = await screener_api._fetch_quotes_sharded(test_syms)

    assert len(shards_received) == 2
    assert len(quotes) == 250  # 第 1 片失败跳过，第 2 片成功拉回 250 只
    assert source == "sina"
    assert as_of == "2026-10-07 14:30:00"


@pytest.mark.asyncio
async def test_fetch_quotes_sharded_empty_symbols():
    """空标的集合快速返回 degraded。"""
    quotes, source, as_of = await screener_api._fetch_quotes_sharded([])
    assert quotes == []
    assert source == "degraded"
    assert as_of is None


@pytest.mark.asyncio
async def test_fetch_quotes_sharded_as_of_aggregation(monkeypatch):
    """as_of 应准确取多个分片中的最新时间戳。"""
    def _fake_batch(syms: list[str]):
        if syms[0] == "000001.SZ":
            return [{"symbol": "000001.SZ", "as_of": "2026-10-07 14:30:00"}], "tencent"
        return [{"symbol": "600000.SH", "as_of": "2026-10-07 14:35:00"}], "tencent"

    monkeypatch.setattr(rt, "fetch_quotes_batch", _fake_batch)
    monkeypatch.setattr(screener_api, "SCREENER_QUOTES_SHARD_SIZE", 1)

    quotes, source, as_of = await screener_api._fetch_quotes_sharded(["000001.SZ", "600000.SH"])
    assert len(quotes) == 2
    assert as_of == "2026-10-07 14:35:00"
