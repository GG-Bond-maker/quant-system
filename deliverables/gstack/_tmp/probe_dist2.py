import polars as pl, glob, os
ROOT = "D:/Python_Project/Alpha Quant Platform/data/parquet"
def dist(name, pattern, ndays=10, dcol="date"):
    files = sorted(glob.glob(os.path.join(ROOT, pattern)))
    print(f"\n### {name}: {len(files)} files")
    if not files: 
        return
    try:
        df = pl.read_parquet(files)
    except Exception as e:
        print("  read FAIL", e); return
    print("  rows=", df.height, "cols=", df.columns[:12])
    if dcol in df.columns:
        cov = df.group_by(dcol).agg(pl.len().alias("n")).sort(dcol, descending=True).head(ndays)
        for r in cov.iter_rows():
            print(f"   {r[0]}  n={r[1]}")
        mx = df[dcol].max()
        n_at = cov.filter(pl.col(dcol)==mx)["n"][0]
        print(f"   >>> max={mx} n_at_max={n_at} max_n={cov['n'].max()}")
    # 非零验收
    for c in df.columns:
        if c in ("amount","volume","close","main_net","open"):
            nn_null = df[c].is_not_null().sum(); nn_zero = (df[c]!=0).sum()
            print(f"   col {c}: non_null={nn_null} non_zero={nn_zero}")

dist("universe_daily", "universe_daily/symbol=__all__/year=2026.snappy.parquet")
dist("announcements", "announcements/symbol=__all__/year=2026.snappy.parquet")
dist("daily_bar_qfq", "daily_bar_qfq/symbol=*/year=2026.snappy.parquet")
