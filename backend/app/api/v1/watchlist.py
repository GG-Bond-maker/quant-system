"""自选收藏 API（AQP）：多资产分组行情 + 量化联动。

设计：
1. **行情以本地 Parquet 为主源**（daily_bar 尾部 60 根）——离线可靠、无限流；
   ETF（无本地日线）走腾讯 K 线（data.etf.fetch_kline，带 TTL 缓存）；
   无本地数据的股票兜底腾讯实时快照（仅最新价/涨跌幅，无 K 线形态）。
2. **K 线状态 / 预警信号为确定性规则**（均线多头/空头、放量突破、触及支撑、
   窄幅震荡），阈值与 data.quality 的风格一致：注释即规范。
3. **PE/PB 批量快照**走腾讯 qt.gtimg.cn 的多代码合并请求（一次 HTTP 拿全部）。
4. **主力资金净流入汇总**复用 data.realtime.fetch_main_fund_flow（多源冗余，
   进程级缓存 5 分钟），全部失败时置 null 由前端显示 —，不阻塞行情主体。
5. 相关性矩阵：本地日线（优先 hfq 复权价，收益不失真）最近 N 交易日 pct_change().corr()。
"""
from __future__ import annotations

import asyncio
import hashlib
from datetime import date, timedelta
from typing import Any

import fastapi
from loguru import logger

from ...cache.keys import k_watchlist_dashboard
from ...cache.swr import cached_or_build
from ...core.config import get_settings
from ...core.auth import require_role
from ...core.errors import APIResponse, ok
from ...data.parquet_store import (missing_columns, read_symbol_dataset,
                                   today_trade_date_or_last)
from ...domain.a_share_rules import code_to_symbol, is_etf_symbol, symbol_to_code

router = fastapi.APIRouter()
_WATCHLIST_BUDGET_SECONDS = 5.5

# ---------------- 类型判定与分组 ----------------


def is_etf_code(symbol: str) -> bool:
    """510300 / 159915 等纯 6 位数字且前缀匹配 ETF 段。

    审计 B5-10（2026-09-21）修复两处缺陷：
        ① 原前缀表 ``_ETF_PREFIXES[:4]`` = ``("51","56","58","15")`` **漏了 16xxxx**
           （深市 LOF），于是 ``normalize_symbol("160123")`` 会走股票分支，
           由 ``code_to_symbol`` 兜底拼成**并不存在**的 ``160123.SH``；
           同时 ``/watchlist`` 汇总里的 ``etf_count`` 把 16x 基金误计为股票。
        ② 原表达式 ``len==6 and isdigit() and ... or startswith("159")`` 因
           ``and`` 优先级高于 ``or``，使长度/数字校验被**绕过**
           （``"159915.SH"``、``"159abc"`` 均返回 True）。

    现统一调用 :func:`app.domain.a_share_rules.is_etf_symbol`（唯一事实来源）。
    """
    return is_etf_symbol(symbol)


def normalize_symbol(raw: str) -> str:
    """收藏存储形态归一：股票 600519.SH / ETF 510300。"""
    raw = raw.strip()
    if "." in raw:
        return raw
    return raw if is_etf_code(raw) else code_to_symbol(raw)


