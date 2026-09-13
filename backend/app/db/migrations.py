"""SQLite 增量列迁移（幂等）：按 PRAGMA table_info 补缺失列。"""
from __future__ import annotations

from sqlalchemy import text
from sqlalchemy.engine import Connection


def ensure_columns(conn: Connection, table: str, columns: dict[str, str]) -> list[str]:
    """为 table 补齐 columns 中缺失的列（ADD COLUMN）。返回本次新增列名。"""
    existing = {row[1] for row in conn.execute(text(f"PRAGMA table_info({table})"))}
    added: list[str] = []
    for col, ddl in columns.items():
        if col not in existing:
            conn.execute(text(f"ALTER TABLE {table} ADD COLUMN {col} {ddl}"))
            added.append(col)
    return added
