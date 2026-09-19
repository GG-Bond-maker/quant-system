"""
市场概览接口（AQP）。

GET /api/v1/market/overview
聚合：主要指数走势 / 涨跌分布与成交额 / 北向与主力资金 / 异动监控 / ML 推荐榜。
集成 Redis 缓存（TTL 300s）；任一数据块失败均【优雅降级】为
    {"status": "unavailable", "reason": "..."}
不阻塞其他块，保证接口恒可用。
"""
from __future__ import annotations

import asyncio
from concurrent.futures import ThreadPoolExecutor
from datetime import date, datetime, timedelta

import pandas as pd
import polars as pl
from fastapi import APIRouter, Depends, Query
from typing import Any

from loguru import logger

from ...cache.keys import (k_market_overview, k_market_overview_daily,
                           k_market_overview_rt)
from ...cache import swr
from ...cache.swr import cached_or_build
from ...cache.redis_client import RedisClient
from ...core.config import get_settings
from ...core.auth import require_role
from ...core.errors import APIResponse, ERR_PARAMS, fail, ok
from ...core.metrics import OVERVIEW_CACHE_TOTAL
from ...data.ingest.akshare_adapter import (CORE_INDICES, fetch_index_daily,
                                            get_akshare, _safe_call)
from ...data.parquet_store import read_symbol_dataset, today_trade_date_or_last
from ...data.screening import filter_universe
# Task 16（A-P1-7）：资金流聚合下沉 services；此处重导出保持调用方路径不变
from ...services.market_service import _build_money_flow  # noqa: F401

router = APIRouter()


# ---------------- 各数据块构建（同步阻塞，均在 to_thread 中执行） ----------------
def _build_indices() -> dict:
    """核心指数：最新收盘、涨跌幅、近 60 日迷你走势。"""
    items: list[dict] = []
    errors: list[str] = []
    for code, name in CORE_INDICES:
        try:
            df = fetch_index_daily(code)
            if df.is_empty() or df.height < 2:
                errors.append(code)
                continue
            tail = df.tail(60)
            close_last = float(tail["close"][-1])
            close_prev = float(df["close"][-2])
            items.append({
                "code": code,
                "name": name,
                "close": round(close_last, 2),
                "pct": round((close_last / close_prev - 1) * 100, 2),
                "sparkline": [round(float(v), 2) for v in tail["close"].to_list()],
            })
        except Exception as e:
            logger.debug(f"[overview] index fetch degraded {code}: {e!r}")
            errors.append(f"{code}:{type(e).__name__}")
    if not items:
        # 同上：errors 里含异常类名，属内部细节，只进日志不进响应
        logger.warning(f"[overview] indices unavailable: {errors}")
        return {"status": "unavailable", "reason": "指数数据源暂不可用"}
    return {"status": "ok", "items": items, "failed": errors}


def _bucket_of(p: float) -> str:
    """涨跌幅 -> 分布区间键（与前端 BlockGrid 列顺序一致）。"""
    if p <= -7: return "b1"
    if p <= -5: return "b2"
    if p <= -3: return "b3"
    if p < 0: return "b4"
    if p == 0: return "b5"
    if p < 3: return "b6"
    if p < 5: return "b7"
    if p < 7: return "b8"
    return "b9"


def _heat_payload(source: str, pct, extra: dict | None = None) -> dict:
    """涨跌分布公共拼装：涨跌/平家数 + 涨跌停近似 + 区间分布桶（pct 兼容 pandas/polars）。"""
    vals = [float(v) for v in pct.to_list() if v is not None and v == v]
    buckets = {b: 0 for b in ["b1", "b2", "b3", "b4", "b5", "b6", "b7", "b8", "b9"]}
    up = down = flat = limit_up = limit_down = 0
    for p in vals:
        buckets[_bucket_of(p)] += 1
        up += p > 0
        down += p < 0
        flat += p == 0
        limit_up += p >= 9.8      # 近似：未区分板块 20%/30% 档
        limit_down += p <= -9.8
    out = {
        "status": "ok",
        "source": source,
        "up": up,
        "down": down,
        "flat": flat,
        "limit_up": limit_up,
        "limit_down": limit_down,
        "buckets": buckets,
    }
    if extra:
        out.update(extra)
    return out


def _heat_from_local(target: date | None = None) -> dict:
    """东财实时快照不可用时的降级：用本地 daily_bar 计算涨跌分布。"""
    s = get_settings()
    year = (target or today_trade_date_or_last()).year
    from ...data.parquet_store import read_parquet_columns

    files = sorted((s.DATA_ROOT / "daily_bar").glob(f"symbol=*/year={year}.snappy.parquet"))
    if not files:
        return {"status": "unavailable", "reason": "本地无日线数据"}
    df = read_parquet_columns(files, ["symbol", "date", "close", "amount"])
    if df.is_empty():
        return {"status": "unavailable", "reason": "本地日线数据为空"}
    last_day = df["date"].max()
    df = df.sort("date").with_columns(
        pl.col("close").shift(1).over("symbol").alias("prev"))
    last = df.filter(pl.col("date") == last_day).with_columns(
        ((pl.col("close") / pl.col("prev") - 1) * 100).alias("pct"))
    pct = last["pct"].drop_nulls()
    out = _heat_payload("local", pct, {
        "total_amount_yi": round(float(last["amount"].sum() or 0) / 1e8, 1),
        "note": f"实时快照不可用，按本地日线 {last.height} 只标的计算",
    })
    return out


def _build_heat() -> dict:
    """涨跌分布 + 涨停/跌停近似计数 + 两市成交额（东财全市场快照，失败降级本地日线）。"""
    try:
        spot = _safe_call(get_akshare().stock_zh_a_spot_em, )
        pct = pd.to_numeric(spot["涨跌幅"], errors="coerce").dropna()
        amount_yi = float(
            pd.to_numeric(spot.get("成交额"), errors="coerce").sum() / 1e8
        )
        return _heat_payload("em", pct, {"total_amount_yi": round(amount_yi, 1)})
    except Exception as e:
        logger.debug(f"[overview] heat degraded to local: {e!r}")
        local = _heat_from_local()
        if local.get("status") != "ok":
            local["reason"] = f"{type(e).__name__}: {e}"
        return local


