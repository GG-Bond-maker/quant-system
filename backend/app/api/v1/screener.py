"""Screener API（P1-5）：读 pred_daily 排序选股，缓存键含全部参数。

另含「选股中心 · 股票列表」端点 ``GET /stocks``（全市场在册证券，服务端
排序 + 分页）。该端点的业务逻辑全部在 ``data/screening.py``，本模块只做
参数校验、缓存编排（Redis SWR）与分页切片。
"""
from __future__ import annotations

import asyncio
from datetime import date, datetime, timedelta

import polars as pl
from fastapi import APIRouter, Depends, Query
from loguru import logger

from ...cache import swr
from ...cache.keys import k_screener, k_screener_stocks
from ...cache.swr import cached_or_build
from ...core.auth import require_role
from ...core.config import get_settings
from ...core.errors import ERR_DATA_EMPTY, ERR_PARAMS, APIResponse, AQPException, ok
from ...data.screening import (
    STOCK_SORT_FIELDS,
    apply_quote_snapshot,
    build_market_stock_rows,
    compute_stats,
    enrich_items,
    filter_stock_rows,
    filter_universe,
    instrument_info,
    load_screener_snapshot,
    sort_stock_rows,
    stock_options,
)

router = APIRouter()

# 股票列表：全市场行缓存 60s（键只含 basis，分页/排序在内存里做）。
# sort / dir 的白名单校验与回落统一在 data.screening.sort_stock_rows 里做
# （业务逻辑不下沉到路由层），本模块只持有分页与 basis 的边界常量。
STOCKS_TTL = 60
STOCKS_BASIS = ("auto", "realtime", "daily")
STOCKS_Q_MAX_LEN = 32
STOCKS_PAGE_SIZE_MAX = 100


def _load_instrument_info() -> dict[str, tuple[str | None, str | None]]:
    """名称/行业兜底（薄委托：实现在 data.screening，快照写入共用同一口径）。"""
    return instrument_info()


def _freshness(as_of: str | None, now: datetime | None = None) -> dict:
    """数据时效披露：``as_of`` 相对「最近已收盘交易日」落后的交易日数。

    单一事实来源（取代前端拿快照日期与 ``market.trade_date`` 做字符串比对）：
    - ``expected`` = 最近**已收盘**交易日（盘中/盘前/非交易日 → 上一交易日）；
    - ``lag_trading_days`` = ``(as_of, expected]`` 区间内的交易日数；
    - ``is_stale`` = ``lag > 0``。

    口径依据 ``domain.calendar``（真实交易日历，识别节假日/调休）。日历不可用
    或 ``as_of`` 非法时 ``lag_trading_days=None``——前端按「未知」处理，
    绝不误报为「陈旧」也不误报为「最新」。

    背景（审计 P1-4）：晚间例行以「当天日期」幂等，服务未常驻的交易日会永久
    漏跑，导致 predictions/screener 停在若干交易日前；此前仅有「非最新」文案，
    缺落后天数，用户无法判断陈旧程度。
    """
    out: dict = {"as_of": as_of, "expected": None, "lag_trading_days": None,
                 "is_stale": None, "note": ""}
    if not as_of:
        out["note"] = "无数据日期，无法判定新鲜度"
        return out
    try:
        from ...data.calendar_store import get_calendar
        from ...domain.calendar import last_completed_trade_day, range_trade_days

        cal = get_calendar()
        if len(cal) == 0:
            out["note"] = "交易日历不可用，无法判定新鲜度"
            return out
        expected = last_completed_trade_day(cal, now=now)
        d0 = date.fromisoformat(str(as_of)[:10])
        lag = len(range_trade_days(d0 + timedelta(days=1), expected, cal))
        out.update({
            "expected": expected.isoformat(),
            "lag_trading_days": lag,
            "is_stale": lag > 0,
            "note": ("数据为最近已收盘交易日产物" if lag == 0
                     else f"数据落后最近已收盘交易日 {lag} 个交易日"),
        })
        return out
    except Exception as e:  # noqa: BLE001 时效披露失败绝不影响榜单本身
        logger.warning(f"[screener] freshness 计算失败: {type(e).__name__}: {e!r}")
        out["note"] = "新鲜度计算失败（日历或日期异常）"
        return out


