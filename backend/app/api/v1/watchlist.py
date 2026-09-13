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
from typing import Any

import fastapi
from loguru import logger

from ...core.config import get_settings
from ...core.auth import require_role
from ...core.errors import APIResponse, ok
from ...data.parquet_store import read_symbol_dataset
from ...domain.a_share_rules import code_to_symbol, symbol_to_code

router = fastapi.APIRouter()

# ---------------- 类型判定与分组 ----------------
_ETF_PREFIXES = ("51", "56", "58", "15", "16", "159")


def is_etf_code(symbol: str) -> bool:
    """510300 / 159915 等纯 6 位数字且前缀匹配 ETF 段。"""
    return len(symbol) == 6 and symbol.isdigit() and symbol[:2] in _ETF_PREFIXES[:4] \
        or symbol.startswith("159")


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
    df = read_symbol_dataset("daily_bar_qfq", symbol)
    if df.is_empty() or "date" not in df.columns:
        # QFQ 分区缺失（未跑全量同步）时回退不复权本地数据
        df = read_symbol_dataset("daily_bar", symbol)
    if df.is_empty() or "date" not in df.columns:
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


_ETF_NAME_MAP: dict[str, str] | None = None


def _etf_names() -> dict[str, str]:
    """ETF 代码 -> 名称（东财全量目录，etf 模块自带 TTL 缓存）。"""
    global _ETF_NAME_MAP
    if _ETF_NAME_MAP is None:
        try:
            from ...data.etf import fetch_cn_etfs

            _ETF_NAME_MAP = {str(e["code"]): e.get("name") or "" for e in fetch_cn_etfs()}
        except Exception:
            _ETF_NAME_MAP = {}
    return _ETF_NAME_MAP


def _load_instrument_names() -> dict[str, str]:
    """instrument 表 symbol -> name（只读 SQLite，一次全量入内存）。"""
    import sqlite3

    try:
        db = get_settings().SQLITE_PATH
        with sqlite3.connect(f"file:{db}?mode=ro", uri=True) as conn:
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
        text = _request("GET", f"https://qt.gtimg.cn/q={codes}", encoding="gbk")
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


def _fund_flow_total(stocks: list[str]) -> float | None:
    """主力资金净流入合计（元）→ 亿元；任一只成功即计入，全失败返回 None。进程缓存 5 分钟。"""
    from ...data.realtime import fetch_main_fund_flow

    total = 0.0
    hit = 0
    for s in stocks:
        try:
            total += fetch_main_fund_flow(s)["main_net"]
            hit += 1
        except Exception:
            continue
    return round(total / 1e8, 2) if hit else None


# ---------------- 端点 ----------------
@router.get("/dashboard")
async def dashboard(
    symbols: str = fastapi.Query(..., min_length=1, max_length=4000),
    _user: dict = fastapi.Depends(require_role("viewer")),
) -> APIResponse[dict]:
    """自选看板：KPI 汇总 + 逐标的行情（K线状态 / 预警 / 迷你K线序列）。"""
    raw = list(dict.fromkeys(s.strip() for s in symbols.split(",") if s.strip()))[:100]
    syms = [normalize_symbol(s) for s in raw]
    syms = list(dict.fromkeys(syms))

    def _one(sym: str) -> dict:
        bars, source = _bars_for(sym)
        close = pct = None
        amount_yi = None
        if bars:
            last = bars[-1]
            close = last.get("close")
            if len(bars) >= 2 and bars[-2].get("close"):
                pct = round((close / bars[-2]["close"] - 1) * 100, 2)
            if close:
                amt = last.get("amount")
                if amt is None:
                    # 腾讯 K 线 volume 单位为手（100 股）；本地 parquet 自带 amount(元)
                    amt = (last.get("volume") or 0) * 100 * close
                amount_yi = round(amt / 1e8, 2)
        state, alert = _kline_state(bars)
        etf = is_etf_code(sym)
        return {
            "symbol": sym,
            "code": symbol_to_code(sym),
            "type": "etf" if etf else "stock",
            "name": None,
            "close": close,
            "pct": pct,
            "amount_yi": amount_yi,
            "kline_state": state,
            "alert": alert,
            "date": bars[-1]["date"] if bars else None,
            "closes": [b.get("close") for b in bars[-30:]],
            "source": source,
        }

    # 每只标的独立线程取行情（默认线程池自动排队，N≤100 安全）
    items = list(await asyncio.gather(*[asyncio.to_thread(_one, s) for s in syms]))

    names = _load_instrument_names()
    etf_names = _etf_names()
    valuation = await asyncio.to_thread(_batch_valuation, syms)
    for it in items:
        v = valuation.get(it["symbol"]) or {}
        it["name"] = (v.get("name") or names.get(it["symbol"])
                      or (etf_names.get(it["code"]) if it["type"] == "etf" else None))
        if it["type"] == "stock":
            it["pe"] = v.get("pe")
            it["pb"] = v.get("pb")
            if it["close"] is None and v.get("price"):
                it["close"], it["pct"] = v["price"], v.get("pct")
        else:
            it["pe"] = it["pb"] = None

    # KPI：平均涨跌幅 / 预警数 / 主力净流入（仅股票口径）
    stocks = [it["symbol"] for it in items if it["type"] == "stock"]
    pcts = [it["pct"] for it in items if it["pct"] is not None]
    avg_pct = round(sum(pcts) / len(pcts), 2) if pcts else None
    alerts = [(it["symbol"], it["alert"]) for it in items if it["alert"]]
    flow_total = await asyncio.to_thread(_fund_flow_total, stocks) if stocks else None

    return ok({
        "summary": {
            "count": len(items),
            "stock_count": sum(1 for i in items if i["type"] == "stock"),
            "etf_count": sum(1 for i in items if i["type"] == "etf"),
            "avg_pct": avg_pct,
            "flow_total_yi": flow_total,
            "alert_count": len(alerts),
            "alert_kinds": sorted({a.split("+")[0] for _, a in alerts}),
            "quote_date": next((it["date"] for it in items if it["date"]), None),
        },
        "items": items,
    })


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
