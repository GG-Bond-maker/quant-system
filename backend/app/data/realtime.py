"""
个股实时 / 准实时外部数据源（AQP）。

与 `data/ingest/*` 的区别：
    ingest 面向【批量落库】（全市场、长周期、写入 Parquet）；
    本模块面向【交互式单股查询】（一次请求只取一只标的的几项指标），
因此不走 AKSHARE_RATE_LIMIT（1.2s，为批量拉取设计，交互场景过慢），
而是使用本模块独立的轻量限速 + 重试。

多源冗余（实测可达性，任一源失败自动降级下一个）：
- 内外盘 / 换手率 / 总市值 / 流通市值：腾讯 qt.gtimg.cn   -> 东财 push2delay
- 主力净流入：东财 push2delay fflow -> push2his fflow     -> 同花顺 stock_fund_flow_individual
- 北向持股：  akshare stock_hsgt_individual_em（东财）
- 股东信息：  东财 datacenter-web（股东户数 + 十大流通股东）
- 公告：      巨潮 cninfo（带原文外链）-> 新浪 stock_notice_report

⚠️ 所有函数均为同步阻塞 IO，调用方必须 `asyncio.to_thread` 移出事件循环。
⚠️ 所有异常都向上抛，由 API 层统一降级为 {"status": "unavailable"}，
    禁止把异常串直接放进 API 响应（内部细节只进日志）。
"""
from __future__ import annotations

import random
import threading
import time
from collections.abc import Callable
from types import ModuleType
from typing import Any

import httpx
from loguru import logger

from ..domain.a_share_rules import symbol_to_code

# ---------------- 交互场景限速（与批量拉取的 AKSHARE_RATE_LIMIT 解耦） ----------------
_MIN_INTERVAL = 0.25          # 两次外部请求最小间隔（秒）
_INTERVAL_JITTER = 0.15       # 随机抖动，避免多源并发形成固定节奏
_TIMEOUT = 12.0               # 单次请求超时（秒）
_RETRY = 3                    # 单源重试次数
_BACKOFF = 0.6                # 指数退避基数

_lock = threading.Lock()
_last_call = 0.0

_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
    ),
    "Accept": "*/*",
    "Referer": "https://data.eastmoney.com/",
}

# akshare 惰性导入：模块 import 时不加载，避免拖慢服务启动
_ak_module: ModuleType | None = None
_ak_lock = threading.Lock()


def _ak() -> ModuleType:
    """惰性导入并缓存 akshare 模块（线程安全）。"""
    global _ak_module
    if _ak_module is None:
        with _ak_lock:
            if _ak_module is None:
                import akshare as ak

                _ak_module = ak
    return _ak_module


def _throttle() -> None:
    """模块级限速：交互场景下保证任意两次外部调用有最小间隔。"""
    global _last_call
    with _lock:
        need = _last_call + _MIN_INTERVAL + random.uniform(0, _INTERVAL_JITTER)
        now = time.time()
        if now < need:
            time.sleep(need - now)
        _last_call = time.time()


def _request(
    method: str,
    url: str,
    *,
    params: dict[str, Any] | None = None,
    encoding: str | None = None,
    retries: int = _RETRY,
    headers: dict[str, str] | None = None,
    cookies: dict[str, str] | None = None,
) -> Any:
    """限速 + 重试地执行一次 HTTP 请求，返回 text 或 json。

    :param encoding: 指定时返回 text（并按该编码解码），否则返回 json。
    :param headers: 自定义请求头（会与默认头合并，同名覆盖）。
    :param cookies: 请求 cookies（dict 格式）。
    """
    merged = {**_HEADERS, **(headers or {})}
    last: Exception | None = None
    for i in range(max(1, retries)):
        _throttle()
        try:
            r = httpx.request(
                method, url, params=params, headers=merged, cookies=cookies,
                timeout=_TIMEOUT, follow_redirects=True,
            )
            r.raise_for_status()
            if encoding:
                r.encoding = encoding
                return r.text
            return r.json()
        except Exception as e:  # noqa: BLE001 网络/解析异常统一重试后抛出
            last = e
            logger.debug(f"[realtime] {url.split('?')[0]} try {i} fail: {type(e).__name__}")
            if i < retries - 1:
                time.sleep(_BACKOFF * (2**i))
    raise RuntimeError(f"外部数据源请求失败: {type(last).__name__}")