def _sectors_from_local(top_n: int = 12) -> dict:
    """东财板块榜不可用时的降级：本地日线 + instrument.industry 聚合行业涨跌。"""
    try:
        import sqlite3

        s = get_settings()
        year = today_trade_date_or_last().year
        files = sorted((s.DATA_ROOT / "daily_bar").glob(f"symbol=*/year={year}.snappy.parquet"))
        if not files:
            return {"status": "unavailable", "reason": "本地无日线数据"}
        df = pl.read_parquet(files, columns=["symbol", "date", "close"])
        last_day = df["date"].max()
        df = df.sort("date").with_columns(
            pl.col("close").shift(1).over("symbol").alias("prev"))
        last = df.filter(pl.col("date") == last_day).with_columns(
            ((pl.col("close") / pl.col("prev") - 1) * 100).alias("pct"))
        with sqlite3.connect(f"file:{s.SQLITE_PATH}?mode=ro", uri=True) as conn:
            rows = conn.execute(
                "SELECT symbol, COALESCE(NULLIF(industry, ''), '其他'), name "
                "FROM instrument").fetchall()
        meta = {r[0]: (r[1], r[2]) for r in rows}
        recs: dict[str, list[tuple[float, str]]] = {}
        for r in last.iter_rows(named=True):
            ind = meta.get(r["symbol"], ("其他", None))[0]
            if r["pct"] is not None:
                recs.setdefault(ind, []).append((float(r["pct"]), r["symbol"]))
        items = []
        for ind, lst in recs.items():
            avg = sum(p for p, _ in lst) / len(lst)
            top_sym = max(lst, key=lambda x: x[0])[1]
            items.append({
                "name": ind,
                "pct": round(avg, 2),
                "leader": meta.get(top_sym, (None, None))[1],
                "leader_pct": None,
                "count": len(lst),
            })
        items.sort(key=lambda x: -x["pct"])
        return {"status": "ok", "source": "local", "items": items[:top_n],
                "note": f"实时板块源不可用，按本地样本 {sum(len(v) for v in recs.values())} 只聚合"}
    except Exception as e:  # noqa: BLE001
        logger.warning(f"[overview] local sectors degraded: {type(e).__name__}: {e!r}")
        return {"status": "unavailable", "reason": "板块数据源暂不可用"}


def _build_sectors(top_n: int = 12) -> dict:
    """热门板块：东财行业板块涨幅榜 Top-N，附领涨股；失败降级本地行业聚合。"""
    try:
        df = _safe_call(get_akshare().stock_board_industry_name_em)
        df = df.sort_values("涨跌幅", ascending=False).head(top_n)
        items = []
        for _, r in df.iterrows():
            leader = r.get("领涨股票")
            items.append({
                "name": str(r["板块名称"]),
                "pct": round(float(r["涨跌幅"]), 2),
                "leader": str(leader) if leader and str(leader) != "nan" else None,
                "leader_pct": (round(float(r["领涨股票-涨跌幅"]), 2)
                               if r.get("领涨股票-涨跌幅") is not None
                               and str(r.get("领涨股票-涨跌幅")) != "nan" else None),
            })
        return {"status": "ok", "items": items}
    except Exception as e:
        # MEDIUM 修复：不要把内部异常串（ConnectionError / RemoteDisconnected 等）
        # 直接放进 API 响应 —— 实测曾把
        # "ConnectionError: ... RemoteDisconnected(...)" 原样返回给前端。
        # 细节只进日志（warning 便于排查），对外只回固定文案 + trace_id。
        logger.warning(f"[overview] sectors degraded: {type(e).__name__}: {e!r}")
        return _sectors_from_local(top_n)


def _build_anomalies(heat: dict) -> dict:
    """异动监控：日频数据下输出涨停近似名单；盘中快速波动需实时源（P2）。"""
    items: list[dict] = []
    if heat.get("status") == "ok":
        try:
            spot = _safe_call(get_akshare().stock_zh_a_spot_em, )
            pct = pd.to_numeric(spot["涨跌幅"], errors="coerce")
            mask = pct >= 9.8
            sub = spot.loc[mask, ["代码", "名称", "涨跌幅"]].head(20)
            items = [
                {"rule": "R_HT", "code": str(r["代码"]), "name": str(r["名称"]),
                 "pct": round(float(r["涨跌幅"]), 2)}
                for _, r in sub.iterrows()
            ]
        except Exception as e:
            logger.debug(f"[overview] anomalies scan degraded: {e!r}")
            items = []
    return {
        "status": "ok" if items else "degraded",
        "items": items,
        "note": "涨停为 ±9.8% 近似口径；盘中放量/快速波动异动需实时行情源，P2 接入",
    }


def _latest_announcements() -> dict[str, dict]:
    """公告摘要：symbol -> 最新一条 {title, sentiment, pub_date}。

    读取口径与个股面板 ``panels.build_events`` 共用 ``data.announcements``
    公共读函数，消除同一「公告」语义的两套实现（审计 F-01 / §4-2）。
    """
    try:
        from ...data.announcements import latest_announcement_by_symbol

        return latest_announcement_by_symbol()
    except Exception as e:  # noqa: BLE001 资讯仅为附注，失败不影响推荐榜
        logger.debug(f"[overview] announcements degraded: {e!r}")
        return {}


