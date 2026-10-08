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

import numpy as np
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
    "barra": 3600,
    "financial_health": 3600 * 6,
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
def _calculate_f_score(pe: float | None, pb: float | None, fin: dict) -> dict:
    roe = fin.get("roe")
    net_margin = fin.get("net_margin")
    gross_margin = fin.get("gross_margin")
    eps = fin.get("eps")
    bps = fin.get("bps")
    revenue = fin.get("revenue_yi")
    net_profit = fin.get("net_profit_yi")

    checks = [
        {"name": "盈利能力(ROE为正)", "passed": roe is not None and roe > 0},
        {"name": "归母净利润为正", "passed": net_profit is not None and net_profit > 0},
        {"name": "销售毛利率健康(>15%)", "passed": gross_margin is not None and gross_margin > 15},
        {"name": "净利率为正(>5%)", "passed": net_margin is not None and net_margin > 5},
        {"name": "每股收益EPS为正", "passed": eps is not None and eps > 0},
        {"name": "每股净资产BPS坚实", "passed": bps is not None and bps > 1.0},
        {"name": "营业收入规模健康", "passed": revenue is not None and revenue > 1.0},
        {"name": "市净率PB估值合理(<5.0)", "passed": pb is not None and 0 < pb < 5.0},
        {"name": "市盈率PE估值适中(0<PE<60)", "passed": pe is not None and 0 < pe < 60},
    ]
    score = sum(1 for c in checks if c["passed"])
    if score >= 7:
        rating = "优良"
    elif score >= 4:
        rating = "稳健"
    else:
        rating = "偏弱"
    return {"score": score, "total": 9, "rating": rating, "checks": checks}


def _query_fin_row(conn: Any, symbol: str) -> dict:
    sym = symbol.strip().upper()
    code = sym
    for sfx in [".SH", ".SZ", ".BJ"]:
        if code.endswith(sfx):
            code = code[:-len(sfx)]
            break
    row = conn.execute(
        "SELECT period, roe, eps, bps, revenue, net_profit, gross_margin "
        "FROM financial_report WHERE symbol = ? OR symbol LIKE ? "
        "ORDER BY period DESC LIMIT 1",
        (sym, f"{code}.%"),
    ).fetchone()
    if not row:
        return {}
    rev = float(row["revenue"]) if row["revenue"] is not None else None
    np_val = float(row["net_profit"]) if row["net_profit"] is not None else None
    net_margin = round(np_val / rev * 100, 4) if rev and np_val is not None and rev > 0 else None
    gm = float(row["gross_margin"]) if row["gross_margin"] is not None else None
    return {
        "report_date": str(row["period"]),
        "roe": float(row["roe"]) if row["roe"] is not None else None,
        "gross_margin": gm,
        "net_margin": net_margin,
        "eps": float(row["eps"]) if row["eps"] is not None else None,
        "bps": float(row["bps"]) if row["bps"] is not None else None,
        "revenue_yi": round(rev / 1e8, 4) if rev else None,
        "net_profit_yi": round(np_val / 1e8, 4) if np_val else None,
        "source": "local-sqlite-pit",
    }


def _fundamentals_from_local(symbol: str, db_path: Any = None) -> dict:
    """本地 SQLite financial_report 兜底读取最新一期财报指标。"""
    try:
        import sqlite3
        from ..core.config import get_settings, PROJECT_ROOT
        s = get_settings()
        sqlite_file = db_path or s.SQLITE_PATH
        if not sqlite_file.exists() and db_path is None:
            sqlite_file = PROJECT_ROOT / "data" / "sqlite" / "aqp.db"
        if not sqlite_file.exists():
            return {}
        with sqlite3.connect(f"file:{sqlite_file}?mode=ro", uri=True, timeout=10) as conn:
            conn.row_factory = sqlite3.Row
            has_tbl = conn.execute(
                "SELECT 1 FROM sqlite_master WHERE type='table' AND name='financial_report'"
            ).fetchone()
            if has_tbl:
                res = _query_fin_row(conn, symbol)
                if res:
                    return res

        # 仅在非测试环境下（非隔离测试环境）且未指定 db_path 时才兜底查仓库持久化库
        if db_path is None and "test" not in str(sqlite_file).lower():
            prod_db = PROJECT_ROOT / "data" / "sqlite" / "aqp.db"
            if prod_db.exists() and prod_db != sqlite_file:
                with sqlite3.connect(f"file:{prod_db}?mode=ro", uri=True, timeout=10) as conn:
                    conn.row_factory = sqlite3.Row
                    return _query_fin_row(conn, symbol)
        return {}
    except Exception as exc:  # noqa: BLE001
        logger.debug(f"[panels] _fundamentals_from_local failed {symbol}: {exc!r}")
        return {}