def _first_source(sources: list[tuple[str, Callable[[], Any]]], what: str) -> Any:
    """按顺序尝试多个数据源，返回首个成功结果；全部失败抛 RuntimeError。"""
    errs: list[str] = []
    for name, fn in sources:
        try:
            return fn()
        except Exception as e:  # noqa: BLE001 换源，异常只进日志
            logger.debug(f"[realtime] {what} source '{name}' failed: {type(e).__name__}: {e!r}")
            errs.append(f"{name}:{type(e).__name__}")
    raise RuntimeError(f"{what} 全部数据源失败 [{'; '.join(errs)}]")


# ---------------- 代码转换 ----------------
def market_prefix(symbol: str) -> str:
    """600519.SH -> sh / 000001.SZ -> sz / 430047.BJ -> bj。"""
    suffix = symbol.split(".")[-1].upper()
    return {"SH": "sh", "SZ": "sz", "BJ": "bj"}.get(suffix, "sh")


def em_secid(symbol: str) -> str:
    """600519.SH -> 1.600519（东财 secid：1=沪 0=深/京）。"""
    code = symbol_to_code(symbol)
    return f"{'1' if market_prefix(symbol) == 'sh' else '0'}.{code}"


def tencent_code(symbol: str) -> str:
    """600519.SH -> sh600519（腾讯行情代码）。"""
    return f"{market_prefix(symbol)}{symbol_to_code(symbol)}"


def sina_symbol(symbol: str) -> str:
    """600519.SH -> sh600519（新浪行情代码，与腾讯同格式）。"""
    return tencent_code(symbol)


# ---------------- 腾讯实时行情：内外盘 / 换手率 / 市值 ----------------
def fetch_tencent_quote(symbol: str) -> dict:
    """腾讯 qt.gtimg.cn 实时快照。

    返回 dict（字段缺失或无法解析时抛异常，由上层降级）：
        price / prev_close / pct / turnover(换手率 %) / outer_vol(外盘 手)
        inner_vol(内盘 手) / float_cap_yi(流通市值 亿) / total_cap_yi(总市值 亿)
        / pe_ttm / pb / quote_time

    字段索引（腾讯私有协议，实测校验：600519 PE=19.92 PB=6.46；
    000001 PE=5.20 PB=0.48 与 BPS=24.13 反算 0.483 一致）：
        3=现价 4=昨收 7=外盘 8=内盘 30=时间 32=涨跌幅 38=换手率
        39=市盈率TTM 44=流通市值(亿) 45=总市值(亿) 46=市净率
    """
    text = _request("GET", f"https://qt.gtimg.cn/q={tencent_code(symbol)}", encoding="gbk")
    if "~" not in text:
        raise RuntimeError("腾讯行情返回格式异常")
    body = text.split('="', 1)[-1].rstrip('";\n')
    f = body.split("~")
    if len(f) < 46:
        raise RuntimeError(f"腾讯行情字段数不足: {len(f)}")

    def _f(idx: int) -> float | None:
        try:
            v = f[idx].strip()
            return float(v) if v else None
        except (ValueError, IndexError):
            return None

    outer = _f(7) or 0.0     # 外盘（主动买入，手）
    inner = _f(8) or 0.0     # 内盘（主动卖出，手）
    return {
        "price": _f(3),
        "prev_close": _f(4),
        "pct": _f(32),
        "turnover": _f(38),
        "outer_vol": outer,
        "inner_vol": inner,
        "float_cap_yi": _f(44),
        "total_cap_yi": _f(45),
        "pe_ttm": _f(39),
        "pb": _f(46),
        "quote_time": f[30].strip() if len(f) > 30 else None,
        "source": "tencent",
    }


