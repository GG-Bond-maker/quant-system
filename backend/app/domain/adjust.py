"""
复权算法（纯函数，Polars 驱动，无 IO）。

规则（与通达信/东方财富通用约定一致）：
- 前复权 qfq：以最新价为基准，历史价向下调整（能和现价直接比较，K 线默认）；
- 后复权 hfq：以上市首日为基准，所有价格乘累计因子（用于长期收益率计算）；
- 不复权 none：直接返回原始价。

复权因子 adj_factor 约定（后复权累计因子）：
    raw_close * adj_factor = 当日相对于基点的可比价（后复权价）
因此：
    hfq = raw * adj_factor
    qfq = raw * adj_factor / adj_factor_latest   （按 symbol 取该 symbol 最新因子）

数学不变量（ValidatorSuite 关键用例）：
    qfq 最后一日收盘价 == raw 最后一日收盘价（今日价格不被"再修正"）。
"""
from __future__ import annotations

from typing import Any

import polars as pl

_ADJUST_MODES = {"qfq", "hfq", "none"}


def _to_pl(df: Any) -> pl.DataFrame:
    """把 pandas DataFrame / dict 列表 / Polars DataFrame 统一转为 Polars。"""
    if isinstance(df, pl.DataFrame):
        return df
    if isinstance(df, (list, tuple)):
        return pl.DataFrame(df)
    import pandas as pd

    if isinstance(df, pd.DataFrame):
        return pl.from_pandas(df)
    raise TypeError(f"unsupported df type: {type(df)}")


def apply_adjust_df(
    df: Any,
    factor_df: Any,
    adjust: str = "qfq",
    price_cols: tuple[str, ...] = ("open", "high", "low", "close"),
) -> pl.DataFrame:
    """对 OHLCV 应用复权，返回带调整价的新 DataFrame（列名保持不变）。

    :param df:        行情表，必须含 date/symbol + OHLC 列；
    :param factor_df: 复权因子表，列 date/symbol/adj_factor（后复权累计因子）；
    :param adjust:    qfq | hfq | none；
    :return:          调整后的 Polars DataFrame（内部按 symbol+date 排序）。
    """
    if adjust not in _ADJUST_MODES:
        raise ValueError(f"adjust must be one of {sorted(_ADJUST_MODES)}, got {adjust}")
    bar = _to_pl(df)
    if adjust == "none":
        return bar

    fac = _to_pl(factor_df).select(["symbol", "date", "adj_factor"])
    merged = (
        bar.sort(["symbol", "date"])
        .join(fac, on=["symbol", "date"], how="left")
        # 未匹配到因子（上市首日之前等）填 1，等价于不复权
        .with_columns(pl.col("adj_factor").fill_null(1.0))
    )

    if adjust == "hfq":
        factor_col = pl.col("adj_factor")
        drop_cols = ["adj_factor"]
    else:  # qfq：每个 symbol 取最新（最后一行）因子作为基准
        latest = merged.group_by("symbol").agg(pl.col("adj_factor").last().alias("f_today"))
        merged = merged.join(latest, on="symbol", how="left")
        factor_col = pl.col("adj_factor") / pl.col("f_today")
        drop_cols = ["adj_factor", "f_today"]

    exprs = [pl.col(c) * factor_col for c in price_cols]
    return merged.with_columns(exprs).drop(drop_cols)
