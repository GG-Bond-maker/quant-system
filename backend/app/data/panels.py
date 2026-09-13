"""
个股详情面板数据块构建（AQP）。

每个构建函数对应前端一个组件，独立 try/except 由 API 层包装为
    {"status": "ok" | "degraded" | "unavailable", ...}
任一数据源失败不影响其他组件（与 market/overview 的降级策略一致）。

数据块划分：
- quote       实时快照：总市值 / 流通市值 / 换手率 / 内外盘占比
- money_flow  主力净流入（净额 + 净占比）
- north       北向（陆股通）持股
- events      近期事件：最近 N 条公告（带原文外链）
- holders     股东信息：股东户数 + 十大流通股东
- chip        筹码分布（本地 Parquet 实时计算，无网络）
- risk        风险度量（本地 Parquet + 基准指数）

⚠️ 本模块所有构建函数都是同步阻塞 IO，调用方必须 asyncio.to_thread。
⚠️ 异常直接向上抛，禁止把异常串写进返回值（内部细节只进日志）。
"""
from __future__ import annotations

from datetime import date, timedelta

import pandas as pd
import polars as pl
from loguru import logger

from .parquet_store import read_symbol_dataset, today_trade_date_or_last
from ..domain.a_share_rules import symbol_to_code
from ..domain.chip import chip_distribution
from ..domain.risk import risk_metrics
from .ingest.announcements import classify
from .realtime import (
    fetch_events,
    fetch_financial_indicators,
    fetch_holder_num,
    fetch_main_fund_flow,
    fetch_north_holding,
    fetch_quote,
    fetch_top10_float_holders,
)

# 各数据块缓存 TTL（秒）—— 按数据更新频率分级
TTL = {
    "quote": 300,        # 盘中快照
    "money_flow": 300,
    "north": 3600 * 6,   # 北向持股按日更新
    "fundamentals": 3600 * 6,  # 财报按季更新，PE/PB 随价但日级足够
    "events": 3600,      # 公告按日更新
    "holders": 3600 * 6, # 股东信息按季度更新
    "chip": 3600,
    "risk": 3600,
}


# ---------------- quote ----------------
def build_quote(symbol: str) -> dict:
    """实时快照：总市值 / 流通市值 / 换手率 / 内外盘占比。"""
    q = fetch_quote(symbol)
    outer = q.get("outer_vol") or 0.0
    inner = q.get("inner_vol") or 0.0
    total_vol = outer + inner
    return {
        "status": "ok",
        "price": q.get("price"),
        "pct": q.get("pct"),
        "turnover": q.get("turnover"),                       # 换手率 %
        "total_cap_yi": q.get("total_cap_yi"),               # 总市值（亿元）
        "float_cap_yi": q.get("float_cap_yi"),               # 流通市值（亿元）
        "outer_vol": outer or None,                          # 外盘（手）
        "inner_vol": inner or None,                          # 内盘（手）
        "outer_ratio": round(outer / total_vol * 100, 2) if total_vol > 0 else None,
        "inner_ratio": round(inner / total_vol * 100, 2) if total_vol > 0 else None,
        "quote_time": q.get("quote_time"),
        "source": q.get("source"),
    }


# ---------------- money_flow ----------------
def build_money_flow(symbol: str) -> dict:
    """主力资金净流入（净额元 + 净占比%）。"""
    f = fetch_main_fund_flow(symbol)
    net = f.get("main_net")
    return {
        "status": "ok",
        "date": f.get("date"),
        "main_net": net,                                       # 元
        "main_net_yi": round(net / 1e8, 4) if net is not None else None,
        "main_net_ratio": f.get("main_net_ratio"),             # %
        "super_large_net_yi": _yi(f.get("super_large_net")),
        "large_net_yi": _yi(f.get("large_net")),
        "medium_net_yi": _yi(f.get("medium_net")),
        "small_net_yi": _yi(f.get("small_net")),
        "source": f.get("source"),
    }


def _yi(v: float | None) -> float | None:
    return None if v is None else round(v / 1e8, 4)


# ---------------- north ----------------
def build_north(symbol: str) -> dict:
    """北向（陆股通）持股。"""
    n = fetch_north_holding(symbol)
    return {
        "status": "ok",
        "date": n.get("date"),
        "hold_shares": n.get("hold_shares"),                   # 股
        "hold_cap_yi": _yi(n.get("hold_market_cap")),          # 持股市值（亿元）
        "pct_of_float": n.get("pct_of_float"),                 # 占 A 股百分比 %
        "source": n.get("source"),
    }