def _watchlist_quotes(symbols: list[str]) -> dict:
    """自选股行情快照：名称/行业 + daily_bar 最新收盘与当日涨跌 + 最新预测分。"""
    from ...data.parquet_store import read_symbol_dataset

    ins = _load_instrument_info()

    # 最新一期预测分（无预测的标的 score 为 null）
    scores: dict[str, float] = {}
    pred_files = sorted((get_settings().DATA_ROOT / "predictions").glob("date=*.parquet"))
    if pred_files:
        pred = pl.read_parquet(pred_files[-1]).filter(pl.col("symbol").is_in(symbols))
        for r in pred.iter_rows(named=True):
            scores[r["symbol"]] = round(float(r["pred_score"]), 6)

    items = []
    for sym in symbols:
        name, industry = ins.get(sym, (None, None))
        close = pct = None
        qdate = None
        df = read_symbol_dataset("daily_bar", sym)
        if df is not None and df.height >= 1:
            tail = df.tail(2).sort("date")
            close = float(tail["close"][-1])
            qdate = str(tail["date"][-1])[:10]
            if tail.height >= 2:
                prev = float(tail["close"][-2])
                if prev:
                    pct = round((close / prev - 1) * 100, 2)
        score = scores.get(sym)
        risk = ("low" if score >= 0.3 else "mid" if score >= 0.1 else "high") if score is not None else None
        items.append({
            "symbol": sym, "name": name, "industry": industry,
            "close": close, "pct": pct, "date": qdate,
            "score": score, "risk": risk,
        })
    return {"count": len(items), "items": items}


def _load_predictions(target: date | None) -> pl.DataFrame:
    s = get_settings()
    pred_dir = s.DATA_ROOT / "predictions"
    if target:
        path = pred_dir / f"date={target.strftime('%Y%m%d')}.parquet"
        if not path.exists():
            raise AQPException(ERR_DATA_EMPTY, f"无 {target} 的预测结果，请先运行流水线")
        return pl.read_parquet(path)
    files = sorted(pred_dir.glob("date=*.parquet"))
    if not files:
        raise AQPException(ERR_DATA_EMPTY, "尚无预测结果，请先运行训练与推理流水线")
    return pl.read_parquet(files[-1])


def _pred_dates() -> list[date]:
    """全部可用的预测日期（升序），用于回溯前一交易日的榜单做对比。"""
    out: list[date] = []
    for f in sorted((get_settings().DATA_ROOT / "predictions").glob("date=*.parquet")):
        try:
            out.append(date.fromisoformat(f.stem.replace("date=", "")))
        except ValueError:
            continue
    return out


def _build_items(
    pred: pl.DataFrame, trade_date: str, top_k: int, board: str
) -> tuple[list[dict], int]:
    """给定某交易日的预测结果，产出富化后的榜单条目（供今日 / 昨日复用）。

    返回 ``(items, pool_size)``：

    - ``items``：按 pred_score 降序、截断到 top_k 的榜单条目列表；
    - ``pool_size``：**截断前**的股票池规模（universe 过滤 ST/停牌 + 板块过滤后、
      ``.head(top_k)`` 之前的有效标的数）。前端「股票数量」卡应使用此字段，
      而非 ``len(items)``（后者是榜单截断后的数，等于 top_k 上限）。
    """
    # 共享口径（data.screening）：universe join + ST/停牌/板块过滤 + score 降序，
    # 与盘后快照写入完全一致（L2-1 单一事实源）。
    df, pool_size = filter_universe(pred, trade_date, board)
    items = enrich_items(df, top_k)
    return items, pool_size


def _stats(items: list[dict], pool_size: int) -> dict:
    """榜单聚合指标（薄委托：实现在 data.screening.compute_stats）。"""
    return compute_stats(items, pool_size)


