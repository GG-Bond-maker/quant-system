"""构建全历史 universe_daily（第七阶段：HIGH-001）。

    python scripts/build_universe.py            # 全量重建
    python scripts/build_universe.py --verify   # 只校验

清理旧的 1 日 universe 后重新生成，覆盖 daily_bar 的全部交易日 × 全部标的。
"""
from __future__ import annotations

import argparse
import shutil
import sys
from pathlib import Path

BACKEND_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(BACKEND_ROOT))

import polars as pl  # noqa: E402

from app.core.config import get_settings  # noqa: E402
from app.core.logging import setup_logging  # noqa: E402
from app.data.parquet_store import read_all_symbols, read_symbol_dataset  # noqa: E402
from app.data.universe import build_universe_history  # noqa: E402
from app.db.init_db import init_database  # noqa: E402
import asyncio  # noqa: E402


def main() -> None:
    ap = argparse.ArgumentParser(prog="build_universe.py")
    ap.add_argument("--verify", action="store_true", help="只校验已落盘数据")
    args = ap.parse_args()

    setup_logging()
    s = get_settings()
    asyncio.run(init_database())
    base = s.DATA_ROOT / "universe_daily"

    if not args.verify:
        # 旧数据只有 1 个交易日，直接整体重建（先归档，不静默删除）
        if base.exists():
            legacy = s.DATA_ROOT / "universe_daily_legacy"
            if legacy.exists():
                shutil.rmtree(legacy, ignore_errors=True)
            shutil.move(str(base), str(legacy))
            print(f"旧 universe 已归档 -> {legacy}")
        df = build_universe_history(persist=True)
        print(f"构建完成 rows={df.height}")

    # ---- 校验 ----
    print("\n" + "=" * 70)
    print("校验 universe_daily")
    print("=" * 70)
    files = sorted((s.DATA_ROOT / "universe_daily" / "symbol=__all__").glob("year=*.parquet"))
    print(f"分区文件：{[f.name for f in files]}")
    df = pl.concat([pl.read_parquet(f) for f in files], how="vertical_relaxed")
    print(f"总行数：{df.height}")
    print(f"交易日数：{df['date'].n_unique()}")
    print(f"标的数：{df['symbol'].n_unique()}")
    print(f"日期范围：{df['date'].min()} ~ {df['date'].max()}")
    print(f"列：{list(df.columns)}")

    # 与 daily_bar 的交易日覆盖对比
    bars = pl.concat([read_symbol_dataset("daily_bar", sym).select(["date", "symbol"])
                      for sym in read_all_symbols("daily_bar")],
                     how="vertical_relaxed")
    u_dates = set(df["date"].unique().to_list())
    b_dates = set(bars["date"].unique().to_list())
    print(f"\ndaily_bar 交易日：{len(b_dates)}")
    print(f"universe 交易日：{len(u_dates)}")
    print(f"缺失交易日：{len(b_dates - u_dates)}")
    print(f"多余交易日：{len(u_dates - b_dates)}")

    # 字段完整性
    print("\n字段非空率：")
    n = df.height
    for c in df.columns:
        nn = n - int(df[c].null_count())
        print(f"    {c:<18} {nn:>8}/{n}  ({nn / n:6.2%})")

    # 停牌/涨跌停合理性
    print(f"\nis_halted=True 占比：{df['is_halted'].sum() / n:.2%}")
    valid = df.filter(pl.col("limit_up").is_not_null())
    if valid.height:
        bad = valid.filter(pl.col("limit_up") <= pl.col("limit_down"))
        print(f"limit_up <= limit_down 的异常行：{bad.height}")
        print(f"limit_pct 分布：{valid['limit_pct'].value_counts().sort('limit_pct').to_dicts()}")

    missing = b_dates - u_dates
    if missing:
        print(f"\n✗ 有 {len(missing)} 个交易日未覆盖")
        sys.exit(1)
    print("\n✓ universe 覆盖 daily_bar 全部交易日")


if __name__ == "__main__":
    main()
