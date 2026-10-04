"""`data_jobs.task_id` 关联列回归（B1+，2026-10-04 上线前全检）。

## 背景

`/datacenter` 页此前有**两块**"最近任务"摘要：

* **结论块** —— `/overview` 的 `last_job`（来自 `data_jobs`）：`verdict` / `advice` /
  `job_scope` / `scope_note` / `stale`；
* **进度块** —— `/sync/tasks/{id}`（来自 `task_store`）：`progress` / `logs` / `started_at`。

两者是同一次执行的**两个视角**，但后端此前无法表达这层关联，前端只能靠
`job_type`（`sync_<mode>`）+ `trade_date` **软匹配**去猜 —— 于是页面上出现两个
都叫「查看详情」的按钮，用户会读成"两次运行"。

B1+ 的修法：`data_jobs` 加 `task_id` 列（指向 `task_store` 的那次执行），
`/sync/tasks/{id}` 追加 `link_state` 三态 + `job` 结论行 ⇒ **一个数据源即可同时
展示结论与过程**，前端不再猜。

## 本文件守什么

1. `_record_sync_job` **真的把 task_id 写进库**，且 upsert 时**会更新**它
   （否则前端永远拿到建行时的旧 id，比不写更糟——是"看似权威的错误关联"）；
2. `data_jobs` 的唯一约束**仍在**（幂等锁未被破坏，`orchestrator._ensure_job` 依赖它）；
3. `_build_last_job` 按**位置**解包的元组含 `task_id`，并原样带出（NULL → None）；
4. `_linked_job_for_task` 的三态语义正确：
   `linked`（id 精确相等）/ `other`（有结论行但属于别的任务）/ `none`（无结论行）；
5. 迁移机制能把 `task_id` 补到**已存在的旧库**上（`ensure_columns` 幂等）。
"""
from __future__ import annotations

import sqlite3
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

BACKEND_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(BACKEND_ROOT))

from app.api.v1 import datacenter as dc
from app.services import sync_service as ss

# 与 models.DataJob 同形状（缺列会让 SELECT 报错被 except 吞掉 ⇒ 假绿）
_DATA_JOBS_DDL = """
CREATE TABLE data_jobs (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    job_type TEXT NOT NULL,
    trade_date TEXT NOT NULL,
    status TEXT NOT NULL,
    current_step TEXT,
    started_at TEXT,
    finished_at TEXT,
    error_message TEXT,
    traceback TEXT,
    duration_ms INTEGER NOT NULL,
    codes_count INTEGER,
    job_scope TEXT,
    task_id TEXT,
    created_at TEXT DEFAULT (CURRENT_TIMESTAMP) NOT NULL,
    updated_at TEXT DEFAULT (CURRENT_TIMESTAMP) NOT NULL,
    UNIQUE (job_type, trade_date)
)
"""


@pytest.fixture
def job_db(tmp_path, monkeypatch):
    """隔离 sqlite（含 data_jobs），并把 sync_service / datacenter 的 settings 指过去。"""
    db = tmp_path / "aqp.db"
    with sqlite3.connect(db) as conn:
        conn.execute(_DATA_JOBS_DDL)
        conn.commit()
    monkeypatch.setattr(ss, "get_settings",
                        lambda: SimpleNamespace(SQLITE_PATH=db, DATA_ROOT=tmp_path))
    monkeypatch.setattr(dc, "get_settings",
                        lambda: SimpleNamespace(SQLITE_PATH=db, DATA_ROOT=tmp_path))
    return db


def _rows(db) -> list[tuple]:
    with sqlite3.connect(db) as conn:
        return conn.execute(
            "SELECT job_type, trade_date, status, task_id FROM data_jobs "
            "ORDER BY id").fetchall()


# ---------------- 1/2：写入 + upsert + 约束仍在 ----------------

def test_record_sync_job_writes_task_id(job_db):
    ss._record_sync_job("incremental", "SUCCESS", 1234, None, 5208,
                        task_id="task-abc")
    rows = _rows(job_db)
    assert len(rows) == 1
    job_type, _td, status, task_id = rows[0]
    assert job_type == "sync_incremental"
    assert status == "SUCCESS"
    assert task_id == "task-abc", "task_id 未落库 ⇒ 前端仍只能软匹配"


def test_upsert_updates_task_id_not_just_inserts(job_db):
    """同日同模式再次执行必须**覆盖** task_id。

    若只在 INSERT 时写、ON CONFLICT 分支漏掉它，前端会永远拿到**建行时**的旧 id
    —— 那不是"没关联"，而是"看似权威的**错误**关联"，比不写更糟。
    """
    ss._record_sync_job("incremental", "SUCCESS", 100, None, 10, task_id="task-1")
    ss._record_sync_job("incremental", "FAILED", 200, "boom", 10, task_id="task-2")
    rows = _rows(job_db)
    assert len(rows) == 1, "同日同模式应 upsert 覆盖为一行（唯一约束语义）"
    assert rows[0][3] == "task-2", "upsert 未更新 task_id ⇒ 关联停留在旧任务"