def _build_recommend(k: int, target: date | None = None) -> dict:
    """ML 每日推荐榜：predictions 分区按 pred_score 降序 Top-K，
    附名称 / 近 30 根 OHLC（迷你K线）/ 最新公告（近期资讯）/ 真实横截面分位。

    rank_pct 说明：pred_score 在【当日全市场预测样本】中的百分位（0~100），
    由真实预测分布秩计算，非置信度。此前前端曾用 |pred_score|*1400+40 自造
    "信心指数"（保底 40、封顶 99），属编造数值，已由本字段取代。
    """
    s = get_settings()
    pred_root = s.DATA_ROOT / "predictions"
    if target is not None:
        chosen = pred_root / f"date={target.strftime('%Y%m%d')}.parquet"
        files = [chosen] if chosen.exists() else []
    else:
        files = sorted(pred_root.glob("date=*.parquet"))
    if not files:
        return {
            "status": "unavailable",
            "as_of": target.isoformat() if target else None,
            "reason": "model_not_ready",
            "message": "模型尚未产出该交易日的预测结果，请先运行训练与推理流水线",
            "coverage": {"available": 0, "total": 0, "ratio": None},
            "items": [],
        }
    try:
        df = pl.read_parquet(files[-1])
        if df.is_empty():
            return {
                "status": "unavailable",
                "as_of": target.isoformat() if target else None,
                "reason": "model_not_ready",
                "message": "模型预测分区为空，请重新运行推理流水线",
                "coverage": {"available": 0, "total": 0, "ratio": None},
                "items": [],
            }
        # 横截面分位：与当日全部预测样本比秩（样本数为 1 时退化为 100.0）
        trade_date = str(df["date"][0])[:10]
        candidates, pool_size = filter_universe(
            df, trade_date, "all", require_universe=True)
        denom = max(pool_size - 1, 1)
        candidates = candidates.with_columns(
            ((pl.col("pred_score").rank("average") - 1) / denom * 100)
            .round(1).alias("rank_pct")
        )
        top = (
            candidates.sort("pred_score", descending=True)
            .head(k)
            .with_columns(pl.col("pred_score").round(6))
            .to_dicts()
        )
        info: dict = {}
        try:
            from .screener import _load_instrument_info

            info = _load_instrument_info()
        except Exception as e:  # noqa: BLE001 名称兜底失败不影响推荐榜本身
            logger.debug(f"[overview] recommend name enrich degraded: {e!r}")
        anns = _latest_announcements()
        items = []
        for d in top:
            bars: list[dict] = []
            close = pct = amount = None
            try:
                bdf = read_symbol_dataset("daily_bar", d["symbol"]).sort("date").tail(30)
                if not bdf.is_empty() and "close" in bdf.columns:
                    bars = [{"date": str(x)[:10], "open": o, "high": h, "low": lo, "close": c}
                            for x, o, h, lo, c in zip(
                                bdf["date"].to_list(), bdf["open"].to_list(),
                                bdf["high"].to_list(), bdf["low"].to_list(),
                                bdf["close"].to_list())
                            if all(v is not None for v in (o, h, lo, c))]
                    close_value = bdf["close"][-1]
                    close = float(close_value) if close_value is not None else None
                    if bdf.height >= 2 and bdf["close"][-2] not in (None, 0):
                        pct = round((close / float(bdf["close"][-2]) - 1) * 100, 2) \
                            if close is not None else None
                    if "amount" in bdf.columns and bdf["amount"][-1] is not None:
                        amount = float(bdf["amount"][-1])
            except Exception as e:  # noqa: BLE001
                logger.debug(f"[overview] recommend bars degraded {d['symbol']}: {e!r}")
            # 预测分本身不是可交易行情；关键行情全空的候选不得作为推荐项返回。
            if close is None and pct is None and amount is None:
                continue
            items.append({
                "date": d["date"],
                "symbol": d["symbol"],
                "name": info.get(d["symbol"], (None, None))[0],
                "pred_score": d["pred_score"],
                "rank_pct": d.get("rank_pct"),
                "model_version": d.get("model_version"),
                "feature_version": d.get("feature_version"),
                "label_horizon": d.get("label_horizon"),
                "candidate_pool_size": pool_size,
                "filter_policy": "tradable_universe_exclude_st_halted",
                "close": close,
                "pct": pct,
                "amount": amount,
                "bars": bars,
                "news": anns.get(d["symbol"]),
            })

        as_of = trade_date
        total = len(top)
        available = len(items)
        coverage = {"available": available, "total": total,
                    "ratio": round(available / total, 4) if total else None}
        if total == 0:
            status, reason, message = (
                "ok", "no_matching_signals", "当前没有满足条件的有效信号")
        elif available == 0:
            status, reason, message = (
                "unavailable", "market_data_missing", "候选标的缺少有效行情，暂不展示推荐")
        elif available < total:
            status, reason, message = (
                "degraded", "market_data_partial",
                f"已过滤 {total - available} 条缺少有效行情的候选")
        else:
            status, reason, message = "ok", None, "数据正常"

        from .screener import _freshness

        freshness = _freshness(as_of)
        if freshness.get("is_stale") is True:
            status, reason = "degraded", "data_stale"
            message = freshness.get("note") or "行情数据未更新至最近已收盘交易日"
        return {
            "status": status,
            "as_of": as_of,
            "date": files[-1].stem.replace("date=", ""),
            "reason": reason,
            "message": message,
            "coverage": coverage,
            "freshness": freshness,
            "sample_size": pool_size,
            "items": items,
        }
    except Exception as e:
        logger.warning(f"[overview] recommend degraded: {type(e).__name__}: {e!r}")
        return {
            "status": "unavailable",
            "as_of": target.isoformat() if target else None,
            "reason": "recommendation_build_failed",
            "message": "推荐数据暂不可用，请稍后重试",
            "coverage": {"available": 0, "total": 0, "ratio": None},
            "items": [],
        }


