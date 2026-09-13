"""update_daily：按日期拉取行情 -> 标准化 -> 写入 Parquet（P0-Major#5）。

同时写入两个复权口径：
    daily_bar（不复权，K 线展示） / daily_bar_hfq（后复权，特征计算基准）

用法（backend 目录下）：
    python scripts/update_daily.py                                  # 示例标的，最近 5 个交易日
    python scripts/update_daily.py --codes 600519,000001 --days 30
    python scripts/update_daily.py --start 2022-01-01 --end 2024-12-31
"""
from __future__ import annotations

import argparse
import sys
from datetime import date, timedelta
from pathlib import Path

BACKEND_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(BACKEND_ROOT))

from app.core.logging import setup_logging  # noqa: E402
from app.data.ingest.akshare_adapter import fetch_daily_bar  # noqa: E402
from app.data.ingest.tasks import write_daily_bars  # noqa: E402

DEFAULT_CODES = "600519,000001,300750"


def main() -> None:
    parser = argparse.ArgumentParser(prog="update_daily.py")
    parser.add_argument("--codes", default=DEFAULT_CODES, help="逗号分隔的 6 位代码")
    parser.add_argument("--days", type=int, default=5, help="回看自然日数（默认 5）")
    parser.add_argument("--start", default=None, help="起始 YYYY-MM-DD（优先于 --days）")
    parser.add_argument("--end", default=None, help="结束 YYYY-MM-DD（默认今天）")
    args = parser.parse_args()

    setup_logging()
    end = args.end or date.today().isoformat()
    start = args.start or (date.today() - timedelta(days=args.days)).isoformat()
    codes = [c.strip() for c in args.codes.split(",") if c.strip()]

    total = 0
    for code in codes:
        for adjust in ("", "hfq"):
            try:
                df = fetch_daily_bar(code, start, end, adjust=adjust)
            except Exception as e:
                print(f"❌ {code} adjust={adjust or 'none'} 拉取失败: {e!r}")
                continue
            if df.is_empty():
                print(f"⚠️ {code} adjust={adjust or 'none'} 无数据")
                continue
            n = write_daily_bars(code, df, adjusts=(adjust,))
            total += n
            print(f"✅ {code} adjust={adjust or 'none'}: {df.height} 行 "
                  f"({df['date'][0]} ~ {df['date'][-1]})")
    print(f"update_daily 完成：{len(codes)} 只标的，共写入 {total} 行（含双口径）")


if __name__ == "__main__":
    main()  # 同步入口：adapter 内部已完成限速与重试
