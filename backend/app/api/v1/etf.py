"""
ETF 中心接口（AQP）。

- GET /api/v1/etf/overview      市场概览 5 卡（含前一交易日对比）
- GET /api/v1/etf/list          ETF 列表：国家/板块/类型/指数/管理公司/规模/成立日期/关键词
- GET /api/v1/etf/performance   多 ETF 累计涨跌序列（ETF表现 折线图）
- GET /api/v1/etf/scale         规模变化（柱 = 估算规模，线 = 样本数）
- GET /api/v1/etf/flow          资金净流入榜（近1日/近5日/近10日）
- GET /api/v1/etf/hot           热门 ETF TOP N（按成交额）

自选（我的自选ETF）由前端 localStorage 管理，与股票自选一致，无后端依赖。
"""
from __future__ import annotations

import asyncio
from datetime import date
from typing import Any

import orjson
from fastapi import APIRouter, Depends, Query

from ...cache.keys import NS
from ...cache.redis_client import RedisClient
from ...core.auth import require_role
from ...core.errors import APIResponse, ok
from ...data import etf as E

router = APIRouter()

_SNAP_KEY = f"{NS}:etf:snap:history"
_SNAP_MAX = 30

# 概览默认展示的代表 ETF（ETF表现 折线图默认序列）
DEFAULT_PERF = "510300,510500,159915,513500,512100"


def _filter_catalog(**kw: Any) -> list[dict]:
    """按筛选条件过滤目录。所有条件可缺省，条件之间为 AND。"""
    items = E.build_catalog()
    country = kw.get("country")
    if country and country != "all":
        items = [x for x in items if x["country"] == country]
    board = kw.get("board")
    if board and board != "all":
        items = [x for x in items if x["board"] == board]
    etype = kw.get("etype")
    if etype and etype != "all":
        items = [x for x in items if x["type"] == etype]
    if kw.get("index"):
        idx = str(kw["index"]).lower()
        items = [x for x in items if idx in (x.get("tracking_index") or "").lower()]
    if kw.get("manager"):
        mg = str(kw["manager"]).lower()
        items = [x for x in items if mg in (x.get("manager") or "").lower()]
    if kw.get("q"):
        q = str(kw["q"]).lower()
        items = [x for x in items
                 if q in (x.get("code") or "").lower() or q in (x.get("name") or "").lower()]
    lo, hi = kw.get("min_size"), kw.get("max_size")
    if lo is not None:
        items = [x for x in items if x.get("size_yi") is not None and x["size_yi"] >= lo]
    if hi is not None:
        items = [x for x in items if x.get("size_yi") is not None and x["size_yi"] <= hi]
    d_from, d_to = kw.get("inception_from"), kw.get("inception_to")
    if d_from:
        items = [x for x in items if x.get("inception") and x["inception"] >= str(d_from)]
    if d_to:
        items = [x for x in items if x.get("inception") and x["inception"] <= str(d_to)]
    return items


def _overview_snapshot() -> dict:
    """聚合当日概览：数量 / 总市值 / 平均涨跌幅 / 资金净流入 / 成交额。

    P2-8 口径拆分：核心统计（数量/总规模/成交额/净流入）只统计
    境内有真实行情的中国 ETF；美/日/韩条目为目录补充（日韩无行情、
    美股规模为配置汇率折算值），单独在 overseas 里披露，不再混入合计。
    """
    cat = E.build_catalog()
    cn = [x for x in cat if x["country"] == "cn"]
    overseas = [x for x in cat if x["country"] != "cn"]
    pcts = [x["pct"] for x in cn if x.get("pct") is not None]
    try:
        flow = E.fetch_flow("1d", limit=100)
    except Exception:  # noqa: BLE001 资金流不可用时不阻塞概览
        flow = []
    net_inflow = sum(f["net_inflow"] or 0 for f in flow if f.get("net_inflow"))
    us_quoted = [x for x in overseas if x["country"] == "us" and x.get("size_yi")]
    return {
        "etf_count": len(cn),
        "total_size_yi": round(sum(x.get("size_yi") or 0 for x in cn), 2),
        "avg_pct": round(sum(pcts) / len(pcts), 4) if pcts else None,
        "net_inflow_yi": round(net_inflow / 1e8, 2),
        "amount_yi": round(sum(x.get("amount") or 0 for x in cn) / 1e8, 2),
        "overseas": {
            "us_count": sum(1 for x in overseas if x["country"] == "us"),
            "jp_count": sum(1 for x in overseas if x["country"] == "jp"),
            "kr_count": sum(1 for x in overseas if x["country"] == "kr"),
            # 美股规模为汇率折算值（非人民币实盘口径），日韩仅目录无行情
            "us_size_yi": round(sum(x.get("size_yi") or 0 for x in us_quoted), 2),
            "note": "美股规模为美元按配置汇率折算；日/韩为标的目录（行情不可达，未计入统计）",
        },
    }


