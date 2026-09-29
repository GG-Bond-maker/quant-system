"""
数据库初始化（AQP）。

1. 确保 SQLite 文件所在目录存在；
2. 执行 DB 级持久化 PRAGMA（WAL 等只需执行一次，之后所有连接自动继承）；
3. Base.metadata.create_all 建表 + 增量迁移补列/补索引（幂等，重复运行安全）。
   ⚠️ ``create_all`` **不会**给**已存在**的表补索引 ⇒ P1 性能索引由
   ``migrations.ensure_indexes`` 显式回填（见下方第 3 个 ``run_sync``）。

⚠️ SQLite WAL（Write-Ahead Logging）说明：
    - journal_mode=WAL      ：允许读写并发，大幅减少 "database is locked" 错误；
    - synchronous=NORMAL    ：WAL 模式下安全，性能优于 FULL；
    - busy_timeout=30000    ：写冲突时等待 30s 而非立即报错；
    - foreign_keys=ON       ：开启外键约束（SQLite 默认关闭）。
"""
from __future__ import annotations

import asyncio
from pathlib import Path

from loguru import logger
from sqlalchemy import text

from ..core.config import get_settings
from . import (
    models,  # noqa: F401
    )
from .models import Base
from .session import _get_engine


async def init_database() -> None:
    """幂等执行：重复运行安全。"""
    s = get_settings()
    db_url: str = s.SQLITE_URL
    if db_url.startswith("sqlite+aiosqlite:///"):
        db_path = Path(db_url.replace("sqlite+aiosqlite:///", "", 1))
        db_path.parent.mkdir(parents=True, exist_ok=True)
        logger.info(f"sqlite db path: {db_path}")

    engine = _get_engine()
    async with engine.begin() as conn:
        await conn.execute(text("PRAGMA journal_mode=WAL"))
        await conn.execute(text("PRAGMA synchronous=NORMAL"))
        await conn.execute(text("PRAGMA busy_timeout=30000"))
        await conn.execute(text("PRAGMA foreign_keys=ON"))
        await conn.run_sync(Base.metadata.create_all)
        # 增量迁移：feature_runs 补齐 P2 网格搜索所需列（幂等）
        from app.db.migrations import ensure_columns, ensure_indexes

        await conn.run_sync(lambda c: ensure_columns(
            c, "feature_runs",
            {"params_json": "TEXT", "train_ic": "REAL", "valid_ic": "REAL",
             "test_ic": "REAL", "train_rankic": "REAL", "valid_rankic": "REAL",
             "test_rankic": "REAL", "train_time": "REAL", "dataset_version": "TEXT"}))
        # 增量迁移：model_registry 补齐第三/五阶段治理列（幂等）
        await conn.run_sync(lambda c: ensure_columns(
            c, "model_registry",
            {"status": "VARCHAR(16) DEFAULT 'candidate'",
             "dataset_version": "VARCHAR(64)",
             "valid_start": "DATE", "valid_end": "DATE",
             "test_start": "DATE", "test_end": "DATE",
             "valid_icir": "REAL", "valid_rmse": "REAL",
             "test_ic": "REAL", "test_rank_ic": "REAL",
             "test_icir": "REAL", "test_rmse": "REAL",
             "label_quality_json": "TEXT",
             "promoted_at": "DATETIME", "promoted_by": "VARCHAR(64)",
             "promote_reason": "TEXT"}))
        # 增量迁移：P1 性能索引（幂等；create_all 不会给**已存在**的表补索引）
        await conn.run_sync(lambda c: ensure_indexes(c, [
            ("ix_datajob_status_finished", "data_jobs", "status, finished_at"),
            ("ix_paper_order_status_created", "paper_orders", "status, created_at"),
            ("ix_alert_event_is_read", "alert_events", "is_read"),
            ("ix_model_registry_prod_status", "model_registry", "is_production, status"),
        ]))
    logger.info("database initialized (WAL mode + all tables created if not exist)")
    await engine.dispose()


if __name__ == "__main__":
    asyncio.run(init_database())
    print("db init done.")