def _screen(target: date | None, strategy: str, top_k: int, board: str) -> tuple[dict, str | None]:
    """实时算榜：返回 ``(响应体, 真实特征版本)``。

    D-02/T-08：第二个返回值取自 predictions 分区的 ``feature_version`` 真实列
    （step_infer 写入）——供审计落库，**绝不能**用 screener 策略名冒充特征版本；
    旧分区缺列时返回 ``None``（由调用方按 :func:`assert_known_feature_version`
    如实落库 / 告警）。该值不进入响应体，故不改变前端契约。
    """
    if strategy != "alpha_basic_v1":
        raise AQPException(40000, f"未知策略: {strategy}（当前仅 alpha_basic_v1）")
    pred = _load_predictions(target)
    trade_date = str(pred["date"].max())[:10]

    # 真实特征版本：来自 predictions 分区列（step_infer 写入的 fv），单一事实源。
    fv_cell = (pred["feature_version"][0]
               if ("feature_version" in pred.columns and pred.height) else None)
    real_feature_version = str(fv_cell) if fv_cell is not None else None

    items, pool_size = _build_items(pred, trade_date, top_k, board)

    # 前一交易日对比：同 top_k / board 口径，供概览卡的「较昨日」展示。
    # 任一步失败都只降级为 prev=None，不影响当日榜单。
    prev_date: str | None = None
    prev_stats: dict | None = None
    try:
        dates = _pred_dates()
        cur = date.fromisoformat(trade_date)
        if cur in dates:
            idx = dates.index(cur)
            if idx > 0:
                p_date = dates[idx - 1]
                p_pred = _load_predictions(p_date)
                p_items, p_pool_size = _build_items(
                    p_pred, str(p_pred["date"].max())[:10], top_k, board)
                prev_date = str(p_pred["date"].max())[:10]
                prev_stats = _stats(p_items, p_pool_size)
    except Exception as e:  # noqa: BLE001 对比块降级不影响主榜单
        logger.warning(f"[screener] prev-day stats degraded: {type(e).__name__}: {e!r}")

    return ({"date": trade_date, "strategy": strategy, "top_k": top_k,
             "board": board, "count": len(items), "items": items,
             "stats": {"today": _stats(items, pool_size), "prev": prev_stats,
                       "prev_date": prev_date}}, real_feature_version)


@router.get("/watchlist", response_model=APIResponse[dict])
async def screener_watchlist(
    symbols: str = Query(..., min_length=6, max_length=4000,
                         description="逗号分隔的标的代码，如 600519.SH,000001.SZ"),
    _user: dict = Depends(require_role("viewer")),
) -> APIResponse[dict]:
    """自选股行情快照：名称/行业/最新收盘与当日涨跌/最新预测分（个性化数据，不缓存）。"""
    syms = list(dict.fromkeys(s.strip() for s in symbols.split(",") if s.strip()))
    if not syms:
        raise AQPException(40000, "symbols 不能为空")
    if len(syms) > 100:
        raise AQPException(40000, "自选股一次最多查询 100 只")
    data = await asyncio.to_thread(_watchlist_quotes, syms)
    return ok(data)


@router.get("", response_model=APIResponse[dict])
async def screener(
    day: str | None = Query(None, alias="date", pattern=r"^\d{4}-\d{2}-\d{2}$"),
    strategy: str = Query("alpha_basic_v1"),
    top_k: int = Query(50, ge=1, le=200),
    board: str = Query("all"),
    refresh: int = Query(0, ge=0, le=1,
                         description="1=跳过缓存读强制重算（5s 防抖；重算结果回写缓存）"),
    _user: dict = Depends(require_role("viewer")),
) -> APIResponse[dict]:
    """智能选股：score DESC，Redis 缓存键 = date+strategy+top_k+board。

    缓存策略与 market/overview 一致（L1-2）：TTL 300s 过期后 30 分钟内
    回旧值（stale=true）+ 后台重建；refresh=1 强制重算（防抖 5s）。
    """
    from ...db.models import FeatureRun
    from ...db.session import get_session_factory

    target = date.fromisoformat(day) if day else None
    key = k_screener(target.isoformat() if target else "latest", strategy, top_k, board)

    # L2-1：快照优先（目标 <200ms）——refresh=1 例外（§3.2 语义是"强制重算"）。
    # 快照命中后按 SWR 同款写回 Redis（同 key），TTL 内的后续请求零 SQLite 查询；
    # 无当日快照 / top_k 超物化窗口 / 日期错位 时回落实时计算（响应标 from_snapshot）。
    if not refresh:
        snap = await asyncio.to_thread(
            load_screener_snapshot,
            target.isoformat() if target else None, strategy, board, top_k)
        if snap is not None:
            snap["from_cache"] = False
            # ⚠️ 先写缓存再加 freshness：freshness 依赖「当前时间」，若随缓存
            # 固化会在 TTL 内一直返回旧值（甚至把陈旧误报为最新）。
            await swr.write_cache(key, snap, ttl=300, stale_window=1800)
            snap["freshness"] = _freshness(snap.get("date"))
            return ok(snap)

    # D-02/T-08：_screen 回传 predictions 真实特征版本（不进入响应体），供 _log_run
    # 落库。build() 与 after_build 同作用域（且 after_build 仅在真实重建路径调用，
    # 此时 real_feature_version 必已被赋值），故 nonlocal 传递安全。
    real_feature_version: str | None = None

    async def _build() -> dict:
        nonlocal real_feature_version
        data, real_feature_version = await asyncio.to_thread(
            _screen, target, strategy, top_k, board)
        return data

    async def _log_run(_data: dict) -> None:
        try:
            # D-02/T-08：feature_version 取 predictions 真实列 + 白名单校验；
            # 严禁用策略名（strategy）冒充特征版本（生产是 alpha_basic_v2g）。
            # 未登记版本 fail-fast，由下方 except 记日志，不影响榜单响应。
            from ...ml.features import assert_known_feature_version

            feature_version = assert_known_feature_version(
                real_feature_version, where="screener._log_run")
            factory = get_session_factory()
            async with factory() as sess:
                sess.add(FeatureRun(strategy=strategy,
                                    trade_date=date.fromisoformat(_data["date"]),
                                    model_version=None,
                                    feature_version=feature_version,
                                    top_k=top_k))
                await sess.commit()
        except Exception as e:  # noqa: BLE001 审计落库失败不影响榜单响应
            logger.warning(f"[screener] feature_runs log failed: {e!r}")

    data = await cached_or_build(key, _build, ttl=300, refresh=refresh,
                                 after_build=_log_run)
    # 同上：freshness 每次请求实时计算，不参与缓存
    data["freshness"] = _freshness(data.get("date"))
    return ok(data)


