"""管道互斥锁（core/pipeline_lock.py）单元测试（C-01/C-02/C-03）。

验证五类管道任务（sync / fetch / pipeline / mirror / training）的互斥语义：
非阻塞申请、冲突拒绝、释放后可复用、非法任务名防呆。
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

BACKEND_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(BACKEND_ROOT))

from app.core.pipeline_lock import (  # noqa: E402
    PipelineBusy,
    current_pipeline_owner,
    pipeline_slot,
)


def test_slot_exclusive_and_reusable():
    """占用期冲突必须拒绝；释放后可再次申请；owner 状态正确。"""
    with pipeline_slot("sync"):
        assert current_pipeline_owner() == "sync"
        with pytest.raises(PipelineBusy) as ei:
            with pipeline_slot("mirror"):
                pass
        assert ei.value.owner == "sync"
        assert "sync" in str(ei.value)
    # 释放后 owner 清空、可复用
    assert current_pipeline_owner() is None
    with pipeline_slot("mirror"):
        assert current_pipeline_owner() == "mirror"


def test_invalid_task_name_rejected():
    """非法任务名直接 ValueError（防拼写错误导致观测混乱）。"""
    with pytest.raises(ValueError):
        with pipeline_slot("trainning"):  # 拼写错误
            pass


def test_all_pipeline_task_names_valid():
    """所有接入点任务名必须全部合法（防止接入点改名后锁失效）。"""
    for task in ("sync", "fetch", "pipeline", "mirror", "training"):
        with pipeline_slot(task):
            pass


def test_custom_fetch_rejected_when_pipeline_slot_is_busy():
    """自定义抓取写 daily_bar 前也必须申请同一把管道锁。"""
    from app.api.v1 import datacenter as datacenter_mod

    with pipeline_slot("sync"):
        with pytest.raises(PipelineBusy):
            datacenter_mod._run_fetch(
                ["000001.SZ"], "stock", "2026-01-02", "2026-01-02")


def test_release_on_exception():
    """任务抛异常也必须释放锁（finally 兜底），不得永久卡死管道。"""
    with pytest.raises(RuntimeError, match="boom"):
        with pipeline_slot("training"):
            raise RuntimeError("boom")
    assert current_pipeline_owner() is None
    with pipeline_slot("training"):  # 能再次申请
        pass


def test_run_pipeline_under_lock_is_rejected_when_busy(monkeypatch, tmp_path):
    """run_pipeline 必须在管道锁内：占住锁时调用应立即抛 PipelineBusy（不排队）。"""
    from app.orchestrator import run_pipeline

    with pipeline_slot("mirror"):
        with pytest.raises(PipelineBusy):
            run_pipeline(__import__("datetime").date(2026, 9, 12), dry_run=True)
