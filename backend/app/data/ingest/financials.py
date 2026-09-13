"""财务报表最小集（P1-4）：sina 财务指标 -> announce_date/period 严格区分。

PIT 红线：sina 接口只给报告期（period），无公告日；按项目 L2 规则使用
保守代理披露日 announce_date = report_date + 45 天，并打 is_proxy_announce 标记。
"""
from __future__ import annotations

from datetime import date, timedelta

import pandas as pd
import polars as pl
from sqlalchemy.dialects.sqlite import insert as sqlite_insert

from ...db.models import FinancialReport
from ...db.session import get_session_factory

PROXY_ANNOUNCE_DAYS = 45


def fetch_financials(symbol: str, start_year: str = "2022") -> pl.DataFrame:
    """拉取单只标的财务指标（sina 源）。"""
    import akshare as ak

    code = symbol.split(".")[0]
    df = ak.stock_financial_analysis_indicator(symbol=code, start_year=start_year)
    if df is None or df.empty:
        return pl.DataFrame()
    df = df.rename(columns={"日期": "period"})
    df["period"] = pd.to_datetime(df["period"], errors="coerce").dt.date
    df = df.dropna(subset=["period"])
    col_map = {"净资产收益率(%)": "roe", "总资产利润率(%)": "roa",
               "每股收益(元/股)": "eps", "主营业务收入(元)": "revenue",
               "净利润(元)": "net_profit", "总资产(元)": "total_assets",
               "负债总额(元)": "total_liability"}
    out = pd.DataFrame({"symbol": symbol, "period": df["period"]})
    for src, dst in col_map.items():
        out[dst] = pd.to_numeric(df[src], errors="coerce") if src in df.columns else None
    out["announce_date"] = [d + timedelta(days=PROXY_ANNOUNCE_DAYS) for d in out["period"]]
    out["is_proxy_announce"] = True
    out["source"] = "akshare"
    return pl.from_pandas(out)


def save_financials(df: pl.DataFrame) -> int:
    rows = df.to_dicts()
    if not rows:
        return 0
    ins = sqlite_insert(FinancialReport)
    stmt = ins.on_conflict_do_nothing(index_elements=["symbol", "period", "announce_date"])

    async def _go() -> None:
        factory = get_session_factory()
        async with factory() as sess:
            await sess.execute(stmt, rows)
            await sess.commit()
    import asyncio
    asyncio.run(_go())
    return len(rows)


def load_financials_asof(symbol: str, asof: date) -> pl.DataFrame:
    """PIT 读取：仅 announce_date <= asof 的记录可见（未来财报绝对不可见）。"""
    import sqlite3

    from ...core.config import get_settings

    conn = sqlite3.connect(get_settings().SQLITE_PATH)
    rows = conn.execute(
        "SELECT period, announce_date, revenue, net_profit, roe, roa, eps, is_proxy_announce "
        "FROM financial_report WHERE symbol=? AND announce_date<=? ORDER BY period",
        (symbol, asof.isoformat())).fetchall()
    conn.close()
    if not rows:
        return pl.DataFrame()
    df = pl.DataFrame(rows, orient="row", schema=["period", "announce_date",
                      "revenue", "net_profit", "roe", "roa", "eps",
                      "is_proxy_announce"])
    return df.with_columns(pl.col("period").str.to_date(),
                           pl.col("announce_date").str.to_date())