async def _overview_with_prev() -> dict:
    """今日概览 + 前一存档快照对比。

    对比数据来自 Redis 存档（每天首次访问时写入一份），
    因此首日运行的「较昨日」为 null（不编造）。
    """
    snap = await asyncio.to_thread(_overview_snapshot)
    today = date.today().isoformat()

    hist_raw = await RedisClient.get(_SNAP_KEY)
    history: list[dict] = orjson.loads(hist_raw) if hist_raw else []
    prev = next((h for h in history if h.get("date") and h["date"] < today), None)

    if not any(h.get("date") == today for h in history):
        history.append({"date": today, **snap})
        history = sorted(history, key=lambda x: x.get("date") or "")[-_SNAP_MAX:]
        await RedisClient.set(_SNAP_KEY, orjson.dumps(history), ex=3600 * 24 * 60)

    return {
        "today": {**snap, "date": today},
        "prev": {**prev, "date": prev["date"]} if prev else None,
    }


@router.get("/overview", response_model=APIResponse[dict])
async def etf_overview(    _user: dict = Depends(require_role("viewer")),
) -> APIResponse[dict]:
    """市场概览：ETF 数量 / 总市值 / 平均涨跌幅 / 资金净流入 / 成交额 + 较昨日。"""
    return ok(await _overview_with_prev())


@router.get("/list", response_model=APIResponse[dict])
async def etf_list(
    country: str = Query("all", description="all/cn/us/jp/kr"),
    board: str = Query("all", description="宽基ETF/行业ETF/主题ETF/Smart Beta/跨境ETF/..."),
    etype: str = Query("all", description="股票型/债券型/商品型/货币型/跨境型"),
    index: str | None = Query(None, description="跟踪指数关键词"),
    manager: str | None = Query(None, description="管理公司关键词"),
    q: str | None = Query(None, max_length=32, description="代码/名称关键词"),
    min_size: float | None = Query(None, ge=0, description="规模下限（亿元）"),
    max_size: float | None = Query(None, ge=0, description="规模上限（亿元）"),
    inception_from: str | None = Query(None, pattern=r"^\d{4}-\d{2}-\d{2}$"),
    inception_to: str | None = Query(None, pattern=r"^\d{4}-\d{2}-\d{2}$"),
    sort: str = Query("size", description="size/amount/pct/code"),
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=100),
    _user: dict = Depends(require_role("viewer")),
) -> APIResponse[dict]:
    """ETF 列表（筛选器 + 搜索共用），返回分页结果与可选值集合。"""
    items = await asyncio.to_thread(
        _filter_catalog, country=country, board=board, etype=etype, index=index,
        manager=manager, q=q, min_size=min_size, max_size=max_size,
        inception_from=inception_from, inception_to=inception_to,
    )
    keyfn = {
        "amount": lambda x: x.get("amount") or 0,
        "pct": lambda x: x.get("pct") or 0,
        "code": lambda x: x.get("code") or "",
    }.get(sort, lambda x: x.get("size_yi") or 0)
    items.sort(key=keyfn, reverse=(sort != "code"))

    total = len(items)
    start = (page - 1) * page_size
    rows = items[start:start + page_size]

    catalog_all = await asyncio.to_thread(E.build_catalog)
    options = {
        "boards": sorted({x["board"] for x in catalog_all}),
        "types": sorted({x["type"] for x in catalog_all}),
        "indexes": sorted({x["tracking_index"] for x in catalog_all if x.get("tracking_index")}),
        "managers": sorted({x["manager"] for x in catalog_all if x.get("manager")}),
    }
    return ok({
        "total": total, "page": page, "page_size": page_size, "items": rows,
        "options": options,
    })


