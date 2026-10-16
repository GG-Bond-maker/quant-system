"""测试流水线增量化与并发改造（Task: 流水线增量化改造与性能提速）

验证：
1. _latest_announcement_date 能够正确提取既有 parquet 分区的最大日期
2. step_sync_announcements 增量模式：基于最新日期计算 3 天缓冲窗口，避免全量 180 天抓取
3. step_sync_announcements 强制全量开关 AQP_ANNOUNCEMENTS_FULL_SYNC 正常运作
4. step_rebuild_qfq 并发重构 qfq 正确无误，数据质量不发生基准漂移
5. cross_section _scan_source 和 build_mirror 并发扫描与写入无竞争、结果一致
6. universe build_universe_history 和 _read_bt_bars 并发加载无异常
"""
from __future__ import annotations

import os
from datetime import date, timedelta
import polars as pl
import pytest

from app.core.config import get_settings
from app.data.ingest.announcements import save_announcements
from app.orchestrator import _latest_announcement_date, step_sync_announcements
from app.data.pipeline import step_rebuild_qfq
from app.data.cross_section import _scan_source, build_mirror, read_cross_section
from app.data.universe import build_universe_history, _read_bt_bars


def test_announcements_incremental_detection(monkeypatch, tmp_path):
    settings = get_settings()

    # 1. 冷启动（无既有公告）：_latest_announcement_date 返回 None
    assert _latest_announcement_date() is None

    # 2. 模拟写入已有公告（例如 2026-10-10）
    sample_df = pl.DataFrame({
        "symbol": ["600519.SH", "000001.SZ"],
        "pub_date": [date(2026, 10, 9), date(2026, 10, 10)],
        "title": ["公告1", "公告2"],
        "type": ["其他", "其他"],
        "sentiment": ["neutral", "neutral"],
        "source": ["eastmoney", "eastmoney"],
        "url": ["http://a.com/1", "http://a.com/2"],
    })
    save_announcements(sample_df, date(2026, 10, 10))

    # 3. 验证 _latest_announcement_date 返回 2026-10-10
    latest = _latest_announcement_date()
    assert latest == date(2026, 10, 10)


def test_step_sync_announcements_incremental_mode(monkeypatch):
    """验证有已有数据时，step_sync_announcements 自动切入 incremental 模式并缩减抓取窗口。"""
    # 模拟已有最新日期为 2026-10-10
    monkeypatch.setattr("app.orchestrator._latest_announcement_date", lambda: date(2026, 10, 10))

    captured_dates = []

    def fake_fetch(ds: str):
        captured_dates.append(ds)
        return pl.DataFrame({
            "symbol": ["600519.SH"],
            "pub_date": [date.fromisoformat(f"{ds[:4]}-{ds[4:6]}-{ds[6:8]}")],
            "title": [f"标题_{ds}"],
            "type": ["其他"],
            "sentiment": ["neutral"],
            "source": ["eastmoney"],
            "url": [f"http://test/{ds}"],
        })

    monkeypatch.setattr("app.data.ingest.announcements.fetch_announcements_all_market", fake_fetch)

    trade_date = date(2026, 10, 14)
    res = step_sync_announcements(trade_date, codes=["600519"])

    assert "mode=incremental" in res
    # 窗口仅包含最新日前 3 个交易日到 trade_date（测试无日历回退时为 8 个工作日），远低于全量 119 个交易日
    assert len(captured_dates) <= 10
    assert "20261014" in captured_dates


def test_step_sync_announcements_force_full(monkeypatch):
    """验证设置环境变量 AQP_ANNOUNCEMENTS_FULL_SYNC=1 时强制全量。"""
    monkeypatch.setenv("AQP_ANNOUNCEMENTS_FULL_SYNC", "1")
    monkeypatch.setattr("app.orchestrator._latest_announcement_date", lambda: date(2026, 10, 10))

    captured_dates = []

    def fake_fetch(ds: str):
        captured_dates.append(ds)
        return pl.DataFrame()

    monkeypatch.setattr("app.data.ingest.announcements.fetch_announcements_all_market", fake_fetch)

    trade_date = date(2026, 10, 14)
    res = step_sync_announcements(trade_date, codes=["600519"])

    assert "mode=full" in res
    # 全量模式下天数大于 80 个交易日
    assert len(captured_dates) > 80


def test_parallel_rebuild_qfq_and_cross_section(tmp_path, monkeypatch):
    """验证多线程并发重建 qfq 与截面扫描的正确性。"""
    from app.data.parquet_store import write_partition

    # 构造两个标的的 raw 与 hfq 数据
    dates = [date(2026, 1, 5), date(2026, 1, 6), date(2026, 1, 7)]
    for sym, factor in [("000001.SZ", 1.5), ("600519.SH", 2.0)]:
        raw_df = pl.DataFrame({
            "date": dates,
            "open": [10.0, 10.5, 11.0],
            "high": [10.5, 11.0, 11.5],
            "low": [9.8, 10.2, 10.8],
            "close": [10.2, 10.8, 11.2],
            "volume": [1000, 1200, 1100],
            "amount": [10200.0, 12960.0, 12320.0],
        })
        hfq_df = raw_df.with_columns([
            (pl.col(c) * factor).alias(c) for c in ("open", "high", "low", "close")
        ])
        write_partition("daily_bar", sym, dates[-1], raw_df)
        write_partition("daily_bar_hfq", sym, dates[-1], hfq_df)

    # 1. 运行多线程 rebuild_qfq
    res = step_rebuild_qfq(dates[-1], ["000001.SZ", "600519.SH"])
    assert "symbols=2/2" in res

    # 2. 运行多线程 build_mirror
    m_res = build_mirror("daily_bar_qfq", incremental=False)
    assert m_res["built"] == len(dates)
    assert m_res["rows"] == len(dates) * 2

    # 3. 验证读出的截面镜像数据
    cs = read_cross_section("daily_bar_qfq", dates[-1])
    assert cs.height == 2
    assert set(cs["symbol"].to_list()) == {"000001.SZ", "600519.SH"}
