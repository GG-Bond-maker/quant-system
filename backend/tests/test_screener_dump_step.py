"""step_screener_dump 的 event loop 依赖回归（缺陷 6，P1）。

背景（2026-09-18 定位）：``orchestrator.step_screener_dump`` 末行原为::

    asyncio.get_event_loop().run_until_complete(_log_run())

它**依赖调用方已在本线程 set 过 event loop**。``_run_pipeline_impl`` 会
``asyncio.new_event_loop() + set_event_loop()``，所以生产流水线路径能跑；但任何
**直接调用该步骤**的场景必崩（实测恢复脚本直接调 ``STEP_FUNCTIONS["screener_dump"]``）::

    RuntimeError: There is no current event loop in thread 'MainThread'.

为何是 P1（连环静默，与缺陷 2 同类）：该函数**先**写 top50 parquet、
``write_screener_snapshot()``、特征版本白名单校验，**最后**才写 ``FeatureRun``
审计行 ⇒ 崩在末行时数据其实都已落库，但整步被标 FAILED；而 ``_run_pipeline_impl``
是 fail-fast ⇒ 紧随其后的 ``build_cs_mirror`` 被中止 ⇒ **截面镜像静默停更**。

修法：无 loop 时就地 ``new_event_loop() + set_event_loop()``（同一线程复用）。
⚠️ 绝不可改成 ``asyncio.run(_log_run())``：它另起一个 loop，与
``_run_pipeline_impl`` 当前 loop 不是同一个；异步 SQLAlchemy 引擎的连接池绑定
loop，跨 loop 复用连接会永久 pending（本仓已有同类事故前例）。

隔离：不写共享 ``data/``——DATA_ROOT 指向 tmp_path，快照写入与会话工厂均打桩；
调用在**独立线程**内完成，避免污染 pytest 主线程的 loop 状态。

关于「无 loop」条件如何复现（重要）：
    单独跑本文件时，新建线程里 ``asyncio.get_event_loop()`` 确实抛 RuntimeError；
    但**全量跑**时其它用例/插件可能留下更宽松的 loop 策略，使新建线程也自带 loop
    （实测出现过），届时「前置断言」会失真 ⇒ 曾造成全量 flaky。
    故本文件不依赖环境残留，而是**显式建模**该条件（把 ``get_event_loop`` 打桩成
    抛 RuntimeError），使用例在任何上下文里都确定性成立。

变异反证（均实测）：
  - 把该行改回裸 ``asyncio.get_event_loop().run_until_complete(_log_run())``
    ⇒ ``test_screener_dump_works_without_preexisting_event_loop`` 变红；
  - 若把修法写成无条件 ``asyncio.new_event_loop()``
    ⇒ ``test_screener_dump_reuses_existing_loop`` 变红。
"""
from __future__ import annotations

import asyncio
import sys
import threading
from datetime import date
from pathlib import Path

import polars as pl

BACKEND_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(BACKEND_ROOT))

from app.core.config import get_settings  # noqa: E402
from app.orchestrator import step_screener_dump  # noqa: E402

TRADE_DAY = date(2026, 9, 17)


# ------------------------------------------------------------------ 打桩件
class _FakeSession:
    """最小异步会话替身：只记录 add/commit，不碰真实 DB。"""

    def __init__(self, sink: list) -> None:
        self._sink = sink

    def add(self, obj) -> None:  # noqa: ANN001
        self._sink.append(obj)

    async def commit(self) -> None:
        self._sink.append("COMMIT")


class _FakeSessionFactory:
    """替身 async_sessionmaker：``factory()`` 返回异步上下文管理器。"""

    def __init__(self, sink: list) -> None:
        self._sink = sink

    def __call__(self):  # noqa: ANN204
        sess = _FakeSession(self._sink)

        class _CM:
            async def __aenter__(self):
                return sess

            async def __aexit__(self, *exc) -> bool:
                return False

        return _CM()


def _write_predictions(data_root: Path, day: date) -> None:
    """写一份最小 predictions 分区（不含 feature_version ⇒ 走 None 分支）。"""
    pred_dir = data_root / "predictions"
    pred_dir.mkdir(parents=True, exist_ok=True)
    df = pl.DataFrame({
        "symbol": [f"{600000 + i:06d}.SH" for i in range(60)],
        "pred_score": [1.0 - i * 0.001 for i in range(60)],
    })
    df.write_parquet(pred_dir / f"date={day.strftime('%Y%m%d')}.parquet")


