"""Count external calls during a single _build_rt invocation (no prod code changes)."""
from __future__ import annotations
import sys, time
sys.path.insert(0, r"D:\Python_Project\Alpha Quant Platform\backend")

import app.data.ingest.akshare_adapter as ada
import app.data.realtime as rt

calls = {"akshare": [], "http": []}

_orig_safe = ada._safe_call
def spy_safe(func, *a, **k):
    name = getattr(func, "__name__", repr(func))
    calls["akshare"].append(name)
    return _orig_safe(func, *a, **k)
ada._safe_call = spy_safe

# realtime._request path
_orig_req = rt._request
def spy_req(method, url, **k):
    calls["http"].append(url.split("?")[0])
    return _orig_req(method, url, **k)
rt._request = spy_req

from app.api.v1.market import _build_rt, _build_heat, _build_anomalies, _build_indices
from app.data.parquet_store import today_trade_date_or_last

# also patch the symbol used in market.py
import app.api.v1.market as mk

td = today_trade_date_or_last()
t0 = time.perf_counter()
out = _build_rt(td)
dt = time.perf_counter() - t0
print(f"_build_rt wall = {dt:.2f}s")
print(f"akshare calls = {len(calls['akshare'])}: {calls['akshare']}")
print(f"realtime http calls = {len(calls['http'])}: {calls['http']}")
for blk, v in out.items():
    if isinstance(v, dict):
        print(f"  block {blk}: status={v.get('status')}")
    else:
        print(f"  {blk} = {v}")