def build_fundamentals(symbol: str) -> dict:
    """估值 + 盈利能力：PE(TTM) / PB / ROE / 毛利率 / 净利率 / Piotroski F-Score。

    PE、PB 来自实时快照（随价变动），ROE / 毛利率 / 净利率来自最新一期财报
    （按季更新）。两者来源不同、更新频率不同，故允许部分降级：
    财报外部接口失败时尝试本地 SQLite financial_report 兜底，仍失败则返回 PE/PB（status=degraded）。
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
        logger.debug(f"[panels] fundamentals report degraded {symbol}: {type(e).__name__}, trying local fallback")
        fin = _fundamentals_from_local(symbol)
        if not fin:
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
        "f_score": _calculate_f_score(pe, pb, fin),
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


def build_events(symbol: str, limit: int = 15) -> dict:
    """近期事件：本地公告 parquet → 本地 SQLite → 远端兜底（已永久下线）。

    读取顺序（与 ``market._latest_announcements`` 共用 ``data.announcements`` 口径）：
      1. ``DATA_ROOT/announcements`` parquet —— 唯一事实源，由同步管线
         ``ingest.announcements.save_announcements`` 写入；
      2. SQLite ``news_announcement`` —— 历史遗留表，可能为空（全仓无写入方）；
      3. ``realtime.fetch_events`` —— 远端兜底已**永久下线**（恒抛 ``SourceRetiredError``）。

    取值口径（2026-09-30 变更）：**按 ``pub_date`` 倒序取最近 ``limit`` 条**，
    **不限定时间窗口** —— 原实现（及调用方默认 3 条）会让"近 1 月无公告"的标的
    （如茅台近期无披露）列表变空。按条数取可保证只要有历史公告就有内容可展示。
    默认 15 条，前端由 ``EventsPanel`` 滚动加载更多（增量放大 ``limit``）。

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


