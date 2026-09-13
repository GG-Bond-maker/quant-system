"""生成 qfq（前复权）数据集：由 raw + hfq 确定性推导，无需网络。

    python scripts/build_qfq.py
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

BACKEND_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(BACKEND_ROOT))

import polars as pl  # noqa: E402

from app.core.config import get_settings  # noqa: E402
from app.core.logging import setup_logging  # noqa: E402
from app.data.parquet_store import read_all_symbols, read_symbol_dataset  # noqa: E402
from app.data.repair import build_qfq_dataset  # noqa: E402


def main() -> None:
    ap = argparse.ArgumentParser(prog="build_qfq.py")
    ap.add_argument("--verify", action="store_true", help="只校验不重建")
    args = ap.parse_args()

    setup_logging()
    s = get_settings()
    symbols = read_all_symbols("daily_bar_hfq")
    print(f"标的数：{len(symbols)}")

    if not args.verify:
        total = 0
        for i, sym in enumerate(symbols, 1):
            n = build_qfq_dataset(s.DATA_ROOT, sym)
            total += n
            if i % 40 == 0:
                print(f"  ... {i}/{len(symbols)} rows={total}")
        print(f"qfq 生成完成：{len(symbols)} 只，{total} 行")

    # ---- 校验：三套数据的一致性不变量 ----
    print("\n校验 qfq / hfq / raw 三套数据")
    print("-" * 70)
    bad: list[str] = []
    for sym in symbols:
        raw = read_symbol_dataset("daily_bar", sym)
        hfq = read_symbol_dataset("daily_bar_hfq", sym)
        qfq = read_symbol_dataset("daily_bar_qfq", sym)
        if raw.is_empty() or hfq.is_empty() or qfq.is_empty():
            bad.append(f"{sym}: 数据缺失 raw={raw.height} hfq={hfq.height} qfq={qfq.height}")
            continue
        # 不变量 1：qfq 最后一日收盘 == raw 最后一日收盘
        r_last = float(raw["close"][-1])
        q_last = float(qfq["close"][-1])
        if abs(r_last - q_last) > max(0.01, abs(r_last) * 1e-6):
            bad.append(f"{sym}: qfq 末日收盘 {q_last:.4f} != raw {r_last:.4f}")
        # 不变量 2：qfq/hfq 为常数比（同一 symbol 内）
        m = (qfq.select(["date", "close"]).rename({"close": "q"})
             .join(hfq.select(["date", "close"]).rename({"close": "h"}),
                   on="date", how="inner")
             .filter(pl.col("h") > 0)
             .with_columns((pl.col("q") / pl.col("h")).alias("ratio")))
        if m.height:
            lo, hi = float(m["ratio"].min()), float(m["ratio"].max())
            if (hi - lo) > max(1e-9, hi * 1e-9):
                bad.append(f"{sym}: qfq/hfq 非常数比 [{lo:.9f}, {hi:.9f}]")
        # 不变量 3：三套数据的日期集合一致
        if set(raw["date"].to_list()) != set(qfq["date"].to_list()):
            bad.append(f"{sym}: raw/qfq 日期集合不一致")

    if bad:
        print(f"  ✗ {len(bad)} 个问题")
        for b in bad[:15]:
            print(f"      {b}")
        sys.exit(1)
    print(f"  ✓ {len(symbols)} 只标的全部通过：qfq 末日==raw、qfq/hfq 常数比、日期一致")


if __name__ == "__main__":
    main()
