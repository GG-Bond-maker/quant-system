"""测试异步回测任务调度、外部 HTTP 连接复用与生命周期优雅清理。"""
from __future__ import annotations

import asyncio
import pytest
import httpx
from unittest.mock import AsyncMock, MagicMock, patch

from app.data.realtime import get_shared_client, close_shared_client
from app.db.session import dispose_engine, _get_engine
from app.cache.redis_client import RedisClient
from app.services.task_store import get_task
from app.api.v1.backtest import (
    StrategyBacktestRequest,
    _async_strategy_worker,
    run_strategy_backtest_async,
    get_backtest_task_status,
)


def test_shared_client_lifecycle():
    """验证共享 HTTP Client 单例创建、复用与安全关闭。"""
    close_shared_client()
    c1 = get_shared_client()
    assert isinstance(c1, httpx.Client)
    assert not c1.is_closed
    c2 = get_shared_client()
    assert c1 is c2

    close_shared_client()
    assert c1.is_closed

    # 再次调用安全重建
    c3 = get_shared_client()
    assert not c3.is_closed
    assert c3 is not c1
    close_shared_client()


@pytest.mark.asyncio
async def test_dispose_engine_and_redis_close():
    """验证 dispose_engine 与 RedisClient.close 正常执行无异常。"""
    # 触发引擎创建
    engine = _get_engine()
    assert engine is not None
    await dispose_engine()

    # RedisClient close
    await RedisClient.close()
    assert RedisClient._pool is None
    assert RedisClient._client is None


@pytest.mark.asyncio
async def test_async_strategy_backtest_lifecycle():
    """验证异步策略回测任务的提交、后台运行与持久化结果查询。"""
    req = StrategyBacktestRequest(
        strategy_name="趋势跟踪测试",
        strategy_type="ma_cross",
        start="2023-01-01",
        end="2023-06-30",
        init_cash=1_000_000,
        commission_rate=0.0003,
        slippage_bps=5.0,
        max_participation=0.05,
        short_ma=5,
        long_ma=20,
        trailing_stop_pct=3.0,
        symbols=["000001.SZ"],
    )

    user = {"sub": "test_researcher", "roles": ["researcher"]}

    fake_result = {
        "strategy_name": req.strategy_name,
        "trades": [{"symbol": "000001.SZ", "pnl": 1200.0}],
        "metrics": {"sharpe": 1.8, "total_return": 0.12},
    }

    with patch("app.api.v1.backtest._run_strategy", return_value=fake_result):
        resp = await run_strategy_backtest_async(req, _user=user)
        assert resp.code == 0
        data = resp.data
        task_id = data["task_id"]
        assert data["status"] == "queued"
        assert data["strategy_name"] == "趋势跟踪测试"

        # 等待后台任务协程完成
        for _ in range(20):
            task = get_task(task_id)
            if task and task["status"] in ("succeeded", "failed"):
                break
            await asyncio.sleep(0.01)

    # 查询任务状态
    query_resp = await get_backtest_task_status(task_id, _user=user)
    assert query_resp.code == 0
    task_data = query_resp.data
    assert task_data["task_id"] == task_id
    assert task_data["status"] == "succeeded"
    assert task_data["result"]["metrics"]["sharpe"] == 1.8
    assert task_data["progress"]["progress_pct"] == 100


@pytest.mark.asyncio
async def test_async_strategy_backtest_failure_handling():
    """验证异步策略回测发生异常时的错误记录与状态流转。"""
    req = StrategyBacktestRequest(
        strategy_name="异常策略测试",
        strategy_type="ma_cross",
        start="2022-01-01",
        end="2022-06-30",
        init_cash=1_000_000,
        commission_rate=0.0003,
        slippage_bps=5.0,
        max_participation=0.05,
        short_ma=5,
        long_ma=20,
        trailing_stop_pct=3.0,
        symbols=["000001.SZ"],
    )
    user = {"sub": "test_researcher", "roles": ["researcher"]}

    with patch("app.api.v1.backtest._run_strategy", side_effect=ValueError("数据不足")):
        resp = await run_strategy_backtest_async(req, _user=user)
        task_id = resp.data["task_id"]

        for _ in range(20):
            task = get_task(task_id)
            if task and task["status"] in ("succeeded", "failed"):
                break
            await asyncio.sleep(0.01)

    query_resp = await get_backtest_task_status(task_id, _user=user)
    assert query_resp.code == 0
    assert query_resp.data["status"] == "failed"
    assert "数据不足" in query_resp.data["error_message"]
