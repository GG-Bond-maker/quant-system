"""
AKShare 数据采集适配器（AQP）。

职责：
1. 把 AKShare 返回的中文列名 DataFrame 统一转换成 AQP 标准 Polars Schema
   （date: Date, open/high/low/close/volume/amount: Float64, symbol/code: String）；
2. 线程安全限速：任意两次 AKShare 请求间隔 >= AKSHARE_RATE_LIMIT 秒 + 随机抖动，
   防止东方财富封临时 IP（threading.Lock 保证多线程并发下的限速正确性）；
3. Tenacity 重试：指数退避（1s -> 2s -> 4s ...），默认 AKSHARE_RETRY=3 次；
4. 批量并发：ThreadPoolExecutor（max_workers 默认 4，不建议超过 4，
   总 QPS ~= max_workers / AKSHARE_RATE_LIMIT）。

⚠️ akshare 采用惰性导入：本模块被 API 进程 import 时不加载 akshare，
仅在实际调用拉取函数时才加载，保证服务启动速度与依赖解耦。
"""
from __future__ import annotations

import random
import threading
import time
from collections.abc import Callable
from datetime import datetime
from types import ModuleType
from typing import Any

import pandas as pd
import polars as pl
from loguru import logger
from tenacity import (
    retry,
    retry_if_exception_type,
    stop_after_attempt,
    wait_exponential,
)

from ...core.config import get_settings
from ...domain.a_share_rules import code_to_symbol

# ---------- AKShare 惰性加载 ----------
_ak_module: ModuleType | None = None
_ak_lock = threading.Lock()


def _ak() -> ModuleType:
    """惰性导入并缓存 akshare 模块（线程安全）。"""
    global _ak_module
    if _ak_module is None:
        with _ak_lock:
            if _ak_module is None:
                import akshare as ak  # 延迟到首次调用

                _ak_module = ak
    return _ak_module


def get_akshare() -> ModuleType:
    """``_ak`` 的公开别名（Task 16）：api/services 层经此取 akshare 模块，
    保留惰性导入特性；业务层不再直接引用私有名 _ak。"""
    return _ak()


# ---------- 线程安全限速 ----------
_last_call: float = 0.0
_throttle_lock = threading.Lock()


def _throttle() -> None:
    """限速：确保任意两次 AKShare 调用间隔不小于 AKSHARE_RATE_LIMIT 秒（含随机抖动）。

    使用 threading.Lock 保证多线程并发场景下的全局限速正确。
    """
    global _last_call
    s = get_settings()
    jitter = random.uniform(0, s.AKSHARE_RATE_LIMIT * 0.3)
    gap = s.AKSHARE_RATE_LIMIT + jitter
    with _throttle_lock:
        need = _last_call + gap
        now = time.time()
        if now < need:
            time.sleep(need - now)
        _last_call = time.time()


def _retry_decorator() -> Any:
    """构造重试装饰器（读取配置，避免模块导入期依赖 Settings 单例）。"""
    return retry(
        stop=stop_after_attempt(max(1, get_settings().AKSHARE_RETRY)),
        wait=wait_exponential(multiplier=1, min=1, max=8),
        retry=retry_if_exception_type((Exception,)),
        reraise=True,
    )


def _safe_call(func: Callable[..., Any], *args: Any, **kwargs: Any) -> Any:
    """限速 + 重试地执行一次 AKShare 调用。"""
    return _retry_decorator()(_throttled_call)(func, *args, **kwargs)


def _throttled_call(func: Callable[..., Any], *args: Any, **kwargs: Any) -> Any:
    _throttle()
    return func(*args, **kwargs)


# ---------- 列名映射（东方财富日线接口） ----------
_RENAME_DAILY: dict[str, str] = {
    "日期": "date",
    "开盘": "open",
    "收盘": "close",
    "最高": "high",
    "最低": "low",
    "成交量": "volume",
    "成交额": "amount",
    "振幅": "amplitude",
    "涨跌幅": "pct",
    "涨跌额": "change",
    "换手率": "turnover",
}