def _fetch_em_quote(symbol: str) -> dict:
    """东财 push2delay 行情快照（腾讯不可用时的降级源）。

    f43=现价 f60=昨收 f116=总市值 f117=流通市值 f162=市盈率TTM
    f167=市净率 f168=换手率 f170=涨跌幅
    """
    data = _request(
        "GET",
        "https://push2delay.eastmoney.com/api/qt/stock/get",
        params={
            "secid": em_secid(symbol),
            "fields": "f43,f60,f116,f117,f162,f167,f168,f170",
            "ut": "b2884a393a59ad64002292a3e90d46a5",
        },
    )
    d = (data or {}).get("data")
    if not d:
        raise RuntimeError("东财行情返回空数据")

    def _v(key: str, scale: float = 1.0) -> float | None:
        v = d.get(key)
        if not isinstance(v, (int, float)) or v in (-1, 0):
            return None
        return round(v / scale, 4)

    return {
        "price": _v("f43", 100),
        "prev_close": _v("f60", 100),
        "pct": _v("f170", 100),
        "turnover": _v("f168", 100),
        "outer_vol": None,      # 东财快照不直接提供内外盘
        "inner_vol": None,
        "float_cap_yi": _v("f117", 1e8),
        "total_cap_yi": _v("f116", 1e8),
        "pe_ttm": _v("f162", 100),     # 市盈率 TTM
        "pb": _v("f167", 100),         # 市净率
        "quote_time": None,
        "source": "eastmoney",
    }


def fetch_quote(symbol: str) -> dict:
    """实时快照（多源冗余）。"""
    return _first_source(
        [("tencent", lambda: fetch_tencent_quote(symbol)),
         ("eastmoney", lambda: _fetch_em_quote(symbol))],
        "实时快照",
    )


# ---------------- 批量实时行情（§3.3，一次 HTTP 拉 N 只） ----------------
def _parse_tencent_batch(text: str, symbols: list[str]) -> dict[str, dict]:
    """解析腾讯批量行情响应（私有协议，字段索引 2026-09-05 实测校验）。

    字段索引：1=名称 3=现价 4=昨收 5=今开 6=成交量(手) 30=时间(YYYYMMDDHHMMSS)
    31=涨跌额 32=涨跌% 33=最高 34=最低 37=成交额(万元) 38=换手率
    44=流通市值(亿) 45=总市值(亿) 47=涨停价 48=跌停价。
    """
    code_by_tencent = {tencent_code(s): s for s in symbols}
    out: dict[str, dict] = {}
    for chunk in text.replace(";", "").split("v_"):
        if '="' not in chunk:
            continue
        var, body = chunk.split('="', 1)
        sym = code_by_tencent.get(var.strip())
        if sym is None:
            continue
        f = body.rstrip('"\n').split("~")
        if len(f) < 49 or not f[3]:
            continue

        def _f(idx: int) -> float | None:  # noqa: B023 循环内定义仅在本轮使用
            try:
                v = f[idx].strip()
                return float(v) if v else None
            except (ValueError, IndexError):
                return None

        ts = f[30].strip() if len(f) > 30 else ""
        as_of = None
        if len(ts) == 14:
            as_of = f"{ts[:4]}-{ts[4:6]}-{ts[6:8]} {ts[8:10]}:{ts[10:12]}:{ts[12:14]}"
        vol_hand = _f(6)
        amount_wan = _f(37)
        out[sym] = {
            "symbol": sym,
            "name": f[1].strip() or None,
            "price": _f(3),
            "pct": _f(32),
            "open": _f(5),
            "high": _f(33),
            "low": _f(34),
            "prev_close": _f(4),
            # 口径与 daily_bar 对齐：volume=手（volume_spike 规则直接可比），
            # amount=元（万元×1e4）
            "volume": vol_hand,
            "amount": amount_wan * 1e4 if amount_wan is not None else None,
            "turnover": _f(38),
            "float_cap_yi": _f(44),
            "total_cap_yi": _f(45),
            "limit_up": _f(47),
            "limit_down": _f(48),
            "as_of": as_of,
            "source": "tencent",
        }
    return out


