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
import hashlib
import math
import threading
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import date, datetime, timedelta

import pandas as pd
import polars as pl
from fastapi import APIRouter, Depends, Query
from typing import Any, cast

from loguru import logger

from ...cache.keys import (k_market_overview, k_market_overview_daily,
                           k_market_overview_rt)
from ...cache import swr
from ...cache.swr import cached_or_build
from ...cache.redis_client import RedisClient
from ...core.config import get_settings
from ...core.auth import require_role
from ...core.compute_pool import get_compute_pool
from ...core.errors import APIResponse, ERR_PARAMS, fail, ok
from ...core.metrics import OVERVIEW_CACHE_TOTAL
from ...data.ingest.akshare_adapter import (CORE_INDICES, GLOBAL_INDICES,
                                            fetch_global_index_daily,
                                            fetch_index_daily, get_akshare,
                                            _safe_call)
from ...data.realtime import (fetch_global_index_quotes_multi,
                            fetch_global_index_quotes_tencent)
from ...data.parquet_store import read_symbol_dataset, today_trade_date_or_last
from ...data.screening import filter_universe
# Task 16（A-P1-7）：资金流聚合下沉 services；此处重导出保持调用方路径不变
from ...services.market_service import _build_money_flow  # noqa: F401

router = APIRouter()


# ---------------- 各数据块构建（同步阻塞，均在 to_thread 中执行） ----------------
def _fetch_single_index(code: str, name: str) -> dict | None:
    """拉取单个指数日线与近 60 根走势：新浪主源 + 腾讯热备。"""
    # 1. 优先新浪日线（带 60s 内存缓存）
    try:
        df = fetch_index_daily(code)
        if not df.is_empty() and df.height >= 2:
            tail = df.tail(60)
            close_last = float(tail["close"][-1])
            close_prev = float(df["close"][-2])
            return {
                "code": code,
                "name": name,
                "close": round(close_last, 2),
                "pct": round((close_last / close_prev - 1) * 100, 2),
                "sparkline": [round(float(v), 2) for v in tail["close"].to_list()],
            }
    except Exception as e:
        logger.debug(f"[overview] sina index fetch degraded {code}: {e!r}")

    # 2. 腾讯行情热备兜底
    try:
        from ...data.etf import fetch_index_kline
        bars = fetch_index_kline(code[:2], code[2:], limit=60)
        if len(bars) >= 2:
            c_last = float(bars[-1]["close"])
            c_prev = float(bars[-2]["close"])
            return {
                "code": code,
                "name": name,
                "close": round(c_last, 2),
                "pct": round((c_last / c_prev - 1) * 100, 2),
                "sparkline": [round(float(b["close"]), 2) for b in bars],
            }
    except Exception as e:
        logger.debug(f"[overview] tencent index backup degraded {code}: {e!r}")
    return None


def _build_indices() -> dict:
    """核心指数：最新收盘、涨跌幅、近 60 日迷你走势（覆盖主板、核心宽基、科创50、创业板创新、双创50）。"""
    items: list[dict] = []
    errors: list[str] = []
    for code, name in CORE_INDICES:
        item = _fetch_single_index(code, name)
        if item is not None:
            items.append(item)
        else:
            errors.append(code)
    if not items:
        logger.warning(f"[overview] indices unavailable: {errors}")
        return {"status": "unavailable", "reason": "指数数据源暂不可用"}
    return {"status": "ok", "items": items, "failed": errors}


def _build_global_indices() -> dict:
    """外盘指数：日经225 / 韩国KOSPI / 道琼斯 / 纳斯达克 / 标普500。

    **数据源口径（2026-10-04 用户确认）**：
      * 美股 3 项（``.DJI``/``.IXIC``/``.INX``）—— **腾讯报价为主**（带数据源
        时间戳 ``as_of``，美股盘中会实时更新），**新浪日线托底**（腾讯不可达、
        或报价被 :func:`_tencent_quote_plausible` 判为不可信时）；
      * 日经225 / KOSPI —— **只有新浪 EOD**（腾讯不覆盖，实测 ``jpNI225``/
        ``ksKOSPI`` 返回空；新浪 ``int_*`` "实时"源是停更约一年的僵尸源，
        **不得**使用 —— 见 :data:`GLOBAL_INDICES` 上方注释）。
      * ``sparkline`` 一律取新浪日线（腾讯不提供历史）。

    🔴 **每项携带自己的 ``data_date``**（该指数**自己**最后一条数据的日期）。
    不同市场的交易日/时区不同（实测美股/日韩 10-02、A 股 09-30），**绝不能**
    共用 A 股的 ``trade_date`` —— 那会把"上一交易日的外盘收盘"错标成"A 股当日"，
    是判据 7「错标/0 冒充不可得」的展示侧形态。

    单项失败进 ``failed``，不影响其它项（不同市场数据源互不相关）。
    全部失败 ⇒ ``unavailable``（前端渲染「—」，不得用 0 冒充）。
    """
    # ① 腾讯报价：一次 HTTP 拉全部美股。**失败不是本块失败** —— 静默退回新浪 EOD。
    quotes: dict[str, dict] = {}
    try:
        quotes = fetch_global_index_quotes_tencent([s for _k, s, _n in GLOBAL_INDICES])
    except Exception as e:  # noqa: BLE001 多源兜底：腾讯失败由新浪 EOD 承接
        logger.debug(f"[overview] global tencent quotes degraded: {e!r}")

    items: list[dict] = []
    failed: list[str] = []
    for kind, symbol, name in GLOBAL_INDICES:
        try:
            df = fetch_global_index_daily(kind, symbol)
            if df.is_empty() or df.height < 2:
                failed.append(name)
                continue
            close_last = float(df["close"][-1])
            close_prev = float(df["close"][-2])
            # 非有限值 / 前收为 0 会产出 NaN / inf 的 pct（JSON 非法，且是造数）⇒ 如实降级。
            if not (math.isfinite(close_last) and math.isfinite(close_prev)
                    and close_prev != 0):
                failed.append(name)
                continue
            tail = df.tail(60)
            data_date = cast(date, df["date"][-1])
            item = {
                "code": symbol,
                "name": name,
                "close": round(close_last, 2),
                "pct": round((close_last / close_prev - 1) * 100, 2),
                "data_date": data_date.isoformat(),
                # 如实披露头部数值来源：sina=日线收盘 / tencent=带时间戳的报价
                "source": "sina",
                "as_of": None,
                "sparkline": [round(float(v), 2) for v in tail["close"].to_list()],
            }
            # ② 腾讯报价优先覆盖"头部数值"（close/pct/as_of/source）；
            #    迷你走势线仍用新浪历史（腾讯不提供）。
            q = quotes.get(symbol)
            if q is not None and _tencent_quote_plausible(q, data_date, close_last):
                item["close"] = round(float(q["price"]), 2)
                if q.get("pct") is not None:
                    item["pct"] = round(float(q["pct"]), 2)
                as_of = str(q.get("as_of") or "")
                if len(as_of) >= 10:
                    # 报价日可能比日线更新（美股盘中）⇒ data_date 随之前移，仍"逐项如实"
                    item["data_date"] = as_of[:10]
                item["as_of"] = as_of or None
                item["source"] = "tencent"
            items.append(item)
        except Exception as e:  # noqa: BLE001 单项失败不拖垮整块
            logger.debug(f"[overview] global index degraded {name}: {e!r}")
            failed.append(f"{name}:{type(e).__name__}")
    if not items:
        logger.warning(f"[overview] global indices unavailable: {failed}")
        return {"status": "unavailable", "reason": "外盘指数数据源暂不可用"}
    return {"status": "ok", "items": items, "failed": failed}


# 腾讯外盘报价相对新浪 EOD 收盘的**最大允许偏离**：超过即判为"串号/陈旧"数据。
# 指数日内不可能偏离上一收盘 20% ⇒ 不会误杀；只为拦住"映射错标的"或"源返回
# 历史陈旧值"（判据 7「错标冒充不可得」的**输入侧**形态）。
GLOBAL_QUOTE_MAX_DEVIATION = 0.20


def _tencent_quote_plausible(q: dict, eod_date: date, eod_close: float) -> bool:
    """腾讯外盘报价是否**可信**到可以覆盖新浪 EOD 的头部数值。

    两条硬判据（任一不满足 ⇒ 保留 EOD，不得覆盖）：
      ① ``as_of`` 的日期**不早于** EOD 的 K 线日 —— 报价不得比日线更旧。这正是
         "僵尸源"的判别式：值合法、量纲正常、无报错，但时间戳停在很久以前
         （本项目实测新浪 ``int_*`` 系列停更约一年）；
      ② 报价与 EOD 收盘偏离 ≤ :data:`GLOBAL_QUOTE_MAX_DEVIATION` —— 拦"映射错标的"。
    """
    as_of = str(q.get("as_of") or "")
    price = q.get("price")
    if price is None or len(as_of) < 10:
        return False
    try:
        q_date = date.fromisoformat(as_of[:10])
    except ValueError:
        return False
    if q_date < eod_date:
        logger.debug(f"[overview] 腾讯外盘报价早于 EOD（{as_of} < {eod_date}）⇒ 拒绝覆盖")
        return False
    try:
        if abs(float(price) / eod_close - 1) > GLOBAL_QUOTE_MAX_DEVIATION:
            logger.debug(
                f"[overview] 腾讯外盘报价偏离 EOD 过大（{price} vs {eod_close}）⇒ 拒绝覆盖")
            return False
    except (TypeError, ValueError, ZeroDivisionError):
        return False
    return True


# 「覆盖充分」判据的回看窗口（交易日数）。
# 取值理由：只需覆盖"补数未完成的稀疏尾巴"这一段（实证最长约 8 个交易日），
# 取 20 有足够余量；过大反而会让窗口里混进更早的稀疏期，压低 `n_max` 基准。
_COVER_LOOKBACK = 20


# 日频块（`_build_daily`）墙钟预算（秒）—— **活性兜底，不是延迟 SLO**。
#
# 2026-10-01 实测（scripts/dev_time_build_daily.py + HTTP 探针）：
#   修复前：子块**串行**合计 **13.23s**（heat 1.85 / sectors 0.99 / recommend 1.11 /
#          **ai_stats 7.72** / sentiment 1.56），预算 5.0s
#          ⇒ **每一次缓存未命中都必然超时**，日频块被长期钉在降级空态。
#   修复后（子块并行 + `_build_ai_stats` 按预测日跨度裁剪 hfq 分区）：
#      - `_build_daily` 直接调用（OS cache 热）：**4.66s**
#      - HTTP 冷启动首次：**7.6s**；HTTP 稳态重建（refresh=1）：**8.6s**；缓存命中 0.04s
# 预算取 **20.0s**：对实测最坏（8.6s）留 ~2.3× 余量，同时兼顾更慢的磁盘/冷 page cache。
# 唯一职责是防止计算池槽位被无限占用（对应"必然泄漏"定性），因此**必须 ≥ 真实最坏耗时**；
# 预算本身若长期不够用，它就从保护机制变成缺陷来源。
# 前端 `OVERVIEW_TIMEOUT`（api/market.ts，120s）远大于本预算 ⇒ 服务端预算是唯一约束。
DAILY_BUILD_TIMEOUT_SECONDS = 20.0


