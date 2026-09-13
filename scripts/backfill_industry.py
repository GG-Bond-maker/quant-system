"""一次性回填 instrument.industry（行业板块 -> SQLite），供 GNN 同业 peer 边使用。

用法（需联网，在 backend 目录取虚拟环境）：
    python ../scripts/backfill_industry.py                 # auto：东财失败自动降级新浪
    python ../scripts/backfill_industry.py --source sina   # 强制新浪

数据来源：
- em  ：ak.stock_board_industry_name_em + cons_em（约 86 板块，粒度细；东财断连时不可用）
- sina：ak.stock_sector_spot(indicator="新浪行业") + stock_sector_detail（约 49 板块，
        粒度较粗但 2026-09 实测稳定；东财对本机出口断连期间的备选）
写入仅 UPDATE 已存在的 instrument 行的 industry 字段，不新增/删除行。
"""
from __future__ import annotations

import argparse
import sys
import time
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT / "backend"))

import akshare as ak  # noqa: E402
import sqlite3  # noqa: E402

from app.core.config import get_settings  # noqa: E402


def _backfill_em() -> dict[str, str]:
    """东方财富行业板块（86 个，粒度细）。"""
    boards = ak.stock_board_industry_name_em()
    names = boards["板块名称"].tolist()
    print(f"[em] 共 {len(names)} 个行业板块", flush=True)
    mapping: dict[str, str] = {}
    for i, board in enumerate(names, 1):
        for attempt in range(3):
            try:
                cons = ak.stock_board_industry_cons_em(symbol=board)
                for code in cons["代码"].tolist():
                    mapping.setdefault(str(code), board)
                break
            except Exception as e:  # noqa: BLE001
                if attempt == 2:
                    print(f"[em] [{i}/{len(names)}] {board} 失败: {type(e).__name__}",
                          flush=True)
                else:
                    time.sleep(2)
        if i % 10 == 0:
            print(f"[em] 进度 {i}/{len(names)}，已映射 {len(mapping)} 只", flush=True)
        time.sleep(0.5)
    return mapping


def _backfill_sina() -> dict[str, str]:
    """新浪行业板块（约 49 个，粒度粗；东财不可用时的备选）。"""
    boards = ak.stock_sector_spot(indicator="新浪行业")
    print(f"[sina] 共 {len(boards)} 个行业板块", flush=True)
    mapping: dict[str, str] = {}
    for i, row in enumerate(boards.itertuples(index=False), 1):
        label, name = str(getattr(row, "label")), str(getattr(row, "板块"))
        for attempt in range(3):
            try:
                cons = ak.stock_sector_detail(sector=label)
                for code in cons["code"].astype(str):
                    mapping.setdefault(code.zfill(6), name)
                break
            except Exception as e:  # noqa: BLE001
                if attempt == 2:
                    print(f"[sina] [{i}/{len(boards)}] {name} 失败: {type(e).__name__}",
                          flush=True)
                else:
                    time.sleep(2)
        if i % 10 == 0:
            print(f"[sina] 进度 {i}/{len(boards)}，已映射 {len(mapping)} 只", flush=True)
        time.sleep(0.3)
    return mapping


def main() -> None:
    ap = argparse.ArgumentParser(description="回填 instrument.industry")
    ap.add_argument("--source", choices=["auto", "em", "sina"], default="auto",
                    help="auto=东财失败自动降级新浪（默认）")
    args = ap.parse_args()

    mapping: dict[str, str] | None = None
    if args.source in ("auto", "em"):
        try:
            mapping = _backfill_em()
            if len(mapping) < 100:
                raise RuntimeError(f"东财映射过少（{len(mapping)}），视为失败")
        except Exception as e:  # noqa: BLE001
            print(f"[em] 整体失败 {e!r} -> 降级新浪", flush=True)
            mapping = None
    if mapping is None:
        if args.source == "em":
            print("[em] 已指定 --source em 且失败，退出", flush=True)
            sys.exit(1)
        mapping = _backfill_sina()

    db = get_settings().SQLITE_PATH
    con = sqlite3.connect(db)
    try:
        cur = con.execute("SELECT code FROM instrument")
        codes = {r[0] for r in cur.fetchall()}
        rows = [(industry, code) for code, industry in mapping.items() if code in codes]
        con.executemany("UPDATE instrument SET industry = ? WHERE code = ?", rows)
        con.commit()
        print(f"完成：更新 {len(rows)} 只（instrument 共 {len(codes)} 只）-> {db}",
              flush=True)
    finally:
        con.close()


if __name__ == "__main__":
    main()