def _install_stubs(monkeypatch, data_root: Path) -> list:
    """重定向 DATA_ROOT + 打桩快照写入与会话工厂，返回审计行收集器。"""
    monkeypatch.setattr(get_settings(), "DATA_ROOT", data_root)
    monkeypatch.setattr(
        "app.data.screening.write_screener_snapshot",
        lambda date_str, df: "boards=4 rows=600")
    sink: list = []
    monkeypatch.setattr(
        "app.orchestrator.get_session_factory",
        lambda: _FakeSessionFactory(sink))
    return sink


def _run_in_thread(fn) -> dict:
    """在独立线程内执行 fn，返回 {res|exc}（避免污染主线程 loop 状态）。"""
    box: dict = {}

    def _target() -> None:
        try:
            box["res"] = fn()
        except BaseException as e:  # noqa: BLE001 如实记录任何异常
            box["exc"] = e

    t = threading.Thread(target=_target, name="screener-dump-thread", daemon=True)
    t.start()
    t.join(30)
    return box


def test_screener_dump_works_without_preexisting_event_loop(tmp_path, monkeypatch):
    """本线程**无当前 event loop** 时直接调用该步骤 ⇒ 不得抛 RuntimeError。

    「无 loop」用打桩建模（见模块 docstring）：``asyncio.get_event_loop()`` 恒抛
    ``RuntimeError: There is no current event loop...``——即缺陷的真实触发条件。

    变异反证：改回裸 ``asyncio.get_event_loop().run_until_complete(_log_run())``
    ⇒ 该 RuntimeError 直接冒泡 ⇒ 本用例在 ``assert box.get("exc") is None`` 处变红。
    """
    sink = _install_stubs(monkeypatch, tmp_path)
    _write_predictions(tmp_path, TRADE_DAY)

    def _no_current_loop():  # noqa: ANN202
        raise RuntimeError(
            "There is no current event loop in thread 'screener-dump-thread'.")

    monkeypatch.setattr(asyncio, "get_event_loop", _no_current_loop)

    box = _run_in_thread(lambda: step_screener_dump(TRADE_DAY, []))

    assert box.get("exc") is None, f"无 loop 线程调用该步骤抛错: {box.get('exc')!r}"
    assert str(box.get("res")).startswith("top50 dumped"), box.get("res")
    # 审计行确实写到了（不再是「崩在末行、静默丢审计」）
    assert any(x == "COMMIT" for x in sink), sink


def test_screener_dump_reuses_existing_loop(tmp_path, monkeypatch):
    """线程**已有** loop 时不得另起一个（防「过度修」破坏 loop 绑定的连接池）。

    ``asyncio.get_event_loop()`` 打桩为返回 sentinel，``asyncio.new_event_loop``
    打桩为记录器 ⇒ 断言后者**未被调用**。

    变异反证：若把修法写成无条件 ``asyncio.new_event_loop() + set_event_loop()``
    ⇒ 记录器被调用 ⇒ 本用例变红。
    """
    _install_stubs(monkeypatch, tmp_path)
    _write_predictions(tmp_path, TRADE_DAY)

    sentinel = asyncio.new_event_loop()      # 打桩前先造好真实 loop
    created: list = []
    original_new_event_loop = asyncio.new_event_loop

    def _record_new_loop():  # noqa: ANN202
        created.append("NEW_LOOP")
        return original_new_event_loop()

    monkeypatch.setattr(asyncio, "get_event_loop", lambda: sentinel)
    monkeypatch.setattr(asyncio, "new_event_loop", _record_new_loop)

    box = _run_in_thread(lambda: step_screener_dump(TRADE_DAY, []))
    sentinel.close()

    assert box.get("exc") is None, f"已有 loop 时调用该步骤抛错: {box.get('exc')!r}"
    assert created == [], f"已有 loop 仍被另建，跨 loop 复用连接池风险: {created}"
    assert str(box.get("res")).startswith("top50 dumped"), box.get("res")
