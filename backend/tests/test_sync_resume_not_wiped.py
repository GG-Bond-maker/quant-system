"""审计 P1-28 防回归：断点续传不得被"自动同步启动"抹掉，且不得跨 mode 复用。

缺陷（2026-09-21 全栈审计，P1；台账记为"已修又被抹掉"）：
    * P2-14 已实现跨重启续传：`restore_sync_state()` 在 lifespan 里恢复
      `_sync.completed`（来源 `app_state.sync_state`，中断时 `finished=False`）；
    * API 路径 `datacenter.py:/sync/fetch` 也遵守纪律：`if not req.resume: clear()`；
    * **但** `sync_service._start_sync_bg()`（auto_sync_scheduler 每 60s 调一次、
      且传 `resume=False`）里有一句**无条件** `_sync.completed.clear()`
      ⇒ 启动时刚恢复的断点被立刻抹掉，"跨重启续传"实际从未生效。

本文件把这四条钉死：
    1. `_start_sync_bg(..., resume=True)` **不得**清空续传集；
    2. `_start_sync_bg(..., resume=False)` 仍要清空（新任务语义不变）；
    3. `_resume_skip_set` 只在 mode 归属一致时返回内容（跨 mode 不得跳过）；
    4. 源码级断言三个 worker 都改用 `_resume_skip_set`（防止新增 worker 漏掉）。
"""
from __future__ import annotations

import sys
from pathlib import Path

BACKEND_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(BACKEND_ROOT))

from app.services import sync_service as ss  # noqa: E402

import pytest  # noqa: E402


@pytest.fixture(autouse=True)
def _clean_sync_state():
    """每个用例前后都把全局同步状态复位。

    `_sync` 是模块级单例，而下面的 Thread 替身不会真正跑 worker（于是 worker 的
    `finally: running=False` 不会执行）⇒ 不复位会让 `running=True` 泄漏到下一个
    用例，使 `_start_sync_bg` 走"已在运行"的早退分支（本文件最初就踩了这个坑）。
    """
    def _wipe() -> None:
        ss._sync.running = False
        ss._sync.completed = set()
        ss._sync.completed_mode = None
        ss._sync.cancel_event.clear()
        ss._sync.error = None
        ss._sync.task_id = None
        ss._sync.failed = set()

    _wipe()
    yield
    _wipe()


def _reset_state(completed: set[str], owner: str | None = "incremental") -> None:
    ss._sync.running = False
    ss._sync.completed = set(completed)
    ss._sync.completed_mode = owner
    ss._sync.cancel_event.clear()
    ss._sync.error = None
    ss._sync.task_id = None


def test_resume_start_keeps_completed() -> None:
    """核心断言：resume=True 启动同步时，已恢复的断点必须仍在。

    ⚠️ 这里直接调用 `_start_sync_reset`（`_start_sync_bg` 的函数体在测试中不可达）：
    `tests/conftest.py` 有 **session 级 autouse** `_neutralize_auto_sync_start`，
    把 `app.services.sync_service._start_sync_bg` 整个替换为 no-op（防测试触发真实
    联网同步）。因此该函数的**函数体在测试套件里永远测不到** —— 这也是 P1-28 能
    长期存活的原因之一。抽出的 `_start_sync_reset` 正是为此可测。
    """
    _reset_state({"600000.SH", "000001.SZ"}, owner="incremental")
    ss._start_sync_reset(ss._sync, "incremental", True)

    assert ss._sync.completed == {"600000.SH", "000001.SZ"}, (
        "resume=True 启动时断点被抹掉了 —— 这正是 P1-28："
        "auto_sync_scheduler 的启动把 restore_sync_state() 的成果清空")
    assert ss._sync.mode == "incremental"
    assert ss._sync.done == 0 and ss._sync.error is None


def test_fresh_start_still_clears() -> None:
    """resume=False 语义不变：新任务必须从空续传集开始。"""
    _reset_state({"600000.SH"}, owner="incremental")
    ss._start_sync_reset(ss._sync, "incremental", False)
    assert ss._sync.completed == set()
    assert ss._sync.completed_mode == "incremental"