def fetch_tencent_quotes_batch(symbols: list[str]) -> dict[str, dict]:
    """腾讯批量实时快照：一次 HTTP 请求拉全部标的（1 次请求 = 1 份限速配额）。

    Returns:
        {symbol: quote dict}；单只解析失败不影响其余（如实缺报，不造数）。

    Raises:
        RuntimeError: 请求层失败（由上层降级链处理）。
    """
    codes = ",".join(tencent_code(s) for s in symbols)
    text = _request("GET", f"https://qt.gtimg.cn/q={codes}", encoding="gbk")
    if '="' not in text:
        raise RuntimeError("腾讯批量行情返回格式异常")
    return _parse_tencent_batch(text, symbols)


def _fetch_sina_quotes_batch(symbols: list[str]) -> dict[str, dict]:
    """新浪批量实时快照（腾讯不可用时的降级源；hq.sinajs.cn 需 Referer）。"""
    codes = ",".join(sina_symbol(s) for s in symbols)
    code_by_sina = {sina_symbol(s): s for s in symbols}
    text = _request(
        "GET", f"https://hq.sinajs.cn/list={codes}", encoding="gbk",
        headers={"Referer": "https://finance.sina.com.cn"},
    )
    out: dict[str, dict] = {}
    for chunk in text.replace(";", "").split("hq_str_"):
        if "=" not in chunk:
            continue
        var, body = chunk.split("=", 1)
        sym = code_by_sina.get(var.strip())
        if sym is None:
            continue
        f = body.strip().strip('"').split(",")
        if len(f) < 32 or not f[3]:
            continue

        def _f(x: str) -> float | None:  # noqa: B023
            try:
                v = x.strip()
                return float(v) if v else None
            except ValueError:
                return None

        price, prev = _f(f[3]), _f(f[2])
        d, t = f[30].strip(), f[31].strip()
        vol_share = _f(f[8])
        out[sym] = {
            "symbol": sym,
            "name": f[0].strip() or None,
            "price": price,
            "pct": (round((price / prev - 1) * 100, 2)
                    if price is not None and prev else None),
            "open": _f(f[1]),
            "high": _f(f[4]),
            "low": _f(f[5]),
            "prev_close": prev,
            # 新浪 volume 单位为股，统一换算为手（与 daily_bar/腾讯口径一致）
            "volume": vol_share / 100 if vol_share is not None else None,
            "amount": _f(f[9]),                                          # 元
            "turnover": None,
            "float_cap_yi": None,
            "total_cap_yi": None,
            "limit_up": None,
            "limit_down": None,
            "as_of": f"{d[:4]}-{d[4:6]}-{d[6:8]} {t}" if len(d) == 8 and t else None,
            "source": "sina",
        }
    return out


def fetch_quotes_batch(symbols: list[str]) -> tuple[list[dict], str]:
    """批量实时行情（降级链：腾讯 → 新浪 → degraded，不造数）。

    Returns:
        ``(quotes, source)``：quotes 按传入顺序；source ∈ {tencent, sina, degraded}。
        全部源失败时返回 ``([], "degraded")``（空数组而非编造快照）。
    """
    if not symbols:
        return [], "degraded"
    errs: list[str] = []
    for name, fn in [("tencent", lambda: fetch_tencent_quotes_batch(symbols)),
                     ("sina", lambda: _fetch_sina_quotes_batch(symbols))]:
        try:
            data = fn()
            if data:
                return [data[s] for s in symbols if s in data], name
            errs.append(f"{name}:empty")
        except Exception as e:  # noqa: BLE001 换源，异常只进日志
            logger.warning(f"[realtime] quotes batch source '{name}' failed: {type(e).__name__}: {e!r}")
            errs.append(f"{name}:{type(e).__name__}")
    logger.warning(f"[realtime] quotes batch degraded: {'; '.join(errs)}")
    return [], "degraded"


# ---------------- 主力资金净流入 ----------------
_FFLOW_FIELDS2 = "f51,f52,f53,f54,f55,f56,f57,f58,f59,f60,f61,f62,f63,f64,f65"


