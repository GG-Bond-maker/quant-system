"""北交所覆盖可行性探针（Phase 0 之二）。

背景：``expand_universe_2500.py:83`` 记录「新浪日线不支持北交所（且 92 段会被
``_sina_symbol`` 误映射为 sh）」。用户要求本次扩充**包含北交所（348 只）**，
故必须逐个源实测，而不是引用注释下结论。

做法：逐个源强制（``AQP_DAILY_SOURCE_PRIORITY`` 单源），对 3 类北交所代码段各抽样，
直接调 ``fetch_daily_bar``（**显式传真实 end 日期**）。

同时暴露 ``_sina_symbol`` 对 92 段的映射事实（用于判断"抓不到"还是"抓错了"）。
"""
from __future__ import annotations

import os
import sys
from pathlib import Path

BACKEND_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(BACKEND_ROOT))

from app.data.ingest import akshare_adapter as ada  # noqa: E402

# 三类北交所代码段
SAMPLES = {
    "43xxxx(北交所老段)": ["430047", "430139"],
    "83xxxx/87xxxx(北交所)": ["832000", "871981"],
    "92xxxx(北交所新段)": ["920819", "920000"],
}
START, END = "2022-01-01", "2026-09-30"

print("=" * 72)
print("Phase 0 · 北交所覆盖可行性探针")
print("=" * 72)

# ---------- 0. _sina_symbol 映射事实 ----------
print("\n[0] `_sina_symbol` 代码段映射（决定「抓不到」vs「抓错标的」）")
for c in ["600519", "000001", "430047", "832000", "871981", "920819", "920000"]:
    try:
        print(f"      {c} -> {ada._sina_symbol(c)}")
    except Exception as e:  # noqa: BLE001
        print(f"      {c} -> 抛异常 {type(e).__name__}: {e}")

# ---------- 1. 逐源实测 ----------
print("\n[1] 逐源实测（单源强制，显式 end）")
for src in ["sina", "tencent", "eastmoney", "baostock"]:
    os.environ["AQP_DAILY_SOURCE_PRIORITY"] = src
    ada.get_settings.cache_clear() if hasattr(ada, "get_settings") else None
    print(f"\n  ── 源 = {src} ──")
    for seg, codes in SAMPLES.items():
        for c in codes:
            try:
                df = ada.fetch_daily_bar(c, START, END, "")
                n = df.height if hasattr(df, "height") else len(df)
                if n:
                    first = df["date"][0] if hasattr(df, "height") else None
                    last = df["date"][-1] if hasattr(df, "height") else None
                    print(f"      ✅ {seg:22s} {c}: {n:5d} 行  {first} ~ {last}")
                else:
                    print(f"      ⚠️ {seg:22s} {c}: 空返回")
            except Exception as e:  # noqa: BLE001
                print(f"      ❌ {seg:22s} {c}: {type(e).__name__}: {str(e)[:80]}")

print("\n" + "=" * 72)
print("结论判据：只要**任一源**对**任一段**返回非空行，即说明北交所可覆盖。")
print("=" * 72)