def _build_ai_stats() -> dict:
    """用训练标签 horizon 评估推荐质量：RankIC、Top-K 精度和超额收益。"""
    try:
        s = get_settings()
        pred_files = sorted((s.DATA_ROOT / "predictions").glob("date=*.parquet"))
        if not pred_files:
            return {"status": "unavailable", "reason": "无预测结果"}
        pred = pl.concat([
            pl.read_parquet(f).select([c for c in
                ("date", "symbol", "pred_score", "model_version",
                 "feature_version", "label_horizon") if c in pl.read_parquet_schema(f)])
            .with_columns(pl.col("date").cast(pl.Date)) for f in pred_files
        ], how="diagonal_relaxed")
        horizon = int(s.ML_LABEL_HORIZON)
        # 评估必须锁定最新快照对应的模型/特征/标签版本，禁止把不同版本的分数池化。
        latest = pred.filter(pl.col("date") == pred["date"].max())
        for column in ("model_version", "feature_version"):
            if column in pred.columns and latest[column].drop_nulls().len():
                value = latest[column].drop_nulls()[0]
                pred = pred.filter(pl.col(column) == value)
        if "label_horizon" in pred.columns:
            pred = pred.filter(pl.col("label_horizon") == horizon)
        hfq_files = sorted((s.DATA_ROOT / "daily_bar_hfq").glob("symbol=*/year=*.snappy.parquet"))
        if not hfq_files:
            return {"status": "unavailable", "reason": "无后复权行情，无法评估推荐"}
        from ...data.parquet_store import read_parquet_columns
        min_symbols_per_day = 5
        bar = read_parquet_columns(hfq_files, ["symbol", "date", "close"])
        bar = bar.sort(["symbol", "date"]).with_columns(
            pl.col("close").shift(-horizon).over("symbol").alias("future_close"),
            pl.col("date").shift(-horizon).over("symbol").alias("future_date"),
        ).with_columns([
            (pl.col("future_close") / pl.col("close") - 1).alias("fwd_ret"),
            ((pl.col("future_date") - pl.col("date")).dt.total_days()).alias("gap_days"),
        ]).filter(pl.col("gap_days") <= horizon * 3 + 10)
        joined = (pred.join(bar.select(["symbol", "date", "fwd_ret"]),
                            on=["symbol", "date"], how="inner")
                  .drop_nulls(["pred_score", "fwd_ret"]))
        if joined.height < 30:
            return {"status": "unavailable", "reason": "可回溯样本不足"}
        pdf = joined.to_pandas()
        daily_ic: list[float] = []
        top_returns: list[float] = []
        all_returns: list[float] = []
        eligible_dates: list = []
        direction_hits = direction_total = 0
        top_k = 20
        for _, day in pdf.groupby("date"):
            if len(day) < min_symbols_per_day:
                continue
            ic = day["pred_score"].rank().corr(day["fwd_ret"].rank())
            if pd.isna(ic):
                continue
            eligible_dates.append(day["date"].iloc[0])
            daily_ic.append(float(ic))
            ranked = day.sort_values("pred_score", ascending=False)
            top = ranked.head(min(top_k, len(ranked)))
            top_returns.append(float(top["fwd_ret"].mean()))
            all_returns.append(float(day["fwd_ret"].mean()))
            direction_hits += int(((day["pred_score"] >= 0) == (day["fwd_ret"] >= 0)).sum())
            direction_total += len(day)
        if not daily_ic:
            return {"status": "unavailable", "reason": "有效截面不足，无法计算 RankIC"}
        eligible = pdf[pdf["date"].isin(eligible_dates)]
        top = eligible.sort_values(["date", "pred_score"], ascending=[True, False]).groupby("date").head(top_k)
        return {
            "status": "ok", "horizon": horizon,
            "label_price_basis": "hfq" if hfq_files else "raw_fallback",
            "rank_ic": round(float(pd.Series(daily_ic).mean()), 4),
            "rank_ic_positive_ratio": round(float((pd.Series(daily_ic) > 0).mean()), 4),
            "top_k": top_k,
            "top_k_precision": round(float((top["fwd_ret"] > 0).mean()), 4),
            "top_k_excess_return": round(float(pd.Series(top_returns).mean() - pd.Series(all_returns).mean()), 4),
            "directional_hit_rate": round(direction_hits / direction_total, 4) if direction_total else None,
            "samples": joined.height, "n_days": len(daily_ic),
        }
    except Exception as e:
        logger.debug(f"[overview] ai stats degraded: {type(e).__name__}: {e!r}")
        return {"status": "unavailable", "reason": f"{type(e).__name__}: {e}"}


def _build_sentiment(heat: dict) -> dict:
    """AI 市场情绪指数（0-100）：广度为主、涨跌停温差为辅的确定性加权。

        score = 50 + (涨-跌)/max(涨+跌,1)×45 + (涨停-跌停)×1.5，截断 [0,100]
    分档：<20 极度恐慌 / <40 恐慌 / <60 中性 / <80 乐观 / ≥80 贪婪
    P2-10：响应显式标注为平台自研口径（非任何第三方情绪指数），
    权重为产品设定而非拟合结果，避免被误读为市场标准指标。
    """
    if heat.get("status") != "ok":
        return {"status": "unavailable", "reason": "缺涨跌分布，无法计算情绪"}
    up, down = heat["up"], heat["down"]
    breadth = (up - down) / max(up + down, 1)
    score = round(50 + breadth * 45 + (heat["limit_up"] - heat["limit_down"]) * 1.5)
    score = max(0, min(100, score))
    label = ("极度恐慌" if score < 20 else "恐慌" if score < 40
             else "中性" if score < 60 else "乐观" if score < 80 else "贪婪")
    return {
        "status": "ok", "score": score, "label": label,
        "kind": "platform",
        "basis": "平台自研口径：score = 50 + (涨-跌)/(涨+跌)×45 + (涨停-跌停)×1.5，"
                 "权重为产品设定，非第三方情绪指数",
    }


