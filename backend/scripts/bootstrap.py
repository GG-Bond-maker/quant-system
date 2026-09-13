"""bootstrap：环境检查 + SQLite 初始化 + 目录初始化 + 线程配置（P0-Major#5）。

用法（backend 目录下）：
    python scripts/bootstrap.py
"""
from __future__ import annotations

import asyncio
import sys
from pathlib import Path

BACKEND_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(BACKEND_ROOT))

from app.core.config import effective_cpu_threads, get_settings  # noqa: E402
from app.core.logging import setup_logging  # noqa: E402
from app.data.ingest.tasks import init_sqlite_file  # noqa: E402
from app.db.init_db import init_database  # noqa: E402


def check_env() -> None:
    """环境自检：Python 版本 / 目录可写。不通过直接退出。"""
    if sys.version_info < (3, 11):
        print(f"❌ 需要 Python 3.11+，当前 {sys.version.split()[0]}")
        sys.exit(1)
    s = get_settings()
    for p in (s.DATA_ROOT, s.MODEL_ROOT, s.LOG_DIR, s.SQLITE_PATH.parent):
        try:
            p.mkdir(parents=True, exist_ok=True)
        except OSError as e:
            print(f"❌ 目录不可写 {p}: {e}")
            sys.exit(1)
    print(f"✅ 环境检查通过：python {sys.version.split()[0]}, "
          f"统一线程数 = {effective_cpu_threads()}")


def main() -> None:
    setup_logging()
    print("==> [1/3] 环境检查")
    check_env()
    print("==> [2/3] SQLite 初始化（WAL + 8 表）")
    init_sqlite_file()
    asyncio.run(init_database())
    print("==> [3/3] 数据目录初始化")
    s = get_settings()
    for d in (s.DATA_ROOT, s.MODEL_ROOT, s.LOG_DIR):
        print(f"    {d}")
    print("✅ bootstrap 完成。下一步：python -m app.data.ingest 拉取数据")


if __name__ == "__main__":
    main()
