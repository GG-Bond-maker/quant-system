"""调度器**驻留 / 窗口外首 tick** 隔离回归（2026-09-18）。

与 ``test_scheduler_isolation.py`` 的**分工**（后者不覆盖这一点，见其 docstring）：
    后者在**测试体内部**直接 ``await auto_sync_scheduler()``，落在那时生效的 function 级
    patch 窗口**内**，只验证「patch 生效时触发谓词为假」的**单点谓词**。
    真实失效模式（2026-09-18 全量套件复现定位）是：调度器由 ``main.lifespan`` 在
    **module 作用域**的 ``TestClient(app)`` 夹具中启动，其**首 tick 在 module 夹具装配期**
    执行 —— 早于 function 级 autouse ``_neutralize_auto_sync``；且 module 级把
    ``DATA_ROOT`` 重定向到私有 tmp 根（其 ``.parent`` 下无 ``.auto_sync.json``）⇒
    ``_load_auto_sync()`` 回退默认 ``{"enabled": True, "time": "15:45"}`` ⇒ 墙钟 ≥15:45 时
    触发**真实增量同步**（akshare 联网）、抢 ``pipeline_slot("sync")``，令并发/后续用例拿到
    ``PipelineBusy: 管道任务 [sync] 正在执行``。
    本用例以「module 级 TestClient + 私有 DATA_ROOT + 钉死晚时刻」**原样复现窗口外首 tick**。

红 / 绿（判据 = **实际危害**，不是日志行）：
    - 未加**会话级** no-op（``conftest._neutralize_auto_sync_start``）时：首 tick 触发真实
      ``_start_sync_bg`` ⇒ ``_sync.running`` 变 True 且 ``pipeline_slot("sync")`` 被占用
      ⇒ 本用例**红**（已实测：见交付报告）。
    - 加上会话级 ``_start_sync_bg`` no-op 后：触发分支即便命中也不起后台同步 ⇒ 锁始终空闲
      ⇒ 本用例**绿**。

为何不用日志行做判据：会话级 no-op 只掐「启动入口」``_start_sync_bg``，``auto_sync_scheduler``
的触发分支仍会打印 ``[datacenter] auto sync triggered`` —— 故必须以「锁被占用 / running 被置
True」这类**真实副作用**为判据，否则又是「测了假东西」。

时间无关：用例内把 ``sync_service.datetime`` 钉到 23:59（≥ 默认 15:45），不依赖真实墙钟。
"""
from __future__ import annotations

import sys
import time
from datetime import datetime
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

BACKEND_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(BACKEND_ROOT))

from app.core import pipeline_lock  # noqa: E402
from app.core.config import get_settings  # noqa: E402
from app.main import app  # noqa: E402
from app.services import sync_service  # noqa: E402


class _LateDatetime(datetime):
    """把 ``sync_service`` 内的 ``datetime.now()`` 钉到 2026-09-17 23:59（≥ 默认 15:45）。"""

    @classmethod
    def now(cls, tz=None):  # noqa: ANN001, ANN003
        return cls(2026, 9, 17, 23, 59, 0)


@pytest.fixture(scope="module")
def late_client(tmp_path_factory):
    """module 级 TestClient：私有 DATA_ROOT（无 .auto_sync.json）+ 钉死晚时刻。

    刻意对齐 ``tests/test_screener_stocks.py`` / ``tests/test_screener_snapshot.py`` 的真实触发
    条件：module 级先重定向 ``DATA_ROOT`` 再 ``with TestClient(app)``，使调度器**首 tick 在
    module 夹具装配期（function 级 patch 生效之前）**执行。装配顺序 session → module → function，
    故该窗口正是 function 级隔离覆盖不到的地方。
    """
    mp = pytest.MonkeyPatch()
    private_root = tmp_path_factory.mktemp("sched_lifetime_data")
    # 私有 DATA_ROOT：其 .parent 下无 .auto_sync.json ⇒ _load_auto_sync 回退默认 enabled=True
    mp.setattr(get_settings(), "DATA_ROOT", private_root)
    mp.setattr(sync_service, "datetime", _LateDatetime)          # 钉死晚时刻（≥15:45）
    mp.setattr(sync_service._sync, "running", False)             # 前置：未在运行
    # 持久信号：_start_sync_bg 会写 _sync.started_at；no-op 后恒为 None（供判据使用）
    mp.setattr(sync_service._sync, "started_at", None)
    # 即便触发也不打网络：让 _run_incremental 在**持锁期间**停留足够久，使「锁被占用」可被稳定观测
    mp.setattr(sync_service, "_refresh_trade_calendar", lambda: time.sleep(2.0))
    mp.setattr("app.data.ingest.tasks.fetch_and_write_daily_bars",
               lambda *a, **k: (0, []))
    with TestClient(app) as c:
        time.sleep(0.4)  # 给调度器首 tick 在 module 夹具装配期执行的机会
        yield c
    mp.undo()


def _wait_sync_owner(timeout: float = 3.0) -> str | None:
    """轮询等待：观察 ``pipeline_slot("sync")`` 是否被占用（返回最终 owner）。"""
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if pipeline_lock.current_pipeline_owner() == "sync":
            return "sync"
        time.sleep(0.05)
    return pipeline_lock.current_pipeline_owner()


def test_first_tick_outside_patch_window_must_not_acquire_sync_lock(late_client):
    """窗口外首 tick 不得起真实同步 / 不得占用 ``pipeline_slot("sync")``（本用例是红绿判据）。

    判据 = **实际危害**：真实同步一旦启动，``_sync.started_at`` 被写入（持久信号，不随同步
    结束而清零），且期间 ``pipeline_slot("sync")`` 被占用。会话级 no-op 掐掉 ``_start_sync_bg``
    后二者皆不发生。**不用日志行**做判据（触发分支仍会打 ``auto sync triggered``）。
    """
    owner = _wait_sync_owner()
    assert sync_service._sync.started_at is None, (
        "调度器在 module 夹具窗口外的首 tick 真实启动了后台同步"
        "（_start_sync_bg 被调用，_sync.started_at 被写入）——隔离失效")
    assert owner != "sync", (
        "调度器在 module 夹具窗口外的首 tick 占用了 pipeline_slot('sync')（隔离失效）")
    assert sync_service._sync.running is False, \
        "后台同步被真实启动（_sync.running=True）"