def _pred_dates() -> list[str]:
    """可用预测交易日（升序，YYYYMMDD），供前端交易日切换下拉。"""
    out = []
    for f in sorted((get_settings().DATA_ROOT / "predictions").glob("date=*.parquet")):
        stem = f.stem.replace("date=", "")
        if stem.isdigit():
            out.append(stem)
    return out


def _build_rt(td: date) -> dict:
    """实时块：受限并发抓取，绝不以串行全市场扫描阻塞首屏。

    每个子块仍保留自身的真实降级数据；三个互不依赖的 IO 块最多并发三个，
    外层路由另有五秒响应预算，避免外部源重试时拖住用户请求。
    """
    with ThreadPoolExecutor(max_workers=3, thread_name_prefix="overview-rt") as pool:
        indices_future = pool.submit(_build_indices)
        flow_future = pool.submit(_build_money_flow)
        anomalies_future = pool.submit(lambda: _build_anomalies(_build_heat()))
        def _result(future, label: str) -> dict:
            try:
                return future.result()
            except Exception as exc:  # noqa: BLE001
                logger.warning(f"[overview] {label} degraded: {type(exc).__name__}")
                return {"status": "unavailable", "reason": "实时数据源暂不可用"}

        blocks = {
            "indices": _result(indices_future, "indices"),
            "money_flow": _result(flow_future, "money_flow"),
            "anomalies": _result(anomalies_future, "anomalies"),
        }
    degraded = any(
        (block.get("status") or "") in ("degraded", "unavailable")
        for block in blocks.values()
    )
    blocks["data_freshness"] = {
        "status": "degraded" if degraded else "fresh",
        "source": "bounded_realtime",
    }
    return blocks


def _build_daily(td: date, recommend_k: int) -> dict:
    """日频块（L2-2）：涨跌分布/板块/推荐榜/情绪，**零外部依赖**口径。

    - heat/sectors 用本地日线聚合（_heat_from_local/_sectors_from_local），
      外部源全挂时本块完全不受影响（验收口径）；
    - recommend 读 predictions、ai_stats 读 parquet、pred_dates 读分区名。
    - 写一次读多次：TTL 至次日盘后（_daily_ttl），盘后流水线更新日线后自然轮换。
    """
    heat = _heat_from_local()
    blocks = {
        "heat": heat,
        "sectors": _sectors_from_local(),
        "recommend": _build_recommend(recommend_k),
        "ai_stats": _build_ai_stats(),
        "sentiment": _build_sentiment(heat),
        "pred_dates": _pred_dates(),
    }
    degraded = any(
        isinstance(block, dict) and (block.get("status") or "") in ("degraded", "unavailable")
        for block in blocks.values()
    )
    blocks["data_freshness"] = {
        "status": "degraded" if degraded else "fresh",
        "source": "local_daily",
    }
    return blocks


def _build_overview(td: date, recommend_k: int) -> dict:
    """兼容聚合：实时与日频块并发构建，字段与旧 ``/overview`` 保持兼容。"""
    with ThreadPoolExecutor(max_workers=2, thread_name_prefix="overview") as pool:
        rt_future = pool.submit(_build_rt, td)
        daily_future = pool.submit(_build_daily, td, recommend_k)
        rt = rt_future.result()
        daily = daily_future.result()
    return {**rt, **daily, "data_freshness": {
        "status": ("degraded" if any(
            payload.get("status") == "degraded"
            for payload in (rt.get("data_freshness", {}), daily.get("data_freshness", {}))
        ) else "fresh"),
        "source": "bounded_composite",
    }}


# ---------------- 路由 ----------------
@router.get("/index/kline", response_model=APIResponse[dict])
async def index_kline(
    code: str = Query("sh000001", description="核心指数代码（sh000001/sz399001/...）"),
    limit: int = Query(400, ge=30, le=800, description="返回的日线根数"),
    refresh: int = Query(0, ge=0, le=1, description="1=跳过 fetch 层缓存强制重拉"),
) -> APIResponse[dict]:
    """核心指数日 K（腾讯源，fetch 层自带 TTL 缓存）。

    选股中心「大盘走势」数据源；仅开放 CORE_INDICES 白名单，避免任意代码探测。
    返回 ``{code, name, bars: [{date, close}]}``（升序）；bars 为空表示数据源暂不可达，
    前端按空态处理，不造数。
    """
    names = dict(CORE_INDICES)
    if code not in names:
        return fail(ERR_PARAMS, f"不支持的指数代码: {code}（可选：{'/'.join(names)}）")

    def _build() -> dict:
        from ...data.etf import fetch_index_kline  # 腾讯源，内部带 TTL 缓存
        bars = fetch_index_kline(code[:2], code[2:], limit=limit, force=bool(refresh))
        return {"code": code, "name": names[code],
                "bars": [{"date": b["date"], "close": b["close"]} for b in bars]}

    return ok(await asyncio.to_thread(_build))


# ---------------- 批量实时行情（§3.3，Sprint2） ----------------
# 抓取/缓存/降级链实现在 core/quotes_hub.quotes_snapshot —— 与 SSE quotes 频道
# 共享同一份 QUOTES_TTL 进程缓存（外部请求次数与调用方/订阅数量无关）。
QUOTES_MAX_SYMBOLS = 200


