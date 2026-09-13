"""数据质量扫描 CLI（只读，不修改任何数据）。

    python scripts/qc_scan.py [--dataset daily_bar] [--json out.json] [--verbose]

输出：按 dataset 汇总的问题统计 + 明细（默认只列 error）。
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

BACKEND_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(BACKEND_ROOT))

from app.core.config import get_settings  # noqa: E402
from app.core.logging import setup_logging  # noqa: E402
from app.data.quality import (  # noqa: E402
    DEFAULT_THRESHOLDS, load_trade_days, scan_dataset,
)

DEFAULT_SETS = ("daily_bar", "daily_bar_hfq", "daily_bar_qfq")


def main() -> None:
    ap = argparse.ArgumentParser(prog="qc_scan.py")
    ap.add_argument("--dataset", nargs="*", default=list(DEFAULT_SETS))
    ap.add_argument("--json", default=None, help="导出完整问题明细到 JSON")
    ap.add_argument("--verbose", action="store_true", help="同时打印 warn")
    args = ap.parse_args()

    setup_logging()
    s = get_settings()
    trade_days = load_trade_days(s.SQLITE_PATH)
    print(f"DATA_ROOT = {s.DATA_ROOT}")
    print(f"交易日历：{len(trade_days)} 天" if trade_days else "交易日历：不可用（跳过日历校验）")
    print(f"阈值：{DEFAULT_THRESHOLDS}")
    print()

    all_rows: list[dict] = []
    total_err = 0
    for ds in args.dataset:
        rep = scan_dataset(s.DATA_ROOT, ds, DEFAULT_THRESHOLDS, trade_days)
        print("=" * 74)
        print(f"[{ds}] symbols={rep.n_symbols} files={rep.n_files} rows={rep.n_rows}")
        print("=" * 74)
        if not rep.issues:
            print("  无问题")
        for kind, n in sorted(rep.by_kind().items(), key=lambda kv: -kv[1]):
            print(f"  {kind:<15} {n:>6} 行")
        show = rep.issues if args.verbose else rep.errors
        for i in show:
            print(f"  [{i.severity:<5}] {i.kind:<14} {i.symbol}  {i.detail[:110]}")
        total_err += len(rep.errors)
        all_rows += [i.as_row() for i in rep.issues]
        print()

    print("=" * 74)
    print(f"合计 error 问题数：{total_err}")
    if args.json:
        Path(args.json).write_text(
            json.dumps(all_rows, ensure_ascii=False, indent=2), encoding="utf-8")
        print(f"明细已导出：{args.json}")


if __name__ == "__main__":
    main()