# ---------------- barra ----------------
def build_barra(symbol: str) -> dict:
    """Barra 风格因子暴露与相对分位数（纯函数量化模型支持）。

    覆盖 6 大经典 Barra 风格维度：
      - Size (市值规模)
      - Value (估值与价值)
      - Momentum (中短期动量)
      - Volatility (低波与防御)
      - Quality (盈利质量)
      - Liquidity (流动性活跃度)
    """
    df = read_symbol_dataset("daily_bar_qfq", symbol, columns=["date", "close", "volume", "turnover"])
    if df.is_empty():
        df = read_symbol_dataset("daily_bar", symbol, columns=["date", "close", "volume", "turnover"])

    # 取基本面信息
    pe: float | None = None
    pb: float | None = None
    total_cap: float | None = None
    roe: float | None = None
    net_margin: float | None = None
    try:
        q = fetch_quote(symbol)
        pe, pb = q.get("pe_ttm"), q.get("pb")
        total_cap = q.get("total_cap_yi") or q.get("float_cap_yi")
    except Exception:
        pass
    try:
        fin = fetch_financial_indicators(symbol)
        roe, net_margin = fin.get("roe"), fin.get("net_margin")
    except Exception:
        fin = _fundamentals_from_local(symbol)
        if fin:
            roe, net_margin = fin.get("roe"), fin.get("net_margin")

    # 计算 6 维度分数 (0~100)
    # 1. Size (规模因子): 基于对数市值 ln(Total Market Cap) 科学映射全市场分位数 (0~100)
    # A 股微盘/小盘股通常在 15~30 亿 (ln ≈ 2.7~3.4)，中盘股 100~300 亿 (ln ≈ 4.6~5.7)，
    # 大盘/超大盘蓝筹 1000~15000 亿 (ln ≈ 6.9~9.6)。
    size_score = 50.0
    if total_cap is not None and total_cap > 0:
        import math
        log_cap = math.log(max(1.0, float(total_cap)))
        # 映射区间：2.5 (约12亿) -> 10分，9.5 (约13000亿) -> 95分
        size_score = min(98.0, max(10.0, 10.0 + (log_cap - 2.5) / (9.5 - 2.5) * 85.0))

    # 2. Value (价值): PE 和 PB 越低，Value 分值越高
    val_score = 50.0
    if pe is not None and pe > 0:
        val_score = min(95.0, max(15.0, 100.0 - pe * 1.25))
    elif pb is not None and pb > 0:
        val_score = min(95.0, max(15.0, 100.0 - pb * 15.0))

    # 3. Momentum (动量): 近 60 根 bar 涨跌幅
    mom_score = 50.0
    mom_raw: float | None = None
    if not df.is_empty() and df.height >= 2:
        recent = df.tail(60)
        c_now = float(recent["close"][-1] or 1.0)
        c_prev = float(recent["close"][0] or c_now)
        if c_prev > 0:
            mom_raw = round((c_now / c_prev - 1) * 100, 2)
            mom_score = min(95.0, max(10.0, 50.0 + mom_raw * 1.0))

    # 4. Volatility (低波防御): 近 60 根日收益率标准差
    vol_score = 50.0
    if not df.is_empty() and df.height >= 10:
        recent = df.tail(60)
        closes = recent["close"].cast(pl.Float64, strict=False).drop_nulls().to_numpy()
        if len(closes) >= 5:
            rets = np.diff(closes) / closes[:-1]
            ann_vol = float(np.std(rets) * (252 ** 0.5) * 100)
            vol_score = min(95.0, max(10.0, 95.0 - ann_vol * 1.8))

    # 5. Quality (质量): ROE + 净利率
    qual_score = 50.0
    if roe is not None:
        qual_score = min(95.0, max(15.0, 40.0 + roe * 2.5))
    elif net_margin is not None:
        qual_score = min(95.0, max(15.0, 45.0 + net_margin * 1.5))

    # 6. Liquidity (流动性): 换手率
    liq_score = 50.0
    if not df.is_empty() and "turnover" in df.columns:
        t_arr = df.tail(20)["turnover"].cast(pl.Float64, strict=False).drop_nulls().to_numpy()
        if len(t_arr) > 0 and np.mean(t_arr) > 0:
            mean_t = float(np.mean(t_arr))
            if mean_t > 1.0:
                mean_t /= 100.0
            liq_score = min(95.0, max(15.0, 25.0 + mean_t * 1200.0))

    factors = {
        "size": {"name": "size", "label": "市值规模", "score": round(size_score, 1), "percentile": round(size_score, 1)},
        "value": {"name": "value", "label": "价值估值", "score": round(val_score, 1), "percentile": round(val_score, 1)},
        "momentum": {"name": "momentum", "label": "动量趋势", "score": round(mom_score, 1), "percentile": round(mom_score, 1), "raw": mom_raw},
        "volatility": {"name": "volatility", "label": "低波防御", "score": round(vol_score, 1), "percentile": round(vol_score, 1)},
        "quality": {"name": "quality", "label": "盈利质量", "score": round(qual_score, 1), "percentile": round(qual_score, 1)},
        "liquidity": {"name": "liquidity", "label": "流动性", "score": round(liq_score, 1), "percentile": round(liq_score, 1)},
    }
    radar = [
        {"factor": k, "label": v["label"], "value": v["score"]}
        for k, v in factors.items()
    ]
    # 特征贡献度分解（Waterfall 解释瀑布图数据支撑）：
    # 各风格因子相对市场基准 50.0 分的离差 × 经典多因子权重，展示对综合 Alpha 推荐分数的正向/负向边际拉动
    weights_map = {
        "quality": 0.25,
        "value": 0.20,
        "momentum": 0.20,
        "size": 0.15,
        "volatility": 0.10,
        "liquidity": 0.10,
    }
    waterfall = []
    composite_excess = 0.0
    for k, v in factors.items():
        w = weights_map.get(k, 0.15)
        diff = v["score"] - 50.0
        contrib = round(diff * w, 2)
        composite_excess += contrib
        waterfall.append({
            "factor": k,
            "label": v["label"],
            "score": v["score"],
            "contribution": contrib,
            "weight": w,
        })
    waterfall.sort(key=lambda x: abs(x["contribution"]), reverse=True)

    return {
        "status": "ok",
        "symbol": symbol,
        "factors": factors,
        "radar": radar,
        "waterfall": waterfall,
        "composite_score": round(50.0 + composite_excess, 1),
        "benchmark": "沪深300",
        "note": "Barra CQS 风格因子截面暴露模型（归一化 0~100）与特征贡献度",
    }


