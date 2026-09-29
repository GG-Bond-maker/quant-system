"""消费侧守卫：``failed_count`` 从内部集合 → ``snapshot()`` → HTTP 线路键名（本轮口径）。

背景
----
作者用例 ``tests/test_failed_count_per_round.py`` 的 12 条断言**全部**落在内部集合
``_sync.failed`` 上（``in set()`` / ``== set()``）。但从"后端把失败清干净"到"前端
真的显示 0"，中间还有两段**未被任何用例覆盖**的链路：

    内部集合 ``_sync.failed``
        └─(1) ``_SyncState.snapshot()["failed_count"]``  ← 前端实际渲染的数值来源
              └─(2) ``GET /api/v1/datacenter/sync/status`` 响应的 ``data.failed_count``
                    ← 前端 ``api/datacenter.ts`` 实际读取的**键名**

为什么"只有生产侧守卫"不够
--------------------------
生产侧用例证明的是"启动新一轮任务会清空 ``_sync.failed``"。它**不能**证明：

    * ``snapshot()`` 真的把该集合暴露成名为 ``failed_count`` 的字段
      （若有人把它改名成 ``failed`` / ``failed_n``，内部集合用例仍全绿，
       而前端 ``types/datacenter.ts`` 读的 ``data.failed_count`` 变成 ``undefined``
       ⇒ UI 又回到"静默显示 0"，且没有任何用例变红）；
    * 该字段的**取值口径**确实来自 ``failed`` 集合
      （若有人误改成 ``len(self.completed)``，同样没有任何既有用例能发现）。

本文件把这两段消费侧链路钉死。下面的 ``test_..._snapshot_zero_after_new_round``
同时可回归生产侧纪律：删掉 ``trigger_sync`` 里的 ``_sync.failed.clear()`` 后，
它读到的 ``snapshot()["failed_count"]`` 会 > 0 而**变红**（见交付报告 A5 的实测）。

测试纪律
--------
* 不打桩被测逻辑（复位/计数/快照/路由均为真实代码）；
* 只替身外部 IO：``task_store`` / ``Thread`` / ``fetch_and_write_daily_bars`` /
  ``pipeline_slot``，绝不发真实网络、不写真实 DATA_ROOT；
* 隔离 ``_sync`` 单例（``dc._sync is ss._sync``）避免污染同进程其它用例；
* 鉴权走默认 ``ADMIN_TOKEN``（conftest 已钉回默认值）；HTTP 恒 200，
  断言业务信封 ``code`` 而非 HTTP 状态。
"""
from __future__ import annotations

import asyncio
import contextlib
import os
import sys
import types
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

BACKEND_ROOT = Path(__file__).resolve().parents[1]
if str(BACKEND_ROOT) not in sys.path:
    sys.path.insert(0, str(BACKEND_ROOT))

import app.api.v1.datacenter as dc  # noqa: E402
import app.data.ingest.tasks as tasks  # noqa: E402
from app.main import app  # noqa: E402

SYM = "159915.SZ"
_ADMIN_H = {
    "Authorization": f"Bearer {os.environ.get('ADMIN_TOKEN', 'aqp-dev-token-change-me')}"
}


# ---------------------------------------------------------------------------
# 替身
# ---------------------------------------------------------------------------
class _FakeThread:
    """``threading.Thread`` 替身：只记录 start，绝不真的跑 worker（可联网）。"""

    def __init__(self, *args, **kwargs) -> None:
        self.started = False

    def start(self) -> None:  # noqa: D401
        self.started = True


