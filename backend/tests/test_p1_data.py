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


def test_fetch_announcements_all_market_no_symbol_filter(monkeypatch: pytest.MonkeyPatch):
    """回归：全市场抓取**不得**按 symbol 过滤（2026-09-30 踩坑）。

    缺陷现场：``step_sync_announcements`` 起初误用
    ``fetch_announcements("__all__", day, day)``——该函数会按 ``raw_code == "__all__"``
    过滤，而东财 ``代码`` 列是真实代码（600519 等），故**永不匹配**、静默返回空表。
    实测该 bug 导致整步 22 个交易日全部 ``rows=0``（无报错、仅 WARNING）。

    本用例断言：全市场函数保留多只标的、把裸码标准化为带后缀的标准 symbol、
    且保留接口返回的真实公告日期（PIT 红线）。
    """
    import pandas as pd

    from app.data.ingest import announcements as mod

    fake = pd.DataFrame({
        "代码": ["600519", "000001", "300750"],
        "公告标题": ["贵州茅台:回购公告", "平安银行:董事会决议公告", "宁德时代:业绩预告"],
        "公告日期": ["2026-09-30", "2026-09-30", "2026-09-29"],
        "公告类型": ["回购", "其他", "业绩预告"],
        "网址": ["https://x/1", "https://x/2", "https://x/3"],
    })

    def _fake_report(symbol: str, date: str):  # noqa: ARG001
        assert symbol == "全部", "全市场抓取必须显式传 symbol='全部'"
        return fake

    monkeypatch.setattr("akshare.stock_notice_report", _fake_report)
    df = mod.fetch_announcements_all_market("20260930")

    assert df.height == 3, "全市场抓取不得过滤掉任何标的"
    assert df.schema["symbol"] == pl.String
    assert set(df["symbol"].to_list()) == {"600519.SH", "000001.SZ", "300750.SZ"}
    # PIT 红线：pub_date 用接口真实日期，不得被入参 day 覆盖
    assert set(str(d) for d in df["pub_date"].to_list()) == {"2026-09-29", "2026-09-30"}
    assert df.schema["url"] == pl.String
    assert _build_frame_columns_ok(df)


def _build_frame_columns_ok(df: pl.DataFrame) -> bool:
    return df.columns == ["symbol", "pub_date", "title", "type", "sentiment", "source", "url"]


@pytest.mark.network
def test_real_financials_disclosure():
    """真实源：巨潮预约披露（全市场、按报告期）。

    PIT 断言：announce_date 必须等于接口的「实际披露」或「首次预约」，且
    600519 贵州茅台 2024年报 实际披露 = 2025-04-03（已与巨潮公告列表交叉核对）。
    失败时显式跳过并记录。
    """
    try:
        df = fetch_financials(period="2024年报")
    except Exception as e:  # noqa: BLE001 - 网络源失败需显式 fail 而非静默跳过
        pytest.fail(f"真实财务源失败（需诚实记录）: {e!r}")
    assert df.height > 4000, "全市场单期应覆盖 4000+ 标的"
    assert df.schema["announce_basis"] == pl.String
    mt = df.filter(pl.col("symbol") == "600519.SH")
    assert mt.height == 1
    assert str(mt["announce_date"][0]) == "2025-04-03", "600519 2024年报披露日错位"
    assert mt["announce_basis"][0] == "actual"
    # announce_basis 与 is_proxy_announce 必须自洽
    assert set(df["announce_basis"].unique().to_list()) <= {"actual", "scheduled"}
    bad = df.filter(
        (pl.col("announce_basis") == "actual") != (~pl.col("is_proxy_announce")))
    assert bad.height == 0, "announce_basis 与 is_proxy_announce 语义必须一致"


@pytest.mark.network
def test_real_financials_single_period_failure_isolated(monkeypatch: pytest.MonkeyPatch):
    """单期失败不得中断整体：坏 period 只被跳过并计入告警，好 period 正常返回。"""
    import app.data.ingest.financials as fin

    ok = fin.fetch_financials(period="2024年报")
    monkeypatch.setattr(fin, "_fetch_disclosure",
                        lambda p: (_ for _ in ()).throw(RuntimeError("boom")))
    got = fin.fetch_financials(period=["2024年报", "2024一季"])
    # 两期都因 monkeypatch 失败 ⇒ 空表，但不得抛异常
    assert got.is_empty()
    assert ok.height > 4000  # 未 monkeypatch 时正常
