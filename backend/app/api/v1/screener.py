"""Screener API（P1-5）：读 pred_daily 排序选股，缓存键含全部参数。"""
from __future__ import annotations

import asyncio
from datetime import date, datetime, timedelta

import polars as pl
from fastapi import APIRouter, Depends, Query
from loguru import logger

from ...cache.keys import k_screener
from ...cache import swr
from ...cache.swr import cached_or_build
from ...core.config import get_settings
from ...core.auth import require_role
from ...core.errors import ERR_DATA_EMPTY, APIResponse, AQPException, ok
from ...data.screening import (compute_stats, enrich_items,
                               filter_universe, instrument_info,
                               load_screener_snapshot)

router = APIRouter()


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