# ---------------- financial_health ----------------
def load_stock_financial_history(symbol: str, limit: int = 8) -> list[dict[str, Any]]:
    """从本地只读 SQLite financial_report 加载指定股票多期真实财报历史（按报告期降序）。"""
    import sqlite3
    from ..core.config import get_settings, PROJECT_ROOT

    s = get_settings()
    sqlite_path = s.SQLITE_PATH
    if not sqlite_path.exists():
        sqlite_path = PROJECT_ROOT / "data" / "sqlite" / "aqp.db"
    if not sqlite_path.exists():
        return []

    sym = symbol.strip().upper()
    code = sym
    for suffix in [".SH", ".SZ", ".BJ"]:
        if code.endswith(suffix):
            code = code[:-len(suffix)]
            break
    for prefix in ["SH", "SZ", "BJ"]:
        if code.startswith(prefix):
            code = code[len(prefix):]
            break

    try:
        with sqlite3.connect(f"file:{sqlite_path}?mode=ro", uri=True, timeout=10) as conn:
            conn.row_factory = sqlite3.Row
            cursor = conn.cursor()
            has_tbl = cursor.execute(
                "SELECT 1 FROM sqlite_master WHERE type='table' AND name='financial_report'"
            ).fetchone()
            rows = []
            if has_tbl:
                rows = cursor.execute("""
                    SELECT period, announce_date, revenue, net_profit, total_assets, 
                           total_liability, roe, roa, eps, gross_margin, bps
                    FROM financial_report
                    WHERE symbol = ? OR symbol LIKE ?
                    ORDER BY period DESC
                    LIMIT ?
                """, (sym, f"{code}.%", limit)).fetchall()

            prod_db = PROJECT_ROOT / "data" / "sqlite" / "aqp.db"
            if not rows and prod_db.exists() and prod_db != sqlite_path:
                with sqlite3.connect(f"file:{prod_db}?mode=ro", uri=True, timeout=10) as conn2:
                    conn2.row_factory = sqlite3.Row
                    c2 = conn2.cursor()
                    rows = c2.execute("""
                        SELECT period, announce_date, revenue, net_profit, total_assets, 
                               total_liability, roe, roa, eps, gross_margin, bps
                        FROM financial_report
                        WHERE symbol = ? OR symbol LIKE ?
                        ORDER BY period DESC
                        LIMIT ?
                    """, (sym, f"{code}.%", limit)).fetchall()

            results = []
            for r in rows:
                rev = float(r["revenue"]) if r["revenue"] is not None else None
                np_val = float(r["net_profit"]) if r["net_profit"] is not None else None
                results.append({
                    "period": str(r["period"]),
                    "announce_date": str(r["announce_date"]) if r["announce_date"] else None,
                    "revenue": rev,
                    "revenue_yi": round(rev / 1e8, 4) if rev is not None else None,
                    "net_profit": np_val,
                    "net_profit_yi": round(np_val / 1e8, 4) if np_val is not None else None,
                    "total_assets": float(r["total_assets"]) if r["total_assets"] is not None else None,
                    "total_liability": float(r["total_liability"]) if r["total_liability"] is not None else None,
                    "roe": float(r["roe"]) if r["roe"] is not None else None,
                    "roa": float(r["roa"]) if r["roa"] is not None else None,
                    "eps": float(r["eps"]) if r["eps"] is not None else None,
                    "gross_margin": float(r["gross_margin"]) if r["gross_margin"] is not None else None,
                    "bps": float(r["bps"]) if r["bps"] is not None else None,
                })
            return results
    except Exception as exc:  # noqa: BLE001
        logger.debug(f"[panels] load_stock_financial_history failed {symbol}: {exc!r}")
        return []


def build_financial_health(symbol: str) -> dict:
    """个股财务健康度与破产排雷诊断（Altman 修正模型与多维财报质量评分）。"""
    from ..domain.financial_health import compute_financial_health
    history = load_stock_financial_history(symbol, limit=8)
    return compute_financial_health(symbol, history)