# ---------------- K 线形态 / 预警（平台量化判定规则） ----------------
def _kline_state(bars: list[dict]) -> tuple[str | None, str | None]:
    """由最近 80 根日线推导 (K线状态, 预警信号)。

    K 线状态判定式（优先级自上而下，首个命中即返回）：
        放量突破   收盘 > 过去20日最高价 且 成交量 ≥ 2×MA(量,5)
        均线空头   收盘 < MA20 且 前一日收盘 ≥ 前一日MA20（破位）；
                   或 MA5<MA10<MA20<MA60 且 收盘 < MA5（空头排列，同徽标）
        触及支撑   最低 ≤ MA20 且 收盘 > MA20 且 |收盘-MA20|/MA20 ≤ 1%
        均线多头   MA5 > MA10 > MA20 > MA60 且 收盘 > MA5
        窄幅震荡   (10日最高 - 10日最低) / 10日最低 < 3%
    预警信号（独立判定）：
        突破MA20   昨收 ≤ 前一日MA20 且 今收 > MA20
        放量异动   成交量 ≥ 2×MA(量,5)
    """
    if len(bars) < 61:
        return None, None
    closes = [b["close"] for b in bars if b.get("close") is not None]
    highs = [b.get("high") or b.get("close") or 0 for b in bars]
    lows = [b.get("low") or b.get("close") or 0 for b in bars]
    vols = [b.get("volume") or 0 for b in bars]
    if len(closes) < 61:
        return None, None
    c, prev = closes[-1], closes[-2]
    ma = lambda n: sum(closes[-n:]) / n                      # noqa: E731
    ma5, ma10, ma20, ma60 = ma(5), ma(10), ma(20), ma(60)
    ma20_prev = sum(closes[-21:-1]) / 20
    vol_last = vols[-1]
    vol_ma5 = sum(vols[-6:-1]) / 5 if any(vols[-6:-1]) else None

    alert = None
    if prev <= ma20_prev < c:
        alert = "突破MA20"
    if vol_ma5 and vol_last >= 2 * vol_ma5:
        alert = "放量异动" if alert is None else f"{alert}+放量异动"

    if vol_ma5 and vol_last >= 2 * vol_ma5 and c > max(highs[-21:-1]):
        return "放量突破", alert
    if (c < ma20 and prev >= ma20_prev) or (ma5 < ma10 < ma20 < ma60 and c < ma5):
        return "均线空头", alert
    if lows[-1] <= ma20 and c > ma20 and ma20 and abs(c - ma20) / ma20 <= 0.01:
        return "触及支撑", alert
    if ma5 > ma10 > ma20 > ma60 and c > ma5:
        return "均线多头", alert
    lo10 = min(lows[-10:])
    if lo10 and (max(highs[-10:]) - lo10) / lo10 < 0.03:
        return "窄幅震荡", alert
    return "平稳", alert


# ---------------- 行情来源 ----------------
def _etf_market(code: str) -> str:
    """ETF 6 位代码 -> 腾讯 K 线市场前缀：5/56/58 开头沪市，15/16 开头深市。"""
    return "sh" if code.startswith(("5", "9")) else "sz"


def _bars_for(symbol: str) -> tuple[list[dict], str | None]:
    """取最近 80 根日线（MA60 判定需要 ≥61 根）。返回 (bars, source)。"""
    if is_etf_code(symbol):
        from ...data.etf import fetch_kline

        try:
            bars = fetch_kline(_etf_market(symbol), symbol, 80)
            if bars:
                return bars[-80:], "tencent-kline"
        except Exception as e:
            logger.warning(f"[watchlist] ETF {symbol} K线获取失败: {e!r}")
        return [], None
    # M3 修复：形态判定优先使用前复权（QFQ）价格 —— 除权跳空若不复权会被
    # 误判为"放量突破/跌破均线"，均线多头排列也必须基于连续价格序列。
    # 只读取形态判定所需列及近一年分区，避免每次看板请求反复扫描全历史宽表。
    start = date.today() - timedelta(days=400)
    columns = ["date", "open", "high", "low", "close", "volume", "amount"]
    df = read_symbol_dataset("daily_bar_qfq", symbol, start=start, columns=columns)
    # [AQP 第 10 轮] 投影读**容忍缺列**（`read_symbol_dataset` 会退化为可用列子集），
    # 原判据只看 `is_empty()` 与 `"date"` ⇒ 缺 `close`/`open` 的分区照样通过，
    # 随后 `df["close"]` 抛 ColumnNotFoundError → **裸 50000**（自选股页整页不可用）。
    # 现在把"必需列齐备"并入同一判据 ⇒ 走既有的外部源回退，并留 warning（绝不静默）。
    if df.is_empty() or missing_columns(df, columns):
        df = read_symbol_dataset("daily_bar", symbol, start=start, columns=columns)
    if df.is_empty() or missing_columns(df, columns):
        logger.warning(f"[watchlist] {symbol} 本地行情缺列或为空"
                       f"（missing={missing_columns(df, columns)}），回退外部源")
        from ...data.etf import fetch_kline

        mp = {"SH": "sh", "SZ": "sz", "BJ": "sh"}.get(symbol.split(".")[-1], "sh")
        try:
            return fetch_kline(mp, symbol_to_code(symbol), 80)[-80:], "tencent-kline"
        except Exception:
            return [], None
    df = df.tail(80).sort("date")
    bars = [{
        "date": str(d)[:10], "open": o, "high": h, "low": lo, "close": c,
        "volume": v, "amount": a,
    } for d, o, h, lo, c, v, a in zip(
        df["date"].to_list(), df["open"].to_list(), df["high"].to_list(),
        df["low"].to_list(), df["close"].to_list(), df["volume"].to_list(),
        df["amount"].to_list())]
    return bars, "local-parquet"


