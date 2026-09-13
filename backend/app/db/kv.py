"""app_state 表的同步 KV 读写（P2-14：worker 线程持久化任务状态用）。

异步会话（aiosqlite）绑定创建时的事件循环，后台线程无法直接复用；
此处用原生 sqlite3 直连（与 datacenter 记录 data_jobs 同款模式，
WAL + busy_timeout 由 DB 级 PRAGMA 保证）。value 恒为 JSON 字符串。
"""
from __future__ import annotations

import json
import sqlite3
from typing import Any

from ..core.config import get_settings


def kv_get(key: str, default: Any = None) -> Any:
    """读 JSON 值；库不存在/键不存在返回 default。"""
    s = get_settings()
    if not s.SQLITE_PATH.exists():
        return default
    conn = sqlite3.connect(f"file:{s.SQLITE_PATH}?mode=ro", uri=True)
    try:
        row = conn.execute(
            "SELECT value FROM app_state WHERE key = ?", (key,)).fetchone()
        return json.loads(row[0]) if row else default
    except Exception:  # noqa: BLE001 读失败按无值处理（表未建/坏 JSON）
        return default
    finally:
        conn.close()


def kv_set(key: str, value: Any) -> None:
    """写 JSON 值（UPSERT）。调用方自行捕获异常（持久化失败不应影响主流程）。"""
    s = get_settings()
    conn = sqlite3.connect(s.SQLITE_PATH)
    try:
        conn.execute(
            "INSERT INTO app_state (key, value, updated_at) "
            "VALUES (?, ?, datetime('now','localtime')) "
            "ON CONFLICT(key) DO UPDATE SET "
            "value = excluded.value, updated_at = datetime('now','localtime')",
            (key, json.dumps(value, ensure_ascii=False)))
        conn.commit()
    finally:
        conn.close()