def _standardize_daily(df: pd.DataFrame, code: str) -> pl.DataFrame:
    """把 AKShare 日线原始 DataFrame 规范化为 AQP 标准 Polars Schema。

    标准列：date(Date) / open,high,low,close,volume,amount(Float64)
            / symbol,code(String) / pct,turnover,amplitude,change(Float64, 若源提供)
    """
    if df is None or df.empty:
        return pl.DataFrame()
    df = df.rename(columns={k: v for k, v in _RENAME_DAILY.items() if k in df.columns})
    if "date" not in df.columns:
        # Task 13：主源（东财）返回了**非空**响应却没有 date 列 —— 说明接口列名
        # 已变更或解析失败。抛带上下文的可读异常，避免下游 ``.sort("date")`` 抛出
        # 难以定位的 polars 列缺失错误；空响应（停牌/退市）已在上面正常返回空表。
        raise ValueError(
            f"东方财富日线响应缺 date 列（code={code}, rows={len(df)}, "
            f"实际列={list(df.columns)}）——主源接口可能已变更")
    df["date"] = pd.to_datetime(df["date"], errors="coerce").dt.date
    df["code"] = code
    df["symbol"] = code_to_symbol(code)
    num_cols = ["open", "high", "low", "close", "volume", "amount", "pct", "turnover"]
    for c in num_cols:
        if c in df.columns:
            df[c] = pd.to_numeric(df[c], errors="coerce").astype("float64")
    df["source"] = "akshare"
    keep = [c for c in df.columns if c in {
        "date", "symbol", "code", "open", "high", "low", "close",
        "volume", "amount", "pct", "turnover", "amplitude", "change", "source",
    }]
    return pl.from_pandas(df[keep]).sort("date")


# ---------- 单只拉取 ----------
def _sina_symbol(code: str) -> str:
    """把 6 位代码转成新浪接口格式：600519 -> sh600519。"""
    if code.startswith(("6", "9")):
        return f"sh{code}"
    if code.startswith(("0", "2", "3")):
        return f"sz{code}"
    if code.startswith(("4", "8")):
        return f"bj{code}"
    raise ValueError(f"无法识别的代码前缀: {code}")


def _fetch_daily_bar_em(code: str, start: str, end: str, adjust: str) -> pl.DataFrame:
    """主源：东方财富 stock_zh_a_hist（中文列名）。"""
    df = _safe_call(
        _ak().stock_zh_a_hist,
        symbol=code,
        period="daily",
        start_date=start.replace("-", ""),
        end_date=end.replace("-", ""),
        adjust=adjust,
    )
    return _standardize_daily(df, code)


def _missing_date_diagnostic(
    source: str, code: str, columns: Any, rows: int
) -> str:
    """构造"日线响应缺 date 列"的可读诊断串（含数据源/代码/实际列/行数）。

    消息刻意写清三要素，便于一眼区分是"数据源不覆盖"还是"接口变更"。
    """
    return (f"{source}日线响应缺 date 列（code={code}, rows={rows}, "
            f"实际列={list(columns)}）——可能该源不覆盖此标的、被限流或接口已变更")


def _fetch_daily_bar_sina(code: str, start: str, end: str, adjust: str) -> pl.DataFrame:
    """备用源：新浪 stock_zh_a_daily（已是英文列名；北交所可能不支持，best-effort）。

    ⚠️ 健壮性（Task 13）：新浪对**新上市/无历史/被限流**的标的可能返回畸形或空响应体，
    此时 akshare 内部（``stock_zh_a_sina.py`` 解密后执行 ``data_df["date"]``）会抛**裸
    ``KeyError('date')``**，早于本函数的空值判断，最终向上冒泡污染失败统计。
    这里将该 KeyError 等价识别为「该源暂无此标的数据」，记 WARNING 诊断后返回空 DataFrame
    （空数据在下游按"无数据"处理，不算抓取故障）；**网络类异常（ConnectionError /
    超时等）一律不被拦截，正常向上抛出**。

    另外，若新浪正常返回了**非空** DataFrame 却缺 ``date`` 列（接口列名变更），同样按
    无数据处理并记录诊断，不抛裸 ``KeyError``。
    """
    symbol = _sina_symbol(code)
    try:
        df = _safe_call(
            _ak().stock_zh_a_daily,
            symbol=symbol,
            start_date=start.replace("-", ""),
            end_date=end.replace("-", ""),
            adjust=adjust,
        )
    except KeyError as e:
        # akshare 内部因响应体异常（空/畸形）对缺失 date 字段抛裸 KeyError —— 按无数据处理
        logger.warning(
            f"[sina] {_missing_date_diagnostic('新浪', code, (), 0)}: "
            f"symbol={symbol} adjust={adjust!r} err={e!r} -> 按无数据处理")
        return pl.DataFrame()
    if df is None or df.empty:
        return pl.DataFrame()
    df = df.copy()
    if "date" not in df.columns:
        logger.warning(
            f"[sina] {_missing_date_diagnostic('新浪', code, df.columns, len(df))}: "
            f"symbol={symbol} adjust={adjust!r} -> 按无数据处理")
        return pl.DataFrame()
    df["date"] = pd.to_datetime(df["date"], errors="coerce").dt.date
    df["code"] = code
    df["symbol"] = code_to_symbol(code)
    for c in ["open", "high", "low", "close", "volume", "amount"]:
        if c in df.columns:
            df[c] = pd.to_numeric(df[c], errors="coerce").astype("float64")
    df["source"] = "akshare"
    keep = [c for c in df.columns if c in {
        "date", "symbol", "code", "open", "high", "low", "close",
        "volume", "amount", "pct", "turnover", "amplitude", "change", "source",
    }]
    return pl.from_pandas(df[keep]).sort("date")


