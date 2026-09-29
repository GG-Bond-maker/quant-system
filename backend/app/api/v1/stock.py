"""
个股接口（AQP）。

- GET /api/v1/stock/search            代码/名称模糊搜索
- GET /api/v1/stock/{symbol}/profile  个股档案 + 最新行情
- GET /api/v1/stock/{symbol}/kline    K 线（复权 + MA/MACD/RSI/BOLL 指标）
- GET /api/v1/stock/{symbol}/predict  ML 预测（Redis 缓存 -> ml/predict.py 引擎）
- GET /api/v1/stock/{symbol}/panels  个股详情面板（资金流向/事件/筹码/风险/股东）

复权口径说明：AKShare 返回的即是相应复权价，故按 dataset 区分存储：
    daily_bar（不复权）/ daily_bar_qfq（前复权）/ daily_bar_hfq（后复权）
由 bootstrap / update_daily 脚本负责落库；未拉取的口径返回 ERR_DATA_EMPTY。
"""
from __future__ import annotations

import asyncio
import os
from collections.abc import Callable
from typing import Any

import orjson
import polars as pl
from fastapi import APIRouter, Depends, Query
from loguru import logger
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from ...cache.keys import (
    k_stock_block,
    k_stock_kline,
    k_stock_predict,
    k_stock_profile,
)
from ...cache.redis_client import RedisClient
from ...cache.swr import write_cache
from ...core.auth import require_role
from ...core.errors import (
    ERR_DATA_EMPTY,
    ERR_NOT_FOUND,
    APIResponse,
    AQPException,
    ok,
)
from ...data.panels import (
    TTL,
    build_chip,
    build_events,
    build_fundamentals,
    build_holders,
    build_money_flow,
    build_north,
    build_quote,
    build_risk,
)
from ...data.parquet_store import read_symbol_dataset, today_trade_date_or_last
from ...db.models import Instrument
from ...db.session import get_db
from ...domain.indicators import enrich_kline
from ...ml.predict import predict_symbol

router = APIRouter()

_ADJUST_DATASET = {"none": "daily_bar", "qfq": "daily_bar_qfq", "hfq": "daily_bar_hfq"}

# 个股详情面板单块超时上限（秒）。8 块并发构建，外部源（东财/腾讯）慢或挂起时
# 不能让整个 /panels 响应无限等待——超时块降级为 unavailable，其余块正常返回。
# 可用环境变量 AQP_PANEL_BLOCK_TIMEOUT 覆盖。
_PANEL_BLOCK_TIMEOUT: float = float(os.getenv("AQP_PANEL_BLOCK_TIMEOUT", "4.5"))


def _nan_to_null(df: pl.DataFrame) -> pl.DataFrame:
    """把浮点 NaN 统一替换为 null，避免 JSON 序列化出现非法 NaN 字面量。"""
    cols = [c for c, dt in df.schema.items() if dt.is_float()]
    return df.with_columns([pl.col(c).fill_nan(None) for c in cols]) if cols else df


# ---------------- 搜索 ----------------
@router.get("/search", response_model=APIResponse[list])
async def search_stock(
    q: str = Query(..., min_length=1, max_length=32, description="代码或名称关键词"),
    limit: int = Query(20, ge=1, le=200),
    db: AsyncSession = Depends(get_db),
    _user: dict = Depends(require_role("viewer")),
) -> APIResponse[list]:
    """按代码 / 名称 / 标准代码模糊搜索（SQLite instrument 表）。"""
    like = f"%{q}%"
    stmt = (
        select(Instrument)
        .where(
            (Instrument.name.like(like))
            | (Instrument.code.like(like))
            | (Instrument.symbol.like(like))
        )
        .limit(limit)
    )
    rows = (await db.scalars(stmt)).all()
    return ok([
        {
            "symbol": r.symbol, "code": r.code, "name": r.name,
            "market": r.market, "is_st": r.is_st,
        }
        for r in rows
    ])


