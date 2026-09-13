"""数据校验（P1-1 validate 步骤）：每项返回 (ok, errors)，失败即阻断流水线。"""
from __future__ import annotations

from typing import Any

import polars as pl


def _pl(df: Any) -> pl.DataFrame:
    import pandas as pd

    if isinstance(df, pl.DataFrame):
        return df
    if isinstance(df, pd.DataFrame):
        return pl.from_pandas(df)
    raise TypeError(f"unsupported df type: {type(df)}")


def check_ohlc(df: Any) -> tuple[bool, list[str]]:
    """high >= max(open, close)，low <= min(open, close)，close > 0。"""
    d = _pl(df)
    need = {"open", "high", "low", "close"}
    if not need.issubset(d.columns):
        return False, [f"缺少列 {sorted(need - set(d.columns))}"]
    bad = d.filter(
        (pl.col("high") < pl.max_horizontal("open", "close"))
        | (pl.col("low") > pl.min_horizontal("open", "close"))
        | (pl.col("close") <= 0)
    ).height
    return (bad == 0), ([f"OHLC 关系错误 {bad} 行"] if bad else [])


def check_no_nulls(df: Any, cols: list[str]) -> tuple[bool, list[str]]:
    d = _pl(df)
    errs = [f"列 {c} 含 {d[c].null_count()} 个空值"
            for c in cols if c in d.columns and d[c].null_count() > 0]
    return (not errs), errs


def check_volume(df: Any) -> tuple[bool, list[str]]:
    d = _pl(df)
    if "volume" not in d.columns:
        return False, ["缺少 volume"]
    bad = d.filter(pl.col("volume") < 0).height
    return (bad == 0), ([f"volume < 0 共 {bad} 行"] if bad else [])


def check_duplicates(df: Any, keys: list[str]) -> tuple[bool, list[str]]:
    d = _pl(df)
    if not set(keys).issubset(d.columns):
        return False, [f"缺少去重键 {keys}"]
    dup = d.select(keys).is_duplicated().sum()
    return (dup == 0), ([f"键 {keys} 重复 {dup} 行"] if dup else [])


def check_pct_limit(df: Any, max_abs: float = 0.45) -> tuple[bool, list[str]]:
    """涨跌幅上界：主板首日 44% + 浮点余量；超过说明数据异常。"""
    d = _pl(df)
    if "pct" not in d.columns:
        return True, []
    bad = d.filter(pl.col("pct").abs() > max_abs).height
    return (bad == 0), ([f"|pct| > {max_abs:.0%} 共 {bad} 行"] if bad else [])


def validate_daily_bar(df: Any) -> tuple[bool, list[str]]:
    """日线数据综合校验：任一失败 => (False, errors)。"""
    errs: list[str] = []
    for fn in (lambda: check_ohlc(df),
               lambda: check_no_nulls(df, ["date", "symbol", "open", "high", "low", "close"]),
               lambda: check_volume(df),
               lambda: check_duplicates(df, ["symbol", "date"]),
               lambda: check_pct_limit(df)):
        ok, e = fn()
        errs.extend(e)
    return (not errs), errs
