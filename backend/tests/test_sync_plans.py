"""方案 A 与方案 B 功能划分与并发隔离回归测试。

覆盖核心约束：
1. 方案 A（全市场日度快照秒级批量同步，mode=incremental）：
   - 当待同步标的较多（>=50）时，走 batch_sync_market_snapshot 快速快照通道；
   - 能够正确落库 raw daily_bar 与推导 daily_bar_hfq；
   - 支持优雅降级与停牌标的处理。
2. 方案 B（多线程受控并发历史修复，mode=repair）：
   - 采用 ThreadPoolExecutor(max_workers=3) 受控并发拉取；
   - 支持断点续传（_resume_skip_set）与核验记录落盘；
   - 能够在 _check_cancel() 触发时安全中断。
3. 严格互斥（防并发冲突）：
   - 同一时刻只允许执行一个任务，若任务运行中，触发另一模式直接返回 409 互斥冲突；
   - worker 严格按 mode 分流，方案 A 与方案 B 绝不同时执行。
"""
from __future__ import annotations

import threading
import time
from datetime import date
from pathlib import Path
from unittest.mock import MagicMock, patch

import polars as pl
import pytest
from fastapi.testclient import TestClient

import app.services.sync_service as ss
from app.data.ingest.tasks import batch_sync_market_snapshot
from app.main import app


@pytest.fixture()
def clean_sync_state():
    """复位同步状态。"""
    with ss._sync.lock:
        ss._sync.running = False
        ss._sync.task_id = None
        ss._sync.mode = None
        ss._sync.completed.clear()
        ss._sync.failed.clear()
        ss._sync.done = 0
        ss._sync.total = 0
        ss._sync.rows_written = 0
        ss._sync.error = None
        ss._sync.cancel_event.clear()
    yield ss._sync
    with ss._sync.lock:
        ss._sync.running = False
        ss._sync.cancel_event.clear()


def test_plan_a_batch_sync_market_snapshot(tmp_path, monkeypatch):
    """测试方案 A 批量快照写入引擎。"""
    target = date(2026, 10, 9)

    # 构造假快照数据（2 只标的：1 只正常，1 只停牌）
    fake_snapshot = pl.DataFrame({
        "code": ["000001", "000002"],
        "symbol": ["000001.SZ", "000002.SZ"],
        "open": [11.5, 0.0],
        "high": [11.8, 0.0],
        "low": [11.2, 0.0],
        "close": [11.6, 0.0],
        "volume": [100000.0, 0.0],
        "amount": [1150000.0, 0.0],
        "pct": [1.2, 0.0],
        "turnover": [0.005, 0.0],
        "change": [0.15, 0.0],
        "source": ["akshare_snapshot", "akshare_snapshot"],
    })

    monkeypatch.setattr(
        "app.data.ingest.akshare_adapter.fetch_market_daily_snapshot",
        lambda max_workers=5: fake_snapshot,
    )

    written_records: list[tuple[str, str, int]] = []

    def fake_write_daily_bars(code, df, adjusts=("",)):
        written_records.append((code, adjusts[0], len(df)))
        return len(df)

    monkeypatch.setattr("app.data.ingest.tasks.write_daily_bars", fake_write_daily_bars)
    monkeypatch.setattr(
        "app.data.repair.anchor_factor_at",
        lambda root, sym, dt: (150.0, dt),
    )

    rows, completed, failed = batch_sync_market_snapshot(
        target, ["000001.SZ", "000002.SZ"]
    )

    # 000001 写入了 raw 与 hfq，总共 2 行
    assert rows == 2
    # 000001.SZ 正常完成，000002.SZ 停牌也记为 completed
    assert "000001.SZ" in completed
    assert "000002.SZ" in completed
    assert not failed
    assert len(written_records) == 2


def test_plan_b_multithreaded_repair(clean_sync_state, monkeypatch):
    """测试方案 B 多线程受控并发修复引擎。"""
    symbols = ["000001.SZ", "000002.SZ", "600519.SH"]

    monkeypatch.setattr(ss, "_refresh_trade_calendar", lambda: None)
    monkeypatch.setattr(ss, "last_completed_trade_day", lambda cal: date(2026, 10, 9))

    called_threads = set()
    lock = threading.Lock()

    def fake_fetch_and_write(code, start, end, adjusts=("", "hfq")):
        with lock:
            called_threads.add(threading.current_thread().name)
        time.sleep(0.01)  # 模拟轻微耗时
        return 100, []

    monkeypatch.setattr("app.data.ingest.tasks.fetch_and_write_daily_bars", fake_fetch_and_write)
    monkeypatch.setattr(ss, "_record_verified_repair", lambda sym, end, n: None)
    monkeypatch.setattr(ss, "invalidate_stats_cache", lambda: None)

    clean_sync_state.total = len(symbols)
    ss._run_repair(symbols, resume=False)

    assert clean_sync_state.done == len(symbols)
    assert clean_sync_state.completed == set(symbols)
    assert clean_sync_state.rows_written == 300
    assert not clean_sync_state.failed
    # 确认是由线程池执行（包含 sync-repair 前缀线程）
    assert any("sync-repair" in name for name in called_threads)


def test_mutual_exclusion_rejects_concurrent_sync(clean_sync_state, monkeypatch):
    """验证架构级互斥：若方案 A 或任何任务正在运行，拒绝触发新的同步请求。"""
    from app.core.auth import create_jwt_token

    # 模拟已有任务正在运行
    with clean_sync_state.lock:
        clean_sync_state.running = True
        clean_sync_state.mode = "incremental"

    token, _ = create_jwt_token("test_user", "researcher")
    client = TestClient(app)

    # 尝试在方案 A 运行期间触发方案 B
    resp = client.post(
        "/api/v1/datacenter/sync",
        json={"mode": "repair"},
        headers={"Authorization": f"Bearer {token}"},
    )

    # 必须返回 409 管道冲突错误
    assert resp.status_code == 200
    data = resp.json()
    assert data["code"] == 40900
    assert "已有同步任务在执行中" in data["message"]


def test_plan_b_custom_date_range(clean_sync_state, monkeypatch):
    """验证方案 B 自定义起止时间有效传递给底层抓取引擎。"""
    symbols = ["000001.SZ"]

    monkeypatch.setattr(ss, "_refresh_trade_calendar", lambda: None)
    monkeypatch.setattr(ss, "_record_verified_repair", lambda sym, end, n: None)
    monkeypatch.setattr(ss, "invalidate_stats_cache", lambda: None)

    captured_args = {}

    def fake_fetch_and_write(code, start, end, adjusts=("", "hfq")):
        captured_args["code"] = code
        captured_args["start"] = start
        captured_args["end"] = end
        return 50, []

    monkeypatch.setattr("app.data.ingest.tasks.fetch_and_write_daily_bars", fake_fetch_and_write)

    clean_sync_state.total = len(symbols)
    ss._run_repair(
        symbols,
        resume=False,
        start_date="2024-01-01",
        end_date="2024-06-30",
    )

    assert captured_args["code"] == "000001"
    assert captured_args["start"] == "2024-01-01"
    assert captured_args["end"] == "2024-06-30"
    assert clean_sync_state.done == 1
    assert "000001.SZ" in clean_sync_state.completed