def fetch_daily_bar(code: str, start: str, end: str, adjust: str = "") -> pl.DataFrame:
    """拉取单只股票日线，返回标准 Polars DataFrame。

    双源冗余：主源东方财富 stock_zh_a_hist，失败（东财对部分网络/IP 直接断连）
    自动降级新浪 stock_zh_a_daily，两个源都失败才向上抛错。

    ⚠️ 缺失语义（Task 13）：数据源不覆盖 / 无历史（如新上市标的）时返回**空 DataFrame**
    （下游按"无数据"处理，不算故障）；只有网络类异常或主源结构性异常（非空却缺 date
    列）才会**抛异常**，不会静默吞掉。

    :param code:   6 位纯数字代码，如 "600519"
    :param start:  起始日期 "YYYYMMDD" 或 "YYYY-MM-DD"
    :param end:    结束日期
    :param adjust: "" 不复权 / "qfq" 前复权 / "hfq" 后复权
    """
    try:
        return _fetch_daily_bar_em(code, start, end, adjust)
    except Exception as e:
        logger.warning(f"eastmoney daily fetch fail {code}: {e!r} -> fallback to sina")
        return _fetch_daily_bar_sina(code, start, end, adjust)


# ---------- 交易日历 ----------
def fetch_trade_calendar(start: str = "20000101", end: str | None = None) -> pd.DataFrame:
    """拉取 A 股交易日历，返回 DataFrame[trade_date, is_sh, is_sz, is_bj, week]。

    end=None 时保留数据源返回的全部日期（新浪源通常含当年剩余交易日）。
    ⚠️ 不能默认截断到"今天"：日历被截断后 prev_trade_day 会停在旧日期，
    增量同步目标日随之冻结，新交易日行情永远同步不进来。
    显式传 end（如 CLI 指定截止日）时才做上界过滤。
    """
    df = _safe_call(_ak().tool_trade_date_hist_sina)
    df["trade_date"] = pd.to_datetime(df["trade_date"]).dt.date
    lo = datetime.strptime(start, "%Y%m%d").date()
    if end is not None:
        hi = datetime.strptime(end, "%Y%m%d").date()
        df = df[(df["trade_date"] >= lo) & (df["trade_date"] <= hi)].copy()
    else:
        df = df[df["trade_date"] >= lo].copy()
    df = df.assign(is_sh=True, is_sz=True, is_bj=True)
    df["week"] = pd.to_datetime(df["trade_date"]).dt.weekday + 1
    return df[["trade_date", "is_sh", "is_sz", "is_bj", "week"]].reset_index(drop=True)


# ---------- 证券列表 ----------
def fetch_stock_list() -> pl.DataFrame:
    """拉取 A 股 + 北交所证券列表，统一为标准 Schema。"""
    df = _safe_call(_ak().stock_info_a_code_name)
    df = df.rename(columns={"code": "code", "name": "name"})
    df["symbol"] = df["code"].apply(code_to_symbol)
    df["market"] = df["symbol"].str.split(".").str[1]
    df["instrument_type"] = "stock"
    # ST 判定：证券简称含 "ST"（覆盖 ST/*ST/S*ST），名称来源真实、口径可解释
    df["is_st"] = df["name"].str.upper().str.contains("ST", na=False)
    return pl.from_pandas(
        df[["code", "symbol", "name", "market", "instrument_type", "is_st"]])


