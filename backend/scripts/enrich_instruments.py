"""一次性脚本：补充 instruments 表的 industry / list_date 字段（P3 审计修复5）。

数据源：AKShare 东财行业板块 → 成分股映射；日线首日近似上市日期。
"""
from __future__ import annotations

import sqlite3
import sys
from pathlib import Path

BACKEND_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(BACKEND_ROOT))

import polars as pl
from loguru import logger

from app.core.config import get_settings
from app.data.parquet_store import read_symbol_dataset


def build_industry_map() -> dict[str, str]:
    """东财行业板块 → {symbol: industry_name}。"""
    import akshare as ak

    board_list = ak.stock_board_industry_name_em()
    mapping: dict[str, str] = {}
    for _, row in board_list.iterrows():
        industry_name = str(row["板块名称"])
        try:
            cons = ak.stock_board_industry_cons_em(symbol=industry_name)
            for _, c in cons.iterrows():
                code = str(c["代码"])
                mapping[code] = industry_name
        except Exception:
            continue
    logger.info(f"industry map: {len(mapping)} stocks")
    return mapping


def build_list_date_map() -> dict[str, str]:
    """从 daily_bar 最早日期近似上市日期（覆盖我们实际有数据的标的）。"""
    s = get_settings()
    base = s.DATA_ROOT / "daily_bar"
    if not base.exists():
        return {}
    result: dict[str, str] = {}
    for d in base.iterdir():
        if not d.is_dir() or not d.name.startswith("symbol="):
            continue
        sym = d.name.split("=", 1)[1]
        files = sorted(d.glob("year=*.parquet"))
        if not files:
            continue
        df = pl.read_parquet(files[0])
        if df.height > 0 and "date" in df.columns:
            first = str(df["date"][0])[:10]
            result[sym] = first
    logger.info(f"list_date approx: {len(result)} stocks")
    return result


def update_instruments(industry_map: dict[str, str], list_date_map: dict[str, str]) -> None:
    s = get_settings()
    conn = sqlite3.connect(s.SQLITE_PATH)
    industry_count, list_date_count = 0, 0
    for sym, (code, *_ ) in []:
        pass
    # 先取出所有 symbol → code 映射
    all_rows = conn.execute("SELECT id, symbol, code FROM instrument").fetchall()
    for uid, sym, code in all_rows:
        # industry
        ind = industry_map.get(code)
        if ind:
            conn.execute("UPDATE instrument SET industry=? WHERE id=?", (ind, uid))
            industry_count += 1
        # list_date（从 daily_bar 最早日期近似）
        ld = list_date_map.get(sym)
        if ld:
            conn.execute("UPDATE instrument SET list_date=? WHERE id=? AND list_date IS NULL", (ld, uid))
            list_date_count += 1
    conn.commit()
    conn.close()
    logger.info(f"updated industry={industry_count}, list_date={list_date_count}")


def main() -> None:
    logger.info("==> 获取行业映射...")
    industry_map = build_industry_map()
    logger.info("==> 构建近似上市日期...")
    list_date_map = build_list_date_map()
    logger.info("==> 更新 instruments 表...")
    update_instruments(industry_map, list_date_map)
    # 验证
    s = get_settings()
    conn = sqlite3.connect(s.SQLITE_PATH)
    ind_n = conn.execute("SELECT COUNT(*) FROM instrument WHERE industry IS NOT NULL AND industry != ''").fetchone()[0]
    ld_n = conn.execute("SELECT COUNT(*) FROM instrument WHERE list_date IS NOT NULL").fetchone()[0]
    total = conn.execute("SELECT COUNT(*) FROM instrument").fetchone()[0]
    conn.close()
    print(f"✅ industry 非空: {ind_n}/{total} | list_date 非空: {ld_n}/{total}")


if __name__ == "__main__":
    main()