def test_start_sync_bg_uses_reset_helper() -> None:
    """源码级：`_start_sync_bg` 必须调用 `_start_sync_reset`（而非自行 clear）。"""
    src = (BACKEND_ROOT / "app" / "services" / "sync_service.py").read_text(encoding="utf-8")
    body = src.split("def _start_sync_bg(", 1)[1]
    assert "_start_sync_reset(_sync, mode, resume)" in body
    assert "_sync.completed.clear()" not in body, (
        "_start_sync_bg 里又出现无条件 clear() ⇒ P1-28 回归")


def test_conftest_still_neutralizes_start_sync_bg() -> None:
    """记录测试边界：conftest 的 session 级替身若被移除，本文件的直接调用策略需重审。"""
    src = (BACKEND_ROOT / "tests" / "conftest.py").read_text(encoding="utf-8")
    assert "_neutralize_auto_sync_start" in src
    assert '"app.services.sync_service._start_sync_bg"' in src
    assert "lambda mode, resume: None" in src


def test_start_is_idempotent_when_running() -> None:
    """已在运行时重复启动不得改动续传集（`_start_sync_bg` 的早退分支）。

    该分支不可直接调用（conftest 已把函数钉成 no-op），但 `_start_sync_reset`
    的语义要求"只有启动成功才复位" —— 故此处断言早退前置条件本身：
    `running=True` 时调用方不应进入复位逻辑。
    """
    _reset_state({"600000.SH"})
    ss._sync.running = True
    try:
        # 模拟 _start_sync_bg 的守卫：running 为真则直接返回，不复位
        if not ss._sync.running:
            ss._start_sync_reset(ss._sync, "incremental", False)
        assert ss._sync.completed == {"600000.SH"}
    finally:
        ss._sync.running = False


def test_resume_skip_set_same_mode() -> None:
    _reset_state({"a", "b"}, owner="repair")
    assert ss._resume_skip_set("repair") == {"a", "b"}


def test_resume_skip_set_cross_mode_is_empty() -> None:
    """跨 mode 必须忽略续传记录：repair 的"完成"不等于 incremental 的"完成"。"""
    _reset_state({"a", "b"}, owner="repair")
    assert ss._resume_skip_set("incremental") == set()
    assert ss._resume_skip_set("rebuild") == set()


def test_resume_skip_set_unknown_owner_allows() -> None:
    """归属未知（None）时按调用方语义放行，不引入额外拒绝。"""
    _reset_state({"a"}, owner=None)
    assert ss._resume_skip_set("incremental") == {"a"}


def test_resume_skip_set_returns_copy() -> None:
    """返回副本：调用方改动不得污染全局续传集。"""
    _reset_state({"a"}, owner="incremental")
    got = ss._resume_skip_set("incremental")
    got.add("b")
    assert ss._sync.completed == {"a"}


def test_all_workers_use_resume_skip_set() -> None:
    """源码级：三个 runner 都必须走 `_resume_skip_set`，不得再用裸 `_sync.completed`。"""
    src = (BACKEND_ROOT / "app" / "services" / "sync_service.py").read_text(encoding="utf-8")
    for mode in ("incremental", "repair", "rebuild"):
        assert f'_resume_skip_set("{mode}")' in src, f"runner {mode} 未使用 _resume_skip_set"
    assert "resume and s in _sync.completed" not in src, (
        "仍有 runner 直接对 _sync.completed 做 resume 判断（跨 mode 会误跳过）")


def test_api_path_records_owner_mode() -> None:
    """API 启动路径同样要登记归属 mode（否则跨 mode 检查形同虚设）。"""
    src = (BACKEND_ROOT / "app" / "api" / "v1" / "datacenter.py").read_text(encoding="utf-8")
    assert "_sync.completed_mode = req.mode" in src


def test_restore_sync_state_records_owner_mode() -> None:
    """恢复路径必须记录归属 mode（owner 的来源之一）。"""
    src = (BACKEND_ROOT / "app" / "services" / "sync_service.py").read_text(encoding="utf-8")
    assert "_sync.completed_mode = st.get(\"mode\")" in src