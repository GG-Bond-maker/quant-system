"""调度器测试隔离回归（时段相关 flake 根因，round 5）。

背景（flaky 根因，2026-09-17 定位）：
    conftest 原先仅靠写入 ``DATA_ROOT.parent/.auto_sync.json``（enabled=False）关闭
    autoSync，但该隔离是**路径相对**的。一旦某测试把 ``get_settings().DATA_ROOT``
    重定向到自己的 ``tmp_path``（如 ``tests/test_data_freshness_degradation.py``），
    仍在运行的 ``auto_sync_scheduler`` 每轮 ``_load_auto_sync()`` 就会按**新**路径读
    文件 → 文件不存在 → 回退默认 ``{"enabled": True, "time": "15:45"}``。于是只要
    本机墙钟 >= 15:45 且当日未跑过，调度器即触发**真实增量同步**（akshare 联网）、
    抢锁 ``pipeline_slot("sync")``，令并发/后续用例拿到 ``ERR_PIPELINE_BUSY`` ——
    表现为「只在 15:45 之后的运行里失败」的时段相关 flake
    （QA 复验：23:xx 运行必现、00:xx 运行 0 失败）。

修法（见 conftest）：
    (1) autouse function fixture ``_neutralize_auto_sync`` 直接钉死**读取源**
        ``app.services.sync_service._load_auto_sync`` → ``{"enabled": False, ...}``
        （与 DATA_ROOT 路径、本机墙钟均无关）；
    (2) ``EVENING_ROUTINE_ENABLED`` 纳入环境隔离并置 0，关闭晚间例行调度联网。

本文件全部**时间无关**：用例内把墙钟钉到 23:59（远晚于默认触发时刻 15:45），
若隔离失效则必然触发真实同步——即用最苛刻的时段放大缺陷、且不依赖真实墙钟。

变异反证：
    - 删掉 conftest 的 ``_neutralize_auto_sync`` fixture ⇒
      ``test_auto_sync_neutralized_even_after_data_root_repoint`` 与
      ``test_scheduler_does_not_start_sync_at_late_hour`` 变红；
    - 去掉 conftest 对 ``EVENING_ROUTINE_ENABLED`` 的环境注入 ⇒
      ``test_evening_routine_disabled_in_tests`` 变红。

⚠️ 覆盖边界（2026-09-18 补注，勿误信其覆盖范围）：
    本文件只覆盖「**function patch 生效窗口内**的触发**单点谓词**」——
    三处用例都在测试体内直接调用/patch，全部处于 ``_neutralize_auto_sync`` 生效期。
    它**不覆盖**真实失效模式：调度器由 ``main.lifespan`` 在**module 作用域** ``TestClient``
    夹具中启动、其**首 tick 在 module 夹具装配期（function patch 之前）**执行。后者由
    ``tests/test_scheduler_lifetime_isolation.py`` 覆盖（会话级 ``_start_sync_bg`` no-op）。
    故本文件的绿**不能**单独证明时段相关 flake 已根治。
"""
from __future__ import annotations

import asyncio
import sys
from datetime import datetime
from pathlib import Path

import pytest

BACKEND_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(BACKEND_ROOT))

from app.core.config import get_settings  # noqa: E402
from app.services import sync_service  # noqa: E402


def test_auto_sync_neutralized_even_after_data_root_repoint(tmp_path, monkeypatch):
    """钉死读取源后，即使把 DATA_ROOT 重定向到全新 tmp_path（复现 flake 触发条件），
    ``_load_auto_sync()`` 仍恒返回 ``enabled=False``。

    变异反证：删掉 conftest 的 ``_neutralize_auto_sync`` fixture → 新路径下无
    ``.auto_sync.json`` → 回退默认 ``enabled=True`` → 本用例在 ``assert ... is False``
    处变红。
    """
    monkeypatch.setattr(get_settings(), "DATA_ROOT", tmp_path / "parquet")
    cfg = sync_service._load_auto_sync()
    assert cfg.get("enabled") is False, cfg


async def test_scheduler_does_not_start_sync_at_late_hour(tmp_path, monkeypatch):
    """墙钟钉到 23:59（>= 默认 15:45）+ DATA_ROOT 重定向 ⇒ 调度器**不得**触发同步。

    同时桩掉 ``_start_sync_bg`` 记录调用：若隔离失效，调度器会走到触发分支。
    用 ``asyncio.wait_for`` 给调度器一个短窗口——它内部是 ``while True`` + 60s sleep，
    正常情况下永不返回，故必然 ``TimeoutError``；而隔离失效时触发分支已先执行。

    变异反证：删掉 conftest fixture → ``_load_auto_sync`` 回退 ``enabled=True``，
    23:59 >= 15:45 且当日未跑过 → 触发分支命中 → 记录器被调用 → 本用例变红。
    """
    monkeypatch.setattr(get_settings(), "DATA_ROOT", tmp_path / "parquet")
    # 与真实全局调度状态解耦：确保「未在运行」前置条件成立（否则触发分支会被短路）
    monkeypatch.setattr(sync_service._sync, "running", False)

    class _LateDatetime(datetime):
        """把 sync_service 内的 ``datetime.now()`` 钉到 2026-09-17 23:59。"""

        @classmethod
        def now(cls, tz=None):  # noqa: ANN001, ANN003
            return cls(2026, 9, 17, 23, 59, 0)

    monkeypatch.setattr(sync_service, "datetime", _LateDatetime)

    triggered: list[tuple] = []
    monkeypatch.setattr(
        sync_service, "_start_sync_bg",
        lambda mode, resume: triggered.append((mode, resume)))

    with pytest.raises(asyncio.TimeoutError):
        await asyncio.wait_for(sync_service.auto_sync_scheduler(), 0.3)
    assert triggered == [], f"调度器在 23:59 触发了真实同步，隔离失效: {triggered}"


def test_evening_routine_disabled_in_tests():
    """conftest 注入 ``EVENING_ROUTINE_ENABLED=0`` ⇒ 晚间例行调度在测试中关闭。

    变异反证：去掉 conftest 的该环境变量注入 → 默认 True → 本用例在
    ``assert ... is False`` 处变红。
    """
    assert get_settings().EVENING_ROUTINE_ENABLED is False
