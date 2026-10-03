"""质量异常定位（诊断用）：找出 (a) 量纲异常标的 (b) 尾部陈旧标的。

背景（2026-10-02 权威质检）：
- `[E2] amount/(volume×close)` 中位数区间 = 0.998 ~ **100.083** ⇒ 有标的 volume 单位是**手**而非**股**。
- `[C]` 有 **243 只**最后交易日 = 2026-09-17（目标 2026-09-30）⇒ 尾部陈旧约 2 周。
- 但质检脚本只打印了聚合区间，**没记录是哪些标的** ⇒ 本脚本补齐定位。

为控制耗时，只读每只标的的**首个**与**末个**年份分区（分区按 year 落盘 ⇒ 首分区含最早日期、
末分区含最晚日期），不读全部年份。
"""
from __future__ import annotations

import json
from pathlib import Path

import polars as pl

ROOT = Path(__file__).resolve().parents[2] / "data" / "parquet"
TARGET_LAST = "2026-09-30"

# 量纲恒等式的合理区间：1.0（股）。留宽一点容忍拆股/口径微差。
RATIO_LO, RATIO_HI = 0.9, 1.1


def main() -> int:
    uni = json.loads((ROOT.parent / "universe_full.json").read_text(encoding="utf-8"))
    syms = [x["symbol"] for x in uni["symbols"]]

    unit_bad: list[dict] = []
    stale: list[dict] = []
    for i, sym in enumerate(syms, 1):
        files = sorted((ROOT / "daily_bar" / f"symbol={sym}").glob("year=*.parquet"))
        if not files:
            continue
        try:
            first = pl.read_parquet(files[0], columns=["date", "close", "volume", "amount"])
            last = pl.read_parquet(files[-1], columns=["date"])
        except Exception:  # noqa: BLE001
            continue

        m = first.filter((pl.col("volume") > 0) & (pl.col("close") > 0) & (pl.col("amount") > 0))
        if m.height:
            r = float((m["amount"] / (m["volume"] * m["close"])).median())
            if not (RATIO_LO <= r <= RATIO_HI):
                unit_bad.append({"symbol": sym, "ratio_median": round(r, 4),
                                 "year": files[0].name.split("year=")[1][:4]})

        mx = str(last["date"].max())
        if mx < TARGET_LAST:
            stale.append({"symbol": sym, "last_date": mx})

        if i % 1000 == 0:
            print(f"  …{i}/{len(syms)}", flush=True)

    print(f"\n量纲异常标的 = {len(unit_bad)}")
    for d in unit_bad[:40]:
        print(f"  {d['symbol']}  ratio={d['ratio_median']}  (首分区 {d['year']})")
    print(f"\n尾部陈旧标的（末日 < {TARGET_LAST}）= {len(stale)}")
    from collections import Counter
    dist = Counter(d["last_date"] for d in stale)
    for d, c in dist.most_common(10):
        print(f"  {d}: {c} 只")
    print("  样例:", [d["symbol"] for d in stale[:10]])
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
