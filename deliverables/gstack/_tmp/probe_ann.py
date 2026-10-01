import sys; sys.path.insert(0,".")
import polars as pl, datetime as dt
df = pl.read_parquet("D:/Python_Project/Alpha Quant Platform/data/parquet/announcements/symbol=__all__/year=2026.snappy.parquet")
print("rows:", df.height, "cols:", df.columns)
print("pub_date min/max:", df["pub_date"].min(), df["pub_date"].max())
cov = df.group_by("pub_date").agg(pl.len().alias("n")).sort("pub_date", descending=True).head(5)
print("recent days:"); 
for r in cov.iter_rows(): print("  ", r[0], r[1])
print("null counts:", {c: df[c].null_count() for c in df.columns})
# sentiment 分布
if "sentiment" in df.columns:
    print("sentiment:", df.group_by("sentiment").agg(pl.len()).sort("len", descending=True).head(8).to_dicts())
print("type:", df.group_by("type").agg(pl.len()).sort("len", descending=True).head(8).to_dicts())
