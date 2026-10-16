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


_client_lock = threading.Lock()
_shared_client: httpx.Client | None = None


def get_shared_client() -> httpx.Client:
    """获取进程级持久化的 HTTP Client，复用 TCP/TLS 连接池（Keep-Alive）。"""
    global _shared_client
    if _shared_client is None or _shared_client.is_closed:
        with _client_lock:
            if _shared_client is None or _shared_client.is_closed:
                _shared_client = httpx.Client(
                    headers=_HEADERS,
                    timeout=httpx.Timeout(_TIMEOUT, connect=2.0),
                    limits=httpx.Limits(max_keepalive_connections=20, max_connections=50, keepalive_expiry=30.0),
                    follow_redirects=True,
                )
    return _shared_client


def close_shared_client() -> None:
    """应用生命周期关闭时安全释放外部 HTTP 连接池。"""
    global _shared_client
    with _client_lock:
        if _shared_client is not None:
            try:
                _shared_client.close()
            except Exception:  # noqa: BLE001
                pass
            _shared_client = None


def _request(
    method: str,
    url: str,
    *,
    params: dict[str, Any] | None = None,
    json_body: dict[str, Any] | None = None,
    encoding: str | None = None,
    retries: int = _RETRY,
    headers: dict[str, str] | None = None,
    cookies: dict[str, str] | None = None,
    timeout: float | None = None,
) -> Any:
    """限速 + 重试地执行一次 HTTP 请求，返回 text 或 json。

    :param encoding: 指定时返回 text（并按该编码解码），否则返回 json。
    :param json_body: 以 ``application/json`` 提交的请求体（POST 用；中证指数
        名录接口要求 JSON body，而 httpx 的 ``json=`` 与 ``params`` 互不冲突）。
    :param headers: 自定义请求头（会与默认头合并，同名覆盖）。
    :param cookies: 请求 cookies（dict 格式）。
    """
    merged = {**_HEADERS, **(headers or {})}
    last: Exception | None = None
    client = get_shared_client()
    for i in range(max(1, retries)):
        _throttle()
        try:
            r = client.request(
                method, url, params=params, json=json_body, headers=merged, cookies=cookies,
                timeout=timeout if timeout is not None else _TIMEOUT,
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
    """600519.SH -> sh / 000001.SZ -> sz / 430047.BJ -> bj / 00700.HK -> hk。"""
    suffix = symbol.split(".")[-1].upper()
    return {"SH": "sh", "SZ": "sz", "BJ": "bj", "HK": "hk"}.get(suffix, "sh")


def em_secid(symbol: str) -> str:
    """600519.SH -> 1.600519（东财 secid：1=沪 0=深/京 116=港股）。"""
    sym = symbol.strip().upper()
    if sym.endswith(".HK"):
        from ..domain.hk_rules import hk_symbol_to_code
        return f"116.{hk_symbol_to_code(sym)}"
    code = symbol_to_code(symbol)
    return f"{'1' if market_prefix(symbol) == 'sh' else '0'}.{code}"


def tencent_code(symbol: str) -> str:
    """600519.SH -> sh600519 / 00700.HK -> hk00700（腾讯行情代码）。"""
    sym = symbol.strip().upper()
    if sym.endswith(".HK"):
        from ..domain.hk_rules import hk_symbol_to_code
        return f"hk{hk_symbol_to_code(sym)}"
    return f"{market_prefix(symbol)}{symbol_to_code(symbol)}"


def sina_symbol(symbol: str) -> str:
    """600519.SH -> sh600519 / 00700.HK -> rt_hk00700（新浪行情代码）。"""
    sym = symbol.strip().upper()
    if sym.endswith(".HK"):
        from ..domain.hk_rules import hk_symbol_to_code
        return f"rt_hk{hk_symbol_to_code(sym)}"
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


def _quote_from_local_parquet(symbol: str, data_root: Any = None) -> dict:
    """当所有外部实时网络源均不可用时的离线/兜底源：从本地 Parquet 读取最新已收盘 Bar。"""
    from .parquet_store import read_symbol_dataset
    from ..core.config import get_settings, PROJECT_ROOT
    import polars as pl

    s = get_settings()
    root = data_root or s.DATA_ROOT
    sym = symbol.strip().upper()
    df = pl.DataFrame()
    bar_dir = root / "daily_bar"
    if bar_dir.exists():
        files = sorted(bar_dir.glob(f"symbol={sym}/*.parquet"))
        if not files:
            files = sorted(bar_dir.glob(f"symbol={sym}.*/*.parquet"))
        if files:
            df = pl.read_parquet(files, columns=["date", "close", "turnover", "volume"])
    if df.is_empty():
        df = read_symbol_dataset("daily_bar", symbol, columns=["date", "close", "turnover", "volume"])
    if df.is_empty() and data_root is None and "test" not in str(s.DATA_ROOT).lower():
        prod_root = PROJECT_ROOT / "data" / "parquet" / "daily_bar"
        files = sorted(prod_root.glob(f"symbol={sym}/*.parquet"))
        if not files:
            files = sorted(prod_root.glob(f"symbol={sym}.*/*.parquet"))
        if files:
            df = pl.read_parquet(files, columns=["date", "close", "turnover", "volume"])
    if df.is_empty() or df.height < 1:
        raise RuntimeError(f"本地无 {symbol} 的日线数据")
    last_row = df.tail(1)
    close = float(last_row["close"][0]) if last_row["close"][0] is not None else None
    prev_close = float(df["close"][-2]) if df.height >= 2 and df["close"][-2] is not None else close
    pct = round((close / prev_close - 1) * 100, 2) if (close and prev_close and prev_close > 0) else 0.0
    turnover = float(last_row["turnover"][0]) if "turnover" in last_row.columns and last_row["turnover"][0] is not None else None
    as_of = str(last_row["date"][0])
    return {
        "price": close,
        "prev_close": prev_close,
        "pct": pct,
        "turnover": turnover,
        "outer_vol": None,
        "inner_vol": None,
        "float_cap_yi": None,
        "total_cap_yi": None,
        "pe_ttm": None,
        "pb": None,
        "quote_time": f"{as_of} 15:00:00",
        "source": "local_parquet_cache",
    }


def fetch_quote(symbol: str) -> dict:
    """实时快照（双源降级：腾讯优先，新浪兜底）。"""
    return _first_source(
        [("tencent", lambda: fetch_tencent_quote(symbol)),
         ("sina", lambda: fetch_sina_quote(symbol))],
        "实时快照",
    )


# ---------------- 批量实时行情（§3.3，一次 HTTP 拉 N 只） ----------------
def _parse_tencent_batch(text: str, symbols: list[str]) -> dict[str, dict]:
    """解析腾讯批量行情响应（私有协议，兼容 A 股与港股标的）。

    A股字段索引：1=名称 3=现价 4=昨收 5=今开 6=成交量(手) 30=时间(YYYYMMDDHHMMSS)
    31=涨跌额 32=涨跌% 33=最高 34=最低 37=成交额(万元) 38=换手率
    44=流通市值(亿) 45=总市值(亿) 47=涨停价 48=跌停价。

    港股字段索引（v_hk00700 / v_r_hk00700）：
    1=名称 2=代码 3=现价 4=昨收 5=今开 6=成交量 30=时间(YYYY/MM/DD HH:MM:SS)
    31=涨跌额 32=涨跌% 33=最高 34=最低 37=成交额(元，原生即为元)
    45=总市值(亿) 60=每手股数 75=币种
    """
    code_by_tencent = {tencent_code(s): s for s in symbols}
    out: dict[str, dict] = {}
    for chunk in text.replace(";", "").split("v_"):
        if '="' not in chunk:
            continue
        var, body = chunk.split('="', 1)
        var_clean = var.strip()
        sym = code_by_tencent.get(var_clean)
        if sym is None and var_clean.startswith("r_"):
            sym = code_by_tencent.get(var_clean[2:])
        if sym is None:
            continue
        f = body.rstrip('"\n').split("~")
        if len(f) < 35 or not f[3]:
            continue

        def _f(idx: int) -> float | None:  # noqa: B023 循环内定义仅在本轮使用
            try:
                v = f[idx].strip()
                return float(v) if v else None
            except (ValueError, IndexError):
                return None

        is_hk = sym.upper().endswith(".HK")
        ts = f[30].strip() if len(f) > 30 else ""
        as_of = None
        if len(ts) == 14 and ts.isdigit():
            as_of = f"{ts[:4]}-{ts[4:6]}-{ts[6:8]} {ts[8:10]}:{ts[10:12]}:{ts[12:14]}"
        elif "/" in ts or "-" in ts:
            as_of = ts.replace("/", "-")

        vol_hand = _f(6)
        bids = [
            {"price": _f(9), "volume": _f(10)},
            {"price": _f(11), "volume": _f(12)},
            {"price": _f(13), "volume": _f(14)},
            {"price": _f(15), "volume": _f(16)},
            {"price": _f(17), "volume": _f(18)},
        ]
        asks = [
            {"price": _f(19), "volume": _f(20)},
            {"price": _f(21), "volume": _f(22)},
            {"price": _f(23), "volume": _f(24)},
            {"price": _f(25), "volume": _f(26)},
            {"price": _f(27), "volume": _f(28)},
        ]

        if is_hk:
            # 港股 f[37] 原生即为元；f[60] 为每手股数；f[75] 为币种
            amount_val = _f(37)
            turnover_val = _f(38) if len(f) > 38 else None
            total_cap = _f(45) if len(f) > 45 else None
            float_cap = _f(44) if (len(f) > 44 and f[44].strip()) else total_cap
            lot_size = int(_f(60)) if (len(f) > 60 and _f(60)) else None
            currency = f[75].strip() if len(f) > 75 else "HKD"
            out[sym] = {
                "symbol": sym,
                "name": f[1].strip() or None,
                "price": _f(3),
                "pct": _f(32),
                "open": _f(5),
                "high": _f(33),
                "low": _f(34),
                "prev_close": _f(4),
                "volume": vol_hand,
                "volume_hand": vol_hand,
                "volume_shares": (vol_hand * (lot_size or 100.0)) if vol_hand is not None else None,
                "amount": amount_val,
                "turnover": turnover_val,
                "float_cap_yi": float_cap,
                "total_cap_yi": total_cap,
                "limit_up": None,
                "limit_down": None,
                "lot_size": lot_size,
                "currency": currency,
                "as_of": as_of,
                "source": "tencent",
                "bids": bids,
                "asks": asks,
            }
        else:
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
                "volume": vol_hand,
                "volume_hand": vol_hand,
                "volume_shares": (vol_hand * 100.0) if vol_hand is not None else None,
                "amount": amount_wan * 1e4 if amount_wan is not None else None,
                "turnover": _f(38),
                "float_cap_yi": _f(44),
                "total_cap_yi": _f(45),
                "limit_up": _f(47) if len(f) > 47 else None,
                "limit_down": _f(48) if len(f) > 48 else None,
                "lot_size": 100,
                "currency": "CNY",
                "as_of": as_of,
                "source": "tencent",
                "bids": bids,
                "asks": asks,
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


# ---------------- 全球外盘指数报价（美股/亚太：腾讯首选 + 新浪热备） ----------------
# 腾讯代码映射（覆盖美股）
GLOBAL_INDEX_TENCENT_CODES: dict[str, str] = {
    ".DJI": "usDJI",
    ".IXIC": "usIXIC",
    ".INX": "usINX",
}

# 新浪代码映射（覆盖美股热备 + 亚太实盘行情：日经225 b_NKY / 韩国综合 b_KOSPI）
GLOBAL_INDEX_SINA_CODES: dict[str, str] = {
    ".DJI": "gb_dji",
    ".IXIC": "gb_ixic",
    ".INX": "gb_inx",
    "日经225指数": "b_NKY",
    "首尔综合指数": "b_KOSPI",
}

# 腾讯 ``us*`` 行的字段布局（2026-10-04 实测；与 A 股 ``sh*`` 行**不同**，
# 故**不得**复用 ``_parse_tencent_batch``）：
#   1=名称 2=symbol 3=现价 4=昨收 5=今开 6=成交量 30=时间 31=涨跌额 32=涨跌% 33=最高 34=最低 35=币种
# ⚠️ ``f[30]`` 是**已格式化**的 19 字符串 ``'YYYY-MM-DD HH:MM:SS'``
#    （A 股那套是 14 位 ``YYYYMMDDHHMMSS``，两者不可混用）。
# ⚠️ 恒生 ``hkHSI`` 虽是另一套（斜杠日期 ``'2026/10/02 18:31:07'``），当前**不在**本块范围。
_TENCENT_GLOBAL_MIN_FIELDS = 33


def fetch_global_index_quotes_sina(symbols: list[str]) -> dict[str, dict]:
    """新浪外盘与全球指数实时报价接口（美股三大指数热备 + 亚太指数实盘）。

    :param symbols: 展示用 symbol（如 ``[".DJI", "日经225指数", "首尔综合指数"]``）；
    :returns: ``{symbol: {"price", "prev_close", "pct", "as_of", "source": "sina"}}``。
    """
    pairs = [(s, GLOBAL_INDEX_SINA_CODES[s]) for s in symbols if s in GLOBAL_INDEX_SINA_CODES]
    if not pairs:
        return {}
    q = ",".join(m for _, m in pairs)
    try:
        text = _request(
            "GET", f"https://hq.sinajs.cn/list={q}",
            headers={"Referer": "https://finance.sina.com.cn"},
            encoding="gbk",
        )
    except Exception as e:
        logger.debug(f"[realtime] 新浪全球指数拉取失败: {e}")
        return {}

    out: dict[str, dict] = {}
    for line in (text or "").strip().split(";"):
        line = line.strip()
        if not line or '="' not in line:
            continue
        head, body = line.split('="', 1)
        varname = head.replace("var hq_str_", "").replace("hq_str_", "").strip()
        f = body.rstrip('"\n').split(",")
        matching_sym = None
        for sym, code in pairs:
            if code == varname:
                matching_sym = sym
                break
        if not matching_sym:
            continue

        if varname.startswith("gb_"):
            if len(f) < 5 or not f[1].strip():
                continue
            try:
                price = float(f[1]) if f[1] else None
                pct = float(f[2]) if f[2] else None
                as_of = f[3].strip() if len(f) > 3 and f[3] else None
                chg = float(f[4]) if len(f) > 4 and f[4] else 0.0
                prev_close = round(price - chg, 2) if price is not None else None
                if price is not None:
                    out[matching_sym] = {
                        "price": price,
                        "prev_close": prev_close,
                        "pct": pct,
                        "as_of": as_of,
                        "source": "sina",
                    }
            except (ValueError, IndexError):
                continue
        elif varname.startswith("b_"):
            if len(f) < 9 or not f[1].strip():
                continue
            try:
                price = float(f[1]) if f[1] else None
                pct = float(f[3]) if f[3] else None
                prev_close = float(f[8]) if len(f) > 8 and f[8] else None
                d = f[6].strip() if len(f) > 6 else ""
                t = f[7].strip() if len(f) > 7 else ""
                as_of = f"{d} {t}".strip() if d else None
                if price is not None:
                    out[matching_sym] = {
                        "price": price,
                        "prev_close": prev_close,
                        "pct": pct,
                        "as_of": as_of,
                        "source": "sina",
                    }
            except (ValueError, IndexError):
                continue
    return out


def fetch_global_index_quotes_tencent(symbols: list[str]) -> dict[str, dict]:
    """腾讯外盘指数报价（一次 HTTP 拉多只；只取"头部数值"需要的几项）。

    用途：市场概览「外盘」块里美股 3 项的实时源（带数据源时间戳），
    新浪日线作为托底与迷你走势线来源。

    :param symbols: 展示用 symbol（如 ``[".DJI", ".IXIC", ".INX"]``）；
        不在 :data:`GLOBAL_INDEX_TENCENT_CODES` 中的直接跳过（如实缺报）。
    :returns: ``{symbol: {"price", "prev_close", "pct", "as_of"}}``，
        ``as_of`` 为数据源自带时间戳（可能为 None）。
    :raises DataSourceUnavailable: 请求层失败（由上层降级链处理）。
    """
    pairs = [(s, GLOBAL_INDEX_TENCENT_CODES[s]) for s in symbols
             if s in GLOBAL_INDEX_TENCENT_CODES]
    if not pairs:
        return {}
    want = {tcode: sym for sym, tcode in pairs}
    text = _request("GET", "https://qt.gtimg.cn/q=" + ",".join(t for _, t in pairs),
                    encoding="gbk")
    if '="' not in text:
        raise RuntimeError("腾讯外盘行情返回格式异常")
    out: dict[str, dict] = {}
    for chunk in text.replace(";", "").split("v_"):
        if '="' not in chunk:
            continue
        var, body = chunk.split('="', 1)
        sym = want.get(var.strip())
        if sym is None:
            continue
        f = body.rstrip('"\n').split("~")
        if len(f) < _TENCENT_GLOBAL_MIN_FIELDS or not f[3].strip():
            continue

        def _f(idx: int) -> float | None:  # noqa: B023 循环内定义仅在本轮使用
            try:
                v = f[idx].strip()
                return float(v) if v else None
            except (ValueError, IndexError):
                return None

        price, prev = _f(3), _f(4)
        if price is None or prev is None:
            continue
        out[sym] = {
            "price": price,
            "prev_close": prev,
            "pct": _f(32),
            "as_of": f[30].strip() or None,
            "source": "tencent",
        }
    return out


def fetch_global_index_quotes_multi(symbols: list[str]) -> dict[str, dict]:
    """全球指数实时报价多源聚合（腾讯首选 -> 新浪热备与亚太实盘增强）。

    覆盖：美股三大指数（腾讯首选，新浪热备）、亚太指数（日经225、韩国KOSPI，新浪直连）。
    """
    out: dict[str, dict] = {}
    try:
        out = fetch_global_index_quotes_tencent(symbols)
    except Exception as e:
        logger.debug(f"[realtime] 腾讯外盘行情异常 ({e})，准备走新浪热备")

    missing = [s for s in symbols if s not in out and s in GLOBAL_INDEX_SINA_CODES]
    if missing:
        try:
            sina_res = fetch_global_index_quotes_sina(missing)
            out.update(sina_res)
        except Exception as e:
            logger.debug(f"[realtime] 新浪全球指数热备失败: {e}")

    return out


fetch_global_index_quotes = fetch_global_index_quotes_multi


def fetch_hk_quotes_batch(symbols: list[str]) -> tuple[list[dict], str]:
    """批量港股实时快照（双源降级：腾讯优先，新浪兜底）。

    :param symbols: 如 ``['00700.HK', '09988.HK']`` 或 ``['00700', '09988']``。
    :returns: ``(quotes_list, source_name)``。
    """
    normalized = [s.strip().upper() if s.strip().upper().endswith(".HK") else f"{s.strip().upper()}.HK" for s in symbols]
    return fetch_quotes_batch(normalized)



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

        def _f(x: str) -> float | None:  # noqa: B023
            try:
                v = x.strip()
                return float(v) if v else None
            except (ValueError, IndexError):
                return None

        is_hk = sym.upper().endswith(".HK")
        if is_hk:
            if len(f) < 19 or not f[6]:
                continue
            price = _f(f[6])
            prev = _f(f[3])
            pct = _f(f[8])
            open_p = _f(f[2])
            high = _f(f[4])
            low = _f(f[5])
            amount = _f(f[11])
            vol_share = _f(f[12])
            d, t = f[17].strip(), f[18].strip()
            as_of_str = f"{d.replace('/', '-')} {t}" if d and t else None
            out[sym] = {
                "symbol": sym,
                "name": f[1].strip() or None,
                "price": price,
                "pct": pct if pct is not None else (round((price / prev - 1) * 100, 2) if price and prev else None),
                "open": open_p,
                "high": high,
                "low": low,
                "prev_close": prev,
                "volume": vol_share / 100 if vol_share is not None else None,
                "volume_hand": vol_share / 100 if vol_share is not None else None,
                "volume_shares": vol_share,
                "amount": amount,
                "turnover": None,
                "float_cap_yi": None,
                "total_cap_yi": None,
                "limit_up": None,
                "limit_down": None,
                "lot_size": None,
                "currency": "HKD",
                "as_of": as_of_str,
                "source": "sina",
            }
        else:
            if len(f) < 32 or not f[3]:
                continue
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
                "volume_hand": vol_share / 100 if vol_share is not None else None,
                "volume_shares": vol_share,
                "amount": _f(f[9]),                                          # 元
                "turnover": None,
                "float_cap_yi": None,
                "total_cap_yi": None,
                "limit_up": None,
                "limit_down": None,
                "lot_size": 100,
                "currency": "CNY",
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
    """个股主力资金净流入（多源冗余 + 本地快照兜底）。"""
    try:
        data = _first_source(
            [("em-test", lambda: _fetch_em_fflow("https://push2test.eastmoney.com", symbol)),
             ("em-delay", lambda: _fetch_em_fflow("https://push2delay.eastmoney.com", symbol)),
             ("em-his", lambda: _fetch_em_fflow("https://push2his.eastmoney.com", symbol))],
            "主力资金流",
        )
        _save_local_json_cache("fund_flow", f"main_{symbol}", data)
        return data
    except Exception as e:
        cached = _load_local_json_cache("fund_flow", f"main_{symbol}")
        if cached:
            logger.info(f"[realtime] 个股主力资金流命中本地快照: {symbol}")
            return cached
        raise


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
        retries=1,
        timeout=2.5,
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
        retries=1,
        timeout=2.5,
    )
    diff = ((data or {}).get("data") or {}).get("diff") or []
    if not diff:
        raise RuntimeError(f"东财{sector_type}返回空")
    return [d for d in diff if isinstance(d, dict)]