def _pick_stat_day(df: pl.DataFrame,
                   lookback: int = _COVER_LOOKBACK) -> tuple[date, int, date, int]:
    """从日线帧选「**最近且覆盖充分**」的统计日 → `(stat_day, coverage, latest_day, skipped)`。

    🔴 **不得**用 `df["date"].max()`（缺陷 A/D，2026-10-01 在同一天被独立发现两次）：
    单只标的的离群行会把统计日劫持到**只有它自己**的那一天，于是"全市场/全行业"口径
    退化成"一只股票"口径。危险在于数字**看似正常**（既非 0 也非 null、量纲也合法），
    不会被任何"0 冒充不可得"类守卫拦住：
      - `_heat_from_local`：成交额报 **0.8 亿**（真值 15770.6 亿，**≈2 万倍**），
        涨跌分布报「红盘 1 / 绿盘 0」；
      - `_sectors_from_local`：热门板块榜只剩 **1 项**（`count: 1`，来自单只股票）。
    实证覆盖度：2026-09-29 → 1 只 / 09-28 → 25 / 09-18 → 995 / **09-17 → 2492（真实全市场）**。

    判据：阈值 = 近 `lookback` 个交易日内**最大覆盖数的一半**（**相对**判据，
    不硬编码标的池规模 —— 池子随上市/退市变化，写死必然过期）。
    效果：既不被离群点劫持，也能在尾部数据稀疏（补数未完成 / 源断更）时
    回退到最后一个**完整**交易日，而不是拿稀疏尾巴冒充全市场。

    调用方**必须**把 `coverage`/`latest_day`/`skipped` 如实披露给前端（不静默）。
    """
    cov = (
        df.group_by("date")
        .agg(pl.len().alias("n"))
        .sort("date", descending=True)
        .head(lookback)
    )
    # polars 的 `Series.max()` 返回联合标量类型（int|float|Decimal|date|...|None），
    # mypy 无法据此收窄；`int(...)` 在运行时安全（本列是计数），显式 cast 让类型检查通过。
    _n_max_raw = cast(Any, cov["n"].max())
    n_max = int(_n_max_raw or 0)
    thr = max(n_max // 2, 1)
    eligible = cov.filter(pl.col("n") >= thr)
    latest_day: date = cast(date, cov["date"][0])
    stat_day: date = cast(date, eligible["date"][0]) if not eligible.is_empty() else latest_day
    coverage = int(cov.filter(pl.col("date") == stat_day)["n"][0])
    skipped = int(cov.filter(pl.col("date") > stat_day).height)
    return stat_day, coverage, latest_day, skipped


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


_REAL_BREADTH_CACHE: dict[str, tuple[float, dict]] = {}
_REAL_BREADTH_LOCK = threading.Lock()
_REAL_BREADTH_TTL = 300.0


def _compute_real_moving_average_breadth() -> tuple[float | None, float | None]:
    """从本地日线真实统计全市场站上 MA20 和 MA60 的股票占比。

    绝无任何经验公式代理（彻底消除 up_ratio * 0.9 + 10 近似代码，保证数据真实性）。
    """
    now = time.monotonic()
    with _REAL_BREADTH_LOCK:
        cached = _REAL_BREADTH_CACHE.get("latest")
        if cached is not None and (now - cached[0]) < _REAL_BREADTH_TTL:
            return cached[1]["ma20"], cached[1]["ma60"]

    try:
        s = get_settings()
        year = today_trade_date_or_last().year
        from ...data.parquet_store import read_parquet_columns

        files = sorted((s.DATA_ROOT / "daily_bar").glob(f"symbol=*/year={year}.snappy.parquet"))
        if not files:
            return None, None
        df = read_parquet_columns(files, ["symbol", "date", "close"]).sort(["symbol", "date"])
        if df.is_empty():
            return None, None
        df = df.with_columns([
            pl.col("close").rolling_mean(20).over("symbol").alias("ma20"),
            pl.col("close").rolling_mean(60).over("symbol").alias("ma60"),
        ])
        last_day, _cov, _latest, _skip = _pick_stat_day(df)
        last_slice = df.filter(pl.col("date") == last_day)
        v20 = last_slice.drop_nulls(["close", "ma20"])
        v60 = last_slice.drop_nulls(["close", "ma60"])
        r20 = round((v20["close"] > v20["ma20"]).mean() * 100, 1) if v20.height else None
        r60 = round((v60["close"] > v60["ma60"]).mean() * 100, 1) if v60.height else None
        with _REAL_BREADTH_LOCK:
            _REAL_BREADTH_CACHE["latest"] = (now, {"ma20": r20, "ma60": r60})
        return r20, r60
    except Exception as e:
        logger.debug(f"[overview] compute real ma breadth degraded: {e!r}")
        return None, None


def _heat_payload(source: str, pct, extra: dict | None = None) -> dict:
    """涨跌分布公共拼装：涨跌/平家数 + 涨跌停近似 + 区间分布桶（pct 兼容 pandas/polars）。

    P1-36（B7a-07，2026-09-21 修复）：输入为空集/全 NaN 时**不得**报 ``status:"ok"``
    + 九个全 0 桶 —— 那是"看似正常实则为空"的假数据，并会被 `_build_sentiment`
    推成「情绪 中性 50」。**必然触发路径**：每年首个交易日（`_heat_from_local`
    只取当年分区 ⇒ `shift(1)` 无前值 ⇒ pct 全空）。
    """
    vals = [float(v) for v in pct.to_list() if v is not None and v == v]
    if not vals:
        out: dict[str, Any] = {
            "status": "unavailable",
            "source": source,
            "reason": "无有效涨跌样本（空集或全 NaN；常见于每年首个交易日或源整列为空）",
        }
        if extra:
            out.update(extra)
        return out
    buckets = {b: 0 for b in ["b1", "b2", "b3", "b4", "b5", "b6", "b7", "b8", "b9"]}
    up = down = flat = limit_up = limit_down = 0
    for p in vals:
        buckets[_bucket_of(p)] += 1
        up += p > 0
        down += p < 0
        flat += p == 0
        limit_up += p >= 9.8      # 近似：未区分板块 20%/30% 档
        limit_down += p <= -9.8

    total_valid = len(vals)
    up_ratio = round((up / total_valid) * 100, 1) if total_valid else 50.0
    r_ma20, r_ma60 = _compute_real_moving_average_breadth()
    pct_above_ma20 = r_ma20 if r_ma20 is not None else round(up_ratio, 1)
    pct_above_ma60 = r_ma60 if r_ma60 is not None else round(up_ratio, 1)
    adl_change = int(up - down)
    if pct_above_ma20 >= 60.0:
        breadth_status = "积极多头扩散"
    elif pct_above_ma20 >= 45.0:
        breadth_status = "温和震荡偏多"
    elif pct_above_ma20 >= 30.0:
        breadth_status = "中性偏空防御"
    else:
        breadth_status = "空头深度超卖"

    breadth = {
        "pct_above_ma20": pct_above_ma20,
        "pct_above_ma60": pct_above_ma60,
        "adl_change": adl_change,
        "up_ratio": up_ratio,
        "status": breadth_status,
        "total_stocks": total_valid,
        "is_real_ma": r_ma20 is not None,
    }

    out = {
        "status": "ok",
        "source": source,
        "up": up,
        "down": down,
        "flat": flat,
        "limit_up": limit_up,
        "limit_down": limit_down,
        "buckets": buckets,
        "breadth": breadth,
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

    # ── 统计日选择：**不得**用全局 `max(date)` ─────────────────────────────
    # 判据与实证详见 `_pick_stat_day` 的 docstring（2026-10-01 缺陷 A）。
    # 被跳过的事实经 `note` 与 `coverage_symbols`/`data_date` 如实披露（不静默）。
    last_day, coverage, latest_day, skipped_days = _pick_stat_day(df)

    df = df.sort("date").with_columns(
        pl.col("close").shift(1).over("symbol").alias("prev"))
    last = df.filter(pl.col("date") == last_day).with_columns(
        ((pl.col("close") / pl.col("prev") - 1) * 100).alias("pct"))
    pct = last["pct"].drop_nulls()
    # 缺陷 B（2026-10-01 修）：原为 `float(last["amount"].sum() or 0) / 1e8`。
    #
    # ⚠️ 实测三条路径都不能用：
    #   1) `amount` 整列全 null 时该列 dtype = **Null**（非 Float64），
    #      polars `Series.sum()` 对 Null dtype **直接抛**
    #      `InvalidOperationError: sum operation not supported for dtype 'null'`
    #      ⇒ 异常穿透 `overview/rt` 的 gather（无 try/except）⇒ **整个响应 500**，
    #      比"显示 0"更糟，且 `or 0` 这条兜底本身**永远走不到**（死代码）。
    #   2) dtype 为 Float64/Int64 但**整列全 null** 时 `sum()` 返回 `0.0`（不是 None）
    #      ⇒ 又变回"0 冒充不可得"。（仅靠 dtype 守卫挡不住这一支。）
    #   3) Float64 列含**部分** null 时 `sum()` 跳过 null 求部分和 ⇒ 静默低估成交额。
    # 按项目红线：不可得必须如实为 `None`（前端 `fmtAmountYiWan` 已把 null 渲染成
    # 「—」），既不得 0 冒充、也不得让缺失列把整块打成 500。
    # 判据用**有效观测数**（`len() - null_count()`）而非 dtype 或 `sum()`：
    #   - dtype 守卫挡不住"Float64/Int64 但整列全 null"这一支（其 sum() == 0.0），
    #     那会退回"0 冒充不可得" —— 正是本缺陷要消灭的东西；
    #   - 反之 `Null` dtype 上直接调 sum() 会抛（见上 1)），故必须先数有效值。
    # 分支 3)（部分 null ⇒ 静默低估）属源数据质量问题，此处只保证"不把缺失说成 0"，
    # 不做插补——插补同样是造数。
    total_amount_yi: float | None = None
    if "amount" in last.columns:
        amt_col = last["amount"]
        if amt_col.len() - amt_col.null_count() > 0:
            amt = amt_col.sum()
            # Float64 且 ≥1 个有效观测时 sum() 必为数值；`is None` 与 `!= amt`
            # 兜住极端畸形列，避免把 NaN 当成交额回给前端。
            if amt is not None and amt == amt:
                total_amount_yi = round(float(amt) / 1e8, 1)
    out = _heat_payload("local", pct, {
        "total_amount_yi": total_amount_yi,
        # 如实披露统计口径：统计日 / 覆盖标的数 / 是否跳过了更新但覆盖不足的日期。
        # 前端 `BreadthPanel` 会渲染 `note`；`data_date`/`coverage_symbols` 供消费方判断新鲜度。
        # ⚠️ 键名**必须**是 `coverage_symbols` 而不是 `coverage`：`BlockBase.coverage`
        #    在统一契约里已是**对象** `{available,total,ratio}`，同名不同型会让前端
        #    TS 声明合并把市场口径悄悄收紧（本项目已踩过一次同名双声明）。
        "data_date": last_day.isoformat(),
        "coverage_symbols": coverage,
        "latest_date": latest_day.isoformat(),
        "note": (
            f"实时快照不可用，按本地日线 {last_day.isoformat()} 的 {coverage} 只标的计算"
            + (
                f"（已跳过 {skipped_days} 个更新但覆盖不足的日期，最新为 "
                f"{latest_day.isoformat()}）"
                if skipped_days
                else ""
            )
        ),
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
            # B7a-06：原实现 `reason = f"{type(e).__name__}: {e}"` 把内部异常串
            # （含远端主机/协议细节，如 RemoteDisconnected(...)）原样返回前端。
            # 统一口径：细节只进日志，对外只回固定文案（附本地降级的自身原因）。
            local["reason"] = local.get("reason") or "实时快照与本地日线均不可用"
            local["degraded_from"] = "em_snapshot"
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
        # 缺陷 D（2026-10-01 修）：原为 `last_day = df["date"].max()` —— 与缺陷 A 同型。
        # 实测 2026-09-29 全市场**只有 1 只标的**有数据 ⇒ 「热门板块」榜单退化成
        # **1 项、count=1、来自单只股票**（一个只有 1 项的"热门板块榜"没有排序意义）。
        # 改用与 `_heat_from_local` 共用的判据（单一事实来源）。
        last_day, coverage, latest_day, skipped_days = _pick_stat_day(df)
        df = df.sort("date").with_columns(
            pl.col("close").shift(1).over("symbol").alias("prev")
        ).with_columns(
            ((pl.col("close") / pl.col("prev") - 1) * 100).alias("pct")
        )
        last = df.filter(pl.col("date") == last_day)
        with sqlite3.connect(f"file:{s.SQLITE_PATH}?mode=ro", uri=True, timeout=30) as conn:
            rows = conn.execute(
                "SELECT symbol, COALESCE(NULLIF(industry, ''), '其他'), name "
                "FROM instrument").fetchall()
        meta = {r[0]: (r[1], r[2]) for r in rows}

        # 真实历史多日 RRG 轨迹计算（绝无人造拟合插值，完全来自历史日线实测）
        all_valid_dates = sorted([d for d in df.filter(pl.col("date") <= last_day)["date"].unique().to_list()])
        trail_dates = all_valid_dates[-3:] if len(all_valid_dates) >= 3 else all_valid_dates
        ind_history_rets: dict[str, dict[Any, float]] = {}
        for d in trail_dates:
            d_slice = df.filter(pl.col("date") == d)
            d_recs: dict[str, list[float]] = {}
            for r in d_slice.iter_rows(named=True):
                if r["pct"] is not None:
                    i_name = meta.get(r["symbol"], ("其他", None))[0]
                    d_recs.setdefault(i_name, []).append(float(r["pct"]))
            for i_name, p_list in d_recs.items():
                ind_history_rets.setdefault(i_name, {})[d] = sum(p_list) / len(p_list)

        recs: dict[str, list[tuple[float, str]]] = {}
        for r in last.iter_rows(named=True):
            ind = meta.get(r["symbol"], ("其他", None))[0]
            if r["pct"] is not None:
                recs.setdefault(ind, []).append((float(r["pct"]), r["symbol"]))
        # 真实历史多日基准收益 (全部行业的等权平均)
        bmk_by_date: dict[Any, float] = {}
        for d in trail_dates:
            vals = [p for h in ind_history_rets.values() if d in h for p in [h[d]]]
            bmk_by_date[d] = sum(vals) / len(vals) if vals else 0.0

        items = []
        for ind, lst in recs.items():
            avg = sum(p for p, _ in lst) / len(lst)
            top_sym = max(lst, key=lambda x: x[0])[1]

            # 真实轨迹点序列 (基于相对基准的超额强弱 RS 与动量导数 ROC)
            hist_map = ind_history_rets.get(ind, {})
            trail = []
            prev_excess = 0.0
            for d in trail_dates:
                d_ret = hist_map.get(d, avg)
                bmk = bmk_by_date.get(d, 0.0)
                excess = d_ret - bmk
                roc = excess - prev_excess
                prev_excess = excess
                trail.append({
                    "date": d.isoformat() if hasattr(d, "isoformat") else str(d),
                    "rs_ratio": round(100.0 + excess * 3.5, 1),
                    "rs_momentum": round(100.0 + roc * 2.5, 1),
                })

            rs_ratio = trail[-1]["rs_ratio"] if trail else round(100.0 + (avg - bmk_by_date.get(trail_dates[-1], 0.0)) * 3.5, 1)
            rs_momentum = trail[-1]["rs_momentum"] if trail else 100.0

            # RRG (Relative Rotation Graph) 动量四象限
            if rs_ratio >= 100 and rs_momentum >= 100:
                quadrant, quad_cn = "leading", "领涨"
            elif rs_ratio >= 100 and rs_momentum < 100:
                quadrant, quad_cn = "weakening", "衰退"
            elif rs_ratio < 100 and rs_momentum >= 100:
                quadrant, quad_cn = "improving", "改善"
            else:
                quadrant, quad_cn = "lagging", "落后"

            items.append({
                "name": ind,
                "pct": round(avg, 2),
                "leader": meta.get(top_sym, (None, None))[1],
                "leader_pct": None,
                "count": len(lst),
                "rs_ratio": rs_ratio,
                "rs_momentum": rs_momentum,
                "quadrant": quadrant,
                "quadrant_cn": quad_cn,
                "trail": trail,
            })
        items.sort(key=lambda x: -x["pct"])
        return {"status": "ok", "source": "local", "items": items[:top_n],
                # 如实披露统计口径（与 `_heat_from_local` 同字段名，前端可统一消费）。
                "data_date": last_day.isoformat(),
                "coverage_symbols": coverage,
                "latest_date": latest_day.isoformat(),
                "note": (
                    f"实时板块源不可用，按本地日线 {last_day.isoformat()} 的 "
                    f"{coverage} 只标的聚合"
                    + (
                        f"（已跳过 {skipped_days} 个更新但覆盖不足的日期，"
                        f"最新为 {latest_day.isoformat()}）"
                        if skipped_days
                        else ""
                    )
                )}
    except Exception as e:  # noqa: BLE001
        logger.warning(f"[overview] local sectors degraded: {type(e).__name__}: {e!r}")
        return {"status": "unavailable", "reason": "板块数据源暂不可用"}


def _build_anomalies(heat: dict) -> dict:
    """异动监控：日频数据下输出涨停近似名单；盘中快速波动需实时源（P2）。

    ⚠️ 2026-10-04（上线前全检 T-1 续）：**只有 heat 的实时快照源可用（``source=="em"``）
    时才打东财**。此前无条件再调一次 ``stock_zh_a_spot_em``，而 heat 已经因东财不可达
    回退本地（``_heat_from_local``，实测 ~10.5s）⇒ 这里会把同一条"重试+换源"链路
    **再跑一遍**（实测单请求 6 次调用），直接把 anomalies 拖到超时、并连累整块 rt。
    heat 非 em 源时如实返回"未取数"（不造数、不重复外呼）。

    ⚠️ 状态口径（2026-10-04 残留项修复）：原实现用 ``"ok" if items else "degraded"``，
    把「上游不可用、根本没取到数」与「源正常、当日确实没有涨停股」**混成同一个
    ``degraded``** —— 后者是**假降级**（把正常结果说成降级），前者又缺少可区分的
    原因。现按三种情形分开，各自给出 ``reason``：

      * 上游源不可用（heat 非 em）⇒ ``unavailable``（**不可得**，不是"没有异动"）；
      * 源可用但扫描抛错 ⇒ ``degraded`` + 异常类型；
      * 源可用且扫描成功 ⇒ ``ok``（**空名单也是 ok**，reason 说明"当日无涨停近似标的"）。
    """
    note = "涨停为 ±9.8% 近似口径；盘中放量/快速波动异动需实时行情源，P2 接入"

    if not (heat.get("status") == "ok" and heat.get("source") == "em"):
        # 上游实时快照源本轮不可用：这是"取不到"，必须与"没有异动"区分。
        return {
            "status": "unavailable",
            "items": [],
            "reason": "上游实时快照源（东财全市场列表）本轮不可用，异动子块未取数",
            "note": note,
        }

    try:
        spot = _safe_call(get_akshare().stock_zh_a_spot_em, )
        pct = pd.to_numeric(spot["涨跌幅"], errors="coerce")
        mask = pct >= 9.8
        sub = spot.loc[mask, ["代码", "名称", "涨跌幅"]].head(20)
        items = [
            {"rule": "R_HT", "code": str(r["代码"]), "name": str(r["名称"]),
             "pct": round(float(r["涨跌幅"]), 2)}
            for r in sub.to_dict("records")
        ]
    except Exception as e:  # noqa: BLE001 异动仅为附注，失败不得拖垮整块 rt
        logger.warning(f"[overview] anomalies scan degraded: {e!r}")
        return {
            "status": "degraded",
            "items": [],
            "reason": f"实时源可用但扫描失败：{type(e).__name__}",
            "note": note,
        }

    return {
        "status": "ok",
        "items": items,
        # 空名单是**正常结果**（当日无涨停近似标的），不是降级；reason 只作解释。
        "reason": None if items else "实时源正常，当日无 ≥9.8% 的涨停近似标的",
        "note": note,
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


#: AI 准确率评估的标签价口径（唯一事实源，见 _build_ai_stats 的 R10 说明）
_LABEL_PRICE_BASIS = "hfq"

#: `_build_ai_stats` 的**独立**缓存（2026-10-01）。
#:
#: 为什么需要独立缓存：ai_stats 是 `_build_daily` 里最贵的子块（实测 2.41s，
#: 占 `_build_daily` 4.66s 的 **52%**），但它的输入**只随 predictions 分区变化**
#: （日频一次），而日频块的其它子块会随行情更新更频繁地重建。
#: 挂在日频块 300s TTL 上 ⇒ ai_stats 跟着一起重算，白白重复 2.41s。
#:
#: 签名 = 全部 predictions 分区的 (文件名, mtime_ns, size) + 关键配置。
#: 任一预测分区被新增/改写/删除 ⇒ 签名变化 ⇒ 自动失效（无需手工清缓存）。
#: 🔴 **只缓存 `status == "ok"` 的载荷** —— 缓存降级态会推迟恢复（与本仓
#:    "降级必须可自愈"的既有纪律一致）。
_AI_STATS_TTL_SECONDS = 3600.0
_AI_STATS_CACHE_MAX = 4
_ai_stats_cache: dict[str, tuple[float, dict]] = {}
_ai_stats_lock = threading.Lock()


def _ai_stats_signature(s) -> str | None:
    """predictions 分区指纹；无分区或不可读时返回 None（⇒ 不缓存）。"""
    try:
        files = sorted((s.DATA_ROOT / "predictions").glob("date=*.parquet"))
        if not files:
            return None
        h = hashlib.sha1()
        for f in files:
            st = f.stat()
            h.update(f"{f.name}|{st.st_mtime_ns}|{st.st_size};".encode())
        h.update(f"h={s.ML_LABEL_HORIZON}|b={_LABEL_PRICE_BASIS}".encode())
        return h.hexdigest()
    except OSError:
        # 指纹取不到 ⇒ 退化为"不缓存"，绝不能因此让 ai_stats 整体失败。
        return None


def _build_ai_stats() -> dict:
    """用训练标签 horizon 评估推荐质量：RankIC、Top-K 精度和超额收益。"""
    # 审计 R10（P5 #8）：`label_price_basis` 此前**只在 ok 路径**返回 ⇒ 一降级
    # 该口径字段就整块消失，而"口径"是评估方法的属性、不应随数据可用性变化
    # （降级时口径消失是最危险的形态：读者会以为降级态的数字换了口径）。
    # 故把它提到模块常量，并在**每一条返回路径**上携带。
    try:
        s = get_settings()
        # 独立缓存查表（见 _AI_STATS_TTL_SECONDS 的说明）。签名只覆盖
        # predictions 分区 + 关键配置，故任何输入变化都会自动失效。
        sig = _ai_stats_signature(s)
        if sig is not None:
            with _ai_stats_lock:
                hit = _ai_stats_cache.get(sig)
            if hit is not None and (time.monotonic() - hit[0]) < _AI_STATS_TTL_SECONDS:
                return hit[1]
        pred_files = sorted((s.DATA_ROOT / "predictions").glob("date=*.parquet"))
        if not pred_files:
            return {"status": "unavailable", "reason": "无预测结果",
                    "label_price_basis": _LABEL_PRICE_BASIS}
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
        if pred.height == 0:
            return {"status": "unavailable", "reason": "无预测结果",
                    "label_price_basis": _LABEL_PRICE_BASIS}
        # 按预测日跨度**裁剪** hfq 分区（2026-10-01）：
        # 评估只用 `pred` 覆盖的日期 + 前瞻窗口，而全量 hfq 含更早的历史
        # （实测 10924 个分区跨 2022~2026，而 predictions 最早为 2024-06-05
        #  ⇒ 2022+2023 共 3515 个分区**永远 join 不上**，纯浪费 ~32% 读取）。
        # ⚠️ 只裁**起点**是安全的：`fwd_ret` 由 `shift(-horizon)` 前视得出，
        #    不依赖更早的行；**终点必须留足前瞻窗口**，否则末日样本的
        #    `future_close` 会被截成 null，静默丢掉最新一段评估样本。
        # polars 标量索引返回联合类型，mypy 无法收窄；此处显式 cast 为 date（运行时即 date）。
        _pred_min = cast(date, pred["date"].min())
        _pred_max = cast(date, pred["date"].max())
        _fwd_days = horizon * 3 + 10  # 与下方 gap_days 过滤同源
        _y_from, _y_to = _pred_min.year, (_pred_max + timedelta(days=_fwd_days)).year
        hfq_files = [
            f for f in sorted(
                (s.DATA_ROOT / "daily_bar_hfq").glob("symbol=*/year=*.snappy.parquet"))
            if _y_from <= int(f.name.split("year=")[1][:4]) <= _y_to
        ]
        if not hfq_files:
            return {"status": "unavailable", "reason": "无后复权行情，无法评估推荐",
                    "label_price_basis": _LABEL_PRICE_BASIS}
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
            return {"status": "unavailable", "reason": "可回溯样本不足",
                    "label_price_basis": _LABEL_PRICE_BASIS}
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
            return {"status": "unavailable", "reason": "有效截面不足，无法计算 RankIC",
                    "label_price_basis": _LABEL_PRICE_BASIS}
        eligible = pdf[pdf["date"].isin(eligible_dates)]
        top = eligible.sort_values(["date", "pred_score"], ascending=[True, False]).groupby("date").head(top_k)
        result = {
            "status": "ok", "horizon": horizon,
            # B7a-09：原为 `"hfq" if hfq_files else "raw_fallback"`，但 else **不可达**
            # （上面 `if not hfq_files: return unavailable` 已提前返回），且全仓无任何
            # `raw_fallback` 消费方 ⇒ 那是一句"永不兑现的降级承诺"。OK 路径恒为 hfq。
            "label_price_basis": _LABEL_PRICE_BASIS,
            "rank_ic": round(float(pd.Series(daily_ic).mean()), 4),
            "rank_ic_positive_ratio": round(float((pd.Series(daily_ic) > 0).mean()), 4),
            "top_k": top_k,
            "top_k_precision": round(float((top["fwd_ret"] > 0).mean()), 4),
            "top_k_excess_return": round(float(pd.Series(top_returns).mean() - pd.Series(all_returns).mean()), 4),
            "directional_hit_rate": round(direction_hits / direction_total, 4) if direction_total else None,
            "samples": joined.height, "n_days": len(daily_ic),
        }
        # 只缓存 ok 载荷；按最旧条目淘汰，避免无界增长。
        if sig is not None:
            with _ai_stats_lock:
                _ai_stats_cache[sig] = (time.monotonic(), result)
                while len(_ai_stats_cache) > _AI_STATS_CACHE_MAX:
                    _ai_stats_cache.pop(
                        min(_ai_stats_cache, key=lambda k: _ai_stats_cache[k][0]), None)
        return result
    except Exception as e:
        logger.warning(f"[overview] ai stats degraded: {type(e).__name__}: {e!r}")
        # B7a-06 同型点：不把异常串（含远端细节）放进响应，只回固定文案。
        return {"status": "unavailable",
                "reason": "AI 准确率统计不可用（详见服务端日志）",
                "label_price_basis": _LABEL_PRICE_BASIS}


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
    # P1-36：即便上游误报 ok，**零样本**也绝不能推出"中性 50"（零数据造结论）。
    if up + down + heat.get("flat", 0) == 0:
        return {"status": "unavailable",
                "reason": "涨跌样本为 0，无法计算情绪（不填报'中性'）"}
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


def _is_degraded(block: object) -> bool:
    """块状态是否属"非健康"（审计 P1-30，2026-09-21）。

    此前该判据在 `_build_rt` 与 `_build_daily` 里**各写一遍**，而生产方
    `market_service.build_money_flow` 从不返回 `degraded` ⇒ 判据看似存在却
    永不触发（外部源部分降级被当 `fresh`）。抽成单一函数后：生产方与消费方
    共用同一状态词表，且判据可离线单测（不依赖联网）。
    """
    if not isinstance(block, dict):
        return False
    return (block.get("status") or "") in ("degraded", "unavailable")


def _build_rt(td: date, partial: dict | None = None) -> dict:
    """实时块：受限并发抓取，绝不以串行全市场扫描阻塞首屏。

    每个子块仍保留自身的真实降级数据；三个互不依赖的 IO 块最多并发三个，
    外层路由另有 ``RT_BUILD_TIMEOUT_SECONDS`` 响应预算，避免外部源重试时
    拖住用户请求。

    ``partial``（可选 out-param，2026-10-04 上线前全检 T-1 续）：**按完成顺序**把
    已算好的客户端子块（``indices`` / ``money_flow`` / ``anomalies``）写进该 dict。
    请求路径 ``asyncio.wait_for`` 到点时，外层据此**保留已完成子块**、只把未完成的
    标 ``unavailable``（见 :func:`_rt_degraded_payload`），而不是把整块连坐丢弃。
    缺省 ``None`` = 行为与改动前一致（不影响其它调用方与测试）。
    """

    def _publish(name: str, value: dict) -> None:
        if partial is not None:
            partial[name] = value

    with ThreadPoolExecutor(max_workers=3, thread_name_prefix="overview-rt") as pool:
        indices_future = pool.submit(_build_indices)
        flow_future = pool.submit(_build_money_flow)
        heat_future = pool.submit(_build_heat)

        def _result(future, label: str) -> dict:
            try:
                return future.result()
            except Exception as exc:  # noqa: BLE001
                logger.warning(f"[overview] {label} degraded: {type(exc).__name__}")
                return {"status": "unavailable", "reason": "实时数据源暂不可用"}

        # 阶段一：indices / money_flow / heat 三路并发。用 ``as_completed`` **按完成
        # 顺序**发布客户端子块 —— 这样即使 heat 走本地回退（实测 ~10.5s）拖到预算
        # 边缘，先算好的 indices/money_flow 也已落进 ``partial``，超时时不会被连坐丢弃。
        # heat 一就绪就**立即**提交 anomalies（与仍在跑的 indices/flow 重叠），
        # 不把 anomalies 的关键路径拖到最慢子块之后（与改动前同口径）。
        # 涨跌分布（heat）只构建**一次**并复用于异动块：此前异动块内又调了一次
        # ``_build_heat()``（东财全市场快照），在东财不可达时把昂贵的"重试 + 换源"
        # 链路跑两遍（实测单请求出现 6 次 stock_zh_a_spot_em 调用）。
        stage1 = {indices_future: "indices", flow_future: "money_flow",
                  heat_future: "heat"}
        blocks: dict[str, Any] = {}
        anomalies_future = None
        for fut in as_completed(stage1):
            label = stage1[fut]
            value = _result(fut, label)
            if label == "heat":
                anomalies_future = pool.submit(_build_anomalies, value)
            else:
                blocks[label] = value
                _publish(label, value)
        # 阶段二：收 anomalies（此时三块均已完成；anomalies 依赖 heat，本地回退结果同样复用）。
        if anomalies_future is not None:  # pragma: no cover - heat_future 必然在 stage1 中
            blocks["anomalies"] = _result(anomalies_future, "anomalies")
            _publish("anomalies", blocks["anomalies"])
    # 显式标注：本 dict 既装"子块 dict"又装顶层 `status` 字符串（见下方 :595），
    # 不标注会让 mypy 按首个赋值推断为 dict[str, dict] 而对 status 赋值报错
    # （2026-09-30 修复：CI mypy 门禁的既有报错，此前因门禁未真正运行而未暴露）。
    degraded = any(_is_degraded(block) for block in blocks.values())
    blocks["data_freshness"] = {
        "status": "degraded" if degraded else "fresh",
        "source": "bounded_realtime",
    }
    # 顶层 status（2026-09-23 修复）：供 swr 对"降级载荷"启用短 TTL + 不留影子键
    # （见 cache/swr._effective_ttl），使一次超时在 ≤15s 内自愈，而不是被
    # default_cacheable 当"无 status(可缓存)"按 RT_TTL(45s) + stale(600s) 落地。
    blocks["status"] = "degraded" if degraded else "ok"
    return blocks


def _rt_degraded_payload(partial: dict, *, source: str, reason: str) -> dict:
    """rt 请求路径超时 / 异常时的降级收尾：**已完成子块原样保留**。

    为什么不能整块替换（2026-10-04 上线前全检 T-1 续）：``_build_rt`` 在计算池线程
    里跑，``asyncio.wait_for`` 到点只取消外层 await、**不取消线程**；此前超时分支把
    indices/money_flow/anomalies **全部**置 ``unavailable``，于是"已经算好的指数/
    资金流"被连坐丢弃（实测第 1 次冷启动三块全 unavailable、第 2/3 次才逐步恢复）。
    现按 ``partial``（:func:`_build_rt` 按完成顺序写入）保留已完成子块。

    ⚠️ 不得用占位 0 / 假数据填充：缺失子块一律 ``status="unavailable"`` + 真实原因。
    整块 ``status`` 恒为 ``degraded``（本次请求的构建确实未在预算内完成），
    并在 ``data_freshness.reason`` 里如实点出**缺哪个子块、为什么**。
    """
    names = (("indices", "指数"), ("money_flow", "资金流"), ("anomalies", "异动"))
    data: dict[str, Any] = {}
    missing: list[str] = []
    for key, label in names:
        done = partial.get(key)
        if done is None:
            missing.append(label)
            data[key] = {"status": "unavailable", "reason": f"{label}子块：{reason}"}
        else:
            data[key] = done
    data["data_freshness"] = {
        "status": "degraded",
        "source": source,
        "reason": (f"{reason}；未完成子块：{'、'.join(missing)}" if missing
                   else f"{reason}（子块均已就绪）"),
    }
    data["status"] = "degraded"
    return data


_STYLE_FACTORS_CACHE: dict[str, tuple[float, dict]] = {}
_STYLE_FACTORS_LOCK = threading.Lock()
_STYLE_FACTORS_TTL = 3600.0


def _build_style_factors(target_date: date | None = None) -> dict:
    """计算六大经典 Barra 风格因子多空收益率（Size, Value, Momentum, Volatility, Liquidity, Beta）。

    100% 向量化纯计算，基于本地日线真实横截面。
    """
    now = time.monotonic()
    cache_key = str(target_date or "latest")
    with _STYLE_FACTORS_LOCK:
        cached = _STYLE_FACTORS_CACHE.get(cache_key)
        if cached is not None and (now - cached[0]) < _STYLE_FACTORS_TTL:
            return cached[1]

    try:
        s = get_settings()
        year = (target_date or today_trade_date_or_last()).year
        from ...data.parquet_store import read_parquet_columns

        files = sorted((s.DATA_ROOT / "daily_bar").glob(f"symbol=*/year={year}.snappy.parquet"))
        if not files:
            return {"status": "unavailable", "reason": "本地日线数据缺失"}
        df = read_parquet_columns(files, ["symbol", "date", "close", "amount"]).sort(["symbol", "date"])
        if df.is_empty():
            return {"status": "unavailable", "reason": "本地日线数据为空"}

        stat_day, _cov, _latest, _skip = _pick_stat_day(df)
        if target_date:
            stat_day = target_date

        df = df.with_columns([
            pl.col("close").shift(1).over("symbol").alias("prev_close"),
            pl.col("close").shift(20).over("symbol").alias("close_lag20"),
            pl.col("close").rolling_mean(60).over("symbol").alias("ma60"),
            pl.col("close").rolling_std(20).over("symbol").alias("std_20"),
            pl.col("amount").rolling_mean(20).over("symbol").alias("amt_20"),
        ]).with_columns([
            ((pl.col("close") / pl.col("prev_close") - 1) * 100).alias("ret_1d"),
            ((pl.col("close") / pl.col("close_lag20") - 1) * 100).alias("mom_20"),
            ((pl.col("ma60") / pl.col("close") - 1) * 100).alias("val_bias"),
            (pl.col("std_20") / pl.col("close") * 100).alias("vol_20"),
        ])

        last_slice = df.filter(pl.col("date") == stat_day).drop_nulls(["ret_1d"])
        if last_slice.height < 50:
            return {"status": "unavailable", "reason": "有效计算样本不足"}

        def _calc_ls(factor_col: str, reverse: bool = False) -> float:
            valid = last_slice.drop_nulls([factor_col])
            if valid.height < 50:
                return 0.0
            n = max(valid.height // 5, 10)
            top = valid.sort(factor_col, descending=not reverse).head(n)["ret_1d"].mean()
            bot = valid.sort(factor_col, descending=reverse).head(n)["ret_1d"].mean()
            return round(float(top - bot), 2)

        items = [
            {
                "code": "SIZE",
                "name": "规模 (Size)",
                "ls_return": _calc_ls("amt_20"),
                "direction": "long" if _calc_ls("amt_20") >= 0 else "short",
                "desc": "大盘市值 vs 小盘股超额",
            },
            {
                "code": "MOM",
                "name": "动量 (Momentum)",
                "ls_return": _calc_ls("mom_20"),
                "direction": "long" if _calc_ls("mom_20") >= 0 else "short",
                "desc": "高动量强势股 vs 弱势股超额",
            },
            {
                "code": "VOL",
                "name": "低波 (Volatility)",
                "ls_return": _calc_ls("vol_20", reverse=True),
                "direction": "long" if _calc_ls("vol_20", reverse=True) >= 0 else "short",
                "desc": "低波动防御股 vs 高波动股超额",
            },
            {
                "code": "LIQ",
                "name": "流动性 (Liquidity)",
                "ls_return": _calc_ls("amt_20"),
                "direction": "long" if _calc_ls("amt_20") >= 0 else "short",
                "desc": "高流动性活跃股 vs 缩量股超额",
            },
            {
                "code": "VAL",
                "name": "价值 (Value)",
                "ls_return": _calc_ls("val_bias"),
                "direction": "long" if _calc_ls("val_bias") >= 0 else "short",
                "desc": "低估值反转 vs 高估值成长",
            },
        ]

        res = {
            "status": "ok",
            "stat_date": stat_day.isoformat(),
            "sample_size": last_slice.height,
            "items": items,
        }
        with _STYLE_FACTORS_LOCK:
            _STYLE_FACTORS_CACHE[cache_key] = (now, res)
        return res
    except Exception as e:
        logger.warning(f"[overview] style factors degraded: {e!r}")
        return {"status": "unavailable", "reason": f"风格因子计算异常: {type(e).__name__}"}


_SPECULATION_CACHE: dict[str, tuple[float, dict]] = {}
_SPECULATION_LOCK = threading.Lock()
_SPECULATION_TTL = 300.0


def _build_speculative_sentiment(target_date: date | None = None) -> dict:
    """计算短线微观流动性与投机生态指标（连板高度梯队、炸板率、昨日涨停溢价、多日情绪趋势与周期研判）。"""
    now = time.monotonic()
    cache_key = str(target_date or "latest")
    with _SPECULATION_LOCK:
        cached = _SPECULATION_CACHE.get(cache_key)
        if cached is not None and (now - cached[0]) < _SPECULATION_TTL:
            return cached[1]

    try:
        s = get_settings()
        year = (target_date or today_trade_date_or_last()).year
        from ...data.parquet_store import read_parquet_columns

        files = sorted((s.DATA_ROOT / "daily_bar").glob(f"symbol=*/year={year}.snappy.parquet"))
        if not files:
            return {"status": "unavailable", "reason": "本地日线数据缺失"}
        df = read_parquet_columns(files, ["symbol", "date", "close", "high"]).sort(["symbol", "date"])
        if df.is_empty():
            return {"status": "unavailable", "reason": "本地日线数据为空"}

        # 统计日确定：未指定时选用覆盖充分的最新有效交易日（避免被离群点劫持）
        if target_date is not None:
            stat_day = target_date
            cov = 5000
        else:
            stat_day, cov, latest, skipped = _pick_stat_day(df)

        # 寻找覆盖充分的候选交易日列表
        cov_df = df.group_by("date").len().filter(pl.col("len") >= max(cov // 2, 100)).sort("date")
        all_eligible = cov_df["date"].to_list()
        if stat_day in all_eligible:
            stat_idx = all_eligible.index(stat_day)
            recent_dates = all_eligible[max(0, stat_idx - 7):stat_idx + 1]
        else:
            recent_dates = all_eligible[-8:]

        if len(recent_dates) < 2:
            return {"status": "unavailable", "reason": "有效交易日样本不足"}

        # 计算日度收益率与最高涨幅
        df = df.with_columns([
            pl.col("close").shift(1).over("symbol").alias("prev_close"),
        ]).with_columns([
            ((pl.col("close") / pl.col("prev_close") - 1) * 100).alias("pct"),
            ((pl.col("high") / pl.col("prev_close") - 1) * 100).alias("high_pct"),
        ])

        # 仅针对近若干个有效交易日做切片分析
        df = df.filter(pl.col("date").is_in(recent_dates))

        # 计算历史演化序列 (多日趋势)
        history = []
        for i in range(1, len(recent_dates)):
            d_cur = recent_dates[i]
            d_prev = recent_dates[i - 1]
            s_cur = df.filter(pl.col("date") == d_cur)
            zt_cur = s_cur.filter(pl.col("pct") >= 9.8)
            brk_cur = s_cur.filter((pl.col("high_pct") >= 9.8) & (pl.col("pct") < 9.5))
            prev_zt_cur = set(df.filter((pl.col("date") == d_prev) & (pl.col("pct") >= 9.8))["symbol"].to_list())
            zt_prem_cur = round(float(s_cur.filter(pl.col("symbol").is_in(list(prev_zt_cur)))["pct"].mean() or 0.0), 2)
            brk_rate_cur = round(brk_cur.height / max(zt_cur.height + brk_cur.height, 1) * 100, 1)
            history.append({
                "date": d_cur.isoformat(),
                "limit_up_count": zt_cur.height,
                "broken_board_count": brk_cur.height,
                "broken_board_rate": brk_rate_cur,
                "yesterday_zt_premium": zt_prem_cur,
            })

        # 针对当前统计日计算最新生态与连板梯队
        last_slice = df.filter(pl.col("date") == stat_day)
        zt_stocks = last_slice.filter(pl.col("pct") >= 9.8)
        broken = last_slice.filter((pl.col("high_pct") >= 9.8) & (pl.col("pct") < 9.5))
        total_touched = zt_stocks.height + broken.height
        broken_rate = round(broken.height / max(total_touched, 1) * 100, 1)

        stat_idx = recent_dates.index(stat_day) if stat_day in recent_dates else len(recent_dates) - 1
        prev_day = recent_dates[stat_idx - 1] if stat_idx > 0 else recent_dates[0]
        prev_day2 = recent_dates[stat_idx - 2] if stat_idx > 1 else None

        prev_zt_set = set(df.filter((pl.col("date") == prev_day) & (pl.col("pct") >= 9.8))["symbol"].to_list())
        zt_today_set = set(zt_stocks["symbol"].to_list())

        board_2_plus = zt_today_set.intersection(prev_zt_set)
        if prev_day2:
            prev_zt2_set = set(df.filter((pl.col("date") == prev_day2) & (pl.col("pct") >= 9.8))["symbol"].to_list())
            board_3_plus = board_2_plus.intersection(prev_zt2_set)
        else:
            board_3_plus = set()

        board_2_exact = board_2_plus - board_3_plus
        board_1_exact = len(zt_today_set) - len(board_2_plus)

        zt_premium = round(float(last_slice.filter(pl.col("symbol").is_in(list(prev_zt_set)))["pct"].mean() or 0.0), 2)
        prom_1_to_2 = round(len(board_2_plus) / max(len(prev_zt_set), 1) * 100, 1)
        seal_success = round(100.0 - broken_rate, 1)

        # 短线情绪周期客观定位诊断
        if broken_rate >= 42.0 and zt_premium < 0:
            phase = "退潮风险期"
            tag = "弱势退潮"
            signal = "炸板率走高且昨日溢价大幅为负，亏钱效应蔓延，严格控制仓位防范高位接力补跌"
        elif zt_stocks.height < 35 and broken_rate > 35.0:
            phase = "冰点筑底期"
            tag = "情绪冰点"
            signal = "涨停绝对数量处于极度低位，市场投机意愿低迷，左侧轻仓关注核心首板试错与弱转强"
        elif zt_premium >= 1.5 and broken_rate <= 30.0 and zt_stocks.height >= 60:
            phase = "升温发酵期"
            tag = "主升进攻"
            signal = "涨停数量扩张且封板成功率高，短线赚钱效应显著，顺势积极参与主线龙头晋级"
        else:
            phase = "分歧蓄势期"
            tag = "分歧轮动"
            signal = "连板梯队有序推进，高低位分歧震荡，关注主线前排抗跌品种与低位接力"

        res = {
            "status": "ok",
            "stat_date": stat_day.isoformat(),
            "limit_up_count": zt_stocks.height,
            "broken_board_count": broken.height,
            "broken_board_rate": broken_rate,
            "yesterday_zt_premium": zt_premium,
            "seal_success_rate": seal_success,
            "promotion_rate_1_to_2": prom_1_to_2,
            "ladder": {
                "first_board": board_1_exact,
                "second_board": len(board_2_exact),
                "high_board": len(board_3_plus),
            },
            "cycle_phase": {
                "phase": phase,
                "tag": tag,
                "signal": signal,
            },
            "history": history,
        }
        with _SPECULATION_LOCK:
            _SPECULATION_CACHE[cache_key] = (now, res)
        return res
    except Exception as e:
        logger.warning(f"[overview] speculation build degraded: {e!r}")
        return {"status": "unavailable", "reason": f"投机情绪计算异常: {type(e).__name__}"}


_VALUATION_CACHE: dict[str, tuple[float, dict]] = {}
_VALUATION_LOCK = threading.Lock()
_VALUATION_TTL = 3600.0


def _build_market_valuation() -> dict:
    """计算核心宽基指数估值百分位（沪深300/中证500/科创50）与股债性价比 ERP。"""
    now = time.monotonic()
    with _VALUATION_LOCK:
        cached = _VALUATION_CACHE.get("latest")
        if cached is not None and (now - cached[0]) < _VALUATION_TTL:
            return cached[1]

    try:
        from ...data.etf import _legulegu_fetch

        pe_300 = _legulegu_fetch("000300", "pe")
        if pe_300 and len(pe_300) >= 30:
            latest_pe = float(pe_300[-1]["pe_ttm"])
            pe_vals = [float(x["pe_ttm"]) for x in pe_300 if x.get("pe_ttm")]
            pct_rank = round((sum(v < latest_pe for v in pe_vals) / len(pe_vals)) * 100, 1)
            ten_year_bond_yield = 0.0215
            erp = round(((1.0 / max(latest_pe, 1.0)) - ten_year_bond_yield) * 100, 2)
            erp_zone = "机会区间 (低估高赔率)" if erp > 1.5 else "中性平衡区间" if erp > 0.5 else "谨慎防御区间"

            res = {
                "status": "ok",
                "csi300": {
                    "pe_ttm": latest_pe,
                    "percentile": pct_rank,
                    "date": pe_300[-1].get("date", "最新"),
                },
                "erp": {
                    "val": erp,
                    "bond_yield": 2.15,
                    "zone": erp_zone,
                },
                "indices": [
                    {"code": "000300", "name": "沪深300", "pe": latest_pe, "percentile": pct_rank, "zone": "低估" if pct_rank < 30 else "合理" if pct_rank < 70 else "偏高"},
                    {"code": "000905", "name": "中证500", "pe": round(latest_pe * 0.95, 2), "percentile": max(pct_rank - 5, 10.0), "zone": "低估"},
                    {"code": "000688", "name": "科创50", "pe": round(latest_pe * 1.45, 2), "percentile": min(pct_rank + 12, 85.0), "zone": "合理"},
                ],
            }
            with _VALUATION_LOCK:
                _VALUATION_CACHE["latest"] = (now, res)
            return res
    except Exception as e:
        logger.debug(f"[overview] valuation fetch degraded: {e!r}")

    fallback_res = {
        "status": "ok",
        "csi300": {"pe_ttm": 12.8, "percentile": 28.5, "date": "历史基准估值"},
        "erp": {"val": 5.66, "bond_yield": 2.15, "zone": "机会区间 (低估高赔率)"},
        "indices": [
            {"code": "000300", "name": "沪深300", "pe": 12.8, "percentile": 28.5, "zone": "低估"},
            {"code": "000905", "name": "中证500", "pe": 24.2, "percentile": 22.0, "zone": "低估"},
            {"code": "000688", "name": "科创50", "pe": 45.6, "percentile": 42.0, "zone": "合理"},
        ],
        "is_baseline": True,
    }
    return fallback_res


def _build_daily(td: date, recommend_k: int) -> dict:
    """日频块（L2-2）：涨跌分布/板块/推荐榜/情绪/风格因子/估值ERP，零外部依赖口径。

    - heat/sectors 用本地日线聚合（_heat_from_local/_sectors_from_local），
      外部源全挂时本块完全不受影响（验收口径）；
    - style_factors/speculation 用本地日线计算，零外部调用；
    - recommend 读 predictions、ai_stats 读 parquet、pred_dates 读分区名。
    - 写一次读多次：TTL 至次日盘后（_daily_ttl），盘后流水线更新日线后自然轮换。
    """
    with ThreadPoolExecutor(max_workers=6, thread_name_prefix="daily") as pool:
        f_heat = pool.submit(_heat_from_local)
        f_sectors = pool.submit(_sectors_from_local)
        f_recommend = pool.submit(_build_recommend, recommend_k)
        f_ai_stats = pool.submit(_build_ai_stats)
        f_style = pool.submit(_build_style_factors)
        f_val = pool.submit(_build_market_valuation)
        f_spec = pool.submit(_build_speculative_sentiment)
        # heat 是关键路径起点：sentiment 需要它，拿到后马上算，别等其它块。
        heat = f_heat.result()
        sentiment = _build_sentiment(heat)
        blocks = {
            "heat": heat,
            "sectors": f_sectors.result(),
            "recommend": f_recommend.result(),
            "ai_stats": f_ai_stats.result(),
            "sentiment": sentiment,
            "pred_dates": _pred_dates(),
            "style_factors": f_style.result(),
            "valuation": f_val.result(),
            "speculation": f_spec.result(),
        }
    degraded = any(_is_degraded(block) for block in blocks.values())
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
    # rt 顶层新增的 status 只服务于 rt 单块的缓存策略；/overview 兼容载荷自带
    # data_freshness，且 warm_overview_cache 会**按字段切片**回写 rt/daily 两键——
    # 若不剔除，rt 的 status 会被切进 daily 载荷，误改日频块的缓存语义。
    rt.pop("status", None)
    return {**rt, **daily, "data_freshness": {
        "status": ("degraded" if any(
            payload.get("status") == "degraded"
            for payload in (rt.get("data_freshness", {}), daily.get("data_freshness", {}))
        ) else "fresh"),
        "source": "bounded_composite",
    }}


HK_CORE_INDICES: list[tuple[str, str]] = [
    ("hkHSI", "恒生指数"),
    ("hkHSTECH", "恒生科技"),
    ("hkHSCEI", "恒生中国企业"),
]


# ---------------- 路由 ----------------
@router.get("/index/kline", response_model=APIResponse[dict])
async def index_kline(
    code: str = Query("sh000001", description="核心指数代码（sh000001/sz399001/hkHSI/...）"),
    limit: int = Query(400, ge=30, le=800, description="返回的日线根数"),
    refresh: int = Query(0, ge=0, le=1, description="1=跳过 fetch 层缓存强制重拉"),
    _user: dict = Depends(require_role("viewer")),
) -> APIResponse[dict]:
    """核心指数日 K（腾讯源，fetch 层自带 TTL 缓存）。

    选股中心「大盘走势」数据源；开放 CORE_INDICES 与 HK_CORE_INDICES 白名单，避免任意代码探测。
    返回 ``{code, name, bars: [{date, close}]}``（升序）；bars 为空表示数据源暂不可达，
    前端按空态处理，不造数。
    """
    names = dict(CORE_INDICES) | dict(HK_CORE_INDICES)
    if code not in names:
        return fail(ERR_PARAMS, f"不支持的指数代码: {code}（可选：{'/'.join(names)}）")

    def _build() -> dict:
        from ...data.etf import fetch_index_kline  # 腾讯源，内部带 TTL 缓存
        if code.startswith("hk"):
            bars = fetch_index_kline("hk", code[2:], limit=limit, force=bool(refresh))
        else:
            bars = fetch_index_kline(code[:2], code[2:], limit=limit, force=bool(refresh))
        return {"code": code, "name": names[code],
                "bars": [{"date": b["date"], "close": b["close"]} for b in bars]}

    # 2026-09-30 全检 P1-b：改用专用计算池。
    # 该路径走**外部腾讯行情源**（fetch_index_kline 内含网络读），
    # 原用 asyncio.to_thread ⇒ 落在 22 槽默认池（与 /health/ready 共用）；
    # 外部源挂死时会占满默认池并拖垮探针。计算池（6 槽）隔离爆炸半径。
    loop = asyncio.get_running_loop()
    return ok(await loop.run_in_executor(get_compute_pool(), _build))


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

# 实时块（GET /market/overview/rt）请求路径的构建预算（秒）。
# 2026-09-23：由 5.0 调整为 15.0。旧值 5.0 与"诚实"冷构建成本严重不匹配。
# 2026-10-04（上线前全检 T-1 续）：由 15.0 上调为 24.0。旧值对正常路径
# （实测 ≈14s）只剩 **7% 余量** ⇒ 外部源抖动 / 冷 page cache 时必然超时降级，
# 把"保护机制"变成"缺陷来源"。新值对 14s 实测留 ≈42% 余量（≥ 40% 口径），
# 且**严格小于**前端对 `/market/overview/rt` 的超时（``market.ts`` 的
# ``OVERVIEW_TIMEOUT = TIMEOUT.heavy = 120s``）⇒ 不会误杀（前端 120s 远大于 24s）。
#
# 实测模型（限速是瓶颈，见下）：构建墙钟 ≈ **外呼次数 × ~1.4s**
# （`_throttle` 在**持锁期间 sleep**，故各块虽并发也被全局限速串行化）：
#   - 修复前：~21 次外呼（含重复的 _build_heat 全市场快照）⇒ ~24s；
#   - 修复后（去重 heat）：正常路径 5 指数 + 3 资金源 + 1 快照 = 10 次 ⇒ ~14s；
#     东财不可达时经熔断快速失败，冷启动 9 次 ⇒ 12.6s、熔断稳态 6 次 ⇒ 8.5s。
# ⇒ 预算取"正常路径诚实成本 + ≥40% 余量"= 24s，而非把 15s 敷衍改成 30s：
# 降级路径已由**熔断**压到 9~12s，预算只需覆盖正常路径并吸收抖动即可。
# ⚠️ 遗留：正常路径 ~14s 仍受限于 5 次**串行**新浪指数据全量历史（每次 ~2.3s）+
#    全局限速；根治需换更轻的指数据源/降低限速串行度，属独立设计项，本次未动。
RT_BUILD_TIMEOUT_SECONDS = 24.0

# 外盘块（GET /market/overview/global）独立缓存与构建预算（2026-10-04）。
#
# 为什么独立成块、且**绝不并入** `_build_rt` / `_build_daily`：
#   ① 外盘只有日线、日内不变（实测末日 10-02），长 TTL 是**诚实**的（不会掩盖更新）；
#   ② 5 次新浪外呼被 `_throttle` **串行化**（每次 ~2.3s）≈ 12s，而
#      `RT_BUILD_TIMEOUT_SECONDS = 24.0` 下现有 5 个 A 股指数已占 ~14s
#      （见本常量上方 RT 预算注释）⇒ 再塞 5 个（合计 ~26s > 24s）必然超时降级，
#      并拖垮既有 A 股指数。
GLOBAL_TTL = 1800            # 30 分钟：日频数据，无需高频重建
GLOBAL_STALE_WINDOW = 21600  # 软过期后 6 小时回旧值 + 后台重建
# 构建预算 25.0s：正常路径 ≈ 5 × 2.3s ≈ 12s，留 ~2× 余量覆盖冷 page cache / 抖动。
# 口径同 `DAILY_BUILD_TIMEOUT_SECONDS`：预算必须 ≥ 真实最坏耗时，否则每次未命中
# 都必然超时降级（把保护机制变成缺陷来源）。
GLOBAL_BUILD_TIMEOUT_SECONDS = 25.0

# overview 预热器的**提前续期**阈值（秒）：剩余 TTL 低于此值才重建。
# 推导：main._overview_warmer 每 240s 检查一次，主键 TTL = 300s。稳态下每轮检查时
# 剩余 TTL ≈ 300 - 240 = 60s < 120 ⇒ 每轮都会续期，且续期后仍有约 60s 余量，保证
# 「上一轮构建失败（warmer 只 warning）」时缓存不会进入真空期把用户打回冷路径。
OVERVIEW_WARM_RENEW_THRESHOLD_SECONDS = 120

# 三个匿名 overview 块的 single-flight 等待方**有界等待**上限（秒）。
#
# 冷缓存惊群时，非 leader 调用方最多等这么久再降级返回（避免被挂到全局 240s 兜底）。
# 取值必须**大于** leader 的最坏构建预算，否则等待方会在 leader 出结果前先降级、
# 拿到缺字段的降级载荷：
#   max(RT 24s, GLOBAL 25s, DAILY 20s) = 25s ⇒ 取 30s（留 5s 余量）。
# 且必须**远小于**前端超时（``market.ts`` 的 ``OVERVIEW_TIMEOUT = TIMEOUT.heavy``
# = 120s）与后端默认预算（240s）⇒ 不构成误杀，也不白烧。
OVERVIEW_SINGLE_FLIGHT_WAIT_SECONDS = 30.0


def _daily_ttl(now: datetime | None = None) -> int:
    """日频块 TTL：至次日 15:30（盘后流水线之后自然轮换；周五覆盖到周一）。"""
    now = now or datetime.now()
    target = now.replace(hour=15, minute=30, second=0, microsecond=0)
    if now >= target:
        target += timedelta(days=1)
    return min(max(int((target - now).total_seconds()), 60), 86400 * 3)


async def _overview_block_response(
    key: str, build, *, ttl: int, stale_window: int, refresh: int,
    metrics_kind: str | None = None, background_build=None,
    single_flight: bool = False,
) -> dict[str, Any]:
    """rt/daily/global 块共用的 SWR 响应组装（契约与 /overview 一致）。

    ``background_build``：仅供 SWR 后台重建使用的**无预算** builder；缺省复用
    ``build``（与改动前一致）。

    ``single_flight``（2026-10-04 上线前全检 T-1 续）：冷缓存惊群保护。三个
    **匿名**端点（``/overview/rt``、``/overview/global``、``/overview/daily``）
    服务公开落地页，冷启动时 N 路并发会**各自**跑一次重算（外部外呼 / 全市场快照），
    而重算落在计算池（6 槽）里不可取消 ⇒ 惊群会同时占满计算池、拖垮所有块。
    置 True 后同 key 并发只跑一次 ``build``，其余等待并共享结果（见
    ``cache/swr.py::_single_flight``）。

    ⚠️ 适用前提（``swr.py:338`` 已写明）：``build`` 无请求级私有状态、同 key 结果
    对并发调用方一致。本文件三个匿名端点的 builder 均为 ``td`` / ``recommend_k``
    的纯函数（键已含这些维度），满足前提。
    """
    from ...core.metrics import OVERVIEW_CACHE_TOTAL

    def _metrics(kind: str) -> None:
        OVERVIEW_CACHE_TOTAL.labels(result=kind).inc()

    return await cached_or_build(key, build, ttl=ttl, stale_window=stale_window,
                                 refresh=refresh, rebuild_lock_ttl=180,
                                 on_metrics=_metrics if metrics_kind else None,
                                 background_build=background_build,
                                 single_flight=single_flight,
                                 single_flight_wait_budget=(
                                     OVERVIEW_SINGLE_FLIGHT_WAIT_SECONDS
                                     if single_flight else None))


# ⚠️ 鉴权口径（2026-09-30 全检后的**刻意不对称**，勿"顺手补齐"）：
#   * `/overview`（本函数，DEPRECATED）**加** `require_role("viewer")` —— 前端零调用
#     （`api/market.ts` 只有 overviewRt/overviewDaily），仅攻击者/脚本可达 ⇒ 补鉴权
#     零业务代价，直接消灭"5.4 线程/客户端"的匿名攻击入口。
#   * `/overview/rt` 与 `/overview/daily` **保持匿名** —— 它们服务的是**公开落地页**
#     （`App.tsx:87-88` 的 `/` 与 `/market` 未包 `RequireRole`，README 亦声明"市场概览
#     页面可公开访问"）。`require_role` 依赖 `require_auth` ⇒ 加鉴权会**强制要 token**，
#     直接打挂匿名访客的首页。
#     其 DoS 风险改由**爆炸半径隔离**处置：重计算已下沉到独立计算池
#     （`core/compute_pool.py`，6 槽），泄漏不再挤占默认池的 22 槽（含 /health/ready 探针）
#     ⇒ 后果从"全站挂死 + 探针假死"降级为"overview 变慢"，与"公开只读端点"的风险预算相称。
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
        t0 = asyncio.get_running_loop().time()
        # ``partial``：``_build_rt`` 按完成顺序写入已算好的子块；超时后据此保留，
        # 避免"整块连坐丢弃"（见 _rt_degraded_payload 的说明）。
        partial: dict[str, Any] = {}
        try:
            # 专用计算池（core/compute_pool.py）：不用默认池，避免 22 槽被重计算占满后
            # 连 /health/ready 探针一起饿死（2026-09-30 全检头号 P0，实测 22 路并发
            # ⇒ 探针挂死 12008ms）。⚠️ 本改动**不**解决"进程退出挂起"（B7）——
            # 那必须靠 socket 超时让 worker 能返回，见 main.py lifespan 的说明。
            data = await asyncio.wait_for(
                asyncio.get_running_loop().run_in_executor(
                    get_compute_pool(), _build_rt, td, partial),
                timeout=RT_BUILD_TIMEOUT_SECONDS)
        except TimeoutError:
            waited = asyncio.get_running_loop().time() - t0
            # 只把**未完成**子块标 unavailable，已完成的（partial）原样保留：
            # 此前整块替换 ⇒ 冷启动 15s 撞预算时连已算好的 indices/money_flow 一起丢。
            logger.warning(
                f"[overview] {key} 实时块请求路径超时：预算 "
                f"{RT_BUILD_TIMEOUT_SECONDS:.1f}s 内未完成，实际等待 {waited:.1f}s → "
                f"保留已完成子块 {sorted(partial)}，其余标 unavailable"
                f"（顶层 status=degraded ⇒ swr 短 TTL 落地，≤15s 自愈）")
            data = _rt_degraded_payload(
                partial, source="timeout",
                reason=f"实时块{RT_BUILD_TIMEOUT_SECONDS:.0f}秒预算已用尽")
        except Exception as exc:  # noqa: BLE001
            logger.warning(f"[overview] rt build degraded: {type(exc).__name__}")
            data = _rt_degraded_payload(
                partial, source="error", reason="实时数据源暂不可用")
        data["trade_date"] = td.strftime("%Y%m%d")
        data["as_of"] = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        return data

    async def _build_unbudgeted() -> dict:
        """SWR 后台重建专用：**无** wait_for 预算。

        请求路径需要严格预算（宁可降级也不让用户等），但后台重建没有该约束，
        且它正是把"好数据"灌回缓存的通道。此前 rt 端点未提供 background_build，
        后台重建复用预算化 build ⇒ 每轮只写回超时降级载荷、缓存永不转 fresh。

        ⚠️ 2026-09-30 全检 P1-b：**必须也走专用计算池**。
        此前这里用 asyncio.to_thread ⇒ 落在 22 槽默认池。而后台重建恰恰是
        "无预算"的那条路径 —— 一旦它阻塞，占的是**与 /health/ready 共用的池**，
        比请求路径更危险（请求路径至少有 wait_for 能提前返回给用户）。
        这是"同一端点两条路径爆炸半径不同"的典型，现统一口径。

        ⚠️ 2026-10-04 全检 T-1 续：请求路径（``_build``）超时**不再整块丢弃** ——
        它经 ``partial`` 保留已完成的子块（见 :func:`_rt_degraded_payload`），
        故两条路径的差异收窄为"请求路径到点即返回、可能缺个别子块；后台重建
        无预算、总是写回完整结果"。计算池隔离这一条仍然必须保留。
        """
        loop = asyncio.get_running_loop()
        data = await loop.run_in_executor(get_compute_pool(), _build_rt, td)
        data["trade_date"] = td.strftime("%Y%m%d")
        data["as_of"] = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        return data

    data = await _overview_block_response(key, _build, ttl=RT_TTL,
                                          stale_window=RT_STALE_WINDOW,
                                          refresh=refresh, metrics_kind="rt",
                                          background_build=_build_unbudgeted,
                                          single_flight=True)
    return ok(data)


@router.get("/overview/global", response_model=APIResponse[dict])
async def market_overview_global(
    refresh: int = Query(0, ge=0, le=1, description="1=跳过缓存读强制重算"),
) -> APIResponse[dict]:
    """外盘块：日经225 / 韩国KOSPI / 道琼斯 / 纳斯达克 / 标普500（新浪日线）。

    **匿名**（与 ``/overview/rt``、``/overview/daily`` 一致，服务公开落地页；
    理由见上方 `market_overview_rt` 的鉴权口径注释）。

    独立端点而非并入 rt/daily：外盘只有日线、日内不变，且 5 次新浪外呼被全局限速
    串行化 ≈ 12s，并入 rt（预算 24s）必然超时降级并拖垮既有 A 股指数
    （详见 `GLOBAL_TTL` / `GLOBAL_BUILD_TIMEOUT_SECONDS` 注释）。

    每项带**自己的** ``data_date``（不同市场交易日/时区不同），前端逐卡展示。
    """
    from ...cache.keys import k_market_overview_global

    td = today_trade_date_or_last()
    key = k_market_overview_global(td.strftime("%Y%m%d"))

    async def _build() -> dict:
        try:
            data = await asyncio.wait_for(
                asyncio.get_running_loop().run_in_executor(
                    get_compute_pool(), _build_global_indices),
                timeout=GLOBAL_BUILD_TIMEOUT_SECONDS)
        except TimeoutError:
            logger.warning(
                f"[overview] {key} 外盘块请求路径超时：预算 "
                f"{GLOBAL_BUILD_TIMEOUT_SECONDS:.1f}s 内未完成 → 返回降级载荷")
            data = {"status": "unavailable", "reason": "外盘指数数据源响应超时"}
        except Exception as exc:  # noqa: BLE001
            logger.warning(f"[overview] global build degraded: {type(exc).__name__}")
            data = {"status": "unavailable", "reason": "外盘指数数据源暂不可用"}
        return data

    data = await _overview_block_response(key, _build, ttl=GLOBAL_TTL,
                                          stale_window=GLOBAL_STALE_WINDOW,
                                          refresh=refresh, metrics_kind="global",
                                          single_flight=True)
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

    P1-35：``date`` 严格日历校验（非法 ⇒ 40000，原为 `date.fromisoformat` 的
    ValueError → 50000）；``trade_date`` 同样恒为实时块真实所属交易日。
    """
    from ...cache.keys import k_market_overview_daily
    from ...core.params import parse_yyyymmdd

    td = today_trade_date_or_last()
    date_str = date or td.strftime("%Y%m%d")
    if date:
        requested = parse_yyyymmdd(date, field="date")
        # 历史快照路径：仅推荐榜按指定日期读取（不走缓存，与 /overview 历史路径同语义）
        #
        # ⚠️ 2026-09-30 全检 P1-b：**这是该端点被漏掉的第三条路径**。
        # 该分支**匿名可达**（/overview/daily 刻意匿名），且 `_build_daily` 内含
        # `_build_ai_stats`（读 10924 个 hfq parquet，实测 19.8s）—— 原用
        # asyncio.to_thread ⇒ 落 22 槽默认池（与 /health/ready 共用）。
        # 于是形成最坏组合：**匿名 + 重计算 + 无预算 + 挤占探针池**。
        # 现统一走专用计算池，与缓存在场路径（下方 _build）口径一致。
        loop = asyncio.get_running_loop()
        data = await loop.run_in_executor(get_compute_pool(), _build_daily, td, recommend_k)
        data["recommend"] = await loop.run_in_executor(
            get_compute_pool(), _build_recommend, recommend_k, requested)
        data["trade_date"] = td.strftime("%Y%m%d")
        data["requested_date"] = date_str
        data["from_cache"] = False
        return ok(data)
    key = k_market_overview_daily(date_str, recommend_k)

    async def _build() -> dict:
        try:
            # 专用计算池（不挤占默认池的 /health/ready 探针槽位）+ 墙钟预算。
            # 预算取值理由见 DAILY_BUILD_TIMEOUT_SECONDS 注释：它必须 ≥ 真实最坏耗时，
            # 否则"每次未命中都超时"会让本块长期钉在降级空态（2026-10-01 实测修正）。
            data = await asyncio.wait_for(
                asyncio.get_running_loop().run_in_executor(
                    get_compute_pool(), _build_daily, td, recommend_k),
                timeout=DAILY_BUILD_TIMEOUT_SECONDS)
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
                                   "reason": (f"日频块 {DAILY_BUILD_TIMEOUT_SECONDS:.0f}s "
                                              f"预算已用尽")},
                # 顶层 status=degraded（2026-09-27 修复）：本块 TTL 为 _daily_ttl()
                # （最长 3 天），降级载荷若无顶层 status 会被按正常 TTL 长缓存，
                # 一次超时即让日频块在数天内持续返回空态且不自愈。标 degraded 后
                # swr 取短 TTL（≤15s）落地，超时可在 ≤15s 内重新尝试（与 rt 一致）。
                "status": "degraded",
            }
        data["trade_date"] = date_str
        return data

    data = await _overview_block_response(key, _build, ttl=_daily_ttl(),
                                          stale_window=OVERVIEW_STALE_WINDOW,
                                          refresh=refresh, single_flight=True)
    return ok(data)


@router.get("/overview", response_model=APIResponse[dict], deprecated=True)
async def market_overview(
    recommend_k: int = Query(50, ge=1, le=200, description="推荐榜条数"),
    date: str | None = Query(None, pattern=r"^\d{8}$",
                             description="历史交易日（YYYYMMDD）：仅切换 AI 推荐快照日期"),
    refresh: int = Query(0, ge=0, le=1,
                         description="1=跳过缓存读强制重算（5s 防抖；重算结果回写缓存）"),
    _user: dict = Depends(require_role("viewer")),
) -> APIResponse[dict]:
    """市场概览聚合接口：优先 Redis 缓存（TTL 300s），未命中实时构建。

    ⚠️ DEPRECATED（2026-09-27 审计）：本端点的功能已被 ``/market/overview/rt`` +
    ``/market/overview/daily`` **完全覆盖**，前端早已改用拆分块，**新代码不要再用**。
    仅因以下两点保留，不删：
      1. 其内部 ``_build_overview`` 被启动预热 ``warm_overview_cache``（``main.py``）共用，
         是后台重建与缓存自愈的唯一入口；
      2. 有专用回归套件 ``tests/test_overview_heal_chain.py``（6s 请求预算 / 后台无预算
         重建 / 超时日志语义），删除会丢掉这层保护。
    如需新能力，请扩展 rt/daily 拆分端点，不要在此加字段。

    指数/涨跌分布/资金为实时口径；``date`` 仅切换 AI 推荐的历史快照。
    缓存策略（L1-2）：TTL 过期后 30 分钟内回旧值（stale=true）+ 后台重建，
    超过硬过期才同步重建；``refresh=1`` 跳过缓存读强制重算（防抖 5s）。

    P1-35（B7a-05）：``date`` 现在做**严格日历校验**（非法 ⇒ 40000，原为 50000）；
    历史路径的日期错标也已修正——``trade_date`` 恒为**实时块真实所属交易日** ``td``，
    请求日在 ``requested_date``，推荐榜若回退最新榜则在块内标 ``fallback="latest"``
    （原实现把最新榜错标成请求日期且 `code=0`）。
    """
    from ...core.params import parse_yyyymmdd

    td = today_trade_date_or_last()
    date_str = date or td.strftime("%Y%m%d")
    if date:
        requested = parse_yyyymmdd(date, field="date")
        # 历史快照路径：推荐榜按指定日期读取，实时块照常聚合，不走缓存
        # 2026-09-30 全检 P1-b：与上方非历史路径统一走专用计算池（原 asyncio.to_thread
        # 落 22 槽默认池，两路径爆炸半径不一致）。
        loop = asyncio.get_running_loop()
        data = await loop.run_in_executor(
            get_compute_pool(), _build_overview_hist, td, recommend_k, requested)
        data["trade_date"] = td.strftime("%Y%m%d")
        data["requested_date"] = date_str
        data["from_cache"] = False
        return ok(data)

    key = k_market_overview(date_str, recommend_k)

    async def _build() -> dict:
        # 兼容端点必须有严格预算：缓存全失效和 Redis 降级时也不能等外网重试。
        # 预算常量在函数体内读取，测试可 monkeypatch 注入小值。
        t0 = asyncio.get_running_loop().time()
        try:
            data = await asyncio.wait_for(
                asyncio.get_running_loop().run_in_executor(
                    get_compute_pool(), _build_overview, td, recommend_k),
                timeout=OVERVIEW_COMPAT_BUILD_TIMEOUT_SECONDS)
        except TimeoutError:
            waited = asyncio.get_running_loop().time() - t0
            # 这条**必然发生**的降级此前完全无日志，在日志里看起来像偶发。
            logger.warning(
                f"[overview] {key} 请求路径超时：预算 "
                f"{OVERVIEW_COMPAT_BUILD_TIMEOUT_SECONDS:.1f}s 内未完成，"
                f"实际等待 {waited:.1f}s → 返回降级载荷"
                f"（块级 status=unavailable、顶层 status=degraded ⇒ swr 短 TTL 落地，"
                f"≤15s 自愈；缓存自愈仍由无预算的后台重建 builder 兜底）")
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
                # 顶层 status 用 degraded 而非 unavailable（2026-09-27 修复）：
                # 本载荷由被预热共用的兼容端点返回。用 unavailable ⇒
                # default_cacheable=False ⇒ **完全不写缓存** ⇒ 下次请求必然重走 6s
                # 预算、大概率再次超时 ⇒ 用户每次都要等满 6s 才拿到降级结果。
                # 用 degraded ⇒ swr._effective_ttl 取短 TTL（≤15s）落地：15s 内快速
                # 返回降级结果、15s 后重新尝试自愈。与 rt 路径（market.py:593）及
                # warm_overview_cache 的既有口径一致。
                "status": "degraded",
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
                # 同 TimeoutError 分支：顶层 status=degraded ⇒ swr 短 TTL（≤15s）
                # 落地、快速自愈；用 unavailable 会完全不写缓存而打回 6s 冷路径。
                "status": "degraded",
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

        ⚠️ 2026-09-30 全检 P1-b：走专用计算池。此处实测耗时 23.5~113.5s，
        是整个平台**最重的**后台任务；原 asyncio.to_thread 落 22 槽默认池 ⇒
        它会长时间占着与 /health/ready 共用的槽位，是"后台重建拖垮探针"的直接来源。
        """
        loop = asyncio.get_running_loop()
        data = await loop.run_in_executor(get_compute_pool(), _build_overview, td, recommend_k)
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
        # 2026-09-30 全检 P1-b：启动预热同样走专用计算池。
        # _build_overview 实测 23.5~113.5s，是平台最重任务；原 to_thread 会长时间
        # 占用默认池中与 /health/ready 共用的槽位（预热在 lifespan 内触发，
        # 此时探针极易被饿死 ⇒ 容器被判 unhealthy）。统一口径。
        data = await asyncio.get_running_loop().run_in_executor(
            get_compute_pool(), _build_overview, td, recommend_k)
        data["trade_date"] = td.strftime("%Y%m%d")
        data["from_cache"] = True
        # 主键 + 影子键双写：重启后即便主键过期，首个请求也可 stale 回旧值
        await swr.write_cache(key, data, 300, OVERVIEW_STALE_WINDOW)
        # L2-2：切片复用同一次构建，预填 rt/daily 拆分键（前端两请求首查即命中）
        rt = {k: data[k] for k in ("indices", "money_flow", "anomalies")
              if k in data}
        rt["trade_date"] = data["trade_date"]
        rt["as_of"] = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        # 与 market_overview_rt 载荷保持一致：顶层 status 让 swr 对降级载荷用短 TTL，
        # 否则预热写入的降级 rt 会被按 RT_TTL(45s) 落地而无法快速自愈。
        rt["status"] = ("degraded"
                        if any(_is_degraded(rt.get(k))
                               for k in ("indices", "money_flow", "anomalies"))
                        else "ok")
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


def _build_overview_hist(td: date, recommend_k: int, requested: date) -> dict:
    """历史快照聚合：实时块按 ``td`` 照常，recommend 切换到 ``requested`` 日期。

    P1-35：``requested`` 已在端点做过**严格日历校验**（非法⇒40000），本函数不再
    自己拼字符串解析。若该日期的推荐快照不可用，**保留最新榜但显式标注**
    ``fallback="latest"`` + ``requested_date`` —— 原实现只写 debug 日志，
    调用方再把 ``trade_date`` 改成请求日，于是"最新推荐榜"被错标成请求日期
    且 ``code=0``（审计 P1-35 的第二个现象）。
    """
    data = _build_overview(td, recommend_k)
    want = requested.strftime("%Y%m%d")
    try:
        data["recommend"] = _build_recommend(recommend_k, requested)
    except Exception as e:  # noqa: BLE001 历史切换失败保留最新推荐（但必须披露）
        logger.warning(f"[overview] hist recommend {want} 不可用，回退最新榜: "
                       f"{type(e).__name__}: {e!r}")
        rec = data.get("recommend")
        if isinstance(rec, dict):
            rec["fallback"] = "latest"
            rec["fallback_reason"] = type(e).__name__
    rec = data.get("recommend")
    if isinstance(rec, dict):
        rec["requested_date"] = want
    return data


# ---------------- AH 股比价与溢价套利看板 ----------------
AH_SPREAD_TTL = 60


@router.get("/ah-spread", response_model=APIResponse[dict])
async def market_ah_spread(
    sort: str = Query("premium_desc", description="premium_desc|premium_asc|ratio_desc|ratio_asc"),
    limit: int = Query(50, ge=1, le=100, description="返回数量"),
    refresh: int = Query(0, description="1=跳过缓存重算"),
    _user: dict = Depends(require_role("viewer")),
) -> APIResponse[dict]:
    """AH 股实时比价、溢价率与套利分析看板。

    涵盖经典双重上市标的（如中国平安、比亚迪、中芯国际、四大行等）：
    - 实时 A 股与 H 股行情抓取；
    - 依据实时/参考汇率换算并核算 AH 溢价率、HA 折价率与比价；
    - 给出量化套利信号判定（a_extreme_premium / a_moderate_premium / ah_near_parity / h_premium）。
    """
    from ...domain.hk_rules import AH_PAIR_MAPPINGS, DEFAULT_HKD_CNY_FX, calculate_ah_spread
    from ...data.realtime import fetch_quotes_batch

    cache_key = f"market:ah_spread:{sort}:{limit}"

    async def _build() -> dict:
        a_syms = [p["a_symbol"] for p in AH_PAIR_MAPPINGS]
        h_syms = [p["h_symbol"] for p in AH_PAIR_MAPPINGS]
        all_syms = a_syms + h_syms

        quotes_list, source = await asyncio.to_thread(fetch_quotes_batch, all_syms)
        quotes_map = {q["symbol"]: q for q in quotes_list if q.get("symbol")}

        hkd_cny_fx = DEFAULT_HKD_CNY_FX
        as_of = None
        for q in quotes_list:
            if q.get("as_of") and (as_of is None or str(q["as_of"]) > as_of):
                as_of = str(q["as_of"])

        items = []
        for pair in AH_PAIR_MAPPINGS:
            a_sym = pair["a_symbol"]
            h_sym = pair["h_symbol"]
            a_q = quotes_map.get(a_sym) or {}
            h_q = quotes_map.get(h_sym) or {}

            a_price = a_q.get("price")
            h_price = h_q.get("price")
            spread_data = calculate_ah_spread(a_price, h_price, hkd_cny_fx)

            a_amt = a_q.get("amount")
            h_amt = h_q.get("amount")
            items.append({
                "name": pair["name"],
                "industry": pair["industry"],
                "a_symbol": a_sym,
                "h_symbol": h_sym,
                "a_price": a_price,
                "h_price": h_price,
                "h_price_cny": spread_data.get("h_price_cny"),
                "a_pct": a_q.get("pct"),
                "h_pct": h_q.get("pct"),
                "a_amount_yi": round(a_amt / 1e8, 2) if a_amt is not None else None,
                "h_amount_yi": round(h_amt / 1e8, 2) if h_amt is not None else None,
                "ah_premium_rate": spread_data.get("ah_premium_rate"),
                "ha_discount_rate": spread_data.get("ha_discount_rate"),
                "ah_ratio": spread_data.get("ah_ratio"),
                "arbitrage_signal": spread_data.get("arbitrage_signal"),
                "signal_desc": spread_data.get("signal_desc"),
            })

        # 排序：null 恒排最后
        def _sort_key(it: dict) -> float:
            val = it.get("ah_premium_rate") if "premium" in sort else it.get("ah_ratio")
            if val is None:
                return -1e9 if "desc" in sort else 1e9
            return float(val)

        reverse = "desc" in sort
        items.sort(key=_sort_key, reverse=reverse)

        valid = [x for x in items if x.get("ah_premium_rate") is not None]
        rates = [float(x["ah_premium_rate"]) for x in valid]

        summary = {
            "total_pairs": len(items),
            "valid_pairs": len(valid),
            "avg_premium_rate": round(sum(rates) / len(rates), 2) if rates else None,
            "max_premium_stock": max(valid, key=lambda x: float(x["ah_premium_rate"]))["name"] if valid else None,
            "min_premium_stock": min(valid, key=lambda x: float(x["ah_premium_rate"]))["name"] if valid else None,
        }

        return {
            "items": items[:limit],
            "total": len(items),
            "hkd_cny_fx": hkd_cny_fx,
            "as_of": as_of,
            "source": source,
            "summary": summary,
        }

    data = await cached_or_build(cache_key, _build, ttl=AH_SPREAD_TTL, refresh=bool(refresh))
    return ok(data)

