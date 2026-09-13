"""Fix unit-anomaly rows (volume in 手, turnover in percent) across all parquet datasets.

Detection (universal, works for adjusted datasets):
  expected_r = raw_close / adj_close          (from daily_bar, the unadjusted set)
  r_row      = amount / (close * volume)
  scale      = r_row / expected_r
  scale ≈ 100  -> bad row: volume is 手 (÷100 vs 股), turnover is percent (×100 vs fraction)
  scale ≈ 1    -> good row

Fix: volume *= 100, turnover /= 100.
Modified parquet files are backed up before rewriting.
"""
import shutil
import polars as pl
from pathlib import Path
from datetime import date

ROOT = Path(__file__).resolve().parents[1]
DATA = ROOT / "data" / "parquet"
BACKUP = ROOT / "data" / "backup_turnover_fix_20260902"
DATASETS = ["daily_bar", "daily_bar_qfq", "daily_bar_hfq"]


def load_raw_close() -> dict[tuple[str, date], float]:
    """Build (symbol, date) -> raw close map from unadjusted daily_bar."""
    raw: dict[tuple[str, date], float] = {}
    for p in (DATA / "daily_bar").glob("symbol=*/year=*.parquet"):
        sym = p.parent.name.split("=", 1)[1]
        df = pl.read_parquet(p, columns=["date", "close"])
        for d, c in zip(df["date"], df["close"]):
            raw[(sym, d)] = float(c)
    return raw


def main() -> None:
    raw_close = load_raw_close()
    total_fixed = 0
    for ds in DATASETS:
        fixed_in_ds = 0
        for p in sorted((DATA / ds).glob("symbol=*/year=*.parquet")):
            sym = p.parent.name.split("=", 1)[1]
            df = pl.read_parquet(p)
            if df.height == 0 or "volume" not in df.columns:
                continue
            scale = pl.struct(["date", "close", "volume", "amount"]).map_elements(
                lambda s: (
                    (s["amount"] / (s["close"] * s["volume"])) / (raw_close.get((sym, s["date"]), s["close"]) / s["close"])
                    if s["volume"] and s["amount"] and s["close"] and s["amount"] > 0 and s["volume"] > 0
                    else None
                ),
                return_dtype=pl.Float64,
            ).alias("__scale")
            df = df.with_columns(scale)
            bad = df.filter(
                pl.col("__scale").is_not_null()
                & (pl.col("__scale") > 80) & (pl.col("__scale") < 125)
            )
            if bad.height == 0:
                continue
            # backup once before first modification of this file
            dst = BACKUP / ds / p.parent.name / p.name
            dst.parent.mkdir(parents=True, exist_ok=True)
            if not dst.exists():
                shutil.copy2(p, dst)
            for row in bad.iter_rows(named=True):
                fixed_in_ds += 1
                print(f"FIX {ds} {sym} {row['date']} vol {row['volume']:.0f}->{row['volume']*100:.0f} "
                      f"turnover {row['turnover']:.6f}->{row['turnover']/100:.8f} scale={row['__scale']:.2f}")
            df = df.with_columns(
                pl.when(
                    pl.col("__scale").is_not_null()
                    & (pl.col("__scale") > 80) & (pl.col("__scale") < 125)
                )
                .then(pl.col("volume") * 100)
                .otherwise(pl.col("volume"))
                .alias("volume"),
                pl.when(
                    pl.col("__scale").is_not_null()
                    & (pl.col("__scale") > 80) & (pl.col("__scale") < 125)
                )
                .then(pl.col("turnover") / 100)
                .otherwise(pl.col("turnover"))
                .alias("turnover"),
            ).drop("__scale")
            df.write_parquet(p, compression="snappy")
        print(f"=== {ds}: fixed {fixed_in_ds} rows ===")
        total_fixed += fixed_in_ds
    print(f"TOTAL fixed: {total_fixed}")


if __name__ == "__main__":
    main()