def fetch_market_fund_flow() -> list[dict]:
    """大盘资金流日线（多源降级：``push2test`` → ``push2delay`` → ``push2his`` → 本地快照）。"""
    try:
        data = _first_source(
            [("em-test", lambda: _fetch_em_market_fflow("https://push2test.eastmoney.com")),
             ("em-delay", lambda: _fetch_em_market_fflow("https://push2delay.eastmoney.com")),
             ("em-his", lambda: _fetch_em_market_fflow("https://push2his.eastmoney.com"))],
            "大盘资金流",
        )
        _save_local_json_cache("fund_flow", "market", data)
        return data
    except Exception as e:
        cached = _load_local_json_cache("fund_flow", "market")
        if cached:
            logger.info(f"[realtime] 大盘资金流外部源失败 ({type(e).__name__})，命中本地快照")
            return cached
        raise


def fetch_sector_fund_flow(sector_type: str = "行业资金流") -> list[dict]:
    """行业/概念/地域板块资金流排名（多源降级：``push2test`` → ``push2delay`` → ``push2his`` → 本地快照）。"""
    try:
        data = _first_source(
            [("em-test", lambda: _fetch_em_sector_fflow("https://push2test.eastmoney.com", sector_type)),
             ("em-delay", lambda: _fetch_em_sector_fflow("https://push2delay.eastmoney.com", sector_type)),
             ("em-his", lambda: _fetch_em_sector_fflow("https://push2his.eastmoney.com", sector_type))],
            f"{sector_type}排名",
        )
        _save_local_json_cache("fund_flow", f"sector_{sector_type}", data)
        return data
    except Exception as e:
        cached = _load_local_json_cache("fund_flow", f"sector_{sector_type}")
        if cached:
            logger.info(f"[realtime] 板块资金流外部源失败 ({type(e).__name__})，命中本地快照")
            return cached
        raise


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


