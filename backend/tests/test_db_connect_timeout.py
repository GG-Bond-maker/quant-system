"""项4 回归：状态写入路径的 ``sqlite3.connect`` 超时与 ``task_store`` 对齐（>=30s）。

背景：``task_store._conn`` 用 ``timeout=30``，而 ``db/kv.py``（get/set）与
``sync_service._record_sync_job`` 此前是默认 5s。长事务 / 长 parquet 写入期间 5s 会超时 ⇒
状态写入**静默失败** ⇒ 正是"非终态残留"（KV 卡 running / data_jobs 卡 RUNNING）的成因之一。

隔离：不写共享库；``kv_get`` 仅在 ``SQLITE_PATH.exists()`` 时连接，故先建私有 tmp 库。
"""
from __future__ import annotations

import sqlite3
from types import SimpleNamespace

import pytest

from app.db import kv
from app.services import sync_service

_DDL = [
    "CREATE TABLE app_state (key TEXT PRIMARY KEY, value TEXT, updated_at TEXT)",
    "CREATE TABLE data_jobs ("
    " id INTEGER PRIMARY KEY AUTOINCREMENT, job_type TEXT NOT NULL,"
    " trade_date TEXT NOT NULL, status TEXT NOT NULL, current_step TEXT,"
    " started_at TEXT, finished_at TEXT, error_message TEXT, traceback TEXT,"
    " duration_ms INTEGER NOT NULL,"
    " created_at TEXT DEFAULT (CURRENT_TIMESTAMP) NOT NULL,"
    " updated_at TEXT DEFAULT (CURRENT_TIMESTAMP) NOT NULL,"
    " UNIQUE (job_type, trade_date))",
]


@pytest.fixture
def timeout_db(tmp_path, monkeypatch):
    """私有 tmp 库 + 把 kv / sync_service 的 get_settings 指向它。"""
    db_path = tmp_path / "app.db"
    with sqlite3.connect(str(db_path)) as conn:
        for ddl in _DDL:
            conn.execute(ddl)
        conn.commit()
    monkeypatch.setattr(kv, "get_settings",
                        lambda: SimpleNamespace(SQLITE_PATH=db_path))
    monkeypatch.setattr(sync_service, "get_settings",
                        lambda: SimpleNamespace(SQLITE_PATH=db_path))
    return db_path


@pytest.fixture
def connect_spy(monkeypatch):
    """记录 ``sqlite3.connect`` 的 kwargs，同时透传给真实实现。"""
    calls: list[dict] = []
    real = sqlite3.connect

    def _spy(*args, **kwargs):  # noqa: ANN002, ANN003
        calls.append(kwargs)
        return real(*args, **kwargs)

    monkeypatch.setattr(sqlite3, "connect", _spy)
    return calls


def test_kv_get_timeout_aligned(timeout_db, connect_spy):
    """kv_get（mode=ro 读）真的走到 connect，且 timeout>=30。"""
    calls = connect_spy
    calls.clear()
    kv.kv_get("missing-key")
    assert calls, "kv_get 未走到 connect（SQLITE_PATH 不存在？）"
    assert calls[-1].get("timeout", 0) >= 30, calls[-1]


def test_kv_set_timeout_aligned(timeout_db, connect_spy):
    """kv_set（写）真的走到 connect，且 timeout>=30，且能真正写入。"""
    calls = connect_spy
    calls.clear()
    kv.kv_set("k1", {"a": 1})
    assert calls, "kv_set 未走到 connect"
    assert calls[-1].get("timeout", 0) >= 30, calls[-1]
    assert kv.kv_get("k1") == {"a": 1}


def test_record_sync_job_timeout_aligned(timeout_db, connect_spy):
    """_record_sync_job 真的走到 connect，且 timeout>=30。"""
    calls = connect_spy
    calls.clear()
    sync_service._record_sync_job("incremental", "SUCCESS", 1234)
    assert calls, "_record_sync_job 未走到 connect"
    assert calls[-1].get("timeout", 0) >= 30, calls[-1]
    # 旁证：确实落库成功（说明超时参数不破坏写入）
    with sqlite3.connect(str(timeout_db)) as conn:
        n = conn.execute("SELECT COUNT(*) FROM data_jobs").fetchone()[0]
    assert n == 1