def _fetch_em_fflow(host: str, symbol: str) -> dict:
    """东财个股资金流日线（主力/超大单/大单/中单/小单 净额与净占比）。"""
    data = _request(
        "GET",
        f"{host}/api/qt/stock/fflow/daykline/get",
        params={
            "lmt": "0",
            "klt": "101",
            "secid": em_secid(symbol),
            "fields1": "f1,f2,f3,f7",
            "fields2": _FFLOW_FIELDS2,
            "ut": "b2884a393a59ad64002292a3e90d46a5",
        },
        retries=2,
    )
    klines = ((data or {}).get("data") or {}).get("klines") or []
    if not klines:
        raise RuntimeError("东财资金流返回空")
    cols = klines[-1].split(",")
    # 列序：日期, 主力净额, 小单净额, 中单净额, 大单净额, 超大单净额,
    #       主力净占比, 小单占比, 中单占比, 大单占比, 超大单占比, 收盘价, 涨跌幅, -, -
    return {
        "date": cols[0],
        "main_net": float(cols[1]),            # 元
        "super_large_net": float(cols[5]),
        "large_net": float(cols[4]),
        "medium_net": float(cols[3]),
        "small_net": float(cols[2]),
        "main_net_ratio": float(cols[6]),      # %
        "close": float(cols[11]),
        "source": "eastmoney",
    }


def _fetch_ths_fflow(symbol: str) -> dict:
    """同花顺资金流（兜底）：全市场快照中筛出本股。

    该接口会拉全市场 5000+ 行（约 10s），仅在东财两源都失败时使用。
    """
    code = symbol_to_code(symbol)
    df = _ak().stock_fund_flow_individual(symbol="即时")
    if df is None or df.empty:
        raise RuntimeError("同花顺资金流返回空")
    row = df[df["股票代码"].astype(str) == code]
    if row.empty:
        raise RuntimeError(f"同花顺资金流未找到 {code}")
    r = row.iloc[-1]
    return {
        "date": None,
        "main_net": _cn_amount_to_float(r.get("净额")),
        "super_large_net": None,
        "large_net": None,
        "medium_net": None,
        "small_net": None,
        "main_net_ratio": None,
        "close": _cn_amount_to_float(r.get("最新价"), raw=True),
        "source": "10jqka",
    }


def _cn_amount_to_float(v: Any, raw: bool = False) -> float | None:
    """同花顺金额字符串 -> float：'-1.59亿' / '8733.67万' / '1.20' -> 元。"""
    if v is None:
        return None
    s = str(v).strip().replace(",", "")
    if not s or s in ("-", "--"):
        return None
    try:
        if raw:
            return float(s)
        mult = 1.0
        if s.endswith("万"):
            mult, s = 1e4, s[:-1]
        elif s.endswith("亿"):
            mult, s = 1e8, s[:-1]
        return float(s) * mult
    except ValueError:
        return None


def fetch_main_fund_flow(symbol: str) -> dict:
    """个股主力资金净流入（多源冗余）。"""
    return _first_source(
        [("em-delay", lambda: _fetch_em_fflow("https://push2delay.eastmoney.com", symbol)),
         ("em-his", lambda: _fetch_em_fflow("https://push2his.eastmoney.com", symbol)),
         ("10jqka", lambda: _fetch_ths_fflow(symbol))],
        "主力资金流",
    )


# ---------------- 北向持股 ----------------
def fetch_north_holding(symbol: str) -> dict:
    """北向资金（陆股通）个股持股：akshare stock_hsgt_individual_em。

    返回：date / hold_shares / hold_market_cap / pct_of_float / close
    """
    code = symbol_to_code(symbol)
    df = _ak().stock_hsgt_individual_em(stock=code)
    if df is None or df.empty:
        raise RuntimeError("北向持股数据为空")
    r = df.iloc[-1]
    return {
        "date": str(r.get("持股日期"))[:10],
        "hold_shares": _to_float(r.get("持股数量")),
        "hold_market_cap": _to_float(r.get("持股市值")),
        "pct_of_float": _to_float(r.get("持股数量占A股百分比")),
        "close": _to_float(r.get("当日收盘价")),
        "source": "eastmoney",
    }