@router.get("/quotes", response_model=APIResponse[dict])
async def market_quotes(
    symbols: str = Query(..., min_length=6, max_length=4000,
                         description="逗号分隔的标的代码，如 600519.SH,000001.SZ"),
    _user: dict = Depends(require_role("viewer")),
) -> APIResponse[dict]:
    """批量实时行情快照（腾讯批量一次 HTTP 拉 N 只；1 次请求 = 1 份限速配额）。

    - 上限 200 只（超限业务码 `ERR_PARAMS = 40000`）；相同 symbols 集合（排序后哈希）进程内共享，
      TTL = QUOTES_TTL（默认 15s）——多页面轮询对外部源的压力与调用方数量无关；
    - 降级链：腾讯 → 新浪 → 空数组 + ``source:"degraded"``（不造数）；
    - **纪律**：盘中快照只用于展示，禁止写入 daily_bar（§3.5 一致性口径）。
    """
    from ...data.quotes_hub import quotes_snapshot

    syms = list(dict.fromkeys(s.strip().upper() for s in symbols.split(",") if s.strip()))
    if not syms:
        return fail(ERR_PARAMS, "symbols 不能为空")
    if len(syms) > QUOTES_MAX_SYMBOLS:
        return fail(ERR_PARAMS, f"一次最多查询 {QUOTES_MAX_SYMBOLS} 只（当前 {len(syms)} 只）")
    return ok(await quotes_snapshot(syms))


# 软过期后的旧值保留窗口（秒）：TTL 300s 过期后 30 分钟内回旧值 + 后台重建
OVERVIEW_STALE_WINDOW = 1800
# 实时块 TTL（L2-2）：分钟级数据，45s 兜底 + SWR 10 分钟窗口
RT_TTL = 45
RT_STALE_WINDOW = 600

# 兼容端点 /overview 的**请求路径**构建预算（秒）。
# ⚠️ 它只约束「同步请求路径」——缓存全失效 / Redis 降级时不能让用户等外网重试。
# **不约束后台重建路径**：慢构建（实测 _build_overview 23.5~113.5s）在 6s 预算下
# 100% 超时，若后台重建继承该预算，它每轮只会写回 unavailable 降级载荷，缓存永久
# 无法自愈（见 cached_or_build 的 background_build 参数）。故后台重建走
# _build_unbudgeted（无 wait_for）。
# 模块级常量在函数体内读取（非默认参数/非导入期固化），便于测试 monkeypatch 注入小值。
OVERVIEW_COMPAT_BUILD_TIMEOUT_SECONDS = 6.0

# overview 预热器的**提前续期**阈值（秒）：剩余 TTL 低于此值才重建。
# 推导：main._overview_warmer 每 240s 检查一次，主键 TTL = 300s。稳态下每轮检查时
# 剩余 TTL ≈ 300 - 240 = 60s < 120 ⇒ 每轮都会续期，且续期后仍有约 60s 余量，保证
# 「上一轮构建失败（warmer 只 warning）」时缓存不会进入真空期把用户打回冷路径。
OVERVIEW_WARM_RENEW_THRESHOLD_SECONDS = 120


def _daily_ttl(now: datetime | None = None) -> int:
    """日频块 TTL：至次日 15:30（盘后流水线之后自然轮换；周五覆盖到周一）。"""
    now = now or datetime.now()
    target = now.replace(hour=15, minute=30, second=0, microsecond=0)
    if now >= target:
        target += timedelta(days=1)
    return min(max(int((target - now).total_seconds()), 60), 86400 * 3)


async def _overview_block_response(
    key: str, build, *, ttl: int, stale_window: int, refresh: int,
    metrics_kind: str | None = None,
) -> dict[str, Any]:
    """rt/daily 块共用的 SWR 响应组装（契约与 /overview 一致）。"""
    from ...core.metrics import OVERVIEW_CACHE_TOTAL

    def _metrics(kind: str) -> None:
        OVERVIEW_CACHE_TOTAL.labels(result=kind).inc()

    return await cached_or_build(key, build, ttl=ttl, stale_window=stale_window,
                                 refresh=refresh, rebuild_lock_ttl=180,
                                 on_metrics=_metrics if metrics_kind else None)


@router.get("/overview/rt", response_model=APIResponse[dict])
async def market_overview_rt(
    refresh: int = Query(0, ge=0, le=1, description="1=跳过缓存读强制重算（5s 防抖）"),
) -> APIResponse[dict]:
    """实时块（L2-2）：指数 + 资金流 + 异动监控，TTL 45s + stale 回旧值。

    前端「轻刷新」只打本端点（分钟级看盘不触碰日频块的重物化）。
    """
    from ...cache.keys import k_market_overview_rt

    td = today_trade_date_or_last()
    key = k_market_overview_rt(td.strftime("%Y%m%d"))

    async def _build() -> dict:
        try:
            data = await asyncio.wait_for(asyncio.to_thread(_build_rt, td), timeout=5.0)
        except TimeoutError:
            data = {
                "indices": {"status": "unavailable", "reason": "实时数据源响应超时"},
                "money_flow": {"status": "unavailable", "reason": "实时数据源响应超时"},
                "anomalies": {"status": "unavailable", "reason": "实时数据源响应超时"},
                "data_freshness": {"status": "degraded", "source": "timeout",
                                   "reason": "实时块五秒预算已用尽"},
            }
        except Exception as exc:  # noqa: BLE001
            logger.warning(f"[overview] rt build degraded: {type(exc).__name__}")
            data = {
                "indices": {"status": "unavailable", "reason": "实时数据源暂不可用"},
                "money_flow": {"status": "unavailable", "reason": "实时数据源暂不可用"},
                "anomalies": {"status": "unavailable", "reason": "实时数据源暂不可用"},
                "data_freshness": {"status": "degraded", "source": "error"},
            }
        data["trade_date"] = td.strftime("%Y%m%d")
        data["as_of"] = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        return data

    data = await _overview_block_response(key, _build, ttl=RT_TTL,
                                          stale_window=RT_STALE_WINDOW,
                                          refresh=refresh, metrics_kind="rt")
    return ok(data)


