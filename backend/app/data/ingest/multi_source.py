"""多行情源冗余：AKShare（内含 东财→新浪→BaoStock 降级）→ Eastmoney HTTP。

优先级严格固定；正常情况下只调用 AKShare，仅真实失败才降级。
所有源统一输出 AQP 标准 Polars Schema（含 source 列）：
    date(Date) open/high/low/close/volume/amount(Float64) symbol/code/source(String)

- AKShare：``fetch_daily_bar``，其内部已含 东财 → 新浪 → BaoStock 三级降级；
- Eastmoney：kline HTTP 接口（timeout + retry + 限速 + schema 校验）。

⚠️ 2026-09-30：Eastmoney 主机由 ``push2his`` 切至 ``push2test``。
``push2his`` / ``push2delay`` / ``push2`` / ``82.push2`` 整组被东财应用层阻断
（TLS 握手成功但 HTTP 零字节断开，**代理与直连表现完全相同** ⇒ 非本机代理问题）；
``push2test`` 为同接口同字段的可用主机（实测 fqt=0/1/2 三种复权模式均正常返回）。

⚠️ 2026-09-26：**Tushare 源已彻底移除**（用户无 Tushare 积分，项目硬性禁止
``import tushare`` 与任何 Tushare Token）。本模块及全项目不得再引入该源。
"""
from __future__ import annotations

import time
from collections.abc import Callable

import pandas as pd
import polars as pl
from loguru import logger

from .akshare_adapter import _throttle, fetch_daily_bar

_EM_KLINE_URL = "https://push2test.eastmoney.com/api/qt/stock/kline/get"

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
    from ...domain.a_share_rules import code_to_symbol

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


# ---------------- 源 2：Eastmoney HTTP ----------------
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
    ("eastmoney", _from_eastmoney),
]


def fetch_daily_bar_multi(
    code: str, start: str, end: str, adjust: str = ""
) -> tuple[pl.DataFrame, str]:
    """按 :data:`SOURCES` 顺序依次尝试各源，返回 (标准 DataFrame, source)；
    全部失败则抛最终异常。

    2026-09-26：Tushare 分支已彻底移除（本项目无 Tushare 积分，硬约束禁用），
    现为 ``akshare → eastmoney`` 两源。
    """
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
