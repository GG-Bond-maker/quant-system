"""Measure true-cold _build_daily (fresh process, empty _ai_stats_cache)."""
from __future__ import annotations
import sys, time
sys.path.insert(0, r"D:\Python_Project\Alpha Quant Platform\backend")

import app.api.v1.market as mk
from app.data.parquet_store import today_trade_date_or_last

td = today_trade_date_or_last()
print("ai_stats cache before:", len(mk._ai_stats_cache))
t0 = time.perf_counter()
out = mk._build_daily(td, 50)
dt = time.perf_counter() - t0
print(f"_build_daily TRUE-COLD (empty ai_stats) = {dt:.2f}s")
for k, v in out.items():
    if isinstance(v, dict):
        print(f"  {k}: {v.get('status')}")

# second call: ai_stats now cached
t0 = time.perf_counter()
out2 = mk._build_daily(td, 50)
dt2 = time.perf_counter() - t0
print(f"_build_daily second (ai_stats warm in-proc) = {dt2:.2f}s")