@router.get("/hot", response_model=APIResponse[dict])
async def etf_hot(
    limit: int = Query(5, ge=1, le=50),
    _user: dict = Depends(require_role("viewer")),
) -> APIResponse[dict]:
    """热门 ETF TOP N：按成交额降序（四国合并，行情不可用的排最后）。"""
    items = await asyncio.to_thread(_filter_catalog)
    items.sort(key=lambda x: x.get("amount") or 0, reverse=True)
    return ok({"items": items[:limit]})


_PERIOD_DAYS = {"1d": 1, "5d": 5, "1m": 22, "3m": 66, "6m": 132, "1y": 252,
                "3y": 756, "ytd": 0}
_FLOW_FIELD = {"1d": "1d", "5d": "5d", "10d": "10d"}


@router.get("/performance", response_model=APIResponse[dict])
async def etf_performance(
    symbols: str = Query(DEFAULT_PERF, max_length=400,
                         description="逗号分隔 ETF 代码，中国用 6 位、美国用字母代码"),
    metric: str = Query("pct", pattern=r"^(pct|price)$"),
    period: str = Query("1y", pattern=r"^(1d|5d|1m|3m|6m|1y|3y|ytd)$"),
    _user: dict = Depends(require_role("viewer")),
) -> APIResponse[dict]:
    """多 ETF 累计涨跌（或净值）序列，用于「ETF表现」折线图。

    日本 / 韩国本土 ETF 行情不可达，代码不在数据源内时该序列为空，
    前端据此显示「暂无数据」。
    """
    codes = [s.strip() for s in symbols.split(",") if s.strip()][:8]
    days = _PERIOD_DAYS.get(period, 252)

    def _build() -> dict:
        series: list[dict] = []
        catalog = {x["code"]: x for x in E.build_catalog()}
        for code in codes:
            info = catalog.get(code) or {}
            country = info.get("country") or ("cn" if code.isdigit() else "us")
            market = "us" if country == "us" else (
                "sh" if code.startswith(("5", "6", "9")) else "sz")
            try:
                bars = E.fetch_kline(market, code, limit=800)
            except Exception:  # noqa: BLE001 单序列失败不影响其他
                bars = []
            bars = bars[-days:] if days else bars
            if len(bars) < 2:
                series.append({"code": code, "name": info.get("name") or code,
                               "points": [], "status": "unavailable"})
                continue
            base = bars[0]["close"] or 1.0
            points = [{"date": b["date"],
                       "value": (round(((b["close"] or base) / base - 1) * 100, 2)
                                 if metric == "pct" else b["close"])}
                      for b in bars]
            series.append({"code": code, "name": info.get("name") or code,
                           "points": points, "status": "ok"})
        return {"metric": metric, "period": period, "series": series}

    data = await asyncio.to_thread(_build)
    return ok(data)


@router.get("/scale", response_model=APIResponse[dict])
async def etf_scale(
    period: str = Query("1m", pattern=r"^(1m|3m|1y)$"),
    top_n: int = Query(10, ge=3, le=30, description="纳入规模估算的头部 ETF 数量"),
    _user: dict = Depends(require_role("viewer")),
) -> APIResponse[dict]:
    """ETF 规模变化：头部 N 只按「最新份额 × 历史收盘」估算日度总规模。

    份额 = 最新流通市值 / 最新收盘价；估算口径，非基金公司披露的申赎后规模。
    数量(只) 线为当日有成交的样本数。
    """
    def _build() -> dict:
        cat = [x for x in E.build_catalog()
               if x["country"] == "cn" and x.get("size_yi") is not None]
        cat.sort(key=lambda x: x["size_yi"], reverse=True)
        sample = cat[:top_n]
        days = _PERIOD_DAYS.get(period, 22)
        series_by_code: dict[str, list[dict]] = {}
        for e in sample:
            market = "sh" if e["code"].startswith(("5", "6", "9")) else "sz"
            try:
                bars = E.fetch_kline(market, e["code"], limit=days + 5)[-days:]
            except Exception:  # noqa: BLE001
                bars = []
            series_by_code[e["code"]] = bars

        by_date: dict[str, dict[str, float]] = {}
        for e in sample:
            bars = series_by_code.get(e["code"]) or []
            if not bars:
                continue
            last = bars[-1]
            # 份额(股) = 规模(元) / 最新收盘价；目录里是 size_yi(亿元) 与 price
            shares = (((e["size_yi"] * 1e8) / e["price"])
                      if (e.get("size_yi") and e.get("price")) else None)
            if not shares:
                continue
            for b in bars:
                if b["close"] is None:
                    continue
                by_date.setdefault(b["date"], {})[e["code"]] = b["close"] * shares

        dates = sorted(by_date)
        scale = [{"date": d, "value": round(sum(by_date[d].values()) / 1e8, 2),
                  "count": len(by_date[d])} for d in dates]
        return {"period": period, "sample_size": len(sample), "points": scale,
                "note": "估算口径：最新份额 × 历史收盘价，非基金公司披露规模"}

    data = await asyncio.to_thread(_build)
    return ok(data)


