"""一次性测量：`_build_ai_stats` 独立缓存带来的日频块重建提速。

用法（必须先 cd backend）：
    ./.venv/Scripts/python.exe scripts/dev_measure_ai_stats_cache.py

对照设计：同一进程内连续跑两次 `_build_daily`，两次的 OS page cache 都已是热的
⇒ 差值只来自 ai_stats 是否命中独立缓存（其余子块两次都要重算）。
"""
from __future__ import annotations

import sys
import time
from datetime import date
from pathlib import Path

BACKEND_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(BACKEND_ROOT))

from app.api.v1 import market as M  # noqa: E402


def _wall(fn, *a, **kw) -> tuple[float, dict]:
    t0 = time.perf_counter()
    out = fn(*a, **kw)
    return time.perf_counter() - t0, out


def main() -> None:
    td = date(2026, 9, 30)

    # --- ai_stats 单块：冷 / 热 ---
    M._ai_stats_cache.clear()
    t_cold, cold = _wall(M._build_ai_stats)
    t_hot, hot = _wall(M._build_ai_stats)
    same = {k: v for k, v in cold.items() if k != "status"} == \
           {k: v for k, v in hot.items() if k != "status"}
    print(f"ai_stats 冷  {t_cold:6.2f}s  status={cold.get('status')}")
    print(f"ai_stats 热  {t_hot:6.2f}s  status={hot.get('status')}  载荷一致={same}")

    # --- 日频块：冷（ai_stats 未缓存）/ 热（ai_stats 已缓存）---
    M._ai_stats_cache.clear()
    t_bd_cold, out_cold = _wall(M._build_daily, td, 50)
    t_bd_hot, out_hot = _wall(M._build_daily, td, 50)
    print("-" * 52)
    print(f"_build_daily 冷  {t_bd_cold:6.2f}s  ai_stats={out_cold['ai_stats'].get('status')}")
    print(f"_build_daily 热  {t_bd_hot:6.2f}s  ai_stats={out_hot['ai_stats'].get('status')}")
    saved = t_bd_cold - t_bd_hot
    print(f"节省 {saved:6.2f}s" + (f"  ({saved / t_bd_cold:.0%})" if t_bd_cold else ""))
    print(f"（预算 {M.DAILY_BUILD_TIMEOUT_SECONDS:.0f}s）")

    a, b = out_cold.get("ai_stats") or {}, out_hot.get("ai_stats") or {}
    print("ai_stats 载荷一致 =", {k: v for k, v in a.items() if k != "status"} ==
          {k: v for k, v in b.items() if k != "status"})


if __name__ == "__main__":
    main()