def _load_instrument_names() -> dict[str, str]:
    """instrument 表 symbol -> name（只读 SQLite，一次全量入内存）。"""
    import sqlite3

    try:
        db = get_settings().SQLITE_PATH
        with sqlite3.connect(f"file:{db}?mode=ro", uri=True, timeout=30) as conn:
            rows = conn.execute("SELECT symbol, name FROM instrument").fetchall()
        return dict(rows)
    except Exception:
        return {}


def _batch_valuation(symbols: list[str]) -> dict[str, dict[str, Any]]:
    """腾讯行情批量快照（一次 HTTP，股票与 ETF 通用）。

    返回 {symbol: {name, price, pct, pe?, pb?}}；PE/PB 仅股票有意义。
    """
    if not symbols:
        return {}
    from ...data.realtime import _request, tencent_code

    codes = ",".join(tencent_code(s) for s in symbols)
    out: dict[str, dict[str, Any]] = {}
    try:
        text = _request(
            "GET", f"https://qt.gtimg.cn/q={codes}", encoding="gbk",
            retries=1, timeout=3.0,
        )
        code_by_tencent = {tencent_code(s): s for s in symbols}
        for chunk in text.replace(";", "").split("v_"):
            if '="' not in chunk:
                continue
            var, body = chunk.split('="', 1)
            body = body.rstrip('"\n')
            if "~" not in body:
                continue
            f = body.split("~")
            if len(f) < 47 or not f[3]:
                continue
            sym = code_by_tencent.get(var.strip())
            if sym is None:
                continue
            def _f(x: str) -> float | None:   # noqa: E306
                try:
                    return float(x)
                except (TypeError, ValueError):
                    return None
            out[sym] = {"name": f[1] or None, "price": _f(f[3]), "pct": _f(f[32]),
                        "pe": _f(f[39]), "pb": _f(f[46])}
    except Exception as e:
        logger.warning(f"[watchlist] 腾讯批量快照失败: {e!r}")
    return out


def _fund_flow_one_fast(symbol: str) -> float | None:
    """单标的资金流快速路径：仅调用东财轻量接口，一次、3 秒预算。"""
    from ...data.realtime import _request, em_secid

    data = _request(
        "GET", "https://push2delay.eastmoney.com/api/qt/stock/fflow/daykline/get",
        params={
            "lmt": "1", "klt": "101", "secid": em_secid(symbol),
            "fields1": "f1,f2,f3,f7",
            "fields2": "f51,f52,f53,f54,f55,f56,f57,f58,f59,f60,f61,f62,f63,f64,f65",
            "ut": "b2884a393a59ad64002292a3e90d46a5",
        },
        retries=1,
        timeout=3.0,
    )
    rows = ((data or {}).get("data") or {}).get("klines") or []
    if not rows:
        return None
    try:
        return float(rows[-1].split(",")[1])
    except (IndexError, TypeError, ValueError):
        return None


