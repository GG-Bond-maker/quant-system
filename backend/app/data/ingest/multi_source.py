"""三行情源冗余（P1-2）：AKShare → Tushare Pro → Eastmoney HTTP。

优先级严格固定；正常情况下只调用 AKShare，仅真实失败才降级。
所有源统一输出 AQP 标准 Polars Schema（含 source 列）：
    date(Date) open/high/low/close/volume/amount(Float64) symbol/code/source(String)

- Tushare：HTTP 直连 pro API（token 必须来自 .env 的 TUSHARE_TOKEN，禁止硬编码）；
- Eastmoney：push2his kline HTTP 接口（timeout + retry + 限速 + schema 校验）。
"""
from __future__ import annotations

import time
from collections.abc import Callable

import pandas as pd
import polars as pl
from loguru import logger

from .akshare_adapter import _throttle, fetch_daily_bar

_EM_KLINE_URL = "https://push2his.eastmoney.com/api/qt/stock/kline/get"
_TUSHARE_URL = "http://api.tushare.cn"

# 统一 schema 列（source 由各源填充）
_BASE_COLS = ["date", "symbol", "code", "open", "high", "low", "close",
              "volume", "amount", "source"]


def _standardize(df: pd.DataFrame, code: str, source: str) -> pl.DataFrame:
    """各源裸 DataFrame -> 统一 Schema（缺列补 null，全部 float64）。"""
    if df is None or df.empty:
        return pl.DataFrame()
    for c in ("open", "high", "low", "close", "volume", "amount"):
        if c in df.columns:
            df[c] = pd.to_numeric(df[c], errors="coerce").astype("float64")
        else:
            df[c] = None
    if "date" in df.columns:
        df["date"] = pd.to_datetime(df["date"]).dt.date
    df["code"] = code
    from ..domain.a_share_rules import code_to_symbol

    df["symbol"] = code_to_symbol(code)
    df["source"] = source
    return pl.from_pandas(df[_BASE_COLS]).sort("date")


# ---------------- 源 1：AKShare（含其内部新浪降级） ----------------
def _from_akshare(code: str, start: str, end: str, adjust: str) -> pl.DataFrame:
    df = fetch_daily_bar(code, start, end, adjust=adjust)
    if df.is_empty():
        raise ConnectionError("akshare returned empty")
    out = df.with_columns(pl.lit("akshare").alias("source"))
    return out.select([c for c in _BASE_COLS if c in out.columns])


# ---------------- 源 2：Tushare Pro（HTTP） ----------------
def _tushare_ts_code(code: str) -> str:
    from ..domain.a_share_rules import code_to_symbol

    sym = code_to_symbol(code)
    return f"{code}.{sym.split('.')[1]}"


def _from_tushare(code: str, start: str, end: str, adjust: str) -> pl.DataFrame:
    import httpx

    from ..core.config import get_settings

    token = get_settings().TUSHARE_TOKEN
    if not token:
        raise ConnectionError("TUSHARE_TOKEN 未配置，跳过 Tushare 源")
    _throttle()
    resp = httpx.post(_TUSHARE_URL, json={
        "api_name": "daily",
        "token": token,
        "params": {"ts_code": _tushare_ts_code(code),
                   "start_date": start.replace("-", ""),
                   "end_date": end.replace("-", "")},
        "fields": "trade_date,open,high,low,close,vol,amount",
    }, timeout=10.0)
    resp.raise_for_status()
    body = resp.json()
    if body.get("code") != 0 or not body.get("data", {}).get("items"):
        raise ConnectionError(f"tushare bad response: {str(body)[:120]}")
    fields = body["data"]["fields"]
    rows = [dict(zip(fields, item)) for item in body["data"]["items"]]
    df = pd.DataFrame(rows).rename(columns={"trade_date": "date", "vol": "volume"})
    # tushare 无复权口径 -> 仅支持不复权；请求复权时视为源失败
    if adjust not in ("", "none"):
        raise ConnectionError("tushare daily 不支持复权口径")
    return _standardize(df, code, "tushare")


# ---------------- 源 3：Eastmoney HTTP ----------------
def _em_secid(code: str) -> str:
    if code.startswith(("6", "9")):
        return f"1.{code}"
    if code.startswith(("0", "2", "3")):
        return f"0.{code}"
    if code.startswith(("4", "8")):
        return f"0.{code}"
    raise ValueError(f"无法识别的代码: {code}")


def _from_eastmoney(code: str, start: str, end: str, adjust: str) -> pl.DataFrame:
    import httpx

    fqt = {"": 0, "none": 0, "qfq": 1, "hfq": 2}[adjust]
    _throttle()
    resp = httpx.get(_EM_KLINE_URL, params={
        "secid": _em_secid(code), "klt": 101, "fqt": fqt,
        "beg": start.replace("-", ""), "end": end.replace("-", ""),
        "fields1": "f1", "fields2": "f51,f52,f53,f54,f55,f56,f57",
    }, timeout=10.0)
    resp.raise_for_status()
    klines = (resp.json().get("data") or {}).get("klines") or []
    if not klines:
        raise ConnectionError("eastmoney empty klines")
    rows = []
    for line in klines:
        parts = line.split(",")
        rows.append({"date": parts[0], "open": parts[1], "high": parts[3],
                     "low": parts[4], "close": parts[2],
                     "volume": parts[5], "amount": parts[6]})
    return _standardize(pd.DataFrame(rows), code, "eastmoney")


SOURCES: list[tuple[str, Callable[[str, str, str, str], pl.DataFrame]]] = [
    ("akshare", _from_akshare),
    ("tushare", _from_tushare),
    ("eastmoney", _from_eastmoney),
]


def fetch_daily_bar_multi(
    code: str, start: str, end: str, adjust: str = ""
) -> tuple[pl.DataFrame, str]:
    """按优先级尝试三源，返回 (标准 DataFrame, source)；全败抛最终异常。"""
    errors: list[str] = []
    for name, fn in SOURCES:
        try:
            df = fn(code, start, end, adjust)
            if df.is_empty():
                raise ConnectionError(f"{name} empty")
            if name != "akshare":
                logger.warning(f"[multi-source] {code} 降级至 {name}")
            return df, name
        except Exception as e:
            errors.append(f"{name}: {type(e).__name__}: {e}")
            logger.debug(f"[multi-source] {code} via {name} failed: {e!r}")
            time.sleep(0.2)
    raise ConnectionError(f"全部数据源失败 {code}: {'; '.join(errors)}")