@router.get("/flow", response_model=APIResponse[dict])
async def etf_flow(
    period: str = Query("1d", pattern=r"^(1d|5d|10d)$"),
    limit: int = Query(10, ge=1, le=50),
    _user: dict = Depends(require_role("viewer")),
) -> APIResponse[dict]:
    """ETF 资金净流入榜：东财主力净流入口径，按净流入降序。"""
    data = await asyncio.to_thread(E.fetch_flow, _FLOW_FIELD.get(period, "1d"), limit)
    return ok({"period": period, "items": data})


# ---------------- ETF 详情页（ETF分析） ----------------

_KLINE_PERIODS = {"day": "day", "week": "week", "month": "month"}


def _market_for_code(code: str, country: str) -> str:
    """code + country -> Tencent 市场前缀。"""
    if country == "us":
        return "us"
    if code.startswith(("5", "6", "9")):
        return "sh"
    return "sz"


def _block_ok(payload: dict) -> dict:
    return {"status": "ok", **payload}


def _block_degraded(payload: dict, reason: str) -> dict:
    return {"status": "degraded", "reason": reason, **payload}


def _block_unavailable(reason: str) -> dict:
    return {"status": "unavailable", "reason": reason}


def _build_header_block(code: str, info: dict | None) -> dict:
    """顶部信息栏：最新价 / 涨跌幅 / 成交额 / 管理费率 / 规模 / 跟踪指数。"""
    if not info:
        return _block_unavailable("未找到该 ETF")
    fee: dict = {"management": None, "custody": None, "unit": "%/年"}
    if info.get("country") == "cn":
        try:
            fee = E.fetch_etf_fee(code)
        except Exception:  # noqa: BLE001
            pass
    return _block_ok({
        "name": info.get("name"),
        "code": code,
        "country": info.get("country"),
        "price": info.get("price"),
        "pct": info.get("pct"),
        "amount": info.get("amount"),
        "size_yi": info.get("size_yi"),
        "management_fee": fee.get("management"),
        "custody_fee": fee.get("custody"),
        "tracking_index": info.get("tracking_index"),
        "manager": info.get("manager"),
        "inception": info.get("inception"),
    })


def _build_kline_block(code: str, country: str, period: str) -> dict:
    """K 线：日线 / 周线 / 月线。"""
    market = _market_for_code(code, country)
    try:
        bars = E.fetch_kline(market, code, limit=320)
    except Exception as e:  # noqa: BLE001
        return _block_unavailable(f"K线数据暂时不可用: {type(e).__name__}")
    if len(bars) < 2:
        return _block_unavailable("K线数据不足")
    bars = E.aggregate_kline(bars, period)
    return _block_ok({"period": period, "bars": bars})


def _build_holdings_block(code: str, country: str) -> dict:
    """前十大重仓股 + 行业配置。"""
    if country != "cn":
        return _block_unavailable("非中国境内 ETF 暂无持仓明细")
    holdings: dict | None = None
    industry: dict | None = None
    try:
        holdings = E.fetch_etf_holdings(code)
    except Exception as e:  # noqa: BLE001
        holdings = {"error": str(type(e).__name__)}
    try:
        industry = E.fetch_etf_industry(code)
    except Exception as e:  # noqa: BLE001
        industry = {"error": str(type(e).__name__)}

    if not holdings or "error" in holdings:
        return _block_degraded(
            {"holdings": holdings, "industry": industry},
            "重仓股解析失败" if (holdings and "error" in holdings) else "重仓股数据缺失",
        )
    return _block_ok({
        "holdings": holdings,
        "industry": industry,
    })