async def _fund_flow_total(stocks: list[str]) -> tuple[float | None, str]:
    """受限并发汇总资金流；超大自选集合不做 N 次外部请求而明确降级。"""
    if not stocks:
        return None, "unavailable"
    # 单代码接口无法在 6 秒内可靠覆盖超大列表；宁可声明不可用，也不返回伪造的部分合计。
    if len(stocks) > 12:
        return None, "unavailable"
    semaphore = asyncio.Semaphore(4)

    async def _one(symbol: str) -> float | None:
        async with semaphore:
            try:
                return await asyncio.to_thread(_fund_flow_one_fast, symbol)
            except Exception:
                return None

    values = await asyncio.gather(*(_one(symbol) for symbol in stocks))
    usable = [value for value in values if value is not None]
    if not usable:
        return None, "unavailable"
    status = "ok" if len(usable) == len(stocks) else "degraded"
    return round(sum(usable) / 1e8, 2), status


# ---------------- 端点 ----------------
@router.get("/dashboard")
async def dashboard(
    symbols: str = fastapi.Query(..., min_length=1, max_length=4000),
    _user: dict = fastapi.Depends(require_role("viewer")),
) -> APIResponse[dict]:
    """自选看板：用户隔离 SWR 缓存 + 受限并发本地读取和外部快照。"""
    raw = list(dict.fromkeys(s.strip() for s in symbols.split(",") if s.strip()))[:100]
    syms = list(dict.fromkeys(normalize_symbol(s) for s in raw))
    username = str(_user.get("username") or "anonymous")
    data_date = today_trade_date_or_last().strftime("%Y%m%d")
    symbols_key = hashlib.sha256(",".join(syms).encode("utf-8")).hexdigest()[:20]
    cache_key = k_watchlist_dashboard(username, data_date, symbols_key)

    async def _build() -> dict[str, Any]:
        semaphore = asyncio.Semaphore(8)

        def _one(sym: str) -> dict[str, Any]:
            bars, source = _bars_for(sym)
            close = pct = None
            amount_yi = None
            if bars:
                last = bars[-1]
                close = last.get("close")
                if len(bars) >= 2 and bars[-2].get("close") and close is not None:
                    pct = round((close / bars[-2]["close"] - 1) * 100, 2)
                if close:
                    amt = last.get("amount")
                    if amt is None:
                        amt = (last.get("volume") or 0) * 100 * close
                    amount_yi = round(amt / 1e8, 2)
            state, alert = _kline_state(bars)
            etf = is_etf_code(sym)
            return {
                "symbol": sym, "code": symbol_to_code(sym),
                "type": "etf" if etf else "stock", "name": None,
                "close": close, "pct": pct, "amount_yi": amount_yi,
                "kline_state": state, "alert": alert,
                "date": bars[-1]["date"] if bars else None,
                "closes": [b.get("close") for b in bars[-30:]], "source": source,
            }

        async def _one_limited(sym: str) -> dict[str, Any]:
            async with semaphore:
                return await asyncio.to_thread(_one, sym)

        items = list(await asyncio.gather(*(_one_limited(sym) for sym in syms)))
        names_task = asyncio.to_thread(_load_instrument_names)
        valuation_task = asyncio.to_thread(_batch_valuation, syms)
        stocks = [item["symbol"] for item in items if item["type"] == "stock"]
        names, valuation, flow_result = await asyncio.gather(
            names_task, valuation_task, _fund_flow_total(stocks))
        flow_total, flow_status = flow_result
        # 不在此处拉全量 ETF 目录；快照名称或 instrument 表足以满足看板。
        for item in items:
            valuation_item = valuation.get(item["symbol"]) or {}
            item["name"] = valuation_item.get("name") or names.get(item["symbol"])
            if item["type"] == "stock":
                item["pe"] = valuation_item.get("pe")
                item["pb"] = valuation_item.get("pb")
                if item["close"] is None and valuation_item.get("price"):
                    item["close"], item["pct"] = (
                        valuation_item["price"], valuation_item.get("pct"))
            else:
                item["pe"] = item["pb"] = None

        pcts = [item["pct"] for item in items if item["pct"] is not None]
        alerts = [(item["symbol"], item["alert"]) for item in items if item["alert"]]
        return {
            "summary": {
                "count": len(items),
                "stock_count": len(stocks),
                "etf_count": sum(1 for item in items if item["type"] == "etf"),
                "avg_pct": round(sum(pcts) / len(pcts), 2) if pcts else None,
                "flow_total_yi": flow_total,
                "flow_status": flow_status,
                "alert_count": len(alerts),
                "alert_kinds": sorted({alert.split("+")[0] for _, alert in alerts}),
                "quote_date": next((item["date"] for item in items if item["date"]), None),
            },
            "items": items,
        }

    async def _build_with_budget() -> dict[str, Any]:
        try:
            return await asyncio.wait_for(
                _build(), timeout=_WATCHLIST_BUDGET_SECONDS)
        except TimeoutError:
            logger.warning(
                f"[watchlist] dashboard budget exceeded: user={username} count={len(syms)}")
            # 不伪造行情：保留请求标的身份，所有市场字段明确 unavailable。
            return {
                "status": "degraded",
                "reason": "部分数据源响应超时",
                "summary": {
                    "count": len(syms), "stock_count": sum(
                        1 for symbol in syms if not is_etf_code(symbol)),
                    "etf_count": sum(1 for symbol in syms if is_etf_code(symbol)),
                    "avg_pct": None, "flow_total_yi": None,
                    "flow_status": "unavailable", "alert_count": 0,
                    "alert_kinds": [], "quote_date": None,
                },
                "items": [
                    {
                        "symbol": symbol, "code": symbol_to_code(symbol),
                        "type": "etf" if is_etf_code(symbol) else "stock",
                        "name": None, "close": None, "pct": None,
                        "amount_yi": None, "kline_state": None, "alert": None,
                        "date": None, "closes": [], "source": None,
                        "pe": None, "pb": None, "status": "unavailable",
                    }
                    for symbol in syms
                ],
            }

    result = await cached_or_build(
        cache_key, _build_with_budget, ttl=60, stale_window=300,
        rebuild_lock_ttl=15)
    return ok(result)


