"""一次性诊断脚本：拆解 `_build_daily` 各子块耗时，定位 5s 预算被谁吃掉。

用法（必须先 cd backend）：
    ./.venv/Scripts/python.exe scripts/dev_time_build_daily.py
"""
from __future__ import annotations

import sys
import time
from datetime import date
from pathlib import Path

BACKEND_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(BACKEND_ROOT))

from app.api.v1 import market as M  # noqa: E402


def timed(name: str, fn):
    t0 = time.perf_counter()
    try:
        out = fn()
    except Exception as e:  # noqa: BLE001
        print(f"{name:<12} {time.perf_counter() - t0:6.2f}s  ERR {type(e).__name__}: {e}")
        return None
    dt = time.perf_counter() - t0
    brief = ""
    if isinstance(out, dict):
        brief = f"status={out.get('status')}"
    print(f"{name:<12} {dt:6.2f}s  {brief}")
    return dt


def main() -> None:
    td = M._resolve_trade_date() if hasattr(M, "_resolve_trade_date") else date(2026, 9, 30)
    print(f"trade_date = {td}")
    total = 0.0
    for name, fn in (
        ("heat", lambda: M._heat_from_local()),
        ("sectors", lambda: M._sectors_from_local()),
        ("recommend", lambda: M._build_recommend(50)),
        ("ai_stats", lambda: M._build_ai_stats()),
        ("sentiment", lambda: M._build_sentiment(M._heat_from_local())),
        ("pred_dates", lambda: M._pred_dates()),
    ):
        d = timed(name, fn)
        if d:
            total += d
    print(f"{'SUM':<12} {total:6.2f}s   (预算 {M.DAILY_BUILD_TIMEOUT_SECONDS:.0f}s)")

    print("-" * 46)
    t0 = time.perf_counter()
    out = M._build_daily(td, 50)
    wall = time.perf_counter() - t0
    print(f"{'_build_daily':<12} {wall:6.2f}s  (并行后墙钟)")
    for k in ("heat", "sectors", "recommend", "ai_stats", "sentiment"):
        b = out.get(k) or {}
        print(f"    {k:<10} status={b.get('status')}")
    h = out.get("heat") or {}
    print(f"    heat: amount={h.get('total_amount_yi')} up={h.get('up')} "
          f"down={h.get('down')} data_date={h.get('data_date')} "
          f"coverage_symbols={h.get('coverage_symbols')}")


if __name__ == "__main__":
    main()
