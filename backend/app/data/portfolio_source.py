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

import pandas as pd

from ..cache import memory

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
    """600519 -> sh600519；000001/300750 -> sz000001；510300 -> sh510300；159919 -> sz159919。"""
    return ("sh" if code.startswith(("6", "5")) else "sz") + code


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


def fetch_asset_close(code: str, asset_type: str, start: str, end: str) -> pd.Series:
    """获取单只资产的前复权收盘价序列（索引为 date）。

    主源为东方财富；网络不通时自动降级：股票 -> 腾讯，ETF -> 新浪。
    全部调用经 _safe_call（全局限速 + 重试），结果带 10 分钟进程内 TTL 缓存。
    """
    cache_key = f"aqp:pf:close:{asset_type}:{code}:{start}:{end}"
    cached = memory.lru_get(cache_key)
    if cached is not None:
        return cached  # type: ignore[no-any-return]

    ak, safe = _safe_ak()
    s_start = _to_ak_date(start)
    s_end = _to_ak_date(end)

    df = None
    # 主源：东方财富
    try:
        if asset_type == "etf":
            df = safe(ak.fund_etf_hist_em, symbol=code, period="daily",
                      start_date=s_start, end_date=s_end, adjust="qfq")
        else:
            df = safe(ak.stock_zh_a_hist, symbol=code, period="daily",
                      start_date=s_start, end_date=s_end, adjust="qfq")
    except Exception:  # noqa: BLE001
        df = None

    # 备源：腾讯（股票，支持前复权）/ 新浪（ETF，全量历史后裁剪）
    if df is None or df.empty:
        sym = _market_symbol(code)
        if asset_type == "etf":
            df = _slice_by_date(safe(ak.fund_etf_hist_sina, symbol=sym), start, end)
        else:
            df = safe(ak.stock_zh_a_hist_tx, symbol=sym, start_date=s_start,
                      end_date=s_end, adjust="qfq")

    df = _normalize_col(df)
    df["date"] = _parse_date_series(df["date"])
    df = df.drop_duplicates("date").set_index("date").sort_index()
    result = df["close"].rename(code)
    memory.lru_set(cache_key, result, ttl=_PRICE_CACHE_TTL)
    return result


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