def test_unique_constraint_still_enforced(job_db):
    """唯一约束必须仍在 —— `orchestrator._ensure_job` 的幂等锁依赖它。"""
    with sqlite3.connect(job_db) as conn:
        conn.execute(
            "INSERT INTO data_jobs (job_type, trade_date, status, duration_ms) "
            "VALUES ('daily_pipeline', '2026-10-04', 'SUCCESS', 1)")
        conn.commit()
        with pytest.raises(sqlite3.IntegrityError):
            conn.execute(
                "INSERT INTO data_jobs (job_type, trade_date, status, duration_ms) "
                "VALUES ('daily_pipeline', '2026-10-04', 'FAILED', 1)")


def test_task_id_nullable_for_history_rows(job_db):
    """历史行/无任务执行（如夜间流水线）不写 task_id ⇒ NULL，不得用别的字段冒充。"""
    ss._record_sync_job("incremental", "SUCCESS", 100, None, 10)
    assert _rows(job_db)[0][3] is None


# ---------------- 3：_build_last_job 带出 task_id ----------------

def _row_with_task(task_id):
    """按 /overview SELECT 的**列序**构造 11 元组。"""
    return (1, "sync_incremental", "SUCCESS", "2026-10-04", None,
            "2026-10-04 18:00:00", None, 1000, 5208, "market", task_id)


def test_build_last_job_carries_task_id():
    import datetime as _dt
    job = dc._build_last_job(_row_with_task("task-xyz"), "2026-10-04",
                             _dt.date(2026, 10, 4))
    assert job is not None
    assert job["task_id"] == "task-xyz"
    assert job["verdict"] == "success"


def test_build_last_job_task_id_none_for_history_rows():
    import datetime as _dt
    job = dc._build_last_job(_row_with_task(None), "2026-10-04",
                             _dt.date(2026, 10, 4))
    assert job is not None and job["task_id"] is None


# ---------------- 4：_linked_job_for_task 三态 ----------------

def _insert(db, *, job_type, trade_date, status, task_id):
    with sqlite3.connect(db) as conn:
        conn.execute(
            "INSERT INTO data_jobs (job_type, trade_date, status, duration_ms, "
            "codes_count, job_scope, task_id) VALUES (?, ?, ?, 1, 10, 'subset', ?)",
            (job_type, trade_date, status, task_id))
        conn.commit()


def test_link_state_linked_when_task_id_matches(job_db):
    _insert(job_db, job_type="sync_incremental", trade_date="2026-10-04",
            status="SUCCESS", task_id="task-A")
    job, state = dc._linked_job_for_task(
        {"task_id": "task-A", "task_type": "datacenter.sync.incremental"})
    assert state == "linked"
    assert job is not None and job["task_id"] == "task-A"


def test_link_state_other_when_task_id_differs(job_db):
    """本任务未落 data_jobs / 被更新的执行覆盖 ⇒ 结论行**不能**当成本任务的结论。

    这是三态存在的理由：若此处返回 None 而不给状态，前端只能显示残缺面板、
    既没解释也没线索；给了 `other` 就能**如实**说"这条结论属于另一次执行"。
    """
    _insert(job_db, job_type="sync_incremental", trade_date="2026-10-04",
            status="SUCCESS", task_id="task-A")
    job, state = dc._linked_job_for_task(
        {"task_id": "task-B", "task_type": "datacenter.sync.incremental"})
    assert state == "other"
    assert job is not None, "仍应返回结论行本身，由前端按 state 决定怎么标注"


def test_link_state_none_when_no_job_row(job_db):
    job, state = dc._linked_job_for_task(
        {"task_id": "task-A", "task_type": "datacenter.sync.incremental"})
    assert state == "none"
    assert job is None


def test_link_state_none_for_unknown_task_type(job_db):
    job, state = dc._linked_job_for_task({"task_id": "t", "task_type": ""})
    assert state == "none" and job is None


def test_link_query_matches_by_job_type_derived_from_task_type(job_db):
    """`task_type = datacenter.sync.<mode>` ⇒ 查 `job_type = sync_<mode>`。"""
    _insert(job_db, job_type="sync_repair", trade_date="2026-10-04",
            status="FAILED", task_id="t-repair")
    _insert(job_db, job_type="sync_incremental", trade_date="2026-10-04",
            status="SUCCESS", task_id="t-inc")
    job, state = dc._linked_job_for_task(
        {"task_id": "t-repair", "task_type": "datacenter.sync.repair"})
    assert state == "linked", "按 job_type 精确定位失败 ⇒ 可能串到别的模式"
    assert job["job_type"] == "sync_repair"


