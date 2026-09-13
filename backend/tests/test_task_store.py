"""第 3 阶段：后台任务状态可持久化、可查询、可领取。"""
from __future__ import annotations

from app.services.task_store import claim_task, create_task, get_task, update_task


def test_task_lifecycle_persists_in_isolated_sqlite():
    task_id = create_task("test.long-task")
    assert get_task(task_id)["status"] == "queued"
    assert claim_task(task_id, "worker-a") is True
    assert claim_task(task_id, "worker-b") is False
    update_task(task_id, "succeeded", progress={"done": 3, "total": 3},
                result={"ok": True})
    task = get_task(task_id)
    assert task is not None
    assert task["status"] == "succeeded"
    assert task["progress"] == {"done": 3, "total": 3}
    assert task["result"] == {"ok": True}


def test_cancel_requested_is_not_terminal():
    task_id = create_task("test.cancel")
    update_task(task_id, "cancel_requested")
    task = get_task(task_id)
    assert task is not None
    assert task["finished_at"] is None


def test_expired_running_task_can_be_reclaimed():
    task_id = create_task("test.lease")
    assert claim_task(task_id, "worker-a", lease_seconds=-1) is True
    assert claim_task(task_id, "worker-b", lease_seconds=300) is True
    assert get_task(task_id)["owner"] == "worker-b"