# ---------------- 选股中心 · 股票列表（全市场在册证券） ----------------
async def _fetch_quotes_sharded(symbols: list[str]) -> tuple[list[dict], str,
                                                            str | None]:
    """分片抓取全市场实时快照，返回 ``(quotes, source, as_of)``。

    为什么必须分片：``data/quotes_hub.quotes_snapshot`` 对超过 200 只的请求
    **静默截断前 200 且不报错**——直接丢全市场只会拿到 200 只，其余静默降级，
    页面看起来正常实则残缺。故由本函数按 ``QUOTES_MAX_SYMBOLS`` 切片。

    为什么串行：``realtime._MIN_INTERVAL = 0.25s`` 是模块级全局限速，
    ``asyncio.gather`` 并发分片会瞬间打穿限速并触发外部源限流/封禁；串行
    ``await`` 既满足限速，又让每个分片各自独立（单片失败不影响其余）。

    为什么先排序：排好序后分片内容稳定，才能命中 ``quotes_hub`` 的
    ``QUOTES_TTL``（默认 15s）进程内缓存——否则每次请求的集合顺序不同，
    缓存键哈希不同，外部源压力翻倍。
    """
    from ...data.quotes_hub import QUOTES_MAX_SYMBOLS, quotes_snapshot

    syms = sorted({str(s) for s in symbols if s})
    quotes: list[dict] = []
    source = "degraded"
    as_of: str | None = None
    for i in range(0, len(syms), QUOTES_MAX_SYMBOLS):
        shard = syms[i:i + QUOTES_MAX_SYMBOLS]
        try:
            snap = await quotes_snapshot(shard)
        except Exception as e:  # noqa: BLE001 单片失败只记日志，其余分片照常
            logger.warning(f"[screener.stocks] 行情分片 {i} 失败: {e!r}")
            continue
        quotes.extend(list(snap.get("quotes") or []))
        src = str(snap.get("source") or "degraded")
        if src != "degraded" and source == "degraded":
            source = src
        snap_as_of = snap.get("as_of")
        if snap_as_of and (as_of is None or str(snap_as_of) > as_of):
            as_of = str(snap_as_of)
    return quotes, source, as_of