def _to_float(v: Any) -> float | None:
    try:
        if v is None:
            return None
        f = float(v)
        return None if f != f else f  # NaN -> None
    except (TypeError, ValueError):
        return None


# ---------------- 股东信息 ----------------
_DC_URL = "https://datacenter-web.eastmoney.com/api/data/v1/get"


def _dc_get(report_name: str, filter_expr: str, sort_columns: str,
            sort_types: str, page_size: int) -> list[dict]:
    """东财 datacenter-web 通用查询，返回 result.data 列表。"""
    data = _request(
        "GET",
        _DC_URL,
        params={
            "reportName": report_name,
            "columns": "ALL",
            "filter": filter_expr,
            "pageNumber": 1,
            "pageSize": page_size,
            "sortColumns": sort_columns,
            "sortTypes": sort_types,
            "source": "WEB",
            "client": "WEB",
        },
    )
    return ((data or {}).get("result") or {}).get("data") or []


def fetch_holder_num(symbol: str) -> dict:
    """股东户数（最新一期 + 环比变化）。"""
    code = symbol_to_code(symbol)
    rows = _dc_get(
        "RPT_HOLDERNUMLATEST",
        f'(SECURITY_CODE="{code}")',
        "END_DATE", "-1", 1,
    )
    if not rows:
        raise RuntimeError("股东户数数据为空")
    r = rows[0]
    return {
        "end_date": str(r.get("END_DATE"))[:10],
        "holder_num": _to_float(r.get("HOLDER_NUM")),
        "prev_holder_num": _to_float(r.get("PRE_HOLDER_NUM")),
        "change_ratio": _to_float(r.get("HOLDER_NUM_RATIO")),  # %
        "avg_market_cap": _to_float(r.get("AVG_MARKET_CAP")),  # 户均持股市值（元）
        "avg_hold_num": _to_float(r.get("AVG_HOLD_NUM")),      # 户均持股数
    }


def fetch_top10_float_holders(symbol: str) -> list[dict]:
    """十大流通股东（最新报告期）。"""
    code = symbol_to_code(symbol)
    rows = _dc_get(
        "RPT_F10_EH_FREEHOLDERS",
        f'(SECUCODE="{code}.{market_prefix(symbol).upper()}")',
        "END_DATE,HOLDER_RANK", "-1,1", 10,
    )
    if not rows:
        raise RuntimeError("十大流通股东数据为空")
    end_date = str(rows[0].get("END_DATE"))[:10]
    out: list[dict] = []
    for r in rows:
        if str(r.get("END_DATE"))[:10] != end_date:
            continue
        out.append({
            "rank": _to_float(r.get("HOLDER_RANK")),
            "name": r.get("HOLDER_NAME"),
            "hold_num": _to_float(r.get("HOLD_NUM")),
            "pct_of_float": _to_float(r.get("FREE_HOLDNUM_RATIO")),  # 占流通股 %
            "change": r.get("HOLD_NUM_CHANGE"),
            "holder_type": r.get("HOLDER_TYPE"),
        })
        if len(out) >= 10:
            break
    return out


# ---------------- 公告 / 事件 ----------------
def fetch_cninfo_announcements(symbol: str, limit: int = 3, days: int = 400) -> list[dict]:
    """巨潮资讯公告（带原文外链，可直接跳转详情页）。"""
    from datetime import date, timedelta

    code = symbol_to_code(symbol)
    end = date.today()
    start = end - timedelta(days=days)
    df = _ak().stock_zh_a_disclosure_report_cninfo(
        symbol=code,
        start_date=start.strftime("%Y%m%d"),
        end_date=end.strftime("%Y%m%d"),
    )
    if df is None or df.empty:
        raise RuntimeError("巨潮公告数据为空")
    df = df[df["代码"].astype(str) == code]
    if df.empty:
        raise RuntimeError(f"巨潮未找到 {code} 的公告")
    df = df.sort_values("公告时间", ascending=False).head(limit)
    return [
        {
            "title": str(r["公告标题"]).strip(),
            "date": str(r["公告时间"])[:10],
            "url": str(r.get("公告链接") or ""),
            "source": "cninfo",
        }
        for _, r in df.iterrows()
    ]


