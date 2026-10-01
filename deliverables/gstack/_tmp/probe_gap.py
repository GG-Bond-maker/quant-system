import polars as pl, glob, os, datetime as dt
ROOT = "D:/Python_Project/Alpha Quant Platform/data/parquet"
# 交易日历
import sys; sys.path.insert(0,".")
from app.data.calendar_store import load_from_db
try:
    cal = load_from_db()
    print("calendar type:", type(cal))
except Exception as e:
    print("cal load fail", e); cal=None

# daily_bar 尾部每日覆盖
files = sorted(glob.glob(os.path.join(ROOT,"daily_bar/symbol=*/year=2026.snappy.parquet")))
df = pl.read_parquet(files, columns=["symbol","date"])
cov = df.group_by("date").agg(pl.len().alias("n")).sort("date", descending=True).head(20)
print("\n=== daily_bar 2026 尾部覆盖 ===")
for r in cov.iter_rows(): print(f"  {r[0]} n={r[1]}")

# qfq 尾部
fq = sorted(glob.glob(os.path.join(ROOT,"daily_bar_qfq/symbol=*/year=2026.snappy.parquet")))
dfq = pl.read_parquet(fq, columns=["symbol","date"])
print("\n=== qfq max date ===", dfq["date"].max(), " raw max =", df["date"].max())

# 09-29 是哪只？
d29 = df.filter(pl.col("date")==dt.date(2026,9,29))
print("\n09-29 标的:", d29["symbol"].unique().to_list())
d28 = df.filter(pl.col("date")==dt.date(2026,9,28))
print("09-28 标的数:", d28["symbol"].n_unique(), "例:", d28["symbol"].unique().to_list()[:8])
