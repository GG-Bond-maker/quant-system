"""数据链路缺陷 A 回归（2026-10-03）：失败占比过高时终态不得记 SUCCESS。

背景（2026-09-28 实证）：``sync_incremental`` 在市场数据**大面积未更新**的情况下
仍被记为 SUCCESS，令 ``datacenter.py`` 的健康灯取到错误结论、掩盖停更。
修复：``_sync_worker`` 在 ``noop`` 判定之后、SUCCESS 之前增加判据
``failed/total >= SYNC_MAX_FAILED_RATIO`` ⇒ 记 FAILED（error_message 带 PARTIAL）。

阈值口径（2026-10-03 对齐）：``SYNC_MAX_FAILED_RATIO`` 与 ``SYNC_FAILURE_ALERT_RATIO``
已统一为**同一值 0.05**——前者改为对后者的**单一数据源引用**（此前二者各写各的：
上游 0.05 / 兜底 0.5，属口径漂移）。本文件所有失败率场景均**基于常量相对构造**
（不再写死 0.4/0.5/0.6 之类的绝对值），故阈值再次漂移时本文件仍能正确区分。

隔离纪律：直接驱动 ``_sync_worker`` 的终态分支，不发网络、不写真实 DB
（``_record_sync_job`` / ``_persist_sync_state`` 全部替身）；``_sync`` 单例在每个
用例前复位，避免污染同进程其它用例。
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

BACKEND_ROOT = Path(__file__).resolve().parents[1]
if str(BACKEND_ROOT) not in sys.path:
    sys.path.insert(0, str(BACKEND_ROOT))

import app.services.sync_service as ss  # noqa: E402

from app.services.sync_service import (  # noqa: E402
    SYNC_FAILURE_ALERT_RATIO,
    SYNC_MAX_FAILED_RATIO,
)

# 场景规模：须满足 1/_TOTAL < 阈值（0.05 ⇒ 需 _TOTAL > 20），否则无法构造
# "失败数>0 且占比严格低于阈值"的用例（零星失败会被取整抹平）。
_TOTAL = 100


def _counts_around_threshold(total: int) -> tuple[int, int]:
    """按当前阈值返回 ``(须判 FAILED 的失败数, 须判 SUCCESS 的失败数)``。

    相对常量构造，阈值漂移仍成立（不越界）：
    - ``over``  = ``int(阈值*total) + 1`` ⇒ 占比**严格高于**阈值
      （用整数+1 而非浮点 ``+0.1``，避免阈值下探到 <0.1 时构造出 >1 或负比例）。
    - ``under`` = ``int(阈值*total) - 1`` ⇒ 占比**严格低于**阈值，且 **≥1 只**，
      覆盖真正被 worker 兜底放行的"零星失败"路径（失败数为 0 会令用例失去区分度）。
    """
    k = int(SYNC_MAX_FAILED_RATIO * total)
    over = min(total, max(1, k + 1))
    under = max(1, k - 1)
    return over, under


def _make_symbols(total: int) -> list[str]:
    """生成 ``total`` 个互不相同的标的代码（形如 ``600000.SZ``）。"""
    return [f"600{i:03d}.SZ" for i in range(total)]


@pytest.fixture()
def isolated_sync():
    """复位 `_sync` 单例的可变状态（口径=本轮，跨用例隔离）。"""
    s = ss._sync
    s.completed.clear()
    s.failed.clear()
    s.done = 0
    s.total = 0
    s.rows_written = 0
    s.error = None
    s.task_id = None
    s.logs.clear()
    s.cancel_event.clear()
    yield s


def _prep_worker(monkeypatch, recorded: dict) -> None:
    """隔离 worker 的落库/持久化副作用，并收集终态。"""
    monkeypatch.setattr(
        ss, "_record_sync_job",
        lambda mode, status, dur, err=None: recorded.update(
            mode=mode, status=status, err=err))
    monkeypatch.setattr(ss, "_persist_sync_state", lambda *a, **k: None)


def test_rebuild_mass_failure_records_failed(isolated_sync, monkeypatch):
    """真实可达路径：``_run_rebuild`` 不调用 ``_mark_mass_failure`` ⇒ 靠 worker 级兜底。

    rebuild 的逐标的失败只进 ``failed`` 集、**不置** ``_sync.error``；构造 ``over/_TOTAL``
    只 ``build_qfq_dataset`` 抛错（占比严格高于阈值）⇒ 终态必须 FAILED（而非 SUCCESS），
    且 error_message 含 ``PARTIAL`` 与占比。
    """
    recorded: dict = {}
    _prep_worker(monkeypatch, recorded)
    total = _TOTAL
    over, _under = _counts_around_threshold(total)
    symbols = _make_symbols(total)
    fail_syms = set(symbols[:over])

    def _build(root, sym):
        if sym in fail_syms:
            raise RuntimeError("qfq rebuild failed")
        return 5

    monkeypatch.setattr("app.data.repair.build_qfq_dataset", _build)
    monkeypatch.setattr(ss, "invalidate_stats_cache", lambda: None)

    isolated_sync.total = total
    isolated_sync.done = 0

    ss._sync_worker("rebuild", symbols, resume=False)

    assert recorded.get("status") == "FAILED", recorded
    err = recorded.get("err") or ""
    assert "PARTIAL" in err, err
    assert f"{over}/{total}" in err, err


def test_worker_partial_fail_over_threshold_records_failed(isolated_sync, monkeypatch):
    """worker 判据直测：done>0 且失败占比 ≥ 阈值 ⇒ FAILED（用替身 runner 绕过上游机制）。"""
    recorded: dict = {}
    _prep_worker(monkeypatch, recorded)
    total = _TOTAL
    over, _under = _counts_around_threshold(total)
    symbols = _make_symbols(total)

    def runner(syms, resume=False):
        with ss._sync.lock:
            ss._sync.done = len(syms)
            ss._sync.failed = set(syms[:over])        # over/total 严格 > 阈值
            ss._sync.completed = set(syms[over:])

    monkeypatch.setattr(ss, "_run_incremental", runner)

    isolated_sync.total = total
    isolated_sync.done = 0

    ss._sync_worker("incremental", symbols, resume=False)

    assert recorded.get("status") == "FAILED", recorded
    assert "PARTIAL" in (recorded.get("err") or ""), recorded


def test_worker_partial_fail_below_threshold_still_success(isolated_sync, monkeypatch):
    """反向护栏（阈值必须经受住零星失败）：0 < 失败占比 < 阈值 ⇒ 不得误判为 FAILED。

    用替身 runner 绕过上游 ``_mark_mass_failure``，单独验证本 worker 兜底阈值：只要占比
    **严格低于**阈值，正常的零星网络抖动绝不能被升级为作业失败。
    """
    recorded: dict = {}
    _prep_worker(monkeypatch, recorded)
    total = _TOTAL
    _over, under = _counts_around_threshold(total)
    symbols = _make_symbols(total)

    def runner(syms, resume=False):
        with ss._sync.lock:
            ss._sync.done = len(syms)
            ss._sync.rows_written = 5
            ss._sync.failed = set(syms[:under])       # under/total 严格 < 阈值 且 > 0

    monkeypatch.setattr(ss, "_run_incremental", runner)

    isolated_sync.total = total
    isolated_sync.done = 0

    ss._sync_worker("incremental", symbols, resume=False)

    assert recorded.get("status") == "SUCCESS", recorded


def test_max_failed_ratio_shares_alert_threshold():
    """防漂移护栏：worker 兜底阈值必须**恒等于**上游告警阈值（当前同值 5%）。

    二者曾各写各的（上游 0.05 / 兜底 0.5）而分叉，令同一"失败占比"在两条路径上得出
    相反结论。此断言钉死"单一数据源"：若将来有人把 ``SYNC_MAX_FAILED_RATIO`` 改回
    字面量而与 ``SYNC_FAILURE_ALERT_RATIO`` 不再一致，本用例立即变红。
    """
    assert SYNC_MAX_FAILED_RATIO == SYNC_FAILURE_ALERT_RATIO, (
        SYNC_MAX_FAILED_RATIO, SYNC_FAILURE_ALERT_RATIO)


def test_threshold_scope_is_sane():
    """阈值口径合理性：必须落在 (0, 1] 区间（纯比例，不设绝对下限）。"""
    assert 0.0 < SYNC_MAX_FAILED_RATIO <= 1.0, SYNC_MAX_FAILED_RATIO
