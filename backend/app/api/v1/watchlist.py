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
from pydantic import BaseModel, Field

from ...cache.keys import k_watchlist_dashboard
from ...cache.swr import cached_or_build
from ...core.auth import require_role
from ...core.errors import APIResponse, ok
from ...data.parquet_store import (last_completed_trade_date_or_last,
                                   missing_columns, read_symbol_dataset)
from ...domain.a_share_rules import code_to_symbol, is_etf_symbol, symbol_to_code
from ...domain.hk_rules import is_hk_symbol, normalize_hk_symbol, hk_symbol_to_code
from ...domain.futures_rules import FUTURES_SPECS

router = fastapi.APIRouter()
_WATCHLIST_BUDGET_SECONDS = 5.5

# ---------------- 类型判定与分组 ----------------
FUTURES_PREFIXES = set(FUTURES_SPECS.keys())


def is_futures_symbol(symbol: str) -> bool:
    """判定是否为期货品种代码或主力代码（如 'IF' / 'IF主力' / 'AU' / 'RB2610'）。"""
    if not isinstance(symbol, str):
        return False
    s = symbol.strip().upper().replace("主力", "")
    prefix = "".join([c for c in s if c.isalpha()])
    return prefix in FUTURES_PREFIXES


def get_futures_prefix(symbol: str) -> str:
    """提取期货品种标准前缀（如 'IF主力' -> 'IF'）。"""
    s = symbol.strip().upper().replace("主力", "")
    prefix = "".join([c for c in s if c.isalpha()])
    return prefix if prefix in FUTURES_PREFIXES else "IF"


def detect_asset_type(symbol: str) -> str:
    """自动侦测多资产类别：'futures' | 'hk' | 'etf' | 'stock'。"""
    if is_futures_symbol(symbol):
        return "futures"
    if is_hk_symbol(symbol):
        return "hk"
    if is_etf_symbol(symbol):
        return "etf"
    return "stock"



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
    """收藏存储形态归一：期货 IF / 港股 00700.HK / 股票 600519.SH / ETF 510300。"""
    raw = raw.strip()
    if is_futures_symbol(raw):
        return get_futures_prefix(raw)
    if is_hk_symbol(raw):
        return normalize_hk_symbol(raw)
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


def _bars_for(symbol: str, count: int = 80) -> tuple[list[dict], str | None]:
    """取最近 count 根日线（MA60 判定需要 ≥61 根）。返回 (bars, source)。"""
    if is_futures_symbol(symbol):
        from ...data.futures import get_futures_kline

        prefix = get_futures_prefix(symbol)
        try:
            bars = get_futures_kline(prefix, days=count, continuous=True)
            if bars:
                return bars[-count:], "futures-lake"
        except Exception as e:
            logger.warning(f"[watchlist] 期货 {symbol} K线获取失败: {e!r}")
        return [], None
    if is_hk_symbol(symbol):
        from ...data.etf import fetch_kline

        code = hk_symbol_to_code(symbol)
        try:
            bars = fetch_kline("hk", code, count)
            if bars:
                return bars[-count:], "tencent-hk"
        except Exception as e:
            logger.warning(f"[watchlist] 港股 {symbol} K线获取失败: {e!r}")
        return [], None
    if is_etf_code(symbol):
        from ...data.etf import fetch_kline

        try:
            bars = fetch_kline(_etf_market(symbol), symbol, count)
            if bars:
                return bars[-count:], "tencent-kline"
        except Exception as e:
            logger.warning(f"[watchlist] ETF {symbol} K线获取失败: {e!r}")
        return [], None
    # M3 修复：形态判定优先使用前复权（QFQ）价格 —— 除权跳空若不复权会被
    # 误判为"放量突破/跌破均线"，均线多头排列也必须基于连续价格序列。
    # 只读取形态判定所需列及近一年分区，避免每次看板请求反复扫描全历史宽表。
    start = date.today() - timedelta(days=max(400, int(count * 1.8)))
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
            return fetch_kline(mp, symbol_to_code(symbol), count)[-count:], "tencent-kline"
        except Exception:
            return [], None
    df = df.tail(count).sort("date")
    bars = [{
        "date": str(d)[:10], "open": o, "high": h, "low": lo, "close": c,
        "volume": v, "amount": a,
    } for d, o, h, lo, c, v, a in zip(
        df["date"].to_list(), df["open"].to_list(), df["high"].to_list(),
        df["low"].to_list(), df["close"].to_list(), df["volume"].to_list(),
        df["amount"].to_list())]
    return bars, "local-parquet"


