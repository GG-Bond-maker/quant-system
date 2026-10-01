"""
个股实时 / 准实时外部数据源（AQP）。

与 `data/ingest/*` 的区别：
    ingest 面向【批量落库】（全市场、长周期、写入 Parquet）；
    本模块面向【交互式单股查询】（一次请求只取一只标的的几项指标），
因此不走 AKSHARE_RATE_LIMIT（1.2s，为批量拉取设计，交互场景过慢），
而是使用本模块独立的轻量限速 + 重试。

多源冗余（实测可达性，任一源失败自动降级下一个）：
- 内外盘 / 换手率 / 总市值 / 流通市值：腾讯 qt.gtimg.cn   -> 新浪 hq.sinajs.cn
- 主力净流入：东财 push2delay fflow -> push2his fflow（**两个 host 同属东财** ⇒
  实为东财单源，暂无外源备用；原先注释所称的「同花顺 stock_fund_flow_individual」
  从未接线，2026-09-26 如实更正）
- 北向持股：  akshare stock_hsgt_individual_em（东财）
- 股东信息：  东财 datacenter-web（股东户数 + 十大流通股东）
- 公告：      巨潮 cninfo（带原文外链）-> 东方财富 stock_notice_report

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

from ..core.errors import DataSourceUnavailable
from ..domain.a_share_rules import symbol_to_code

# ---------------- 交互场景限速（与批量拉取的 AKSHARE_RATE_LIMIT 解耦） ----------------
_MIN_INTERVAL = 0.25          # 两次外部请求最小间隔（秒）
_INTERVAL_JITTER = 0.15       # 随机抖动，避免多源并发形成固定节奏
_TIMEOUT = 3.0                # 交互外部请求单次预算（秒）
_RETRY = 1                    # 交互路径不在请求内指数重试，避免叠加超预算
_BACKOFF = 0.6                # 指数退避基数

_lock = threading.Lock()
_last_call = 0.0


class SourceRetiredError(RuntimeError):
    """数据源已**永久下线**（区别于「暂时故障」）。

    - 「暂时不可用」是网络抖动 / 上游限流等可自愈故障，上层应保留 SWR 旧值并
      在短 TTL 后重试；
    - 「永久下线」是产品决策放弃的数据源（无有界数据源、已被明确否决），
      重试无意义。

    上层（如 ``panels.build_north``）据此把两者区分开，返回带 ``retired`` 标记 /
    可区分 ``reason`` 的 unavailable 结构，避免把已下线的块当作可重试故障反复
    请求或长时间缓存空结果。``retired`` 同时作为类属性与实例属性暴露，便于
    ``getattr(exc, "retired", False)`` 式的宽松判别。
    """

    retired: bool = True

    def __init__(self, message: str) -> None:
        super().__init__(message)
        self.retired = True

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
    timeout: float | None = None,
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
                timeout=timeout if timeout is not None else _TIMEOUT, follow_redirects=True,
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
    raise DataSourceUnavailable(f"外部数据源请求失败: {type(last).__name__}")


def _first_source(sources: list[tuple[str, Callable[[], Any]]], what: str) -> Any:
    """按顺序尝试多个数据源，返回首个成功结果；全部失败抛 DataSourceUnavailable。"""
    errs: list[str] = []
    for name, fn in sources:
        try:
            return fn()
        except Exception as e:  # noqa: BLE001 换源，异常只进日志
            logger.debug(f"[realtime] {what} source '{name}' failed: {type(e).__name__}: {e!r}")
            errs.append(f"{name}:{type(e).__name__}")
    raise DataSourceUnavailable(f"{what} 全部数据源失败 [{'; '.join(errs)}]")


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


def fetch_sina_quote(symbol: str) -> dict:
    """新浪 hq.sinajs.cn 单只实时快照（腾讯不可用时的降级源）。

    复用批量接口 ``_fetch_sina_quotes_batch`` 的解析逻辑（单只版）。

    ⚠️ 新浪单只仅有 price/open/prev_close/high/low/volume/amount，**无 PE/PB/换手率
    内外盘/市值** ⇒ 这些字段如实给 ``None``（不估算、不补零）。返回键对齐
    :func:`fetch_tencent_quote` 的 dict 键，缺失字段为 null。
    """
    data = _fetch_sina_quotes_batch([symbol])
    q = data.get(symbol)
    if not q:
        raise RuntimeError(f"新浪未返回 {symbol} 的行情")
    return {
        "price": q.get("price"),
        "prev_close": q.get("prev_close"),
        "pct": q.get("pct"),
        "turnover": None,       # 新浪单只不提供换手率 ⇒ 如实 null
        "outer_vol": None,      # 新浪单只不提供内外盘 ⇒ 如实 null
        "inner_vol": None,
        "float_cap_yi": None,   # 新浪单只不提供市值 ⇒ 如实 null
        "total_cap_yi": None,
        "pe_ttm": None,         # 新浪单只不提供 PE/PB ⇒ 如实 null
        "pb": None,
        "quote_time": q.get("as_of"),
        "source": "sina",
    }


def fetch_quote(symbol: str) -> dict:
    """实时快照（多源冗余：腾讯 → 新浪；东财 push2delay 备源已移除）。"""
    return _first_source(
        [("tencent", lambda: fetch_tencent_quote(symbol)),
         ("sina", lambda: fetch_sina_quote(symbol))],
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
            # ⚠️ volume = 【手】（腾讯 f[6] 原生就是手；新浪路径在上方已 /100 归到手）。
            # 🔴 但 daily_bar.volume 是【股】—— 两者**不**同口径（差 100 倍），
            #    消费方必须自行归一（见 alerts.py 的 volume_spike）。
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


def fetch_main_fund_flow(symbol: str) -> dict:
    """个股主力资金净流入（多源冗余）。

    2026-09-30 修复：旧两个 host（``push2delay`` / ``push2his``）**整组被网络层阻断**
    （实测代理与直连均 ``RemoteProtocolError``，非本机代理问题），导致该卡片长期空白。
    新增 ``push2test`` 为首选主机——实测：
      - 与旧主机**同一接口同一字段**（``/api/qt/stock/fflow/daykline/get``），
        ``cols[1]`` 主力净额与东财报表 ``RPT_DMSK_TS_STOCKNEW.PRIME_INFLOW`` **差 0.00**；
      - 日频 103 个交易日、**零鉴权**（空 header 亦返回 200）、40/40 连续成功（均值 0.22s）；
      - **额外支持 ETF**（旧报表通道不含 ETF）。
    旧主机保留在链尾作降级：均为同厂同服务组，不构成真正跨厂冗余，但至少覆盖
    单机故障/灰度切换场景。

    ⚠️ 口径限制：``lmt=0`` 最多返回 103 个交易日（约半年）。
    """
    return _first_source(
        [("em-test", lambda: _fetch_em_fflow("https://push2test.eastmoney.com", symbol)),
         ("em-delay", lambda: _fetch_em_fflow("https://push2delay.eastmoney.com", symbol)),
         ("em-his", lambda: _fetch_em_fflow("https://push2his.eastmoney.com", symbol))],
        "主力资金流",
    )


# ---------------- 市场级资金流（大盘 / 行业板块） ----------------
# 2026-10-01 修复（数据可信度审计 F1）：`market_service.build_money_flow` 原先
# 直接调 `ak.stock_market_fund_flow()` / `ak.stock_sector_fund_flow_rank()`，
# 而 akshare 内部**硬编码** `push2his` / `push2` —— 两者在本机被网络层整组阻断
# （`RemoteProtocolError`，与 `fetch_main_fund_flow` 同一根因）⇒ 市场概览的
# "大盘主力净流入 / 行业板块资金流"长期 degraded/unavailable，**但数据其实完全可得**。
#
# 实证（2026-10-01，同接口同参数，仅换 host）：
#   `push2test` 200 OK：大盘 daykline 104 行（末行 2026-09-30 主力净额 -118.53 亿）、
#   行业板块 clist 100 行 / total=2877（医药生物 f62=61.9 亿）；
#   `push2his` / `push2` 均 `RemoteProtocolError`。
# ⇒ 与个股/ETF 路径统一到 `push2test` 首选，旧 host 保留链尾作降级。
_MARKET_FFLOW_FIELDS2 = "f51,f52,f53,f54,f55,f56,f57,f58,f59,f60,f61,f62,f63,f64,f65"


def _fetch_em_market_fflow(host: str) -> list[dict]:
    """东财【大盘】资金流日线（近半年），返回原始行 dict 列表（按时间升序）。

    对齐 akshare ``stock_market_fund_flow`` 的接口与列序（``secid=1.000001`` 上证 +
    ``secid2=0.399001`` 深证）。字段名保持中文以复用既有消费方解析。
    """
    data = _request(
        "GET",
        f"{host}/api/qt/stock/fflow/daykline/get",
        params={
            "lmt": "0",
            "klt": "101",
            "secid": "1.000001",
            "secid2": "0.399001",
            "fields1": "f1,f2,f3,f7",
            "fields2": _MARKET_FFLOW_FIELDS2,
            "ut": "b2884a393a59ad64002292a3e90d46a5",
        },
        retries=2,
    )
    klines = ((data or {}).get("data") or {}).get("klines") or []
    if not klines:
        raise RuntimeError("东财大盘资金流返回空")
    cols = ["日期", "主力净流入-净额", "小单净流入-净额", "中单净流入-净额",
            "大单净流入-净额", "超大单净流入-净额", "主力净流入-净占比",
            "小单净流入-净占比", "中单净流入-净占比", "大单净流入-净占比",
            "超大单净流入-净占比", "上证-收盘价", "上证-涨跌幅",
            "深证-收盘价", "深证-涨跌幅"]
    out: list[dict] = []
    for row in klines:
        parts = str(row).split(",")
        if len(parts) < len(cols):
            continue
        out.append({c: parts[i] for i, c in enumerate(cols)})
    if not out:
        raise RuntimeError("东财大盘资金流解析后为空")
    return out


_SECTOR_FFLOW_FIELDS = ("f12,f14,f2,f3,f62,f184,f66,f69,f72,f75,f78,f81,f84,f87,"
                        "f204,f205,f124")
# 行业资金流：`m:90 t:2`（akshare `stock_sector_fund_flow_rank` 的 fs 值与之一致）
_SECTOR_FS = {"行业资金流": "m:90 t:2", "概念资金流": "m:90 t:3", "地域资金流": "m:90 t:1"}


def _fetch_em_sector_fflow(host: str, sector_type: str = "行业资金流") -> list[dict]:
    """东财【行业/概念/地域】板块资金流排名（当日），返回原始行 dict 列表。

    对齐 akshare ``stock_sector_fund_flow_rank``（indicator="今日"）的接口与字段。
    仅取工具需要的列（f12 代码 / f14 名称 / f62 主力净额 / f184 主力净占比 /
    f66 超大单 / f72 大单 / f78 中单 / f84 小单 / f124 数据时间戳）。
    """
    data = _request(
        "GET",
        f"{host}/api/qt/clist/get",
        params={
            "pn": "1", "pz": "100", "po": "1", "np": "1",
            "ut": "b2884a393a59ad64002292a3e90d46a5",
            "fltt": "2", "invt": "2",
            "fid0": "f62", "fid": "f62",
            "fs": _SECTOR_FS.get(sector_type, "m:90 t:2"),
            "stat": "1",
            "fields": _SECTOR_FFLOW_FIELDS,
        },
        retries=2,
    )
    diff = ((data or {}).get("data") or {}).get("diff") or []
    if not diff:
        raise RuntimeError(f"东财{sector_type}返回空")
    return [d for d in diff if isinstance(d, dict)]


def fetch_market_fund_flow() -> list[dict]:
    """大盘资金流日线（多源降级：``push2test`` → ``push2delay`` → ``push2his``）。"""
    return _first_source(
        [("em-test", lambda: _fetch_em_market_fflow("https://push2test.eastmoney.com")),
         ("em-delay", lambda: _fetch_em_market_fflow("https://push2delay.eastmoney.com")),
         ("em-his", lambda: _fetch_em_market_fflow("https://push2his.eastmoney.com"))],
        "大盘资金流",
    )


def fetch_sector_fund_flow(sector_type: str = "行业资金流") -> list[dict]:
    """行业/概念/地域板块资金流排名（多源降级，同 ``fetch_market_fund_flow``）。"""
    return _first_source(
        [("em-test", lambda: _fetch_em_sector_fflow("https://push2test.eastmoney.com", sector_type)),
         ("em-delay", lambda: _fetch_em_sector_fflow("https://push2delay.eastmoney.com", sector_type)),
         ("em-his", lambda: _fetch_em_sector_fflow("https://push2his.eastmoney.com", sector_type))],
        f"{sector_type}排名",
    )


# ---------------- 北向持股 ----------------
def fetch_north_holding(symbol: str) -> dict:
    """北向持股当前无可证明有界的细粒度源，快速声明**永久下线**。

    旧实现调用 AKShare 全量接口且无网络超时，单块取消后线程仍持续扫描。
    该源已被明确否决（不重新接无界抓取），因此抛 :class:`SourceRetiredError`
    （而非泛型 RuntimeError），让上层能把「永久下线」与「暂时故障」区分开：
    调用层据 ``retired=True`` 返回带可区分 ``reason`` 的 unavailable 结构，
    且该块不应被长时间缓存（见 ``panels.TTL["north"]``）。
    """
    raise SourceRetiredError(f"北向持股数据源已永久下线（无有界数据源）: {symbol}")


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


def fetch_em_notices(symbol: str, limit: int = 3) -> list[dict]:
    """东方财富公告（``stock_notice_report``），**非新浪**（巨潮不可用时的降级源，无外链）。

    ⚠️ 命名如实（2026-09-26 修正）：本函数底层 ``_ak().stock_notice_report`` 实际
    数据来源为**东方财富** ``np-anotice-stock.eastmoney.com``，此前误命名为
    ``fetch_sina_notices`` 且返回 ``source="sina"``，属**失实**；现改名并如实标注
    ``source="eastmoney"``。旧名保留为别名（``fetch_sina_notices = fetch_em_notices``）。
    """
    code = symbol_to_code(symbol)
    raw = _ak().stock_notice_report(symbol="全部", date=date_today_str())
    if raw is None or raw.empty:
        raise RuntimeError("东方财富公告数据为空")
    rename = {"公告标题": "title", "公告日期": "pub_date", "公告类型": "category"}
    raw = raw.rename(columns={k: v for k, v in rename.items() if k in raw.columns})
    if "title" not in raw.columns:
        raise RuntimeError("东方财富公告字段漂移")
    raw = raw[raw["title"].astype(str).str.contains(code, na=False)]
    if raw.empty:
        raise RuntimeError(f"东方财富未找到 {code} 的公告")
    return [
        {
            "title": str(r["title"]).strip(),
            "date": date_today_str(),
            "url": None,
            "source": "eastmoney",
        }
        for _, r in raw.head(limit).iterrows()
    ]


# 过渡别名（旧调用方引用旧名；2026-09-26 改名 fetch_sina_notices -> fetch_em_notices）
fetch_sina_notices = fetch_em_notices


def date_today_str() -> str:
    from datetime import date

    return date.today().isoformat()


def fetch_events(symbol: str, limit: int = 3) -> list[dict]:
    """公告远端兜底已**永久禁用**；无本地落库时快速声明不可用。

    两个旧 AKShare 源都会抓取大列表且不暴露请求超时，协程取消后后台线程仍会
    长时间消耗资源；已明确否决重新接入。公告应由同步管线写入 announcements
    parquet（``data/ingest/announcements.save_announcements``）后供面板读取
    （见 ``panels.build_events`` / ``data.announcements``）。

    抛 :class:`SourceRetiredError`（而非泛型 RuntimeError），以便上层区分
    「永久下线」与「暂时故障」。
    """
    raise SourceRetiredError(f"公告远端兜底已永久下线，且本地无 {symbol} 公告数据")


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