# ---------------------------------------------------------------------------
# 夹具
# ---------------------------------------------------------------------------
@pytest.fixture()
def iso_sync():
    """隔离 ``_sync`` 单例的可变状态（前后复位，防跨用例泄漏）。"""
    s = dc._sync
    saved = {
        "running": s.running,
        "completed": set(s.completed),
        "failed": set(s.failed),
        "completed_mode": s.completed_mode,
        "error": s.error,
        "done": s.done,
        "total": s.total,
        "current": s.current,
        "task_id": s.task_id,
        "mode": s.mode,
        "logs": list(s.logs),
    }
    s.cancel_event.clear()
    try:
        yield s
    finally:
        s.running = saved["running"]
        s.completed = saved["completed"]
        s.failed = saved["failed"]
        s.completed_mode = saved["completed_mode"]
        s.error = saved["error"]
        s.done = saved["done"]
        s.total = saved["total"]
        s.current = saved["current"]
        s.task_id = saved["task_id"]
        s.mode = saved["mode"]
        s.logs = saved["logs"]
        s.cancel_event.clear()


@pytest.fixture(scope="module")
def client():
    """真实 TestClient（走 HTTP 路由 + 信封序列化），供线路键名断言。"""
    with TestClient(app) as c:
        yield c


def _stub_sync_launch(monkeypatch) -> None:
    """把 ``trigger_sync`` 的外部依赖换成无副作用替身（不建任务、不起线程）。"""
    monkeypatch.setattr(dc, "create_task", lambda name: "task-consumer-1")
    monkeypatch.setattr(dc, "claim_task", lambda *a, **k: True)
    monkeypatch.setattr(dc, "update_task", lambda *a, **k: None)
    monkeypatch.setattr(dc, "read_all_symbols", lambda *a, **k: [])
    monkeypatch.setattr(dc, "threading", types.SimpleNamespace(Thread=_FakeThread))


# ---------------------------------------------------------------------------
# 1. trigger_sync(resume=False) 后 snapshot()["failed_count"] == 0
#    （消费侧复现生产侧纪律；删掉 clear 会在此变红）
# ---------------------------------------------------------------------------
def test_snapshot_failed_count_zero_after_new_round(iso_sync, monkeypatch):
    """先人工塞入历史失败，非续传启动新一轮 ⇒ snapshot() 的 failed_count 必须归零。

    这条同时守两件事：
      (a) 生产侧：``trigger_sync`` 清空 ``_sync.failed``；
      (b) 消费侧：``snapshot()`` 确实把该集合映射为 ``failed_count`` 字段。
    """
    _stub_sync_launch(monkeypatch)
    dc._sync.running = False
    dc._sync.failed = {"OLD-A", "OLD-B"}
    dc._sync.completed = {"OLD-C"}
    # 未启动前的基线：snapshot 明确把它读成 2
    assert dc._sync.snapshot()["failed_count"] == 2, "snapshot() 未从 failed 集合取数"

    req = dc.SyncRequest(mode="incremental", symbols=["600000.SH"], resume=False)
    asyncio.run(dc.trigger_sync(req, _user={}))

    snap = dc._sync.snapshot()
    assert "failed_count" in snap, "snapshot() 缺少 failed_count 字段（前端读不到）"
    assert snap["failed_count"] == 0, (
        "非续传启动新一轮后 snapshot()['failed_count'] 仍 > 0 ⇒ 前端「上次任务」文案"
        "会显示历史轮累计的失败数（本文件锁定的消费侧缺陷）")


def test_snapshot_failed_count_key_name_pinned(iso_sync):
    """键名钉死：必须是 ``failed_count``（前端 types/datacenter.ts 读的正是该键）。"""
    dc._sync.failed = {"A", "B", "C"}
    snap = dc._sync.snapshot()
    assert "failed_count" in snap, f"snapshot() 键集合: {sorted(snap)}"
    assert "failed" not in snap, "不得把内部集合名直接泄露为线路键"
    assert isinstance(snap["failed_count"], int), type(snap["failed_count"]).__name__
    assert snap["failed_count"] == 3, "failed_count 取值口径必须为 len(_sync.failed)"


