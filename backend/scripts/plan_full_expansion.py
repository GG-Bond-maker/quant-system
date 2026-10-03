"""全市场扩容的 dry-run 范围报告（不抓取、不写盘）。

复用 ``scripts/expand_universe_2500.py`` 的 ``_classify_fetch_coverage``
（**同一个函数**，避免范围判定分叉 —— 这是本项目反复踩过的"同型写法分叉"缺陷），
输出待抓标的数与耗时估计。

用法（cd backend）：
    ./.venv/Scripts/python.exe scripts/plan_full_expansion.py --cover-min 2023-01-05
"""
from __future__ import annotations

import argparse
import json
import sys
import time
from collections import Counter
from datetime import date
from pathlib import Path

BACKEND = Path(__file__).resolve().parents[1]
PROJECT = BACKEND.parent
sys.path.insert(0, str(BACKEND))
sys.path.insert(0, str(PROJECT / "scripts"))

DR = PROJECT / "data" / "parquet"
UNIVERSE = PROJECT / "data" / "universe_full.json"


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--cover-min", default="2023-01-05")
    ap.add_argument("--start", default="2022-01-01")
    ap.add_argument("--end", default="2026-09-30")
    ap.add_argument("--workers", type=int, default=3)
    ap.add_argument("--rate", type=float, default=1.3, help="单次外呼秒数估计")
    args = ap.parse_args()

    import expand_universe_2500 as ex  # noqa: PLC0415

    from app.data.parquet_store import list_symbols_with_data, manifest_invalidate  # noqa: PLC0415

    uni = json.loads(UNIVERSE.read_text(encoding="utf-8"))
    syms = [x["symbol"] for x in uni["symbols"]]

    t0 = time.monotonic()
    manifest_invalidate()
    have = set(list_symbols_with_data("daily_bar"))
    print(f"清单标的数            = {len(syms)}")
    print(f"manifest(磁盘过滤后)  = {len(have)}  ({time.monotonic()-t0:.1f}s)")
    print(f"清单 ∩ manifest       = {len(set(syms) & have)}")

    cover_min = date.fromisoformat(args.cover_min)
    covered, un_raw, un_hfq = ex._classify_fetch_coverage(DR, syms, have, cover_min)
    todo = [s for s in syms if s not in covered]
    brand_new = [s for s in todo if s not in have]
    need_refetch = sorted(set(un_raw) | set(un_hfq))

    print(f"cover_min             = {cover_min}")
    print(f"已覆盖（可跳过）      = {len(covered)}")
    print(f"  其中 raw 口径残缺   = {len(un_raw)}")
    print(f"  其中 hfq 口径残缺   = {len(un_hfq)}")
    print(f"待抓（todo）          = {len(todo)}")
    print(f"  其中库内完全无数据  = {len(brand_new)}")
    print(f"  其中仅有旧分区需回补= {len(todo) - len(brand_new)}")
    print(f"  （raw/hfq 残缺并集） = {len(need_refetch)}")

    # 起点分布（现有标的按 min_year 分桶，用于判断 2022 窗口的实际收益）
    yc: Counter[int] = Counter()
    for s in syms:
        mn = ex._symbol_min_date(DR, "daily_bar", s)
        if mn:
            yc[mn.year] += 1
    print(f"清单内 daily_bar 最早年份分布 = {dict(sorted(yc.items()))}")

    calls = len(todo) * 2
    est_s = calls * args.rate / max(1, args.workers)
    print(f"预计外呼              = {calls} 次（{len(todo)} 只 × 2 口径）")
    print(f"预计耗时              = ≈{est_s/60:.0f} 分钟（{args.workers} 线程，"
          f"{args.rate}s/次）")
    print(f"区间                  = {args.start} ~ {args.end}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
