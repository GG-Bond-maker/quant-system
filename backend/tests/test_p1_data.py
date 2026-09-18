"""P1-4 / P1-6 单元测试：分红覆盖率 / 财务 PIT / 公告可解释情绪规则。"""
from __future__ import annotations

import sys
from datetime import date
from pathlib import Path

import polars as pl
import pytest

BACKEND_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(BACKEND_ROOT))

import asyncio

from app.data.ingest.announcements import classify, load_announcements_asof, save_announcements  # noqa: E402
from app.db.init_db import init_database  # noqa: E402


@pytest.fixture(scope="module", autouse=True)
def _tables():
    asyncio.run(init_database())
from app.data.ingest.dividends import ex_date_coverage  # noqa: E402
from app.data.ingest.financials import fetch_financials, load_financials_asof, save_financials  # noqa: E402


def test_event_classification_rules():
    """情绪/事件标签可解释：正负关键词命中，其余中性。"""
    assert classify("关于回购股份的公告")[1] == "positive"
    assert classify("股东减持计划")[1] == "negative"
    assert classify("2024年业绩预亏公告")[1] == "negative"
    assert classify("股权激励计划(草案)")[0] == "股权激励"
    assert classify("日常经营公告")[1] == "neutral"


def test_dividend_ex_date_coverage():
    df = pl.DataFrame({"symbol": ["A", "B"], "ex_date": [date(2024, 5, 1), None]})
    assert ex_date_coverage(df) == 0.5
    full = pl.DataFrame({"symbol": ["A", "B"], "ex_date": [date(2024, 5, 1), date(2024, 6, 1)]})
    assert ex_date_coverage(full) == 1.0


def test_financial_pit_visibility():
    """财务 PIT：announce_date 之后的财报才可见（L2 红线）。"""
    sym = "PITTEST.SH"
    df = pl.DataFrame({
        "symbol": [sym, sym], "period": [date(2023, 12, 31), date(2024, 3, 31)],
        "announce_date": [date(2024, 2, 14), date(2024, 5, 20)],
        "revenue": [1e9, 2e9], "net_profit": [1e8, 2e8], "total_assets": [1e10, 1e10],
        "total_liability": [5e9, 5e9], "roe": [2.0, 2.1], "roa": [1.0, 1.0],
        "eps": [1.0, 1.1], "is_proxy_announce": [False, False], "source": ["akshare", "akshare"],
    })
    assert save_financials(df) == 2
    visible_feb = load_financials_asof(sym, date(2024, 2, 15))
    assert visible_feb.height == 1  # 只有 2023 年报可见
    visible_apr = load_financials_asof(sym, date(2024, 4, 1))
    assert visible_apr.height == 1  # Q1 财报 5 月才可见（此为真实披露语义演示）
    visible_may = load_financials_asof(sym, date(2024, 5, 21))
    assert visible_may.height == 2


def test_announcements_pit_and_dedup(tmp_path: Path):
    """公告 PIT：pub_date > asof 不可见；(symbol,title) 去重。"""
    monkey_date = date(2024, 6, 5)
    df = pl.DataFrame({
        "symbol": ["600519.SH", "600519.SH", "000001.SZ"],
        "pub_date": [date(2024, 6, 4), date(2024, 6, 10), date(2024, 6, 4)],
        "title": ["回购公告", "减持公告", "增持公告"],
        "type": ["回购", "减持", "其他"], "sentiment": ["positive", "negative", "positive"],
        "source": ["akshare"] * 3, "url": [None] * 3,
    })
    # 直接写 2024 分区（save_announcements 按传入 trade_date 的年份落盘）
    assert save_announcements(df, monkey_date) == 3
    visible = load_announcements_asof(date(2024, 6, 5))
    assert visible.height == 2 and "减持公告" not in visible["title"].to_list()
    # 去重：同 (symbol,title) 重写不增行
    assert save_announcements(df, monkey_date) == 3
    assert load_announcements_asof(date(2024, 6, 30)).height == 3


@pytest.mark.network
def test_real_financials_sina():
    """真实源：sina 财务指标（代理披露日已打标）。失败时显式跳过并记录。"""
    try:
        df = fetch_financials("600519.SH", start_year="2023")
    except Exception as e:
        pytest.fail(f"真实财务源失败（需诚实记录）: {e!r}")
    assert df.height >= 4 and df["is_proxy_announce"].to_list() == [True] * df.height