# ---------------- 档案 + 最新行情 ----------------
@router.get("/{symbol}/profile", response_model=APIResponse[dict])
async def stock_profile(
    symbol: str,
    db: AsyncSession = Depends(get_db),
    _user: dict = Depends(require_role("viewer")),
) -> APIResponse[dict]:
    """个股基本面档案 + Parquet 最新行情；Redis 缓存 6h。"""
    key = k_stock_profile(symbol)
    cached = await RedisClient.get(key)
    if cached:
        return ok(orjson.loads(cached))

    r = await db.scalar(select(Instrument).where(Instrument.symbol == symbol))
    if not r:
        raise AQPException(ERR_NOT_FOUND, f"标的不存在: {symbol}")

    bars = read_symbol_dataset("daily_bar", symbol)
    latest: dict | None = None
    if not bars.is_empty():
        tail = bars.tail(1).to_dicts()[0]
        prev_close = bars["close"][-2] if bars.height >= 2 else None
        pct = (
            round((tail["close"] / prev_close - 1) * 100, 2)
            if prev_close else None
        )
        latest = {
            "date": str(tail["date"]), "close": tail["close"], "pct": pct,
            "open": tail.get("open"), "high": tail.get("high"), "low": tail.get("low"),
            "volume": tail.get("volume"), "amount": tail.get("amount"),
        }

    data = {
        "symbol": r.symbol, "code": r.code, "name": r.name,
        "market": r.market, "type": r.instrument_type,
        "list_date": r.list_date.isoformat() if r.list_date else None,
        "is_st": r.is_st, "industry": r.industry, "area": r.area,
        "latest": latest,
    }
    await RedisClient.set(key, orjson.dumps(data), ex=3600 * 6)
    return ok(data)


# ---------------- K 线 + 指标 ----------------
@router.get("/{symbol}/kline", response_model=APIResponse[dict])
async def stock_kline(
    symbol: str,
    adjust: str = Query("none", pattern=r"^(qfq|hfq|none)$"),
    start: str = Query(..., pattern=r"^\d{8}$", description="YYYYMMDD"),
    end: str = Query(..., pattern=r"^\d{8}$"),
    indicators: bool = Query(True, description="是否叠加 MA/MACD/RSI/BOLL"),
    _user: dict = Depends(require_role("viewer")),
) -> APIResponse[dict]:
    """K 线数据（含复权口径与可选技术指标）；Redis 缓存 10min。"""
    # P1-35：形状由 Query(pattern) 校验，**日历**必须在这里严格校验——
    # 原实现直接 `date(int(start[:4]), ...)`，`start=20269999` 抛 ValueError
    # 被全局处理器归成 code=50000（"系统故障"），而它是纯参数错误（40000）。
    # 放在缓存查询之前：非法参数不消耗任何缓存/IO。
    from ...core.params import parse_yyyymmdd

    s_date = parse_yyyymmdd(start, field="start")
    e_date = parse_yyyymmdd(end, field="end")
    key = k_stock_kline(symbol, adjust, start, end)
    cached = await RedisClient.get(key)
    if cached:
        return ok(orjson.loads(cached))

    dataset = _ADJUST_DATASET[adjust]
    # L3 列裁剪：K 线消费 OHLCV+amount，投影读减少 parquet IO（跨年分区逐文件）
    df = await asyncio.to_thread(
        read_symbol_dataset, dataset, symbol, s_date, e_date,
        ["date", "open", "high", "low", "close", "volume", "amount"])
    if df.is_empty():
        adjust_label = {"qfq": "前复权", "hfq": "后复权", "none": "不复权"}[adjust]
        raise AQPException(
            ERR_DATA_EMPTY,
            f"暂无 {symbol} 的 {adjust_label} K 线数据（dataset={dataset}），请先运行数据更新",
        )

    if indicators:
        df = await asyncio.to_thread(enrich_kline, df)
    df = _nan_to_null(df)
    bars = df.to_dicts()

    result = {
        "symbol": symbol, "adjust": adjust,
        "start": start, "end": end,
        "count": len(bars), "bars": bars,
    }
    await RedisClient.set(key, orjson.dumps(result), ex=600)
    return ok(result)


# ---------------- ML 预测 ----------------
@router.get("/{symbol}/predict", response_model=APIResponse[dict])
async def stock_predict(
        symbol: str,
        _user: dict = Depends(require_role("researcher"))) -> APIResponse[dict]:
    """个股 ML 预测：Redis 优先 -> 未命中调用 ml/predict.py 引擎
    （读 Parquet 历史 -> 因子 -> LightGBM 推理 -> Top-5 SHAP 贡献）。"""
    td = today_trade_date_or_last().strftime("%Y%m%d")
    key = k_stock_predict(symbol, td)
    cached = await RedisClient.get(key)
    if cached:
        return ok(orjson.loads(cached))

    # predict_symbol 为同步 CPU 密集（因子构建），移出事件循环；
    # 其内部抛出的 AQPException 会由全局异常处理器转为统一响应。
    data = await asyncio.to_thread(predict_symbol, symbol)
    await RedisClient.set(key, orjson.dumps(data), ex=3600 * 6)
    return ok(data)