# ---------- 指数日线（新浪源） ----------
# 核心指数：新浪代码 -> 展示名
CORE_INDICES: list[tuple[str, str]] = [
    ("sh000001", "上证指数"),
    ("sz399001", "深证成指"),
    ("sz399006", "创业板指"),
    ("sh000688", "科创50"),
    ("sh000300", "沪深300"),
]


def fetch_index_daily(index_code: str) -> pl.DataFrame:
    """拉取指数日线（新浪源，返回全部历史，按 date 升序）。

    :param index_code: 新浪指数代码，如 "sh000001" / "sz399006"
    """
    df = _safe_call(_ak().stock_zh_index_daily, symbol=index_code)
    if df is None or df.empty:
        return pl.DataFrame()
    df = df.copy()
    df["date"] = pd.to_datetime(df["date"]).dt.date
    for c in ["open", "high", "low", "close", "volume"]:
        if c in df.columns:
            df[c] = pd.to_numeric(df[c], errors="coerce")
    return pl.from_pandas(df).sort("date")


# ---------- 退市名单（Task 6） ----------
def fetch_delist_list() -> pl.DataFrame:
    """拉取沪深已退市证券名单，统一为 [code, name, list_date, delist_date, source]。

    ⚠️ 语义差异：深交所源提供"终止上市日期"（真实退市日）；上交所源只提供
    "暂停上市日期"（≤ 终止上市日），作为退市时点的近似——用于移出宇宙时
    会略早于真实退市，对强平减记是保守方向（更早按更接近真实的纸面价清算）。

    两个源都自带"上市日期"，故一并联回 ``list_date``（缺陷 B5-14：``instrument.
    list_date`` 实测 97.8% 为 NULL）。⚠️ 这只是**部分**回填：名单只覆盖**已退市**
    标的，在册标的那部分仍需别的来源（``scripts/enrich_instruments.py`` 从本地
    行情最早日近似）。源缺该列时如实为 null，绝不造数。
    """
    sh = _safe_call(_ak().stock_info_sh_delist)
    sz = _safe_call(_ak().stock_info_sz_delist, symbol="终止上市公司")
    frames: list[pl.DataFrame] = []
    for df, code_col, name_col, date_col, list_col, src in (
        (sh, "公司代码", "公司简称", "暂停上市日期", "上市日期", "akshare_sh_delist"),
        (sz, "证券代码", "证券简称", "终止上市日期", "上市日期", "akshare_sz_delist"),
    ):
        if df is None or df.empty:
            continue
        missing = {code_col, name_col, date_col} - set(df.columns)
        if missing:
            raise ValueError(f"退市名单源 {src} 缺少列 {missing}，接口可能已变更")
        pdf = df.rename(columns={
            code_col: "code", name_col: "name", date_col: "delist_date"}).copy()
        if list_col in pdf.columns:
            pdf = pdf.rename(columns={list_col: "list_date"})
        else:
            pdf["list_date"] = None  # 源未提供 ⇒ 如实空列
        # 源列可能是 str / datetime64 / datetime.date 混杂，统一在 pandas 侧归一
        pdf["delist_date"] = pd.to_datetime(pdf["delist_date"], errors="coerce").dt.date
        pdf["list_date"] = pd.to_datetime(pdf["list_date"], errors="coerce").dt.date
        pdf["code"] = pdf["code"].astype(str).str.strip()
        std = pl.from_pandas(pdf[["code", "name", "list_date", "delist_date"]])
        std = std.with_columns([pl.col("list_date").cast(pl.Date),
                                pl.col("delist_date").cast(pl.Date)])
        std = std.with_columns(pl.lit(src).alias("source")).drop_nulls("delist_date")
        frames.append(std)
    if not frames:
        return pl.DataFrame(schema={"code": pl.Utf8, "name": pl.Utf8,
                                    "list_date": pl.Date, "delist_date": pl.Date,
                                    "source": pl.Utf8})
    return pl.concat(frames, how="vertical_relaxed").unique(subset=["code"],
                                                            keep="first")
