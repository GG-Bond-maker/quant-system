"""只读诊断脚本：复现 daily_pipeline validate 失败的分母塌缩 + 核对真实数据。

用法：backend/.venv/Scripts/python.exe docs/audit/2026-10-03/_fix/probe_datalink.py
严禁写入任何源码或数据。
"""
from __future__ import annotations

import sys
from datetime import date

sys.path.insert(0, "backend")

from app.data.parquet_store import read_all_symbols, read_symbol_dataset  # noqa: E402
from app.domain.a_share_rules import code_to_symbol, normalize_code  # noqa: E402

FAIL_DATES = [date(2026, 9, 4), date(2026, 9, 18), date(2026, 9, 23),
              date(2026, 9, 28), date(2026, 9, 29), date(2026, 9, 30)]

print("=" * 90)
print("[A] read_all_symbols('daily_bar') 口径")
raw = read_all_symbols("daily_bar")
print(f"  raw symbols (带后缀, skip_empty=True) = {len(raw)}")
print(f"  sample = {raw[:3]}")
norm = list(dict.fromkeys(normalize_code(c) for c in raw))
print(f"  normalize_code 去重后 = {len(norm)}")

print("=" * 90)
print("[B] 全市场 daily_bar 2026 年「每个 symbol 的最新日期」分布")
from collections import Counter  # noqa: E402
import polars as pl  # noqa: E402
from pathlib import Path  # noqa: E402

root = Path("data/parquet/daily_bar")
latest_counter: Counter = Counter()
n_nonempty = 0
n_empty = 0
sample_tail: dict[str, list[str]] = {}
for d in sorted(root.iterdir()):
    if not d.is_dir() or not d.name.startswith("symbol="):
        continue
    files = sorted(d.glob("year=*.parquet"))
    if not files:
        n_empty += 1
        continue
    frames = []
    for f in files:
        try:
            frames.append(pl.read_parquet(f, columns=["date"]))
        except Exception:
            pass
    if not frames:
        n_empty += 1
        continue
    df = pl.concat(frames, how="vertical_relaxed")
    if df.is_empty():
        n_empty += 1
        continue
    n_nonempty += 1
    mx = str(df["date"].max())[:10]
    latest_counter[mx] += 1
print(f"  非空 symbol 目录 = {n_nonempty}；空目录 = {n_empty}")
print("  最新日期 Top20:")
for k, v in sorted(latest_counter.items(), reverse=True)[:20]:
    print(f"    {k}: {v}")

print("=" * 90)
print("[C] 重点标的在 2026-09 的实际日期集合")
for code in ["000001", "000002", "000006", "000007", "000008", "000009"]:
    sym = code_to_symbol(code)
    df = read_symbol_dataset("daily_bar", sym)
    dates = [str(x)[:10] for x in df["date"].to_list()] if not df.is_empty() else []
    sep = [d for d in dates if d.startswith("2026-09")]
    mx = max(dates) if dates else None
    print(f"  {code} ({sym}): rows={len(dates)} max={mx}")
    print(f"      2026-09 dates = {sep}")

print("=" * 90)
print("[D] 逐失败日复现 validate 计数（n_ok / missing / suspended / n_expected）")
try:
    from app.data.calendar_store import get_calendar  # noqa: E402
    from app.domain.calendar import prev_trade_day  # noqa: E402
    cal = get_calendar()
    print(f"  calendar size = {len(cal)}")
except Exception as e:
    print(f"  ! calendar unavailable: {e!r}")
    cal = None

codes = norm  # 6 位纯代码（编排入口规范化后的口径）
for td in FAIL_DATES:
    prev = None
    if cal is not None:
        try:
            prev = prev_trade_day(td, cal)
        except Exception as e:
            print(f"  {td}: prev_trade_day err {e!r}")
    n_ok = n_susp = 0
    missing: list[str] = []
    for c in codes:
        sym = code_to_symbol(c)
        df = read_symbol_dataset("daily_bar", sym, start=td, end=td)
        if not df.is_empty():
            n_ok += 1
            continue
        if prev is None:
            n_susp += 1
            continue
        p = read_symbol_dataset("daily_bar", sym, start=prev, end=prev)
        if p.is_empty():
            n_susp += 1
        else:
            missing.append(c)
    n_expected = n_ok + len(missing)
    cov = n_ok / max(1, len(codes))
    print(f"  {td} (prev={prev}): n_ok={n_ok} missing={len(missing)} "
          f"suspended={n_susp} n_expected={n_expected} "
          f"coverage={cov:.4%} -> 报错文案'昨有今无 {len(missing)}/{n_expected}'")
    print(f"      missing 前10 = {missing[:10]}")
