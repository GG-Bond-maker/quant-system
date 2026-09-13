"""Scan parquet datasets for unit-anomaly rows: r = amount / (close * volume)."""
import polars as pl
from pathlib import Path

base = Path(__file__).resolve().parents[1] / "data" / "parquet"
for ds in ["daily_bar", "daily_bar_qfq", "daily_bar_hfq"]:
    rows = []
    for p in (base / ds).glob("symbol=*/year=*.parquet"):
        df = pl.read_parquet(p)
        if not {"close", "volume", "amount", "turnover"}.issubset(df.columns):
            continue
        bad = df.with_columns(
            (pl.col("amount") / (pl.col("close") * pl.col("volume"))).alias("r")
        ).filter(
            (pl.col("volume") > 0)
            & (pl.col("amount") > 0)
            & (pl.col("r") > 20) & (pl.col("r") < 500)
        )
        if bad.height:
            for row in bad.iter_rows(named=True):
                rows.append((p.parts[-2], row["date"], row["volume"], round(row["turnover"], 6), round(row["r"], 2)))
    print(f"=== {ds}: {len(rows)} bad rows ===")
    for r in sorted(rows, key=lambda x: x[1]):
        print(f"{r[0]} {r[1]} vol={r[2]:.0f} turnover={r[3]} r={r[4]}")
