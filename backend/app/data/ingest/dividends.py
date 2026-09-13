"""分红送配（P1-4）：akshare cninfo 接口 -> 标准schema -> SQLite + Parquet。

字段：symbol/announce_date/record_date/ex_date/dividend/bonus_share/split_ratio/source。
完整性校验：ex_date 非空覆盖率 >= 99% 为达标；支持增量（按 (symbol, ex_date) 去重）。
"""
from __future__ import annotations

from datetime import date

import pandas as pd
import polars as pl


def fetch_dividends(symbol: str, start: str = "2020-01-01",
                    end: str | None = None) -> pl.DataFrame:
    """拉取单只标的历史分红送配（cninfo 源）。字段名漂移时抛错由调用方审计。"""
    import akshare as ak

    end = end or date.today().isoformat()
    code = symbol.split(".")[0]
    df = ak.stock_dividend_cninfo(symbol=code, start_date=start.replace("-", ""),
                                  end_date=end.replace("-", ""))
    if df is None or df.empty:
        return pl.DataFrame()
    rename = {"公告日期": "announce_date", "股权登记日": "record_date",
              "除权除息日": "ex_date", "派息": "dividend",
              "送股": "bonus_share", "转增": "split_ratio"}
    df = df.rename(columns={k: v for k, v in rename.items() if k in df.columns})
    need = {"ex_date"}
    if not need.issubset(df.columns):
        raise ValueError(f"cninfo 分红字段漂移: {list(df.columns)[:8]}")
    for c in ("announce_date", "record_date", "ex_date"):
        if c in df.columns:
            df[c] = pd.to_datetime(df[c], errors="coerce").dt.date
    df["symbol"] = symbol
    df["source"] = "akshare"
    keep = ["symbol", "announce_date", "record_date", "ex_date",
            "dividend", "bonus_share", "split_ratio", "source"]
    for c in keep:
        if c not in df.columns:
            df[c] = None
    return pl.from_pandas(df[keep]).unique(subset=["symbol", "ex_date"], keep="last")


def ex_date_coverage(df: pl.DataFrame) -> float:
    """ex_date 非空覆盖率（0~1）。"""
    if df.is_empty():
        return 0.0
    return 1.0 - df["ex_date"].null_count() / df.height


def save_dividends(df: pl.DataFrame) -> int:
    """SQLite upsert（(symbol, ex_date) 幂等）。"""
    import asyncio

    from sqlalchemy.dialects.sqlite import insert as sqlite_insert

    from ...db.models import DividendSplit
    from ...db.session import get_session_factory

    rows = df.to_dicts()
    if not rows:
        return 0
    ins = sqlite_insert(DividendSplit)
    stmt = ins.on_conflict_do_update(
        index_elements=["symbol", "ex_date"],
        set_={c: ins.excluded[c] for c in
              ("announce_date", "record_date", "dividend", "bonus_share",
               "split_ratio", "source")},
    )

    async def _go() -> None:
        factory = get_session_factory()
        async with factory() as sess:
            await sess.execute(stmt, rows)
            await sess.commit()

    asyncio.run(_go())
    return len(rows)


def load_dividends_asof(asof: date) -> pl.DataFrame:
    """PIT 读取：仅返回 ex_date <= asof 的分红（未来除权不可见）。"""
    from ...core.config import get_settings

    base = get_settings().DATA_ROOT / "dividend_split" / "symbol=__all__"
    files = sorted(base.glob("year=*.parquet"))
    dfs = []
    for f in files:
        d = pl.read_parquet(f)
        if "ex_date" in d.columns:
            d = d.filter(pl.col("ex_date") <= asof)
            dfs.append(d)
    return pl.concat(dfs) if dfs else pl.DataFrame()
