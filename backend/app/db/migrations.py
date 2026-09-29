"""SQLite 增量迁移（幂等）：按 PRAGMA table_info 补缺失列 / 补建缺失索引。"""
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


def ensure_indexes(conn: Connection, indexes: list[tuple[str, str, str]]) -> list[str]:
    """幂等补建索引（``CREATE INDEX IF NOT EXISTS``），返回本次新建的索引名。

    为什么不能只靠 ``Base.metadata.create_all``：create_all 只对**它正在创建的表**
    建索引；本项目 data/sqlite/aqp.db 里这些表早已存在 ⇒ 新加的 ``Index(...)``
    永远不会落库（2026-09-29 P1 实测：加了 Index 后 ``PRAGMA index_list`` 仍无新项）。

    ``indexes`` 为 ``(索引名, 表名, 列定义 DDL)`` 三元组，例如
    ``("ix_datajob_status_finished", "data_jobs", "status, finished_at")``。
    """
    existing = {row[0] for row in conn.execute(
        text("SELECT name FROM sqlite_master WHERE type='index'"))}
    created: list[str] = []
    for name, table, columns in indexes:
        if name in existing:
            continue
        conn.execute(text(
            f"CREATE INDEX IF NOT EXISTS {name} ON {table} ({columns})"))
        created.append(name)
    return created