@router.get("/overview/daily", response_model=APIResponse[dict])
async def market_overview_daily(
    recommend_k: int = Query(50, ge=1, le=200, description="推荐榜条数"),
    date: str | None = Query(None, pattern=r"^\d{8}$",
                             description="历史交易日（YYYYMMDD）：仅切换 AI 推荐快照日期"),
    refresh: int = Query(0, ge=0, le=1, description="1=跳过缓存读强制重算"),
) -> APIResponse[dict]:
    """日频块（L2-2）：本地口径涨跌分布/板块/推荐榜/AI 准确率/情绪，零外部依赖。

    TTL 至次日 15:30（盘后流水线更新日线后自然轮换）；板块/分布为本地
    daily_bar 聚合口径（与实时东财口径的差异已在 note 披露）。
    """
    from ...cache.keys import k_market_overview_daily

    td = today_trade_date_or_last()
    date_str = date or td.strftime("%Y%m%d")
    if date:
        # 历史快照路径：仅推荐榜按指定日期读取（不走缓存，与 /overview 历史路径同语义）
        data = await asyncio.to_thread(_build_daily, td, recommend_k)
        data["recommend"] = await asyncio.to_thread(
            _build_recommend, recommend_k,
            __import__("datetime").date.fromisoformat(
                f"{date[:4]}-{date[4:6]}-{date[6:]}"))
        data["trade_date"] = date_str
        data["from_cache"] = False
        return ok(data)
    key = k_market_overview_daily(date_str, recommend_k)

    async def _build() -> dict:
        try:
            data = await asyncio.wait_for(
                asyncio.to_thread(_build_daily, td, recommend_k), timeout=5.0)
        except TimeoutError:
            data = {
                "heat": {"status": "unavailable", "reason": "本地日频计算超时"},
                "sectors": {"status": "unavailable", "reason": "本地日频计算超时"},
                "recommend": {
                    "status": "unavailable", "as_of": date_str,
                    "reason": "recommendation_timeout", "message": "推荐计算超时，请稍后重试",
                    "coverage": {"available": 0, "total": 0, "ratio": None}, "items": [],
                },
                "ai_stats": {"status": "unavailable", "reason": "本地日频计算超时"},
                "sentiment": {"status": "unavailable", "reason": "本地日频计算超时"},
                "pred_dates": [],
                "data_freshness": {"status": "degraded", "source": "timeout",
                                   "reason": "日频块五秒预算已用尽"},
            }
        data["trade_date"] = date_str
        return data

    data = await _overview_block_response(key, _build, ttl=_daily_ttl(),
                                          stale_window=OVERVIEW_STALE_WINDOW,
                                          refresh=refresh)
    return ok(data)


@router.get("/overview", response_model=APIResponse[dict])
async def market_overview(
    recommend_k: int = Query(50, ge=1, le=200, description="推荐榜条数"),
    date: str | None = Query(None, pattern=r"^\d{8}$",
                             description="历史交易日（YYYYMMDD）：仅切换 AI 推荐快照日期"),
    refresh: int = Query(0, ge=0, le=1,
                         description="1=跳过缓存读强制重算（5s 防抖；重算结果回写缓存）"),
) -> APIResponse[dict]:
    """市场概览聚合接口：优先 Redis 缓存（TTL 300s），未命中实时构建。

    指数/涨跌分布/资金为实时口径；``date`` 仅切换 AI 推荐的历史快照。
    缓存策略（L1-2）：TTL 过期后 30 分钟内回旧值（stale=true）+ 后台重建，
    超过硬过期才同步重建；``refresh=1`` 跳过缓存读强制重算（防抖 5s）。
    """
    td = today_trade_date_or_last()
    date_str = date or td.strftime("%Y%m%d")
    if date:
        # 历史快照路径：推荐榜按指定日期读取，实时块照常聚合，不走缓存
        data = await asyncio.to_thread(_build_overview_hist, td, recommend_k, date)
        data["trade_date"] = date_str
        data["from_cache"] = False
        return ok(data)

    key = k_market_overview(date_str, recommend_k)

    async def _build() -> dict:
        # 兼容端点必须有严格预算：缓存全失效和 Redis 降级时也不能等外网重试。
        # 预算常量在函数体内读取，测试可 monkeypatch 注入小值。
        t0 = asyncio.get_running_loop().time()
        try:
            data = await asyncio.wait_for(
                asyncio.to_thread(_build_overview, td, recommend_k),
                timeout=OVERVIEW_COMPAT_BUILD_TIMEOUT_SECONDS)
        except TimeoutError:
            waited = asyncio.get_running_loop().time() - t0
            # 这条**必然发生**的降级此前完全无日志，在日志里看起来像偶发。
            logger.warning(
                f"[overview] {key} 请求路径超时：预算 "
                f"{OVERVIEW_COMPAT_BUILD_TIMEOUT_SECONDS:.1f}s 内未完成，"
                f"实际等待 {waited:.1f}s → 返回 unavailable 降级载荷"
                f"（该载荷无顶层 status，按 default_cacheable 谓词仍会落地缓存；"
                f"缓存自愈依赖无预算的后台重建 builder）")
            unavailable = {"status": "unavailable", "reason": "概览冷路径响应超时"}
            data = {
                "indices": dict(unavailable), "heat": dict(unavailable),
                "money_flow": dict(unavailable), "anomalies": dict(unavailable),
                "sectors": dict(unavailable),
                "recommend": {"status": "unavailable", "as_of": date_str,
                              "reason": "recommendation_unavailable",
                              "message": "推荐数据暂不可用，请稍后重试",
                              "coverage": {"available": 0, "total": 0, "ratio": None},
                              "items": []},
                "ai_stats": dict(unavailable), "sentiment": dict(unavailable),
                "pred_dates": [],
                "data_freshness": {"status": "degraded", "source": "timeout",
                                   "reason": "兼容概览六秒预算已用尽"},
            }
        except Exception as exc:  # noqa: BLE001
            logger.warning(f"[overview] compatibility build degraded: {type(exc).__name__}")
            unavailable = {"status": "unavailable", "reason": "概览数据源暂不可用"}
            data = {
                "indices": dict(unavailable), "heat": dict(unavailable),
                "money_flow": dict(unavailable), "anomalies": dict(unavailable),
                "sectors": dict(unavailable),
                "recommend": {"status": "unavailable", "as_of": date_str,
                              "reason": "recommendation_unavailable",
                              "message": "推荐数据暂不可用，请稍后重试",
                              "coverage": {"available": 0, "total": 0, "ratio": None},
                              "items": []},
                "ai_stats": dict(unavailable), "sentiment": dict(unavailable),
                "pred_dates": [],
                "data_freshness": {"status": "degraded", "source": "error"},
            }
        data["trade_date"] = date_str
        return data

    async def _build_unbudgeted() -> dict:
        """后台重建专用 builder：**不加 wait_for 预算**。

        请求路径可以有严格预算（宁可降级也不让用户等），但后台重建没有这个约束
        —— 它正是 warm_overview_cache 之外**唯一**能把好数据灌回缓存的路径。
        真实 _build_overview 实测 23.5~113.5s，若继承请求预算（6s）则 100% 超时，
        后台重建每轮只写回 unavailable 降级载荷 ⇒ 缓存永不自愈。

        异常**不在此吞**：交给 _spawn_rebuild 既有的 except Exception 记 warning，
        旧值继续服务（降级载荷也不会被写坏）。
        """
        data = await asyncio.to_thread(_build_overview, td, recommend_k)
        data["trade_date"] = date_str
        return data

    def _metrics(kind: str) -> None:
        OVERVIEW_CACHE_TOTAL.labels(result=kind).inc()  # P2-17：缓存命中率埋点

    data = await cached_or_build(
        key, _build, ttl=300, stale_window=OVERVIEW_STALE_WINDOW,
        refresh=refresh, rebuild_lock_ttl=180, on_metrics=_metrics,
        background_build=_build_unbudgeted)
    return ok(data)


