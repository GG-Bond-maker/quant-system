"""分红送配（P1-4）完整性校验工具。

[2026-09-22 §8.2 第 17 项 死代码清理] 原先另有 `fetch_dividends` / `save_dividends` /
`load_dividends_asof` 三个函数：全仓**零调用方**（仅定义处），且 save 写 SQLite、
load 读 Parquet（**存储介质自相矛盾**，`dividend_split` 数据集从未落盘）⇒ 整条
"分红送配"链路**从未接线**。已删除；将来接线时应**同时**决定单一存储介质并补
端到端用例，而不是复活这三段互相不一致的旧代码。
本模块只保留**有测试守护**的覆盖率校验 :func:`ex_date_coverage`（供完整性审计）。
"""
from __future__ import annotations

import polars as pl


def ex_date_coverage(df: pl.DataFrame) -> float:
    """ex_date 非空覆盖率（0~1）。"""
    if df.is_empty():
        return 0.0
    return 1.0 - df["ex_date"].null_count() / df.height