def fetch_sina_notices(symbol: str, limit: int = 3) -> list[dict]:
    """新浪公告（巨潮不可用时的降级源，无外链）。"""
    code = symbol_to_code(symbol)
    raw = _ak().stock_notice_report(symbol="全部", date=date_today_str())
    if raw is None or raw.empty:
        raise RuntimeError("新浪公告数据为空")
    rename = {"公告标题": "title", "公告日期": "pub_date", "公告类型": "category"}
    raw = raw.rename(columns={k: v for k, v in rename.items() if k in raw.columns})
    if "title" not in raw.columns:
        raise RuntimeError("新浪公告字段漂移")
    raw = raw[raw["title"].astype(str).str.contains(code, na=False)]
    if raw.empty:
        raise RuntimeError(f"新浪未找到 {code} 的公告")
    return [
        {
            "title": str(r["title"]).strip(),
            "date": date_today_str(),
            "url": None,
            "source": "sina",
        }
        for _, r in raw.head(limit).iterrows()
    ]


def date_today_str() -> str:
    from datetime import date

    return date.today().isoformat()


def fetch_events(symbol: str, limit: int = 3) -> list[dict]:
    """近期事件（公告 / 重大新闻），多源冗余，按时间倒序取前 N 条。"""
    return _first_source(
        [("cninfo", lambda: fetch_cninfo_announcements(symbol, limit)),
         ("sina", lambda: fetch_sina_notices(symbol, limit))],
        "公告事件",
    )


# ---------------- 财务指标（ROE / 毛利率 / 净利率） ----------------
def fetch_financial_indicators(symbol: str) -> dict:
    """最新一期财报关键指标（东财 datacenter-web 业绩报表 RPT_LICO_FN_CPD）。

    字段口径（实测核对）：
        WEIGHTAVG_ROE        加权平均净资产收益率 %
        XSMLL                销售毛利率 %（银行/保险等金融股为 null，属正常）
        PARENT_NETPROFIT     归母净利润（元）
        TOTAL_OPERATE_INCOME 营业总收入（元）
    净利率东财不直接给，按【归母净利润 / 营业总收入 × 100】推导
        （实测 600519：445.17 亿 / 922.78 亿 = 48.24%，与公開口径一致）。
    亏损股净利率为负，原样返回（前端按红绿着色）。
    """
    code = symbol_to_code(symbol)
    rows = _dc_get(
        "RPT_LICO_FN_CPD",
        f'(SECURITY_CODE="{code}")',
        "REPORTDATE", "-1", 1,
    )
    if not rows:
        raise RuntimeError("财务指标数据为空")
    r = rows[0]

    revenue = _to_float(r.get("TOTAL_OPERATE_INCOME"))
    profit = _to_float(r.get("PARENT_NETPROFIT"))
    net_margin: float | None = None
    if revenue and profit is not None and revenue > 0:
        net_margin = round(profit / revenue * 100, 4)

    return {
        "report_date": str(r.get("REPORTDATE"))[:10],
        "roe": _to_float(r.get("WEIGHTAVG_ROE")),
        "gross_margin": _to_float(r.get("XSMLL")),
        "net_margin": net_margin,
        "eps": _to_float(r.get("BASIC_EPS")),
        "bps": _to_float(r.get("BPS")),
        "revenue_yi": round(revenue / 1e8, 4) if revenue else None,
        "net_profit_yi": round(profit / 1e8, 4) if profit else None,
        "source": "eastmoney",
    }


# ---------------- 基准指数（Beta 计算用） ----------------
def fetch_benchmark_daily(symbol: str = "sh000300") -> "Any":
    """基准指数日线（新浪源，用于 Beta 计算），返回 pandas.DataFrame。"""
    df = _ak().stock_zh_index_daily(symbol=symbol)
    if df is None or df.empty:
        raise RuntimeError(f"基准指数 {symbol} 数据为空")
    return df