async def warm_overview_cache(recommend_k: int = 50) -> bool:
    """启动预热：提前构建 market/overview 并写入缓存。

    背景：无 Redis 时内存 LRU 兜底 TTL 300s，过期后的首个用户请求要扛
    ~48s 全量重建（指数/涨跌分布/资金等外部数据源聚合）。启动后立即在
    后台预热一次，并把"过期后由谁重建"变为后台刷新，用户请求始终命中。

    续期判据（2026-09-19 修正）：**键不存在** 或 **剩余 TTL < 续期阈值** 时重建，
    否则跳过。此前只看「键是否存在」⇒ 只能在缓存**已过期后**重建；一旦某轮重建失败
    （本函数只 warning、不抛），缓存就进入真空期，用户请求打回 6s 冷路径——与该函数
    「把『过期后由谁重建』变为后台刷新」的意图以及 main.py 里「TTL 300s，提前 60s
    续期」的注释都不符。阈值推导见 ``OVERVIEW_WARM_RENEW_THRESHOLD_SECONDS``。

    影子键存活不触发本函数的提前续期判据问题——那条路径由 stale-while-revalidate
    的后台重建接管；构建失败只记日志。
    """
    try:
        td = today_trade_date_or_last()
        key = k_market_overview(td.strftime("%Y%m%d"), recommend_k)
        # -2 = 不存在（含 Redis/LRU 双降级）-> 必须重建；
        # -1 = 存在但无过期（本键由 write_cache 带 TTL 写入，理论不出现）-> 保险起见视为无需续期；
        # >=0 = 剩余秒数，仅当剩余 < 阈值时提前续期。
        remaining = await RedisClient.ttl(key)
        if remaining == -1 or remaining >= OVERVIEW_WARM_RENEW_THRESHOLD_SECONDS:
            logger.debug(
                f"[overview] warm skip {key}: 剩余 TTL={remaining}s "
                f">= 阈值 {OVERVIEW_WARM_RENEW_THRESHOLD_SECONDS}s")
            return False
        t0 = asyncio.get_event_loop().time()
        data = await asyncio.to_thread(_build_overview, td, recommend_k)
        data["trade_date"] = td.strftime("%Y%m%d")
        data["from_cache"] = True
        # 主键 + 影子键双写：重启后即便主键过期，首个请求也可 stale 回旧值
        await swr.write_cache(key, data, 300, OVERVIEW_STALE_WINDOW)
        # L2-2：切片复用同一次构建，预填 rt/daily 拆分键（前端两请求首查即命中）
        rt = {k: data[k] for k in ("indices", "money_flow", "anomalies")
              if k in data}
        rt["trade_date"] = data["trade_date"]
        rt["as_of"] = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        await swr.write_cache(k_market_overview_rt(td.strftime("%Y%m%d")),
                              rt, RT_TTL, RT_STALE_WINDOW)
        daily = {k: v for k, v in data.items()
                 if k not in ("indices", "money_flow", "anomalies", "from_cache")}
        await swr.write_cache(k_market_overview_daily(td.strftime("%Y%m%d"), recommend_k),
                              daily, _daily_ttl(), OVERVIEW_STALE_WINDOW)
        logger.info(f"[overview] warm cache done in "
                    f"{asyncio.get_event_loop().time() - t0:.1f}s")
        return True
    except Exception as e:  # noqa: BLE001 预热失败不影响启动
        logger.warning(f"[overview] warm cache fail: {e!r}")
        return False


def _build_overview_hist(td: date, recommend_k: int, date: str) -> dict:
    """历史快照聚合：实时块照常，recommend 切换到指定日期。"""
    data = _build_overview(td, recommend_k)
    try:
        target = date[:4] + "-" + date[4:6] + "-" + date[6:]
        data["recommend"] = _build_recommend(recommend_k,
                                             __import__("datetime").date.fromisoformat(target))
    except Exception as e:  # noqa: BLE001 历史切换失败保留最新推荐
        logger.debug(f"[overview] hist recommend fallback: {e!r}")
    return data