def _build_tracking_block(code: str, country: str, info: dict | None) -> dict:
    """超额收益 / 基准对比 + 跟踪误差。"""
    idx = E.resolve_tracking_index(info.get("name") if info else None,
                                   info.get("tracking_index") if info else None)
    if not idx:
        return _block_unavailable("无法识别跟踪指数")
    market, idx_code = idx
    try:
        etf_bars = E.fetch_kline(_market_for_code(code, country), code, limit=252)
        idx_bars = E.fetch_index_kline(market, idx_code, limit=252)
    except Exception as e:  # noqa: BLE001
        return _block_unavailable(f"基准数据暂时不可用: {type(e).__name__}")
    if len(etf_bars) < 20 or len(idx_bars) < 20:
        return _block_unavailable("基准序列不足")

    etf_map = {b["date"]: b for b in etf_bars}
    idx_map = {b["date"]: b for b in idx_bars}
    dates = sorted(set(etf_map) & set(idx_map))
    if len(dates) < 20:
        return _block_unavailable("ETF 与基准日期交集不足")

    # 累计收益率序列（从共同起始日开始）
    base_etf = etf_map[dates[0]]["close"] or 1.0
    base_idx = idx_map[dates[0]]["close"] or 1.0
    points = []
    daily_diffs: list[float] = []
    prev_e: float | None = None
    prev_i: float | None = None
    for d in dates:
        e_close = etf_map[d]["close"] or base_etf
        i_close = idx_map[d]["close"] or base_idx
        e_ret = (e_close / base_etf - 1) * 100
        i_ret = (i_close / base_idx - 1) * 100
        points.append({"date": d, "etf": round(e_ret, 2), "index": round(i_ret, 2)})
        if prev_e is not None and prev_i is not None and prev_e and prev_i:
            daily_diffs.append((e_close / prev_e - 1) - (i_close / prev_i - 1))
        prev_e, prev_i = e_close, i_close

    # 跟踪误差：日收益差的标准差年化（常用定义）
    if len(daily_diffs) >= 2:
        mean = sum(daily_diffs) / len(daily_diffs)
        variance = sum((x - mean) ** 2 for x in daily_diffs) / (len(daily_diffs) - 1)
        te = (variance ** 0.5) * (252 ** 0.5) * 100  # 转为百分比
    else:
        te = None

    # 指数名称美化
    idx_name_map: dict[str, str] = {
        "000300": "沪深300", "000905": "中证500", "000016": "上证50",
        "000852": "中证1000", "399006": "创业板指", "000688": "科创50",
    }
    benchmark_name = (info.get("tracking_index") if info else None) or idx_name_map.get(idx_code, idx_code)

    return _block_ok({
        "benchmark_code": idx_code,
        "benchmark_name": benchmark_name,
        "points": points,
        "tracking_error": round(te, 4) if te is not None else None,
    })


def _build_valuation_block(code: str, info: dict | None) -> dict:
    """估值百分位：当前 PE/PB + 历史分位（乐咕乐股）。"""
    proxy = E.fetch_etf_valuation_proxy(code)
    if not proxy:
        return _block_unavailable("无法获取估值数据")
    pe_pct = proxy.get("pe_percentile")
    pb_pct = proxy.get("pb_percentile")
    if pe_pct is None or pb_pct is None:
        return _block_degraded(
            {
                "pe_ttm": proxy.get("pe_ttm"),
                "pb": proxy.get("pb"),
                "index_code": proxy.get("index_code"),
                "index_name": proxy.get("index_name"),
                "pe_percentile": pe_pct,
                "pb_percentile": pb_pct,
            },
            "仅获取到当前 PE/PB，历史分位计算失败",
        )
    return _block_ok({
        "pe_ttm": proxy.get("pe_ttm"),
        "pb": proxy.get("pb"),
        "index_code": proxy.get("index_code"),
        "index_name": proxy.get("index_name"),
        "pe_percentile": pe_pct,
        "pb_percentile": pb_pct,
    })