@router.get("/stocks", response_model=APIResponse[dict])
async def screener_stocks(
    board: str = Query("all", description="all|main|chinext_star|bse"),
    industry: str = Query("all", description="行业名，all = 不限"),
    q: str = Query("", description="代码/名称关键词（大小写不敏感，≤32 字符）"),
    exclude_st: int = Query(0, description="1=剔除 ST；非法值视为 0"),
    sort: str = Query("", description=f"排序列，白名单 ''|{'|'.join(STOCK_SORT_FIELDS)}"),
    sort_dir: str = Query("desc", alias="dir", description="asc|desc"),
    page: int = Query(1, description="页码，≥1"),
    page_size: int = Query(20, description="每页条数，1~100"),
    basis: str = Query("auto", description="auto|realtime|daily"),
    refresh: int = Query(0, description="1=跳过缓存读强制重算（5s 防抖；结果回写缓存）"),
    _user: dict = Depends(require_role("viewer")),
) -> APIResponse[dict]:
    """全市场在册证券列表：服务端排序 + 分页（选股中心 · 股票列表）。

    数据源与降级：
    - 基础行 = ``data/parquet/cs/daily_bar`` 的**最新有效截面日**（按行数阈值
      往回回溯，见 ``data.screening.latest_valid_section_date``）；
    - 涨跌幅 / 成交额 / 两列市值优先取外部实时快照（腾讯→新浪，200 只一片
      串行抓取）；覆盖率 < 50% 或外部源不可达 → **整表**切本地日终口径，
      ``degraded=True``、两列市值恒 ``null``（红线：绝不部分混用、绝不填 0）；
    - 任何失败都返回 HTTP 200 + 非空列表（最坏情况退化为本地截面行）。

    单位：``close``/``amount`` = 元；``amount_yi``/``total_cap_yi``/
    ``float_cap_yi`` = 亿元；``pct``/``turnover`` = 百分比。

    排序：非法 ``sort`` **不报错**，回落默认并在 ``sort_applied`` / ``dir_applied``
    回显实际生效值（默认 = ``code`` 升序）。
    """
    # ---- 参数校验（非法 -> 业务码 40000；可容错的 -> 静默回落） ----
    if page < 1:
        raise AQPException(ERR_PARAMS, "page 必须 ≥ 1")
    if not 1 <= page_size <= STOCKS_PAGE_SIZE_MAX:
        raise AQPException(ERR_PARAMS, f"page_size 必须在 1~{STOCKS_PAGE_SIZE_MAX} 之间")
    if len(q or "") > STOCKS_Q_MAX_LEN:
        raise AQPException(ERR_PARAMS, f"q 长度不能超过 {STOCKS_Q_MAX_LEN}")
    # board 非法由 filter_stock_rows 统一抛（与 data/screening.filter_universe
    # 同口径、同文案），此处不重复校验以免文案漂移
    exclude_st = 1 if exclude_st == 1 else 0
    basis_eff = basis if basis in STOCKS_BASIS else "auto"

    async def _build() -> dict:
        payload = await asyncio.to_thread(build_market_stock_rows, basis_eff)
        if basis_eff == "daily":
            # 用户显式指定日终口径：不触网，两列市值本来就是 None
            payload.update({
                "basis": "daily",
                "source": "local",
                "degraded": False,
                "quote_coverage": None,
                "basis_desc": f"本地日终截面 {payload.get('trade_date')}（用户指定日终口径）",
            })
            return payload
        quotes, source, as_of = await _fetch_quotes_sharded(
            [str(r["symbol"]) for r in (payload.get("rows") or [])])
        return await asyncio.to_thread(
            apply_quote_snapshot, payload, quotes, source, as_of)

    data = await cached_or_build(k_screener_stocks(basis_eff), _build,
                                 ttl=STOCKS_TTL, refresh=1 if refresh == 1 else 0)

    rows: list[dict] = list(data.get("rows") or [])
    options = stock_options(rows)
    filtered = await asyncio.to_thread(
        filter_stock_rows, rows, board, industry, q, exclude_st)
    sorted_rows, sort_applied, dir_applied = await asyncio.to_thread(
        sort_stock_rows, filtered, sort, sort_dir)

    total = len(sorted_rows)
    start = (page - 1) * page_size
    items = sorted_rows[start:start + page_size]

    return ok({
        "total": total,
        "page": page,
        "page_size": page_size,
        "trade_date": data.get("trade_date"),
        "as_of": data.get("as_of"),
        "basis": data.get("basis"),
        "basis_desc": data.get("basis_desc"),
        "basis_fields": data.get("basis_fields") or {},
        "source": data.get("source"),
        "degraded": bool(data.get("degraded")),
        "quote_coverage": data.get("quote_coverage"),
        "sort_applied": sort_applied,
        "dir_applied": dir_applied,
        "items": items,
        "options": options,
        "stale": bool(data.get("stale", False)),
        "from_cache": data.get("from_cache", False),
    })
