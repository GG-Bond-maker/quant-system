import polars as pl, glob, os
ROOT = "D:/Python_Project/Alpha Quant Platform/data/parquet"
def dist(name, pattern, ndays=12):
    files = sorted(glob.glob(os.path.join(ROOT, pattern)))
    print(f"\n### {name}: {len(files)} files")
    if not files: return
    df = pl.read_parquet(files, columns=["symbol","date"])
    cov = df.group_by("date").agg(pl.len().alias("n")).sort("date", descending=True).head(ndays)
    for r in cov.iter_rows():
        print(f"   {r[0]}  n={r[1]}")
    mx = df["date"].max()
    n_at_max = cov.filter(pl.col("date")==mx)["n"][0]
    print(f"   >>> max(date)={mx} n_at_max={n_at_max} | max_n_overall={cov['n'].max()}")

dist("daily_bar", "daily_bar/symbol=*/year=2026.snappy.parquet")
dist("universe_daily", "universe_daily/*.parquet")
dist("daily_bar_hfq", "daily_bar_hfq/symbol=*/year=2026.snappy.parquet")
