"""组合回测价格源（data 层）。

从 domain/portfolio.py 迁移而来：domain 层有纯函数守卫
（tests/test_domain_purity.py，AST 扫描禁止 import akshare 等 IO 库），
因此 AKShare 拉取逻辑必须放在 data 层。

CRIT-4 修复：所有 AKShare 调用经 akshare_adapter._safe_call
（全局限速 + 指数退避重试），结果带 10 分钟进程内 TTL 缓存，
避免组合回测一次请求串行 N 次无限速外呼触发源站封禁。
"""
from __future__ import annotations

from datetime import date
from typing import Any

import pandas as pd

from ..cache import memory
from ..domain.a_share_rules import market_prefix

_PRICE_CACHE_TTL = 600  # 价格序列进程内缓存（秒），日线数据日更一次足够安全


def _to_ak_date(d: str) -> str:
    """YYYY-MM-DD -> YYYYMMDD"""
    return d.replace("-", "")


def _normalize_col(df: pd.DataFrame) -> pd.DataFrame:
    """统一把中文列名转成英文并选择日期/收盘。"""
    rename = {
        "日期": "date",
        "收盘": "close",
    }
    df = df.rename(columns={k: v for k, v in rename.items() if k in df.columns})
    # 部分数据源列名本身就是 date / close，也兼容
    return df[["date", "close"]].copy()


def _parse_date_series(s: pd.Series) -> pd.Series:
    """把 2023-05-18 或 Timestamp 统一转成 date 对象。"""
    if pd.api.types.is_datetime64_any_dtype(s):
        return s.dt.date
    return pd.to_datetime(s.astype(str)).dt.date


def _market_symbol(code: str) -> str:
    """6 位代码 -> 带交易所前缀的腾讯/新浪格式：600519 -> sh600519、159915 -> sz159915。

    前缀规则**委托** :func:`app.domain.a_share_rules.market_prefix`（唯一事实
    来源），不再在此重复一份 ``6/5 -> sh else sz`` 的私有实现：
        - 消除 ``900xxx``（沪 B）/ ``4xxxxx``（北交所）被旧 else 分支错判为 ``sz`` 的隐患；
        - 与 :func:`code_to_symbol` 共用同一套前缀，避免「入库 symbol」与
          「外呼 symbol」各算各的最危险局面。
    """
    return market_prefix(code) + code


def _slice_by_date(df: pd.DataFrame, start: str, end: str) -> pd.DataFrame:
    """按日期闭区间裁剪（兜底数据源通常返回全量历史）。"""
    df = df.copy()
    df["date"] = _parse_date_series(df["date"])
    s = date.fromisoformat(start)
    e = date.fromisoformat(end)
    return df[(df["date"] >= s) & (df["date"] <= e)]


def _safe_ak():
    """惰性导入 akshare，并返回限速+重试的安全调用包装（复用 akshare_adapter）。"""
    import akshare as ak  # noqa: PLC0415

    # 复用 ingest 适配器的限速 + 重试包装（跨模块私有符号，注释豁免）
    from .ingest.akshare_adapter import _safe_call  # noqa: PLC2701

    return ak, _safe_call


#: 复权口径（basis）取值——与 api/v1/backtest.py 的 price_basis.basis 对齐
_BASIS_QFQ = "qfq"
_BASIS_RAW = "raw"

#: 单资产口径元信息挂在 Series.attrs 上的键名（domain 在 pd.concat **之前**读取）
_BASIS_ATTR_KEY = "aqp_price_basis"


