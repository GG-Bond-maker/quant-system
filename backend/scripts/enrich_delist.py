"""回填退市名单到 instrument.delist_date（整改 Task 6）。

默认 dry-run：只打印将更新的行数与样例，不写库；--apply 才落库。
运行后需重建 universe_daily_bt（build_universe_backtest）使退市行移出宇宙。

    DATA_ROOT=... python scripts/enrich_delist.py [--apply]
"""
from __future__ import annotations

import argparse
import asyncio
import sys
from pathlib import Path

BACKEND_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(BACKEND_ROOT))

import polars as pl  # noqa: E402

from app.core.logging import setup_logging  # noqa: E402
from app.data.ingest.akshare_adapter import fetch_delist_list  # noqa: E402
from app.data.ingest.tasks import upsert_delist_dates  # noqa: E402
from app.db.session import get_session_factory  # noqa: E402
from app.domain.a_share_rules import code_to_symbol  # noqa: E402


async def _existing_symbols() -> set[str]:
    from sqlalchemy import select

    from app.db.models import Instrument

    factory = get_session_factory()
    async with factory() as sess:
        rows = (await sess.scalars(select(Instrument.symbol))).all()
    return set(rows)


def main() -> None:
    ap = argparse.ArgumentParser(prog="enrich_delist.py")
    ap.add_argument("--apply", action="store_true",
                    help="实际写库（默认 dry-run 只打印）")
    args = ap.parse_args()

    setup_logging()
    delist = fetch_delist_list()
    have = asyncio.run(_existing_symbols())

    rows: list[dict] = []
    for rec in delist.iter_rows(named=True):
        try:
            sym = code_to_symbol(rec["code"])
        except ValueError:
            continue
        if sym in have:
            rows.append({"code": rec["code"], "delist_date": rec["delist_date"]})

    print(f"退市名单 {delist.height} 条，与本地 instrument 交集 {len(rows)} 条")
    for r in rows[:10]:
        print("  sample:", r)
    if not args.apply:
        print("dry-run：未写库。加 --apply 执行回填。")
        return
    n = asyncio.run(upsert_delist_dates(pl.DataFrame(rows)))
    print(f"已回填 delist_date：{n} 条")


if __name__ == "__main__":
    main()