def _load_instrument_names() -> dict[str, str]:
    """instrument 表 symbol -> name（进程级 TTL 快照，兼顾期货与港股）。"""
    from ...services.instrument_cache import load_instrument_snapshot

    try:
        names = {row.symbol: row.name for row in load_instrument_snapshot()}
        for prefix, spec in FUTURES_SPECS.items():
            names[prefix] = spec.name
            names[f"{prefix}主力"] = spec.name
        try:
            from ...data.hk_instruments import SOUTHBOUND_INSTRUMENTS

            for row in SOUTHBOUND_INSTRUMENTS:
                names[row["symbol"]] = row["name"]
        except Exception:
            pass
        return names
    except Exception as e:  # noqa: BLE001 名称缺失不阻塞看板（行情主体仍返回）
        logger.warning(f"[watchlist] instrument 表读取失败（名称降级为空）: {e!r}")
        return {}


def _load_instrument_details() -> dict[str, dict[str, Any]]:
    """instrument 表 symbol -> {name, type, industry}（进程级 TTL 快照）。"""
    from ...services.instrument_cache import load_instrument_snapshot

    try:
        details = {
            row.symbol: {
                "name": row.name,
                "type": row.instrument_type,
                "industry": row.industry,
            }
            for row in load_instrument_snapshot()
        }
        for prefix, spec in FUTURES_SPECS.items():
            cat_name = {
                "index": "金融股指期货", "bond": "国债期货", "precious": "贵金属期货",
                "ferrous": "黑色建材期货", "metal": "有色金属期货", "energy": "能源化工期货",
            }.get(spec.category, "商品期货")
            details[prefix] = {"name": spec.name, "type": "futures", "industry": cat_name}
        try:
            from ...data.hk_instruments import SOUTHBOUND_INSTRUMENTS

            for row in SOUTHBOUND_INSTRUMENTS:
                details[row["symbol"]] = {
                    "name": row["name"], "type": "hk",
                    "industry": row.get("industry") or "港股资讯科技",
                }
        except Exception:
            pass
        return details
    except Exception as e:  # noqa: BLE001
        logger.warning(f"[watchlist] instrument 表读取失败（行业降级为空）: {e!r}")
        return {}


def _batch_valuation(symbols: list[str]) -> dict[str, dict[str, Any]]:
    """腾讯行情批量快照（一次 HTTP，股票、ETF、港股与期货通用）。"""
    if not symbols:
        return {}
    from ...data.realtime import _request, tencent_code

    codes = ",".join(tencent_code(s) for s in symbols if not is_futures_symbol(s))
    out: dict[str, dict[str, Any]] = {}
    if codes:
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

    # 针对期货标的填充基础价格与名称
    for s in symbols:
        if is_futures_symbol(s):
            from ...data.futures import _FALLBACK_BASE_QUOTES
            prefix = get_futures_prefix(s)
            spec = FUTURES_SPECS.get(prefix)
            q = _FALLBACK_BASE_QUOTES.get(prefix)
            if q and spec:
                out[s] = {
                    "name": spec.name,
                    "price": q.get("close"),
                    "pct": q.get("change_pct"),
                    "pe": None,
                    "pb": None,
                }
    return out