# ---------------- fundamentals ----------------
def build_fundamentals(symbol: str) -> dict:
    """估值 + 盈利能力：PE(TTM) / PB / ROE / 毛利率 / 净利率。

    PE、PB 来自实时快照（随价变动），ROE / 毛利率 / 净利率来自最新一期财报
    （按季更新）。两者来源不同、更新频率不同，故允许部分降级：
    财报挂了仍返回 PE/PB（status=degraded），反之同理。
    """
    pe: float | None = None
    pb: float | None = None
    quote_err = False
    fin: dict = {}
    fin_err = False

    try:
        q = fetch_quote(symbol)
        pe, pb = q.get("pe_ttm"), q.get("pb")
    except Exception as e:  # noqa: BLE001 子块独立降级
        logger.debug(f"[panels] fundamentals quote degraded {symbol}: {type(e).__name__}")
        quote_err = True

    try:
        fin = fetch_financial_indicators(symbol)
    except Exception as e:  # noqa: BLE001
        logger.debug(f"[panels] fundamentals report degraded {symbol}: {type(e).__name__}")
        fin_err = True

    if quote_err and fin_err:
        raise RuntimeError("基本面全部数据源失败 [quote; report]")
    if pe is None and pb is None and not fin:
        raise RuntimeError("基本面数据为空")

    return {
        "status": "degraded" if (quote_err or fin_err) else "ok",
        "pe_ttm": pe,
        "pb": pb,
        "roe": fin.get("roe"),
        "gross_margin": fin.get("gross_margin"),
        "net_margin": fin.get("net_margin"),
        "eps": fin.get("eps"),
        "bps": fin.get("bps"),
        "revenue_yi": fin.get("revenue_yi"),
        "net_profit_yi": fin.get("net_profit_yi"),
        "report_date": fin.get("report_date"),
        "note": "毛利率对银行 / 保险等金融股不适用（数据源为空）",
    }


# ---------------- events ----------------
def build_events(symbol: str, limit: int = 3) -> dict:
    """近期事件：最近 N 条上市公司公告（标题 + 日期 + 外链）。

    不足 N 条时 items 原样返回（由前端做占位），不抛异常。
    """
    items = fetch_events(symbol, limit=limit)
    for it in items:
        event_type, sentiment = classify(it.get("title", ""))
        it["event_type"] = event_type
        it["sentiment"] = sentiment
        it["summary"] = None   # 列表接口不返回正文，详情点击外链查看
    if not items:
        return {"status": "unavailable", "reason": "暂无近期公告", "items": []}
    return {"status": "ok", "items": items}


# ---------------- holders ----------------
def build_holders(symbol: str) -> dict:
    """股东信息：股东户数 + 十大流通股东。"""
    errs: list[str] = []
    holder_num: dict | None = None
    top10: list[dict] = []
    try:
        holder_num = fetch_holder_num(symbol)
    except Exception as e:  # noqa: BLE001 子块独立降级
        logger.debug(f"[panels] holder_num degraded {symbol}: {type(e).__name__}")
        errs.append("holder_num")
    try:
        top10 = fetch_top10_float_holders(symbol)
    except Exception as e:  # noqa: BLE001
        logger.debug(f"[panels] top10 degraded {symbol}: {type(e).__name__}")
        errs.append("top10")
    if holder_num is None and not top10:
        raise RuntimeError(f"股东信息全部数据源失败 [{'; '.join(errs)}]")
    return {
        "status": "degraded" if errs else "ok",
        "holder_num": holder_num,
        "top10": top10,
        "end_date": (holder_num or {}).get("end_date")
        or (top10[0].get("end_date") if top10 else None),
    }


# ---------------- chip ----------------
def build_chip(symbol: str, lookback: int = 120, current_price: float | None = None) -> dict:
    """筹码分布（本地不复权日线计算）。"""
    df = read_symbol_dataset("daily_bar", symbol)
    if df.is_empty():
        raise RuntimeError("本地无日线数据，无法计算筹码分布")
    r = chip_distribution(df, current_price=current_price, lookback=lookback)
    r["status"] = "ok"
    return r


# ---------------- risk ----------------
def build_risk(symbol: str, window: int = 252) -> dict:
    """风险度量：波动率 / 最大回撤 / 夏普 / Beta / 波动率分位。"""
    df = read_symbol_dataset("daily_bar_qfq", symbol)
    if df.is_empty():
        df = read_symbol_dataset("daily_bar", symbol)   # 无前复权时退化为不复权
    if df.is_empty():
        raise RuntimeError("本地无日线数据，无法计算风险指标")

    bench: pl.DataFrame | None = None
    beta_ok = False
    try:
        bdf = _benchmark_frame()
        if bdf is not None:
            bench = bdf
            beta_ok = True
    except Exception as e:  # noqa: BLE001 Beta 降级不影响其余指标
        logger.debug(f"[panels] benchmark degraded: {type(e).__name__}")

    r = risk_metrics(df, benchmark=bench, window=window)
    r["status"] = "ok" if beta_ok and r.get("beta") is not None else "degraded"
    return r


def _benchmark_frame() -> pl.DataFrame | None:
    """沪深300 日线（Beta 基准），转成标准 Polars Schema。"""
    from .realtime import fetch_benchmark_daily

    pdf: pd.DataFrame = fetch_benchmark_daily("sh000300")
    if pdf is None or pdf.empty:
        return None
    out = pdf[["date", "close"]].copy()
    out["date"] = pd.to_datetime(out["date"]).dt.date
    out["close"] = pd.to_numeric(out["close"], errors="coerce")
    out = out.dropna(subset=["close"])
    return pl.from_pandas(out).sort("date")


# ---------------- 工具 ----------------
def recent_trade_date_str() -> str:
    """缓存键用的交易日（YYYYMMDD）。"""
    return today_trade_date_or_last().strftime("%Y%m%d")


def announcement_window(days: int = 400) -> tuple[date, date]:
    return date.today() - timedelta(days=days), date.today()


def code_of(symbol: str) -> str:
    return symbol_to_code(symbol)