# ---------------- 6：端到端（真实 app + 真实 sqlite，走 HTTP 层） ----------------

_ADMIN = {"Authorization": "Bearer aqp-dev-token-change-me"}


def test_e2e_overview_and_task_detail_are_linked():
    """端到端：`/overview` 给出执行 id，`/sync/tasks/{id}` 据此返回 `linked` + 结论。

    前面各用例都是**直接调用函数**，只证明逻辑正确；本用例走真实 `TestClient`
    + conftest 提供的真实 sqlite，证明：

    1. `data_jobs.task_id` 真的能从 `/overview` 的 `last_job` **穿透到 HTTP 响应**
       （列加上去但 SELECT/序列化漏了 —— 这正是本项目反复出现过的"生产侧改了
       契约、消费侧没跟"类缺陷，必须由端到端用例兜住）；
    2. `/sync/tasks/{id}` 的 `link_state` 对**真实** task_store 行判成 `linked`，
       并把结论行的 `verdict` 一并带出。

    用真实 task_store 的 `create_task`/`update_task` 造数据，避免手写表结构
    与 `models.py` 漂移（前面几处 DDL 夹具的教训）。
    """
    from fastapi.testclient import TestClient

    from app.main import app
    from app.services.task_store import create_task, update_task

    task_id = create_task("datacenter.sync.incremental", owner="e2e-test")
    update_task(task_id, "succeeded", progress={"done": 3, "total": 3})

    # 用真实写入点落 data_jobs（含 task_id），而不是手写 INSERT
    from app.core.config import get_settings
    ss._record_sync_job("incremental", "SUCCESS", 4321, None, 5208,
                        task_id=task_id)
    db = get_settings().SQLITE_PATH

    with TestClient(app) as c:
        r1 = c.get("/api/v1/datacenter/overview", headers=_ADMIN)
        assert r1.status_code == 200, r1.text
        body = r1.json()
        assert body["code"] == 0, body
        lj = body["data"]["last_job"]
        assert lj is not None, "overview 未返回 last_job"
        assert lj["task_id"] == task_id, (
            "task_id 未能穿透 /overview 响应 ⇒ 前端仍只能靠软匹配")

        r2 = c.get(f"/api/v1/datacenter/sync/tasks/{task_id}", headers=_ADMIN)
        assert r2.status_code == 200, r2.text
        data = r2.json()["data"]
        assert data["task_id"] == task_id
        assert data["link_state"] == "linked", (
            f"真实链路未关联（job_type 推导或查询有误）：{data.get('link_state')}")
        assert data["job"] is not None
        assert data["job"]["verdict"] == "success"
        assert data["job"]["task_id"] == task_id
        # 过程（task_store）与结论（data_jobs）在**同一个响应**里同时可用
        assert data["progress"]["total"] == 3

    # 收尾：清掉本用例落的两处数据，避免污染共享库后续用例
    with sqlite3.connect(str(db)) as conn:
        conn.execute("DELETE FROM data_jobs WHERE task_id = ?", (task_id,))
        conn.commit()


def test_e2e_missing_task_still_not_found():
    """缺任务仍走 ERR_NOT_FOUND（本次改动不得破坏既有错误码语义）。"""
    from fastapi.testclient import TestClient

    from app.main import app

    with TestClient(app) as c:
        r = c.get("/api/v1/datacenter/sync/tasks/definitely-missing-task",
                  headers=_ADMIN)
    assert r.status_code == 200, r.text
    assert r.json()["code"] == 40400


# ---------------- 5：迁移幂等（旧库补列） ----------------

def test_ensure_columns_adds_task_id_to_existing_table(tmp_path):
    """`ensure_columns` 必须能给**已存在**的旧 data_jobs 补 task_id（幂等）。"""
    from sqlalchemy import create_engine, text

    from app.db.migrations import ensure_columns

    db = tmp_path / "old.db"
    engine = create_engine(f"sqlite:///{db.as_posix()}")
    with engine.begin() as conn:
        conn.execute(text(
            "CREATE TABLE data_jobs (id INTEGER PRIMARY KEY, job_type TEXT, "
            "trade_date TEXT, UNIQUE(job_type, trade_date))"))
        added = ensure_columns(conn, "data_jobs", {"task_id": "VARCHAR(64)"})
        assert added == ["task_id"]
        # 幂等：再跑一次不应重复添加
        again = ensure_columns(conn, "data_jobs", {"task_id": "VARCHAR(64)"})
        assert again == []
        cols = [r[1] for r in conn.execute(text("PRAGMA table_info(data_jobs)"))]
        assert "task_id" in cols
