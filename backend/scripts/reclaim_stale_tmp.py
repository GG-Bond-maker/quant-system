"""回收 ``DATA_ROOT`` 下陈旧的原子写临时文件（``*.tmp``）。

``_atomic_write_parquet`` / ``_manifest_flush_locked`` 的 ``finally`` 清理在进程被
``TerminateProcess`` 强杀时来不及跑 ⇒ 残留 ``*.tmp``。本 CLI 是
``app.data.parquet_store.reclaim_stale_tmp_files`` 的独立入口，输出 JSON 便于核对。

用法::

    cd backend
    # 先看清单（不删）
    ./.venv/Scripts/python.exe scripts/reclaim_stale_tmp.py --max-age-hours 1 --dry-run
    # 确认后实跑
    ./.venv/Scripts/python.exe scripts/reclaim_stale_tmp.py --max-age-hours 1

⚠️ 只删除 ``*.tmp`` 且 mtime 超龄的文件；``.rwlock`` 等其它文件**不在范围**。
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

BACKEND_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(BACKEND_ROOT))

from app.data.parquet_store import (  # noqa: E402
    DEFAULT_TMP_MAX_AGE_HOURS,
    reclaim_stale_tmp_files,
)


def main() -> int:
    ap = argparse.ArgumentParser(prog="reclaim_stale_tmp.py",
                                 description="回收 DATA_ROOT 下陈旧 *.tmp（原子写残留）")
    ap.add_argument("--max-age-hours", type=float, default=DEFAULT_TMP_MAX_AGE_HOURS,
                    help=f"年龄阈值（小时），默认 {DEFAULT_TMP_MAX_AGE_HOURS}")
    ap.add_argument("--dry-run", action="store_true",
                    help="只统计不删除（核对清单用）")
    args = ap.parse_args()

    res = reclaim_stale_tmp_files(max_age_hours=args.max_age_hours,
                                  dry_run=args.dry_run)
    print(json.dumps(res, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