@router.get("/correlation")
async def correlation(
    symbols: str = fastapi.Query(..., min_length=3, max_length=400),
    days: int = fastapi.Query(60, ge=20, le=250),
    _user: dict = fastapi.Depends(require_role("viewer")),
) -> APIResponse[dict]:
    """自选标的最近 N 交易日收益率相关系数矩阵（本地复权日线，ETF 走腾讯）。"""

    def _calc() -> dict:
        import pandas as pd

        raw = list(dict.fromkeys(s.strip() for s in symbols.split(",") if s.strip()))[:30]
        syms = [normalize_symbol(s) for s in raw]
        closes: dict[str, pd.Series] = {}
        for s in syms:
            bars, _ = _bars_for(s)
            if len(bars) >= 10:
                closes[s] = pd.Series(
                    [b.get("close") for b in bars[-days:]],
                    index=[b["date"] for b in bars[-days:]])
        if len(closes) < 2:
            return {"symbols": [], "matrix": [], "days": days}
        df = pd.DataFrame(closes).sort_index().dropna(how="all")
        corr = df.pct_change().corr(min_periods=20)
        syms_out = list(corr.columns)
        matrix = [[None if corr.isna().iat[i, j] else round(float(corr.iat[i, j]), 3)
                   for j in range(len(syms_out))] for i in range(len(syms_out))]
        return {"symbols": syms_out, "matrix": matrix, "days": days}

    return ok(await asyncio.to_thread(_calc))
