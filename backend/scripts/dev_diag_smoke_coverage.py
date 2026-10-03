"""诊断：为何 step_fetch 判定「已覆盖 2 只 / 待抓 0」而磁盘上没有分区。

逐步打印 build 侧每个中间量，定位是「have 集合错」「min_date 错」还是「参数错」。
"""
from __future__ import annotations

import json
import sys
from datetime import date
from pathlib import Path

BACKEND = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(BACKEND))
PROJECT = BACKEND.parent
sys.path.insert(0, str(PROJECT / "scripts"))

from app.data.parquet_store import list_symbols_with_data, manifest_invalidate  # noqa: E402

DR = PROJECT / "data" / "parquet"
smoke = json.loads((PROJECT / "data" / "universe_smoke.json").read_text(encoding="utf-8"))
syms = [x["symbol"] for x in smoke["symbols"]]
print("冒烟标的 =", syms)

for s in syms:
    for ds in ("daily_bar", "daily_bar_hfq"):
        d = DR / ds / f"symbol={s}"
        print(f"  {ds}/symbol={s}: is_dir={d.is_dir()} "
              f"files={sorted(p.name for p in d.glob('*.parquet')) if d.is_dir() else []}")

manifest_invalidate()
have = set(list_symbols_with_data("daily_bar"))
print("len(have) =", len(have))
for s in syms:
    print(f"  {s} in have? {s in have}")

# 直接调用被测函数
import expand_universe_2500 as ex  # noqa: E402

covered, un_raw, un_hfq = ex._classify_fetch_coverage(
    DR, syms, have, date(2023, 1, 5))
print("covered      =", sorted(covered))
print("uncovered_raw=", un_raw)
print("uncovered_hfq=", un_hfq)

for s in syms:
    print(f"  _symbol_min_date(daily_bar, {s}) = {ex._symbol_min_date(DR, 'daily_bar', s)}")
    print(f"  _symbol_min_date(hfq,       {s}) = {ex._symbol_min_date(DR, 'daily_bar_hfq', s)}")

print("ex.FETCH_COVER_MIN =", ex.FETCH_COVER_MIN)
