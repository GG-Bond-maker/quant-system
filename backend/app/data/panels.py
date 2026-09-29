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

import pandas as pd
import polars as pl
from loguru import logger

from .parquet_store import read_symbol_dataset
from ..domain.chip import chip_distribution
from ..domain.risk import risk_metrics
from .announcements import read_symbol_announcements
from .ingest.announcements import classify
from .realtime import (
    SourceRetiredError,
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
    # 北向持股数据源已**永久下线**（无有界数据源）：块内容恒为 unavailable，
    # 不按「日频数据」缓存 6 小时（21600s）——长缓存只会在未来若重新接入时
    # 让用户最长 6 小时看不到恢复。这里取 60s：既避免每次请求重复组装，
    # 又保证「永久下线」这一状态一旦变化能在一分钟内被上层感知。
    "north": 60,
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
    """北向（陆股通）持股。

    该数据源已**永久下线**（无可证明有界的数据源，且已明确否决重新接无界抓取）：
    此处捕获 :class:`SourceRetiredError` 并返回**可区分**的 unavailable 结构
    （``reason="data_source_retired"`` / ``retired=True``），供前端与其它
    「暂时不可用」（``reason="数据源暂时不可用"``）区分展示。

    为什么不直接把异常上抛：上抛会被 ``stock._cached_block`` 统一改写为
    「数据源暂时不可用」，无法区分「永久」与「暂时」——正是审计 F-02 的问题。
    """
    try:
        n = fetch_north_holding(symbol)
    except SourceRetiredError as exc:
        logger.debug(f"[panels] north retired {symbol}: {exc}")
        return {
            "status": "unavailable",
            "reason": "data_source_retired",
            "message": "北向（陆股通）持股数据源已永久下线，暂不提供该数据",
            "retired": True,
            "date": None,
            "hold_shares": None,
            "hold_cap_yi": None,
            "pct_of_float": None,
            "source": None,
        }
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
def _events_from_parquet(symbol: str, limit: int) -> list[dict]:
    """本地公告 parquet（唯一事实源）→ 面板事件项；共用 announcements 读口径。"""
    return [
        {"title": it["title"], "date": it["date"], "url": it.get("url"),
         "source": it.get("source") or "announcements-parquet"}
        for it in read_symbol_announcements(symbol, limit)
    ]


def _events_from_sqlite(symbol: str, limit: int) -> list[dict]:
    """本地 SQLite ``news_announcement``（历史遗留口径）；无表/空表返回 []。"""
    import sqlite3

    from ..core.config import get_settings

    db_path = get_settings().SQLITE_PATH
    with sqlite3.connect(f"file:{db_path}?mode=ro", uri=True, timeout=30) as conn:
        rows = conn.execute(
            "SELECT title, pub_date, url FROM news_announcement "
            "WHERE symbol=? ORDER BY pub_date DESC LIMIT ?",
            (symbol, limit),
        ).fetchall()
    return [
        {"title": str(title), "date": str(pub_date)[:10],
         "url": url, "source": "local-sqlite"}
        for title, pub_date, url in rows
    ]


def build_events(symbol: str, limit: int = 3) -> dict:
    """近期事件：本地公告 parquet → 本地 SQLite → 远端兜底（已永久下线）。

    读取顺序（与 ``market._latest_announcements`` 共用 ``data.announcements`` 口径）：
      1. ``DATA_ROOT/announcements`` parquet —— 唯一事实源，由同步管线
         ``ingest.announcements.save_announcements`` 写入；
      2. SQLite ``news_announcement`` —— 历史遗留表，可能为空（全仓无写入方）；
      3. ``realtime.fetch_events`` —— 远端兜底已**永久下线**（恒抛 ``SourceRetiredError``）。

    关键修复（审计 E-01）：原实现把「无数据」的显式 ``unavailable`` 写在
    ``fetch_events`` 的 ``raise`` 之后 → 永远不可达，异常被 ``_cached_block``
    统一吞成「数据源暂时不可用」。现在改为：逐一尝试各源，**确实无数据**时
    返回带可区分 ``reason`` 的显式 unavailable（不再抛出）。
    """
    items: list[dict] = []
    source: str | None = None

    # ① 本地公告 parquet（唯一事实源）
    try:
        items = _events_from_parquet(symbol, limit)
        if items:
            source = items[0].get("source") or "announcements-parquet"
    except Exception as e:  # noqa: BLE001 本地读失败继续下探其它源
        logger.debug(f"[panels] parquet events unavailable {symbol}: {type(e).__name__}")
        items = []

    # ② 本地 SQLite（历史遗留口径）
    if not items:
        try:
            items = _events_from_sqlite(symbol, limit)
            if items:
                source = "local-sqlite"
        except Exception as e:  # noqa: BLE001 表缺失/只读失败均按无数据处理
            logger.debug(f"[panels] local sqlite events unavailable {symbol}: {type(e).__name__}")

    # ③ 远端兜底（已永久下线：预期抛 SourceRetiredError）
    if not items:
        try:
            items = fetch_events(symbol, limit=limit) or []
            if items:
                source = "remote"
        except Exception as e:  # noqa: BLE001 兜底源下线属预期，不作故障上报
            logger.debug(f"[panels] remote events retired {symbol}: {type(e).__name__}")

    for it in items:
        event_type, sentiment = classify(it.get("title", ""))
        it["event_type"] = event_type
        it["sentiment"] = sentiment
        it["summary"] = None   # 列表接口不返回正文，详情点击外链查看

    if not items:
        # 显式、可区分的 unavailable：本地无该标的公告（非「数据源暂时不可用」）。
        return {
            "status": "unavailable",
            "reason": "no_local_announcements",
            "message": "本地暂无该标的公告数据（公告同步管线尚未覆盖该标的）",
            "items": [],
        }
    return {"status": "ok", "items": items, "source": source}


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
    """筹码分布（只投影所需列和近似回看区间，避免读取全历史宽表）。"""
    df = read_symbol_dataset(
        "daily_bar", symbol,
        columns=["date", "high", "low", "close", "volume", "amount", "turnover"])
    if df.is_empty():
        raise RuntimeError("本地无日线数据，无法计算筹码分布")
    r = chip_distribution(df, current_price=current_price, lookback=lookback)
    r["status"] = "ok"
    return r


# ---------------- risk ----------------
def build_risk(symbol: str, window: int = 252) -> dict:
    """风险度量：只读取收益计算所需日期/收盘列和有限窗口。"""
    columns = ["date", "close"]
    df = read_symbol_dataset(
        "daily_bar_qfq", symbol, columns=columns)
    if df.is_empty():
        df = read_symbol_dataset(
            "daily_bar", symbol,
            columns=columns)   # 无前复权时退化为不复权
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

    # 审计 B2-16：基准标识由**调用方**给出（此前 risk_metrics 内部硬编码"沪深300"，
    # 无论实际传入哪个基准都会这么标）；rf 用 domain 统一口径（年化 2%）。
    r = risk_metrics(df, benchmark=bench, window=window,
                     benchmark_symbol=_BENCHMARK_LABEL if bench is not None else None)
    r["status"] = "ok" if beta_ok and r.get("beta") is not None else "degraded"
    return r


# Beta 基准的**代码与显示名**（审计 B2-16：此前显示名硬编码在 domain.risk 里，
# 与实际取到的基准无关；现在由本层给出真实标识）。
_BENCHMARK_CODE = "sh000300"
_BENCHMARK_LABEL = "沪深300(sh000300)"


def _benchmark_frame() -> pl.DataFrame | None:
    """沪深300 日线（Beta 基准），转成标准 Polars Schema。"""
    from .realtime import fetch_benchmark_daily

    pdf: pd.DataFrame = fetch_benchmark_daily(_BENCHMARK_CODE)
    if pdf is None or pdf.empty:
        return None
    out = pdf[["date", "close"]].copy()
    out["date"] = pd.to_datetime(out["date"]).dt.date
    out["close"] = pd.to_numeric(out["close"], errors="coerce")
    out = out.dropna(subset=["close"])
    return pl.from_pandas(out).sort("date")
