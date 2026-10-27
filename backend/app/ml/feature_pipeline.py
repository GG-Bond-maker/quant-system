"""Shared PIT feature construction for scheduled, CLI and online inference paths."""
from __future__ import annotations

import sqlite3
from typing import Any

import numpy as np
import pandas as pd

from ..core.config import get_settings
from ..data.parquet_store import read_symbol_dataset
from ..data.text_ingest import attach_text_features
from .features import apply_propagate, build_factors
from .financial_factors import build_financial_factors


def build_feature_panel(raw: Any, universe: list[str] | None = None) -> pd.DataFrame:
    """Join price/volume, graph, disclosed financial and lagged text features."""
    panel = apply_propagate(build_factors(raw), universe=universe)
    if panel.empty:
        return panel
    panel = panel.reset_index(drop=True)
    panel["date"] = pd.to_datetime(panel["date"])
    path = get_settings().SQLITE_PATH
    financials = pd.DataFrame()
    if path.exists():
        with sqlite3.connect(f"file:{path.as_posix()}?mode=ro", uri=True) as conn:
            if conn.execute("SELECT 1 FROM sqlite_master WHERE name='financial_report'").fetchone():
                financials = pd.read_sql_query("SELECT * FROM financial_report WHERE is_proxy_announce=0", conn)
    groups = {symbol: group for symbol, group in financials.groupby("symbol")} if not financials.empty else {}
    block = build_financial_factors(panel, source=lambda symbol, _: groups.get(symbol, pd.DataFrame()), enabled=True)
    panel = pd.concat([panel, block], axis=1)
    # EPS/BPS are per unadjusted share; never divide them by hfq prices.
    closes = []
    for symbol in panel["symbol"].unique():
        bars = read_symbol_dataset("daily_bar", str(symbol))
        if not bars.is_empty():
            frame = bars.select(["date", "close"]).to_pandas().rename(columns={"close": "_unadjusted_close"})
            frame["date"] = pd.to_datetime(frame["date"])
            frame["symbol"] = symbol
            closes.append(frame)
    if closes:
        panel = panel.merge(pd.concat(closes), on=["symbol", "date"], how="left", validate="one_to_one")
        close = panel.pop("_unadjusted_close").where(lambda values: values > 0)
        panel["f_earnings_yield"] = panel["f_eps"] / close
        panel["f_book_to_price"] = panel["f_bps"] / close
    else:
        panel["f_earnings_yield"] = np.nan
        panel["f_book_to_price"] = np.nan
    return attach_text_features(panel).sort_values(["symbol", "date"]).reset_index(drop=True)