# ---------------- 本地 JSON 容灾快照辅助 ----------------
def _save_local_json_cache(subpath: str, filename: str, data: Any) -> None:
    try:
        import json
        from ..core.config import get_settings
        c_dir = get_settings().DATA_ROOT / "cache" / subpath
        c_dir.mkdir(parents=True, exist_ok=True)
        with open(c_dir / f"{filename}.json", "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False)
    except Exception as e:
        logger.debug(f"[realtime] 保存本地快照失败 {subpath}/{filename}: {e}")


def _load_local_json_cache(subpath: str, filename: str) -> Any:
    try:
        import json
        from ..core.config import get_settings
        path = get_settings().DATA_ROOT / "cache" / subpath / f"{filename}.json"
        if path.exists():
            with open(path, "r", encoding="utf-8") as f:
                return json.load(f)
    except Exception as e:
        logger.debug(f"[realtime] 读取本地快照失败 {subpath}/{filename}: {e}")
    return None


# ---------------- 股东信息 ----------------
_DC_HOSTS = [
    "https://datacenter-web.eastmoney.com",
    "https://datacenter.eastmoney.com",
]


def _dc_get(report_name: str, filter_expr: str, sort_columns: str,
            sort_types: str, page_size: int) -> list[dict]:
    """东财 datacenter 通用查询（双主机热备容灾，3.5s 快速失败超时）。"""
    params = {
        "reportName": report_name,
        "columns": "ALL",
        "filter": filter_expr,
        "pageNumber": 1,
        "pageSize": page_size,
        "sortColumns": sort_columns,
        "sortTypes": sort_types,
        "source": "WEB",
        "client": "WEB",
    }
    for host in _DC_HOSTS:
        try:
            data = _request(
                "GET",
                f"{host}/api/data/v1/get",
                params=params,
                timeout=3.5,
                retries=1,
            )
            rows = ((data or {}).get("result") or {}).get("data") or []
            if rows:
                return rows
        except Exception as e:
            logger.debug(f"[realtime] _dc_get on {host} failed: {e}")
    return []


def _fetch_sina_top10_float_holders(code: str) -> list[dict]:
    """新浪 F10 流通股东备选网络源。"""
    from io import StringIO
    import pandas as pd

    url = f"https://vip.stock.finance.sina.com.cn/corp/go.php/vCI_CirculateStockHolder/stockid/{code}.phtml"
    try:
        text = _request("GET", url, encoding="gbk", timeout=4.0, headers={"User-Agent": "Mozilla/5.0"})
        if not text:
            return []
        dfs = pd.read_html(StringIO(text))
        for df in dfs:
            t_str = df.to_string()
            if ("截止日期" in t_str or "ֹ" in t_str) and ("股东" in t_str or "ɶ" in t_str):
                rows: list[dict] = []
                for _, row in df.iterrows():
                    first_val = str(row.values[0]).strip()
                    if first_val.isdigit() and 1 <= int(first_val) <= 10:
                        rank = int(first_val)
                        name = str(row.values[1]).strip() if len(row.values) > 1 else ""
                        hold_num: float | None = None
                        pct: float | None = None
                        try:
                            hold_num = float(str(row.values[2]).replace(",", "").strip())
                        except Exception:
                            pass
                        try:
                            pct = float(str(row.values[3]).replace("%", "").strip())
                        except Exception:
                            pass
                        rows.append({
                            "rank": rank,
                            "name": name,
                            "hold_num": hold_num,
                            "pct_of_float": pct,
                            "change": None,
                            "holder_type": None,
                            "source": "sina",
                        })
                        if len(rows) >= 10:
                            break
                if rows:
                    return rows
    except Exception as e:
        logger.debug(f"[realtime] sina top10 float holders failed for {code}: {e}")
    return []


def fetch_holder_num(symbol: str) -> dict:
    """股东户数（最新一期 + 环比变化；双主机东财 → 本地快照兜底）。"""
    code = symbol_to_code(symbol)
    try:
        rows = _dc_get(
            "RPT_HOLDERNUMLATEST",
            f'(SECURITY_CODE="{code}")',
            "END_DATE", "-1", 1,
        )
        if rows:
            r = rows[0]
            res = {
                "end_date": str(r.get("END_DATE"))[:10],
                "holder_num": _to_float(r.get("HOLDER_NUM")),
                "prev_holder_num": _to_float(r.get("PRE_HOLDER_NUM")),
                "change_ratio": _to_float(r.get("HOLDER_NUM_RATIO")),  # %
                "avg_market_cap": _to_float(r.get("AVG_MARKET_CAP")),  # 户均持股市值（元）
                "avg_hold_num": _to_float(r.get("AVG_HOLD_NUM")),      # 户均持股数
            }
            _save_local_json_cache("holders", f"{symbol}_num", res)
            return res
    except Exception as e:
        logger.debug(f"[realtime] fetch_holder_num remote failed for {symbol}: {e}")

    # 本地快照兜底
    cached = _load_local_json_cache("holders", f"{symbol}_num")
    if cached:
        logger.info(f"[realtime] 命中股东户数本地快照: {symbol}")
        cached["source"] = "local-cache"
        return cached

    raise RuntimeError("股东户数数据为空")


def fetch_top10_float_holders(symbol: str) -> list[dict]:
    """十大流通股东（最新报告期；双主机东财 → 新浪 F10 → 本地快照兜底）。"""
    code = symbol_to_code(symbol)
    # ① 东财双主机尝试
    try:
        rows = _dc_get(
            "RPT_F10_EH_FREEHOLDERS",
            f'(SECUCODE="{code}.{market_prefix(symbol).upper()}")',
            "END_DATE,HOLDER_RANK", "-1,1", 10,
        )
        if rows:
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
            if out:
                _save_local_json_cache("holders", f"{symbol}_top10", out)
                return out
    except Exception as e:
        logger.debug(f"[realtime] fetch_top10_float_holders em failed for {symbol}: {e}")

    # ② 备选网络源：新浪 F10 流通股东
    try:
        sina_out = _fetch_sina_top10_float_holders(code)
        if sina_out:
            _save_local_json_cache("holders", f"{symbol}_top10", sina_out)
            return sina_out
    except Exception as e:
        logger.debug(f"[realtime] fetch_top10_float_holders sina failed for {symbol}: {e}")

    # ③ 本地快照兜底
    cached = _load_local_json_cache("holders", f"{symbol}_top10")
    if cached:
        logger.info(f"[realtime] 命中十大流通股东本地快照: {symbol}")
        return cached

    raise RuntimeError("十大流通股东数据为空")


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
        for r in df.to_dict("records")
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
        for r in raw.head(limit).to_dict("records")
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
    """最新一期财报关键指标（东财 datacenter 双主机 → 本地快照 → 本地 SQLite PIT 财报兜底）。

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
    try:
        rows = _dc_get(
            "RPT_LICO_FN_CPD",
            f'(SECURITY_CODE="{code}")',
            "REPORTDATE", "-1", 1,
        )
        if rows:
            r = rows[0]
            revenue = _to_float(r.get("TOTAL_OPERATE_INCOME"))
            profit = _to_float(r.get("PARENT_NETPROFIT"))
            net_margin: float | None = None
            if revenue and profit is not None and revenue > 0:
                net_margin = round(profit / revenue * 100, 4)

            res = {
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
            _save_local_json_cache("financials", f"{symbol}_indicators", res)
            return res
    except Exception as e:
        logger.debug(f"[realtime] fetch_financial_indicators remote failed for {symbol}: {e}")

    # ① 本地快照兜底
    cached = _load_local_json_cache("financials", f"{symbol}_indicators")
    if cached:
        logger.info(f"[realtime] 命中财务指标本地快照: {symbol}")
        cached["source"] = "local-cache"
        return cached

    # ② 本地 SQLite PIT 财报兜底
    try:
        from .panels import _fundamentals_from_local
        from ..core.config import PROJECT_ROOT
        local_fin = _fundamentals_from_local(symbol)
        if not local_fin:
            prod_db = PROJECT_ROOT / "data" / "sqlite" / "aqp.db"
            if prod_db.exists():
                local_fin = _fundamentals_from_local(symbol, db_path=prod_db)
        if local_fin:
            logger.info(f"[realtime] 命中财务指标本地 SQLite 兜底: {symbol}")
            return local_fin
    except Exception as e:
        logger.debug(f"[realtime] fetch_financial_indicators local sqlite failed: {e}")

    raise RuntimeError("财务指标数据为空")


# ---------------- 基准指数（Beta 计算用） ----------------
def _fetch_benchmark_daily_tencent(symbol: str) -> "Any":
    """备选源：腾讯指数日线接口（无需 AKShare，直接请求腾讯日K）。"""
    import pandas as pd
    url = f"https://web.ifzq.gtimg.cn/appstock/app/fqkline/get?param={symbol},day,,,640,qfq"
    data = _request("GET", url, timeout=4.0)
    stock_data = (data or {}).get("data", {}).get(symbol, {})
    days = stock_data.get("day", []) or stock_data.get("qfqday", [])
    if not days:
        raise RuntimeError(f"腾讯指数日线返回空: {symbol}")
    rows = []
    for d in days:
        if len(d) >= 6:
            rows.append({
                "date": d[0],
                "open": float(d[1]),
                "close": float(d[2]),
                "high": float(d[3]),
                "low": float(d[4]),
                "volume": float(d[5]) * 100,
            })
    df = pd.DataFrame(rows)
    df["date"] = pd.to_datetime(df["date"]).dt.date
    return df[["date", "open", "high", "low", "close", "volume"]]


def fetch_benchmark_daily(symbol: str = "sh000300") -> "Any":
    """基准指数日线（三重防线：新浪/AKShare → 腾讯直接日线 → 本地 Parquet 持久化缓存）。"""
    import pandas as pd
    from .parquet_store import atomic_write_parquet
    from ..core.config import get_settings

    settings = get_settings()
    bench_dir = settings.DATA_ROOT / "benchmarks"
    cache_path = bench_dir / f"{symbol}.parquet"

    # ① 优先尝试新浪 / AKShare 外部源拉取并刷新本地持久化缓存
    try:
        df = _ak().stock_zh_index_daily(symbol=symbol)
        if df is not None and not df.empty:
            try:
                bench_dir.mkdir(parents=True, exist_ok=True)
                atomic_write_parquet(cache_path, df)
            except Exception as e_save:
                logger.debug(f"[realtime] 基准缓存落盘失败: {e_save}")
            return df
    except Exception as e:
        logger.warning(f"[realtime] AKShare 获取基准指数 {symbol} 失败 ({type(e).__name__})，尝试腾讯网络降级")

    # ② 备用网络源：腾讯指数直接日线接口
    try:
        tx_df = _fetch_benchmark_daily_tencent(symbol)
        if tx_df is not None and not tx_df.empty:
            try:
                bench_dir.mkdir(parents=True, exist_ok=True)
                atomic_write_parquet(cache_path, tx_df)
            except Exception as e_save:
                logger.debug(f"[realtime] 腾讯基准缓存落盘失败: {e_save}")
            return tx_df
    except Exception as e:
        logger.warning(f"[realtime] 腾讯获取基准指数 {symbol} 失败 ({type(e).__name__})，尝试本地缓存降级")

    # ③ 全部网络源失败时，读取本地持久化基准 Parquet 缓存
    if cache_path.exists():
        try:
            cached_df = pd.read_parquet(cache_path)
            if cached_df is not None and not cached_df.empty:
                logger.info(f"[realtime] 命中基准指数 {symbol} 本地持久化缓存 ({len(cached_df)} 行)")
                return cached_df
        except Exception as e_read:
            logger.warning(f"[realtime] 读取本地基准缓存异常: {e_read}")

    raise RuntimeError(f"基准指数 {symbol} 全部外部源失败且无本地缓存")