# ---------------- 个股详情面板（分块聚合 + 独立降级） ----------------
async def _cached_block(
    symbol: str,
    block: str,
    trade_date: str,
    builder: Callable[..., dict],
    *,
    params_key: str = "default",
    **kw: Any,
) -> dict:
    """单块：Redis 优先 -> 构建 -> 写缓存；异常降级为 unavailable。

    各块独立缓存、独立 TTL、独立失败：任一数据源抖动只影响对应组件，
    ⚠️ 异常串只进日志，对外统一回 "数据源暂时不可用"。
    """
    key = k_stock_block(symbol, block, trade_date, params_key)
    cached, stale = await RedisClient.get_stale(key)
    if cached:
        data = orjson.loads(cached)
        data["from_cache"] = True
        if stale:
            data["stale"] = True
        return data
    try:
        data = await asyncio.to_thread(builder, symbol, **kw)
    except Exception as e:  # noqa: BLE001 分块降级的核心：捕获一切
        logger.warning(f"[panels] {symbol} block '{block}' unavailable: {type(e).__name__}: {e!r}")
        data = {"status": "unavailable", "reason": "数据源暂时不可用"}
    data["from_cache"] = False
    ttl = TTL.get(block, 600)
    # 与 swr.write_cache 共用降级策略：unavailable 空态**不落地**（避免一次外部源
    # 抖动让该块在整个 TTL——north 21600s / events 3600s——内持续返回空态且不自愈），
    # degraded 用很短 TTL（≤15s）落地以便快速自愈。此处不缓存失败与 panels 的 TTL 数值无关。
    await write_cache(key, data, ttl, stale_window=1800)
    return data


@router.get("/{symbol}/panels", response_model=APIResponse[dict])
async def stock_panels(
    symbol: str,
    event_limit: int = Query(3, ge=1, le=10, description="近期事件条数"),
    chip_lookback: int = Query(120, ge=20, le=500, description="筹码分布回看交易日"),
    risk_window: int = Query(252, ge=60, le=1000, description="风险度量回看交易日"),
    _user: dict = Depends(require_role("viewer")),
) -> APIResponse[dict]:
    """个股详情面板聚合：资金流向 / 基本面 / 近期事件 / 筹码分布 / 风险度量 / 股东信息。

    所有数据块并发构建、独立降级（status: ok | degraded | unavailable），
    接口本身恒可用；每块按自身 TTL 独立缓存（快照 5min，股东信息 6h）。
    """
    td = today_trade_date_or_last().strftime("%Y%m%d")

    jobs: dict[str, Callable[..., Any]] = {
        "quote": lambda: _cached_block(symbol, "quote", td, build_quote),
        "money_flow": lambda: _cached_block(symbol, "money_flow", td, build_money_flow),
        "north": lambda: _cached_block(symbol, "north", td, build_north),
        "fundamentals": lambda: _cached_block(symbol, "fundamentals", td, build_fundamentals),
        "events": lambda: _cached_block(
            symbol, "events", td, build_events,
            params_key=f"limit{event_limit}", limit=event_limit),
        "holders": lambda: _cached_block(symbol, "holders", td, build_holders),
        "chip": lambda: _cached_block(
            symbol, "chip", td, build_chip,
            params_key=f"lookback{chip_lookback}", lookback=chip_lookback),
        "risk": lambda: _cached_block(
            symbol, "risk", td, build_risk,
            params_key=f"window{risk_window}", window=risk_window),
    }
    keys = list(jobs)
    params_by_block = {
        "events": f"limit{event_limit}",
        "chip": f"lookback{chip_lookback}",
        "risk": f"window{risk_window}",
    }

    async def _with_timeout(name: str, coro: Any) -> dict:
        """单块超时上限：外部源慢/挂起时不能让整个面板无限等待。

        超时值在**调用时**读取模块全局（而非用默认参数在定义时绑定），
        这样测试可通过 monkeypatch 覆盖，运行期也一致。

        注：``asyncio.to_thread`` 本身不可取消，超时后线程仍在后台跑完（由
        requests 自身的 connect/read 超时兜底），这里只保证**响应时间有界**。
        """
        timeout = _PANEL_BLOCK_TIMEOUT
        try:
            return await asyncio.wait_for(coro, timeout=timeout)
        except TimeoutError:
            logger.warning(f"[panels] {symbol} block '{name}' 超时（>{timeout:.1f}s）")
            # 优先返回 SWR 影子键旧值；旧值也没有时才显式 unavailable，不造数。
            key = k_stock_block(
                symbol, name, td, params_by_block.get(name, "default"))
            stale_value = await RedisClient.get(key + ":swr")
            if stale_value is not None:
                stale_data = orjson.loads(stale_value)
                stale_data["from_cache"] = True
                stale_data["stale"] = True
                return stale_data
            return {"status": "unavailable", "from_cache": False,
                    "reason": f"数据源响应超时（>{timeout:.1f}s）"}

    results = await asyncio.gather(
        *(_with_timeout(k, f()) for k, f in jobs.items()))

    return ok({
        "symbol": symbol,
        "trade_date": td,
        **dict(zip(keys, results)),
    })