# ---------------------------------------------------------------------------
# 2. HTTP 线路：GET /sync/status → data.failed_count
# ---------------------------------------------------------------------------
def test_sync_status_wire_exposes_failed_count_int(iso_sync, client):
    """线路键名守卫：HTTP 响应 ``data.failed_count`` 存在、为 int，且等于内部集合大小。

    本仓契约：HTTP 恒 200，业务结果在信封 ``code``。故断言 ``code == 0`` 与
    ``data`` 内键名，而非 HTTP 状态。
    """
    dc._sync.failed = {"WIRE-1"}
    r = client.get("/api/v1/datacenter/sync/status", headers=_ADMIN_H)
    assert r.status_code == 200, r.text
    body = r.json()
    assert body.get("code") == 0, f"鉴权/业务码异常: {body}"

    data = body.get("data")
    assert isinstance(data, dict), f"data 非对象: {data!r}"
    assert "failed_count" in data, (
        f"线路响应缺失 data.failed_count（前端读不到 ⇒ 静默 0）；实际键: {sorted(data)}")
    assert isinstance(data["failed_count"], int), (
        f"failed_count 应为 int，实际 {type(data['failed_count']).__name__}")
    assert data["failed_count"] == 1, "线路 failed_count 与内部集合不一致"


def test_sync_status_wire_failed_count_zero_when_empty(iso_sync, client):
    """对照：内部集合为空 ⇒ 线路 failed_count 为 0（证明它反映真实状态，非恒常数）。"""
    dc._sync.failed = set()
    r = client.get("/api/v1/datacenter/sync/status", headers=_ADMIN_H)
    body = r.json()
    assert body.get("code") == 0, body
    assert body["data"]["failed_count"] == 0, body


# ---------------------------------------------------------------------------
# 3. 全链路：_run_fetch 异常 → failed → snapshot() → 线路键名
# ---------------------------------------------------------------------------
def test_run_fetch_exception_reaches_wire_failed_count(iso_sync, client, monkeypatch):
    """``_run_fetch`` 的 except 分支必须一路透传到前端实际读取的线路键 ``failed_count``。

    覆盖「异常 → _sync.failed → snapshot() → HTTP data.failed_count」全链路；
    回归作者用例的第二条漏网：异常标的一旦两端计数都不进，前端静默漏掉。
    """

    def _boom(code, start, end, adjusts=("", "hfq"), fetcher=None):
        raise RuntimeError("all sources down")

    monkeypatch.setattr(tasks, "fetch_and_write_daily_bars", _boom)
    dc._sync.failed = set()
    dc._sync.completed = set()

    dc._run_fetch([SYM], "etf", "2024-01-01", "2024-01-31",
                  slot=contextlib.nullcontext())

    assert SYM in dc._sync.failed, "异常未记入内部 failed"
    assert dc._sync.snapshot()["failed_count"] > 0, "snapshot() 未反映内部 failed"

    r = client.get("/api/v1/datacenter/sync/status", headers=_ADMIN_H)
    body = r.json()
    assert body.get("code") == 0, body
    assert body["data"]["failed_count"] > 0, (
        "异常失败未透传到线路 data.failed_count ⇒ 前端显示「失败 0 只」（谎报）")


def test_run_fetch_no_data_keeps_wire_failed_count_zero(iso_sync, client, monkeypatch):
    """边界（防误伤）：n==0 且无失败口径（停牌/退市）⇒ 线路 failed_count 仍为 0。"""

    def _empty(code, start, end, adjusts=("", "hfq"), fetcher=None):
        return 0, []

    monkeypatch.setattr(tasks, "fetch_and_write_daily_bars", _empty)
    dc._sync.failed = set()
    dc._sync.completed = set()

    dc._run_fetch([SYM], "etf", "2024-01-01", "2024-01-31",
                  slot=contextlib.nullcontext())

    assert SYM in dc._sync.completed and SYM not in dc._sync.failed
    r = client.get("/api/v1/datacenter/sync/status", headers=_ADMIN_H)
    assert r.json()["data"]["failed_count"] == 0, r.json()