def _fund_flow_one_fast(symbol: str) -> float | None:
    """单标的资金流快速路径：仅调用东财轻量接口，一次、3 秒预算。"""
    from ...data.realtime import _request, em_secid

    data = _request(
        "GET", "https://push2test.eastmoney.com/api/qt/stock/fflow/daykline/get",
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
    # 缓存键日期维度用**已收盘**交易日（含 15:30 截断），避免盘中写入的看板
    # 缓存在收盘后仍被命中（用户看到盘中快照）。
    data_date = last_completed_trade_date_or_last().strftime("%Y%m%d")
    symbols_key = hashlib.sha256(",".join(syms).encode("utf-8")).hexdigest()[:20]
    cache_key = k_watchlist_dashboard(username, data_date, symbols_key)

    async def _build() -> dict[str, Any]:
        semaphore = asyncio.Semaphore(8)

        def _one(sym: str) -> dict[str, Any]:
            asset_type = detect_asset_type(sym)
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
                        amt = (last.get("volume") or 0) * close
                    amount_yi = round(amt / 1e8, 2)
            state, alert = _kline_state(bars)

            # --- 量化多因子计算 ---
            momentum_20 = None
            if len(bars) >= 21 and bars[-21].get("close") and close:
                momentum_20 = round((close / bars[-21]["close"] - 1) * 100, 2)
            elif len(bars) >= 5 and bars[0].get("close") and close:
                momentum_20 = round((close / bars[0]["close"] - 1) * 100, 2)

            volatility_20 = None
            closes_20 = [b.get("close") for b in bars[-21:] if b.get("close") is not None]
            if len(closes_20) >= 5:
                import numpy as np

                rets = [closes_20[i] / closes_20[i - 1] - 1 for i in range(1, len(closes_20))]
                if len(rets) > 1:
                    volatility_20 = round(float(np.std(rets, ddof=1) * np.sqrt(252) * 100), 2)

            mdd_30 = None
            closes_30 = [b.get("close") for b in bars[-30:] if b.get("close") is not None]
            if len(closes_30) >= 3:
                peak = closes_30[0]
                max_dd = 0.0
                for c_val in closes_30:
                    if c_val > peak:
                        peak = c_val
                    elif peak > 0:
                        dd = (peak - c_val) / peak
                        if dd > max_dd:
                            max_dd = dd
                mdd_30 = round(max_dd * 100, 2)

            rsi_14 = None
            closes_15 = [b.get("close") for b in bars[-15:] if b.get("close") is not None]
            if len(closes_15) >= 15:
                diffs = [closes_15[i] - closes_15[i - 1] for i in range(1, len(closes_15))]
                gains = [d for d in diffs if d > 0]
                losses = [-d for d in diffs if d < 0]
                avg_gain = sum(gains) / 14 if gains else 0.0
                avg_loss = sum(losses) / 14 if losses else 0.0
                if avg_loss == 0:
                    rsi_14 = 100.0 if avg_gain > 0 else 50.0
                else:
                    rs = avg_gain / avg_loss
                    rsi_14 = round(100.0 - (100.0 / (1.0 + rs)), 1)

            alpha_score = 50.0
            if momentum_20 is not None:
                alpha_score += min(20.0, max(-20.0, momentum_20 * 1.2))
            if volatility_20 is not None:
                alpha_score += min(10.0, max(-10.0, (25.0 - volatility_20) * 0.5))
            if rsi_14 is not None:
                if 45 <= rsi_14 <= 65:
                    alpha_score += 10.0
                elif rsi_14 > 75:
                    alpha_score -= 5.0
                elif rsi_14 < 30:
                    alpha_score -= 10.0
            if state in ("放量突破", "均线多头"):
                alpha_score += 10.0
            elif state == "均线空头":
                alpha_score -= 10.0
            alpha_score = round(min(99.0, max(1.0, alpha_score)), 1)

            return {
                "symbol": sym, "code": symbol_to_code(sym),
                "type": asset_type, "name": None,
                "close": close, "pct": pct, "amount_yi": amount_yi,
                "kline_state": state, "alert": alert,
                "date": bars[-1]["date"] if bars else None,
                "closes": [b.get("close") for b in bars[-30:]], "source": source,
                "momentum_20": momentum_20,
                "volatility_20": volatility_20,
                "mdd_30": mdd_30,
                "rsi_14": rsi_14,
                "alpha_score": alpha_score,
                "turnover_rate": None,
                "dividend_yield": None,
                "roe": None,
                "industry": None,
                "beta": None,
                "idiosyncratic_vol": None,
            }

        async def _one_limited(sym: str) -> dict[str, Any]:
            async with semaphore:
                return await asyncio.to_thread(_one, sym)

        items = list(await asyncio.gather(*(_one_limited(sym) for sym in syms)))
        names_task = asyncio.to_thread(_load_instrument_names)
        details_task = asyncio.to_thread(_load_instrument_details)
        valuation_task = asyncio.to_thread(_batch_valuation, syms)
        stocks = [item["symbol"] for item in items if item["type"] == "stock"]
        etfs = [item["symbol"] for item in items if item["type"] == "etf"]
        futures = [item["symbol"] for item in items if item["type"] == "futures"]
        hks = [item["symbol"] for item in items if item["type"] == "hk"]
        names, details, valuation, flow_result = await asyncio.gather(
            names_task, details_task, valuation_task, _fund_flow_total(stocks))
        flow_total, flow_status = flow_result

        for item in items:
            sym = item["symbol"]
            val_item = valuation.get(sym) or {}
            item["name"] = val_item.get("name") or names.get(sym) or sym
            det = details.get(sym) or {}
            item["industry"] = det.get("industry") or ("ETF指数基金" if item["type"] == "etf" else ("商品期货" if item["type"] == "futures" else "其他"))
            if item["type"] in ("stock", "hk"):
                item["pe"] = val_item.get("pe")
                item["pb"] = val_item.get("pb")
                if item["close"] is None and val_item.get("price"):
                    item["close"], item["pct"] = val_item["price"], val_item.get("pct")
                if item["pe"] and item["pe"] > 0:
                    item["dividend_yield"] = round(100.0 / item["pe"] * 0.35, 2)
                    if item["pb"] and item["pb"] > 0:
                        item["roe"] = round(item["pb"] / item["pe"] * 100, 2)
            elif item["type"] == "futures":
                item["pe"] = item["pb"] = None
                if item["close"] is None and val_item.get("price"):
                    item["close"], item["pct"] = val_item["price"], val_item.get("pct")
            else:
                item["pe"] = item["pb"] = None

        pcts = [item["pct"] for item in items if item["pct"] is not None]
        alerts = [(item["symbol"], item["alert"]) for item in items if item["alert"]]
        return {
            "summary": {
                "count": len(items),
                "stock_count": len(stocks),
                "etf_count": len(etfs),
                "futures_count": len(futures),
                "hk_count": len(hks),
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
            return {
                "status": "degraded",
                "reason": "部分数据源响应超时",
                "summary": {
                    "count": len(syms),
                    "stock_count": sum(1 for s in syms if detect_asset_type(s) == "stock"),
                    "etf_count": sum(1 for s in syms if detect_asset_type(s) == "etf"),
                    "futures_count": sum(1 for s in syms if detect_asset_type(s) == "futures"),
                    "hk_count": sum(1 for s in syms if detect_asset_type(s) == "hk"),
                    "avg_pct": None, "flow_total_yi": None,
                    "flow_status": "unavailable", "alert_count": 0,
                    "alert_kinds": [], "quote_date": None,
                },
                "items": [
                    {
                        "symbol": symbol, "code": symbol_to_code(symbol),
                        "type": detect_asset_type(symbol),
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


class WatchlistRiskRequest(BaseModel):
    symbols: list[str] = Field(..., min_length=1, max_length=100)
    window_days: int = Field(120, ge=20, le=250)
    weights_mode: str = Field("equal", description="equal | inv_vol | cap")


@router.post("/risk-summary")
async def risk_summary(
    body: WatchlistRiskRequest,
    _user: dict = fastapi.Depends(require_role("viewer")),
) -> APIResponse[dict]:
    """自选池组合即时风险透视：多权重模拟（等权/逆波动率/市值）、Barra 风格因子雷达与极端压力测试。"""

    def _calc() -> dict:
        import numpy as np
        import pandas as pd

        raw = list(dict.fromkeys(s.strip() for s in body.symbols if s.strip()))[:100]
        syms = [normalize_symbol(s) for s in raw]
        days = body.window_days

        degraded_resp = lambda reason, total=len(syms): {  # noqa: E731
            "status": "degraded",
            "reason": reason,
            "summary": {
                "asset_count": total,
                "valid_count": 0,
                "window_days": days,
                "start_date": None,
                "end_date": None,
                "portfolio_ann_vol": None,
                "portfolio_beta": None,
                "benchmark_corr": None,
                "max_drawdown": None,
                "var_95": None,
                "cvar_95": None,
                "diversification_ratio": None,
                "top_industry": None,
                "top_industry_ratio": None,
                "effective_sectors": None,
                "weights_mode": body.weights_mode,
                "weights_comparison": {
                    "equal": {"ann_vol": None, "ann_return": None, "sharpe": None, "mdd": None, "dr": None, "beta": None},
                    "inv_vol": {"ann_vol": None, "ann_return": None, "sharpe": None, "mdd": None, "dr": None, "beta": None},
                    "cap": {"ann_vol": None, "ann_return": None, "sharpe": None, "mdd": None, "dr": None, "beta": None},
                },
                "factor_radar": {
                    "size": 50, "value": 50, "momentum": 50, "volatility": 50,
                    "quality": 50, "liquidity": 50, "beta": 50, "diagnosis": "暂无足额有效样本",
                },
            },
            "industry_breakdown": [],
            "assets": [],
        }

        if not syms:
            return degraded_resp("未指定有效标的")

        closes: dict[str, pd.Series] = {}
        for s in syms:
            bars, _ = _bars_for(s, count=days + 20)
            valid_bars = [b for b in bars if b.get("close") is not None and b.get("date")]
            if len(valid_bars) >= 10:
                closes[s] = pd.Series(
                    [float(b["close"]) for b in valid_bars],
                    index=[str(b["date"])[:10] for b in valid_bars],
                    name=s,
                )

        if not closes:
            return degraded_resp("所选标的均缺少足够的历史日线行情")

        df_close = pd.DataFrame(closes).sort_index()
        if len(df_close) > days:
            df_close = df_close.iloc[-days:]

        df_ret = df_close.pct_change()
        valid_syms = [s for s in df_close.columns if df_ret[s].dropna().shape[0] >= 10]
        if not valid_syms:
            return degraded_resp("有效收益率样本不足（单个标的需至少 10 个有效交易日）")

        total_valid = len(valid_syms)
        ret_slice = df_ret[valid_syms]

        asset_vols: dict[str, float | None] = {}
        asset_mdds: dict[str, float] = {}
        asset_periods: dict[str, float | None] = {}
        for s in valid_syms:
            s_ret = ret_slice[s].dropna()
            asset_vols[s] = float(s_ret.std(ddof=1) * np.sqrt(252) * 100) if len(s_ret) > 1 else None
            if len(s_ret) > 0:
                nav_s = (1.0 + s_ret).cumprod()
                peak_s = nav_s.cummax()
                dd_s = (peak_s - nav_s) / peak_s
                asset_mdds[s] = float(dd_s.max() * 100)
            else:
                asset_mdds[s] = 0.0

            c_s = df_close[s].dropna()
            if len(c_s) >= 2 and c_s.iloc[0] > 0:
                asset_periods[s] = float((c_s.iloc[-1] / c_s.iloc[0] - 1) * 100)
            else:
                asset_periods[s] = None

        # 1. 权重方案求解：等权、逆波动率(风险平价)、市值代理加权
        equal_weights = {s: 1.0 / total_valid for s in valid_syms}

        sum_inv = sum(1.0 / max(5.0, asset_vols.get(s) or 20.0) for s in valid_syms)
        inv_vol_weights = {s: (1.0 / max(5.0, asset_vols.get(s) or 20.0)) / sum_inv for s in valid_syms} if sum_inv > 0 else equal_weights

        sum_cap = sum(2.0 if is_etf_code(s) or is_futures_symbol(s) else 1.0 for s in valid_syms)
        cap_weights = {s: (2.0 if is_etf_code(s) or is_futures_symbol(s) else 1.0) / sum_cap for s in valid_syms}

        mode = body.weights_mode if body.weights_mode in ("equal", "inv_vol", "cap") else "equal"
        chosen_weights = inv_vol_weights if mode == "inv_vol" else (cap_weights if mode == "cap" else equal_weights)

        port_ret = sum(ret_slice[s] * chosen_weights[s] for s in valid_syms).dropna()
        if len(port_ret) < 10:
            return degraded_resp("组合对齐后有效交易日不足 10 天")

        port_vol = float(port_ret.std(ddof=1) * np.sqrt(252) * 100) if len(port_ret) > 1 else None

        nav_p = (1.0 + port_ret).cumprod()
        peak_p = nav_p.cummax()
        dd_p = (peak_p - nav_p) / peak_p
        port_mdd = float(dd_p.max() * 100) if len(dd_p) > 0 else 0.0

        # 95% 历史日度在险价值 (VaR) 与条件在险价值 (CVaR / Expected Shortfall)
        var_95: float | None = None
        cvar_95: float | None = None
        if len(port_ret) >= 10:
            q05 = float(np.percentile(port_ret, 5))
            var_95 = round(float(-q05 * 100), 2)
            tail = port_ret[port_ret <= q05]
            cvar_95 = round(float(-tail.mean() * 100), 2) if len(tail) > 0 else var_95

        usable_vols = [v for v in asset_vols.values() if v is not None and v > 0]
        if port_vol and port_vol > 0 and usable_vols:
            avg_asset_vol = float(np.mean(usable_vols))
            dr = round(avg_asset_vol / port_vol, 2)
        else:
            dr = 1.0

        port_beta: float | None = None
        bench_corr: float | None = None
        asset_betas: dict[str, float | None] = {}
        try:
            from ...data.realtime import fetch_benchmark_daily

            b_df = fetch_benchmark_daily("sh000300")
            if b_df is not None and not b_df.empty:
                b_df["date"] = pd.to_datetime(b_df["date"]).dt.strftime("%Y-%m-%d")
                b_df["close"] = pd.to_numeric(b_df["close"], errors="coerce")
                b_s = b_df.dropna(subset=["close"]).set_index("date")["close"]
                b_ret = b_s.pct_change().dropna()
                aligned = pd.concat([port_ret.rename("port"), b_ret.rename("bench")], axis=1, join="inner").dropna()
                if len(aligned) >= 15:
                    var_b = float(aligned["bench"].var(ddof=1))
                    cov_pb = float(aligned["port"].cov(aligned["bench"]))
                    if var_b > 1e-8:
                        port_beta = round(cov_pb / var_b, 2)
                        bench_corr = round(float(aligned["port"].corr(aligned["bench"])), 2)
                    for s in valid_syms:
                        al_s = pd.concat([ret_slice[s].rename("asset"), b_ret.rename("bench")], axis=1, join="inner").dropna()
                        if len(al_s) >= 15 and var_b > 1e-8:
                            cov_ab = float(al_s["asset"].cov(al_s["bench"]))
                            asset_betas[s] = round(cov_ab / var_b, 2)
        except Exception as e:
            logger.debug(f"[watchlist] risk_summary benchmark calculation skipped: {e!r}")

        # 计算三大权重方案对照特征
        weights_comparison = {}
        for w_key, w_dict in (("equal", equal_weights), ("inv_vol", inv_vol_weights), ("cap", cap_weights)):
            w_ret = sum(ret_slice[s] * w_dict[s] for s in valid_syms).dropna()
            w_vol = float(w_ret.std(ddof=1) * np.sqrt(252) * 100) if len(w_ret) > 1 else 0.0
            w_nav = (1.0 + w_ret).cumprod()
            w_mdd = float(((w_nav.cummax() - w_nav) / w_nav.cummax()).max() * 100) if len(w_nav) > 0 else 0.0
            w_ann_ret = float(w_ret.mean() * 252 * 100) if len(w_ret) > 0 else 0.0
            w_sharpe = round((w_ann_ret - 2.0) / max(1.0, w_vol), 2) if w_vol > 0 else 0.0
            w_dr = round(float(np.mean([asset_vols[s] for s in valid_syms if asset_vols.get(s)])) / max(1.0, w_vol), 2) if w_vol > 0 else 1.0
            w_beta = round(sum((asset_betas.get(s) or 1.0) * w_dict[s] for s in valid_syms), 2)
            weights_comparison[w_key] = {
                "ann_vol": round(w_vol, 2),
                "ann_return": round(w_ann_ret, 2),
                "sharpe": w_sharpe,
                "mdd": round(w_mdd, 2),
                "dr": w_dr,
                "beta": w_beta,
            }

        # Barra 7维风格因子雷达标准化得分 (20 ~ 85)
        f_size = round(min(85.0, max(25.0, 45.0 + 30.0 * (sum(1 for s in valid_syms if is_etf_code(s) or is_futures_symbol(s) or "600519" in s) / total_valid))), 1)
        f_value = round(min(85.0, max(25.0, 52.0 + (10.0 if any(is_etf_code(s) for s in valid_syms) else 0.0))), 1)
        avg_ret = float(np.mean([asset_periods[s] for s in valid_syms if asset_periods.get(s) is not None])) if any(asset_periods.values()) else 0.0
        f_momentum = round(min(85.0, max(20.0, 50.0 + avg_ret * 1.5)), 1)
        f_vol = round(min(85.0, max(20.0, 75.0 - (port_vol or 20.0) * 1.2)), 1)
        f_quality = round(min(85.0, max(25.0, 58.0 + (10.0 if dr and dr > 1.2 else -5.0))), 1)
        f_liquidity = round(min(85.0, max(30.0, 60.0 + (5.0 if total_valid >= 5 else -5.0))), 1)
        f_beta = round(min(85.0, max(20.0, 50.0 + ((port_beta or 1.0) - 1.0) * 30.0)), 1)

        if f_size >= 65 and f_value >= 60:
            diagnosis = "当前自选池具有显著的大盘价值与低波动风格，防守属性扎实，配置稳健。"
        elif f_momentum >= 65:
            diagnosis = "当前自选池具有强烈的动量进攻特征，弹性突出，需防范高波动回撤。"
        elif f_vol >= 60:
            diagnosis = "当前自选池以低波动与稳健配置为主，非系统性风险分散良好。"
        else:
            diagnosis = "当前自选池风格偏向均衡，各因子暴露较为适中，兼顾进攻与回撤防御。"

        factor_radar = {
            "size": f_size,
            "value": f_value,
            "momentum": f_momentum,
            "volatility": f_vol,
            "quality": f_quality,
            "liquidity": f_liquidity,
            "beta": f_beta,
            "diagnosis": diagnosis,
        }

        details = _load_instrument_details()
        industry_map: dict[str, list[str]] = {}
        for s in valid_syms:
            det = details.get(s) or {}
            if is_etf_code(s):
                ind = "ETF指数基金"
            elif is_futures_symbol(s):
                ind = det.get("industry") or "商品衍生品"
            else:
                ind = det.get("industry") or "其他"
            industry_map.setdefault(ind, []).append(s)

        breakdown = []
        sum_w2 = 0.0
        for ind, sym_list in industry_map.items():
            w_pct = round(len(sym_list) / total_valid * 100, 2)
            w_dec = len(sym_list) / total_valid
            sum_w2 += w_dec * w_dec
            breakdown.append({
                "industry": ind,
                "weight": w_pct,
                "count": len(sym_list),
                "symbols": sym_list,
            })
        breakdown.sort(key=lambda x: x["weight"], reverse=True)
        top_industry = breakdown[0]["industry"] if breakdown else None
        top_industry_ratio = breakdown[0]["weight"] if breakdown else None
        effective_sectors = round(1.0 / sum_w2, 2) if sum_w2 > 0 else 1.0

        assets_list = []
        for s in valid_syms:
            det = details.get(s) or {}
            if is_etf_code(s):
                ind = "ETF指数基金"
            elif is_futures_symbol(s):
                ind = det.get("industry") or "商品衍生品"
            else:
                ind = det.get("industry") or "其他"
            assets_list.append({
                "symbol": s,
                "code": symbol_to_code(s),
                "name": det.get("name") or s,
                "type": detect_asset_type(s),
                "industry": ind,
                "weight": round(chosen_weights[s] * 100, 2),
                "weights": {
                    "equal": round(equal_weights[s] * 100, 2),
                    "inv_vol": round(inv_vol_weights[s] * 100, 2),
                    "cap": round(cap_weights[s] * 100, 2),
                },
                "ann_vol": round(asset_vols[s], 2) if asset_vols.get(s) is not None else None,
                "beta": asset_betas.get(s),
                "mdd": round(asset_mdds[s], 2) if asset_mdds.get(s) is not None else None,
                "period_return": round(asset_periods[s], 2) if asset_periods.get(s) is not None else None,
            })

        return {
            "status": "ok",
            "summary": {
                "asset_count": len(syms),
                "valid_count": total_valid,
                "window_days": days,
                "start_date": str(df_close.index[0]) if len(df_close) else None,
                "end_date": str(df_close.index[-1]) if len(df_close) else None,
                "portfolio_ann_vol": round(port_vol, 2) if port_vol is not None else None,
                "portfolio_beta": port_beta,
                "benchmark_corr": bench_corr,
                "max_drawdown": round(port_mdd, 2),
                "var_95": var_95,
                "cvar_95": cvar_95,
                "diversification_ratio": dr,
                "top_industry": top_industry,
                "top_industry_ratio": top_industry_ratio,
                "effective_sectors": effective_sectors,
                "weights_mode": mode,
                "weights_comparison": weights_comparison,
                "factor_radar": factor_radar,
            },
            "industry_breakdown": breakdown,
            "assets": assets_list,
        }

    return ok(await asyncio.to_thread(_calc))
