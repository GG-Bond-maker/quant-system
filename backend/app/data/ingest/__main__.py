"""数据初始化命令行入口（AQP）。

用法（在 backend 目录下）：
    python -m app.data.ingest                          # 全量初始化（日历+列表+示例标的日线）
    python -m app.data.ingest --stage calendar         # 仅交易日历
    python -m app.data.ingest --stage instruments      # 仅证券列表
    python -m app.data.ingest --stage daily --codes 600519,000001 --days 120
    python -m app.data.ingest --stage daily --codes 600519 --start 2023-01-01 --end 2024-12-31

daily 阶段同时写入两个复权口径（none -> daily_bar，hfq -> daily_bar_hfq），
hfq 是特征计算基准（asof 稳定，见 tests/test_feature_asof.py）。
"""
from __future__ import annotations

import argparse
import asyncio
import sys
from datetime import date, timedelta
from pathlib import Path

# 允许 python -m 从任意 cwd 执行
BACKEND_ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(BACKEND_ROOT / "backend"))

from loguru import logger

from app.core.logging import setup_logging
from app.data.ingest.akshare_adapter import (
    fetch_stock_list,
    fetch_trade_calendar,
)
from app.data.ingest.tasks import (
    fetch_and_write_daily_bars,
    init_sqlite_file,
    upsert_calendar,
    upsert_instruments,
)
from app.db.init_db import init_database

DEFAULT_CODES = "600519,000001,300750"
DEFAULT_DAYS = 120


async def _stage_calendar(end: str) -> None:
    cal = fetch_trade_calendar(start="20000101", end=end.replace("-", ""))
    n = await upsert_calendar(cal)
    logger.info(f"[calendar] upsert {n} rows ({cal['trade_date'].iloc[0]} ~ {cal['trade_date'].iloc[-1]})")


async def _stage_instruments() -> None:
    lst = fetch_stock_list()
    n = await upsert_instruments(lst)
    logger.info(f"[instruments] upsert {n} rows")


async def _stage_daily(codes: list[str], start: str, end: str) -> None:
    """按复权口径分别抓取并增量写入（CRIT-002：禁止一份 df 写多个口径）。"""
    total = 0
    for code in codes:
        n, failed_adj = fetch_and_write_daily_bars(code, start, end, adjusts=("", "hfq"))
        if n == 0:
            logger.warning(f"[daily] {code} 无数据写入（退市/停牌/网络/被门禁拒绝）")
            continue
        if failed_adj:
            logger.warning(f"[daily] {code} 口径 {failed_adj} 抓取失败（需重试）")
        total += n
        logger.info(f"[daily] {code} 写入 {n} 行（三口径，{start} ~ {end}）")
    logger.info(f"[daily] 完成：{len(codes)} 只标的，共 {total} 行")


async def main_async(args: argparse.Namespace) -> None:
    init_sqlite_file()
    await init_database()

    end = args.end or date.today().isoformat()
    start = args.start or (date.today() - timedelta(days=args.days)).isoformat()
    codes = [c.strip() for c in args.codes.split(",") if c.strip()]

    if args.stage in ("all", "calendar"):
        await _stage_calendar(end=end)
    if args.stage in ("all", "instruments"):
        await _stage_instruments()
    if args.stage in ("all", "daily"):
        await _stage_daily(codes, start=start, end=end)

    logger.info("数据初始化完成 ✅  接下来可启动 API（scripts/run_dev.sh）或运行训练流水线")


def main() -> None:
    parser = argparse.ArgumentParser(
        prog="python -m app.data.ingest",
        description="AQP 数据初始化：交易日历 / 证券列表 / 标的日线（双复权口径，按年分区 Parquet）",
    )
    parser.add_argument("--stage", choices=["all", "calendar", "instruments", "daily"],
                        default="all", help="初始化阶段（默认 all）")
    parser.add_argument("--codes", default=DEFAULT_CODES,
                        help=f"daily 阶段的标的代码，逗号分隔（默认 {DEFAULT_CODES}）")
    parser.add_argument("--days", type=int, default=DEFAULT_DAYS,
                        help=f"daily 阶段回看自然日数（默认 {DEFAULT_DAYS}）")
    parser.add_argument("--start", default=None, help="起始日期 YYYY-MM-DD（优先于 --days）")
    parser.add_argument("--end", default=None, help="结束日期 YYYY-MM-DD（默认今天）")
    args = parser.parse_args()

    setup_logging()
    asyncio.run(main_async(args))


if __name__ == "__main__":
    main()