def _build_flow_block(code: str, country: str) -> dict:
    """单只 ETF 资金流向。"""
    if country != "cn":
        return _block_unavailable("非中国境内 ETF 暂无资金流数据")
    try:
        items = E.fetch_etf_flow_history(code, days=60)
    except Exception as e:  # noqa: BLE001
        return _block_unavailable(f"资金流数据暂时不可用: {type(e).__name__}")
    if not items:
        return _block_unavailable("暂无资金流数据")
    status = "ok" if len(items) >= 5 else "degraded"
    note = None if len(items) >= 5 else "仅获取到最近 1 个交易日的主力净流入"
    return {"status": status, "items": items, "note": note}


def _build_news_block(code: str, country: str) -> dict:
    """基金公告 / 新闻动态（天天基金 F10）。"""
    if country != "cn":
        return _block_unavailable("非中国境内 ETF 暂无公告数据")
    try:
        items = E.fetch_etf_news(code, limit=12)
    except Exception as e:  # noqa: BLE001
        return _block_unavailable(f"公告数据暂时不可用: {type(e).__name__}")
    if not items:
        return _block_unavailable("暂无公告数据")
    return {"status": "ok", "items": items, "note": "数据源：天天基金基金公告"}


def _build_sentiment_block(news_block: dict) -> dict:
    """基于公告标题的关键词情感打分（纯函数，无 IO）。"""
    titles = [it.get("title", "") for it in ((news_block or {}).get("items") or [])]
    if not titles:
        return _block_unavailable("暂无可用于情感统计的公告")
    return {"status": "ok", **E.score_sentiment(titles)}


def _build_chain_block(holdings_block: dict) -> dict:
    """产业链归集：由持仓细分行业按关键词映射到产业链大类（纯函数，无 IO）。"""
    hb = holdings_block or {}
    if hb.get("status") != "ok":
        return _block_unavailable("暂无持仓行业数据")
    items = ((hb.get("industry") or {}).get("items")) or []
    chain = E.build_industry_chain(items)
    if not chain:
        return _block_unavailable("暂无产业链数据")
    return {"status": "ok", "items": chain, "note": "由持仓细分行业归集到产业链大类"}


@router.get("/detail/{code}", response_model=APIResponse[dict])
async def etf_detail(
    code: str,
    kline_period: str = Query("day", pattern=r"^(day|week|month)$"),
    _user: dict = Depends(require_role("viewer")),
) -> APIResponse[dict]:
    """ETF 分析页统一详情接口。

    块：header / kline / holdings / tracking / valuation / flow / news，
    另由 news 派生 sentiment、由 holdings 派生 chain（均为纯函数派生）。

    code 规则：中国 6 位数字、美国字母代码、日本 .T / 韩国 .KS。
    """
    catalog = await asyncio.to_thread(E.build_catalog)
    info = next((x for x in catalog if x["code"] == code), None)
    country = info["country"] if info else ("us" if not code.isdigit() else "cn")

    blocks = await asyncio.gather(
        asyncio.to_thread(_build_header_block, code, info),
        asyncio.to_thread(_build_kline_block, code, country, kline_period),
        asyncio.to_thread(_build_holdings_block, code, country),
        asyncio.to_thread(_build_tracking_block, code, country, info),
        asyncio.to_thread(_build_valuation_block, code, info),
        asyncio.to_thread(_build_flow_block, code, country),
        asyncio.to_thread(_build_news_block, code, country),
        return_exceptions=True,
    )

    keys = ["header", "kline", "holdings", "tracking", "valuation", "flow", "news"]
    result: dict[str, Any] = {}
    overall = "ok"
    for k, b in zip(keys, blocks):
        # 注意用 BaseException 而非 Exception：as_completed 可能带出
        # KeyboardInterrupt/SystemExit 等非 Exception 子类，用 Exception 判断
        # 会让分支外的 b 仍被静态推断为异常对象
        if isinstance(b, BaseException):
            result[k] = _block_unavailable("数据源暂时不可用")
            overall = "degraded"
        else:
            result[k] = b
            if (b.get("status") or "") in ("degraded", "unavailable"):
                overall = "degraded" if overall == "ok" else overall

    # 派生块（纯函数，无 IO）：情感由公告标题统计，产业链由持仓行业归集
    # 上游块缺失时传空字典，派生块自行返回空态而非抛异常
    result["sentiment"] = _build_sentiment_block(result.get("news") or {})
    result["chain"] = _build_chain_block(result.get("holdings") or {})

    return ok({
        "code": code,
        "name": info.get("name") if info else None,
        "country": country,
        "status": overall,
        "blocks": result,
    })