def fetch_asset_close_with_meta(
    code: str, asset_type: str, start: str, end: str,
) -> tuple[pd.Series, dict[str, Any]]:
    """获取单只资产收盘价序列**及其实际生效的复权口径元信息**（索引为 date）。

    与 :func:`fetch_asset_close` 的区别：本函数额外返回该资产**实际拿到**的口径，
    供上层如实披露。背景（审计口径静默缺陷）：主源东方财富为**前复权(QFQ)**，
    但 ETF 的备用源新浪 ``fund_etf_hist_sina`` 返回的是**不复权**全量历史 —
    此前降级后口径由 QFQ 变 RAW 却无人披露，用户拿着"看起来对"的净值做决策。

    主源为东方财富（QFQ）；网络不通时自动降级：
        股票 -> 腾讯（``adjust="qfq"``，**口径不变**）；
        ETF  -> 新浪（**不复权**，口径改变 ⇒ 必须披露）。
    全部调用经 ``_safe_call``（全局限速 + 重试），结果带 10 分钟进程内 TTL 缓存。

    Returns:
        ``(series, meta)``。``meta`` 形如::

            {"code": "159915", "asset_type": "etf",
             "source": "sina", "basis": "raw", "adjusted": False}

        ``source`` ∈ ``{"eastmoney", "tencent", "sina", None}``；
        ``basis`` ∈ ``{"qfq", "raw", None}``（``None`` 仅出现在遗留裸缓存兜底分支）。
        同时把 ``meta`` 写入 ``series.attrs["aqp_price_basis"]`` —— domain 于
        ``pd.concat`` **之前**读取，故不会随 concat 丢失（见 domain/portfolio.py）。
    """
    cache_key = f"aqp:pf:close:{asset_type}:{code}:{start}:{end}"
    cached = memory.lru_get(cache_key)
    if isinstance(cached, tuple) and len(cached) == 2:
        series_cached, meta_cached = cached
        return series_cached, dict(meta_cached)  # type: ignore[arg-type]
    if cached is not None:
        # 兜底：同进程内若存在老格式（裸 Series）条目，无 meta ⇒ 口径未知。
        # 正常路径下不可能出现（本函数与 fetch_asset_close 都写 (series, meta)）。
        return cached, {"code": code, "asset_type": asset_type,
                        "source": None, "basis": None, "adjusted": None}  # type: ignore[return-value]

    ak, safe = _safe_ak()
    s_start = _to_ak_date(start)
    s_end = _to_ak_date(end)

    df = None
    source: str | None = None
    basis: str | None = None

    # 主源：东方财富（前复权）——成功且非空才记源，否则落到备源
    try:
        if asset_type == "etf":
            df = safe(ak.fund_etf_hist_em, symbol=code, period="daily",
                      start_date=s_start, end_date=s_end, adjust=_BASIS_QFQ)
        else:
            df = safe(ak.stock_zh_a_hist, symbol=code, period="daily",
                      start_date=s_start, end_date=s_end, adjust=_BASIS_QFQ)
        if df is not None and not df.empty:
            source, basis = "eastmoney", _BASIS_QFQ
    except Exception:  # noqa: BLE001
        df = None

    # 备源：腾讯（股票，前复权，口径不变）/ 新浪（ETF，全量历史后裁剪，**不复权**）
    if df is None or df.empty:
        sym = _market_symbol(code)
        if asset_type == "etf":
            df = _slice_by_date(safe(ak.fund_etf_hist_sina, symbol=sym), start, end)
            source, basis = "sina", _BASIS_RAW
        else:
            df = safe(ak.stock_zh_a_hist_tx, symbol=sym, start_date=s_start,
                      end_date=s_end, adjust=_BASIS_QFQ)
            source, basis = "tencent", _BASIS_QFQ

    df = _normalize_col(df)
    df["date"] = _parse_date_series(df["date"])
    df = df.drop_duplicates("date").set_index("date").sort_index()
    result = df["close"].rename(code)

    meta: dict[str, Any] = {
        "code": code,
        "asset_type": asset_type,
        "source": source,
        "basis": basis,
        "adjusted": basis == _BASIS_QFQ,
    }
    result.attrs[_BASIS_ATTR_KEY] = meta
    memory.lru_set(cache_key, (result, meta), ttl=_PRICE_CACHE_TTL)
    return result, dict(meta)


def fetch_asset_close(code: str, asset_type: str, start: str, end: str) -> pd.Series:
    """获取单只资产的前复权收盘价序列（索引为 date）。

    **薄封装**：签名与历史保持一致（``-> pd.Series``），内部委托
    :func:`fetch_asset_close_with_meta`。返回的 Series 上已带
    ``attrs["aqp_price_basis"]`` 口径元信息，供 domain 层在 concat 前收集披露。
    """
    series, _meta = fetch_asset_close_with_meta(code, asset_type, start, end)
    return series


def fetch_benchmark_close(code: str, start: str, end: str) -> pd.Series:
    """获取基准指数日线（收盘价）。主源东财，降级腾讯；带 TTL 缓存。"""
    cache_key = f"aqp:pf:bench:{code}:{start}:{end}"
    cached = memory.lru_get(cache_key)
    if cached is not None:
        return cached  # type: ignore[no-any-return]

    ak, safe = _safe_ak()
    df = None
    try:
        df = safe(ak.stock_zh_index_daily_em, symbol=code)
    except Exception:  # noqa: BLE001
        df = None
    if df is None or df.empty:
        # 腾讯源需要带交易所前缀；本项目基准均为沪市指数（000300/000905/000852）
        df = safe(ak.stock_zh_index_daily_tx, symbol="sh" + code)

    df = _normalize_col(df)
    df["date"] = _parse_date_series(df["date"])
    df = df.drop_duplicates("date").set_index("date").sort_index()

    s = date.fromisoformat(start)
    e = date.fromisoformat(end)
    result = df["close"].loc[s:e].rename("benchmark")
    memory.lru_set(cache_key, result, ttl=_PRICE_CACHE_TTL)
    return result
