"""Cross-check 2026-08-28 rows for all candidate bad symbols across the 3 datasets."""
import polars as pl
from pathlib import Path

base = Path(__file__).resolve().parents[1] / "data" / "parquet"
DATE = "2026-08-28"

# All symbols that showed a bad row in any dataset
symbols = ["000001", "000007", "000008", "000009", "000014", "000017", "000019",
           "000025", "000026", "000027", "000028", "000030", "000031", "000032",
           "000036", "000037", "000039", "000048", "000049", "000059", "000060",
           "000063", "000068", "000069"]

for sym in symbols:
    code = f"{sym}.SZ"
    for ds in ["daily_bar", "daily_bar_qfq", "daily_bar_hfq"]:
        p = base / ds / f"symbol={code}" / "year=2026.snappy.parquet"
        if not p.exists():
            print(f"{code} {ds}: NO FILE")
            continue
        df = pl.read_parquet(p)
        if "date" in df.columns and df["date"].dtype == pl.Utf8:
            row = df.filter(pl.col("date") == DATE)
        else:
            row = df.filter(pl.col("date").cast(pl.Utf8).str.starts_with(DATE))
        if row.height == 0:
            print(f"{code} {ds}: no row on {DATE}")
            continue
        r = row["amount"][0] / (row["close"][0] * row["volume"][0]) if row["volume"][0] and row["close"][0] else None
        print(f"{code} {ds}: close={row['close'][0]:.2f} vol={row['volume'][0]:.0f} "
              f"amt={row['amount'][0]:.0f} turnover={row['turnover'][0]} r={r:.2f}" if r else
              f"{code} {ds}: close={row['close'][0]} vol={row['volume'][0]} amt={row['amount'][0]} turnover={row['turnover'][0]} r=None")
