"""panic 收口补刀：``ml/monitor._retrain_worker`` 的 BaseException 兜底 + finally 终态落库。

与 ``tests/test_resilient_loop.py`` 的分工
----------------------------------------
``test_resilient_loop.py`` 断言的是「长驻循环不静默停摆」；本文件断言的是
**daemon 线程执行体的用户可见后果**：

``_retrain_worker`` 由 ``maybe_auto_retrain`` 以 ``threading.Thread`` 起（daemon），
**不经 ASGI 中间件栈** ⇒ B 段 ``PanicGuardMiddleware`` 覆盖不到。原实现有两个洞：

1. ``except Exception`` 漏接 ``BaseException``（polars ``PanicException`` 是
   ``BaseException`` 子类），panic 直接穿出 ``try``；
2. 终态落库原本在 ``try`` **之外**且**无 finally** ⇒ 任何**被重新抛出**的退出路径
   （如致命 BaseException）都会跳过终态落库，KV 停在 ``maybe_auto_retrain`` 写下的
   ``{"status": "running"}`` ⇒ 该函数顶部的并发守卫（``status=="running"`` 且
   ``now - started_ts < 7200``）会据此**静默阻断**自动重训 7200 秒。用户只看到
   「上一次自动重训仍在进行中」，**看不到任何报错**。

⚠️ 隔离（关键，否则污染会话级共享目录）
--------------------------------------
本文件的被测路径会**真实读写** ``DATA_ROOT/features/version=<FV>`` 与 ``app_state`` KV。
若沿用会话级共享的 ``DATA_ROOT``，写入的 stub ``year=*.parquet`` 会被**后续**用例
（``test_pipeline.py`` / ``test_write_endpoints_smoke.py``）当成真实特征去解析 ⇒ 触发
真实 polars 解析异常、令它们集体变红。故这里用 ``retrain_env`` 夹具把
``monitor.get_settings`` 的 ``DATA_ROOT`` 与 ``app.db.kv.get_settings`` 的
``SQLITE_PATH`` **同时**重定向到用例私有 ``tmp_path``。
"""
from __future__ import annotations

import contextlib
import sqlite3
import time
from pathlib import Path

import pandas as pandas
import polars as pl
import pytest
from loguru import logger
from prometheus_client import generate_latest

from app.core.config import get_settings as _real_get_settings
from app.core.resilience import LOG_PREFIX
from app.db.kv import kv_get, kv_set
from app.ml import monitor
from app.ml.features import FEATURE_VERSION

_RETRAIN_KEY = monitor._RETRAIN_KEY


# ------------------------------------------------------------------ 测试工具件
def _real_panic() -> BaseException:
    """制造**真实**的 polars Rust panic（多列帧 + ``dtype=pl.Null`` 单列 sort）。"""
    try:
        pl.DataFrame({
            "symbol": ["600000.SH", "600001.SH"],
            "pred_score": pl.Series([None, None], dtype=pl.Null),
        }).sort("pred_score")
    except BaseException as exc:  # noqa: BLE001 PanicException 是 BaseException
        assert not isinstance(exc, Exception), "必须是不可捕获的 PanicException"
        return exc
    raise AssertionError("未触发 panic（polars 行为可能已变化）")


def _loop_metric(loop_name: str) -> float:
    """读 ``aqp_loop_panic_contained_total{loop="..."}`` 的真实当前值。"""
    needle = f'aqp_loop_panic_contained_total{{loop="{loop_name}"}}'
    for line in generate_latest().decode("utf-8").splitlines():
        if line.startswith(needle):
            return float(line.rsplit(" ", 1)[-1])
    return 0.0


@pytest.fixture
def log_capture(tmp_path):
    """临时 loguru sink（``enqueue=True``，复刻生产 sink 的 pickle 约束）。"""
    log_file = tmp_path / "monitor_retrain.log"
    sink_id = logger.add(str(log_file), level="DEBUG", format="{message}",
                         enqueue=True, encoding="utf-8")

    def _read() -> str:
        try:
            logger.complete()
        except Exception:  # noqa: BLE001 老版本 loguru 无 complete()
            pass
        return log_file.read_text(encoding="utf-8", errors="replace")

    try:
        yield log_file, _read
    finally:
        with contextlib.suppress(ValueError):
            logger.remove(sink_id)


class _SettingsProxy:
    """把真实 settings 的少数字段换成用例私有值，其余字段透传（版本无关）。"""

    def __init__(self, base, **overrides) -> None:
        self._base = base
        self.__dict__.update(overrides)

    def __getattr__(self, name):  # noqa: ANN001 仅在正常查找失败时触发
        return getattr(self._base, name)


@pytest.fixture
def retrain_env(tmp_path, monkeypatch) -> Path:
    """隔离被测路径的真实读写：``DATA_ROOT``（features）与 ``app_state`` KV 全部落到 tmp。

    Returns:
        私有 ``DATA_ROOT``（``tmp_path/parquet``）。
    """
    data_root = tmp_path / "parquet"
    (data_root / "features").mkdir(parents=True, exist_ok=True)
    kv_path = tmp_path / "kv.db"
    conn = sqlite3.connect(str(kv_path))
    try:
        conn.execute("CREATE TABLE IF NOT EXISTS app_state "
                     "(key TEXT PRIMARY KEY, value TEXT, updated_at TEXT)")
        conn.commit()
    finally:
        conn.close()

    base = _real_get_settings()
    monkeypatch.setattr(monitor, "get_settings",
                        lambda: _SettingsProxy(base, DATA_ROOT=data_root))
    monkeypatch.setattr("app.db.kv.get_settings",
                        lambda: _SettingsProxy(base, SQLITE_PATH=kv_path))
    return data_root


def _features_dir(root: Path) -> Path:
    d = root / "features" / f"version={FEATURE_VERSION}"
    d.mkdir(parents=True, exist_ok=True)
    return d


def _seed_one_part(root: Path) -> None:
    """造一个分片，令 worker 的 ``glob("year=*.parquet")`` 非空（否则先抛 RuntimeError）。"""
    (_features_dir(root) / "year=2024.parquet").write_bytes(b"stub-not-really-read")


def _clear_parts(root: Path) -> None:
    for p in _features_dir(root).glob("year=*.parquet"):
        p.unlink()


def _seed_running() -> None:
    """复刻 ``maybe_auto_retrain`` 启动时写下的「running」前置态（写用例私有 KV）。"""
    kv_set(_RETRAIN_KEY, {"status": "running", "started_ts": time.time(),
                          "started_at": "2026-09-19T00:00:00", "trigger": "drift"})


class _RaisingPandas:
    """替换 ``monitor.pd``：``read_parquet`` 抛注入异常；``concat`` 不应到达。"""

    def __init__(self, exc: BaseException) -> None:
        self._exc = exc

    def read_parquet(self, *_a, **_k):  # noqa: ANN002, ANN003
        raise self._exc

    def concat(self, *_a, **_k):  # noqa: ANN002, ANN003
        raise AssertionError("不应到达 pd.concat（read_parquet 应先抛出）")


class _StubPandas:
    """成功路径用：``read_parquet`` 返回小 DataFrame，``concat`` 走真实 pandas。"""

    def read_parquet(self, *_a, **_k):  # noqa: ANN002, ANN003
        return pandas.DataFrame({"symbol": ["600000.SH"]})

    def concat(self, objs, **kwargs):  # noqa: ANN001
        return pandas.concat(objs, **kwargs)


# ================================================== 1) 真 panic：兜住 + finally 落库
def test_retrain_worker_contains_real_panic_and_clears_running(
        log_capture, retrain_env, monkeypatch):
    """真 polars ``PanicException`` ⇒ ①不抛出 ②KV 终态非 running ③**用户不再被静默阻断**。

    反证：删掉新增 ``except BaseException`` 分支（m1）⇒ panic 穿出 ⇒ 本用例在①变红。
    """
    _log_file, read = log_capture
    before = _loop_metric("monitor_retrain")

    _seed_running()                       # 前置：maybe_auto_retrain 已写 running
    _seed_one_part(retrain_env)           # 令 glob 非空，才能走到 read_parquet
    monkeypatch.setattr(monitor, "pd", _RaisingPandas(_real_panic()))

    monitor._retrain_worker("drift")      # ① 不得抛出

    st = kv_get(_RETRAIN_KEY)
    assert st is not None
    assert st["status"] != "running", f"panic 后 KV 仍卡 running（静默阻断 7200s）: {st}"
    assert "finished_ts" in st and "finished_at" in st, f"终态未落库: {st}"

    # ③ 最重要：直接验**用户可见后果**——不得再返回「上一次自动重训仍在进行中」
    monkeypatch.setattr(monitor, "_retrain_worker", lambda reason: None)  # 防万一真起线程
    res = monitor.maybe_auto_retrain("drift")
    assert "仍在进行中" not in res["note"], f"panic 后仍被静默阻断: {res}"
    assert res["attempted"] is False
    assert "距上次重训不足" in res["note"], f"应被间隔规则挡下（而非并发守卫）: {res}"

    # ② 日志真的落盘（enqueue=True sink）+ 指标 +1
    text = read()
    assert LOG_PREFIX in text, f"panic 未留痕: {text[:400]!r}"
    assert "monitor_retrain" in text
    assert "PanicException" in text, "堆栈未随 message 落盘"
    assert _loop_metric("monitor_retrain") - before == 1.0


# ================================================== 2) 致命 BaseException：放行 + finally 仍落库
@pytest.mark.parametrize("exc_cls", [KeyboardInterrupt, SystemExit])
def test_retrain_worker_propagates_fatal_but_still_writes_terminal_state(
        log_capture, retrain_env, monkeypatch, exc_cls):
    """fatal（``KeyboardInterrupt`` / ``SystemExit``）⇒ ①继续传播 ②**finally 仍落下终态**。

    反证：把终态落库从 finally 移回函数尾部（m2）⇒ 致命异常被重新抛出、跳过尾部落库
    ⇒ KV 仍卡 running ⇒ 本用例在②变红（证明 finally 是「任何路径都不留 running」的关键）。
    """
    _log_file, read = log_capture
    _seed_running()
    _seed_one_part(retrain_env)
    monkeypatch.setattr(monitor, "pd", _RaisingPandas(exc_cls()))

    with pytest.raises(exc_cls):          # ① 必须放行
        monitor._retrain_worker("drift")

    st = kv_get(_RETRAIN_KEY)
    assert st is not None
    assert st["status"] != "running", f"fatal 路径下 KV 仍卡 running: {st}"
    assert "finished_ts" in st and "finished_at" in st, f"fatal 路径 finally 未落库: {st}"
    assert st["error"].startswith(exc_cls.__name__), st

    assert LOG_PREFIX not in read(), "fatal BaseException 不应被兜住留痕"


# ================================================== 3) 普通 Exception：语义不变
def test_retrain_worker_ordinary_exception_marks_failed_and_publishes(
        log_capture, retrain_env, monkeypatch):
    """普通 ``Exception``（features 为空 → ``RuntimeError``）：
    status=``failed`` + error + 事件仍发布 + 终态落库 + 不走 resilience 分支。"""
    _log_file, read = log_capture
    published: list = []
    monkeypatch.setattr("app.core.events.publish_threadsafe",
                        lambda topic, message: published.append((topic, message)))

    _features_dir(retrain_env)            # 造目录但**不放分片** ⇒ parts 为空 ⇒ RuntimeError
    _clear_parts(retrain_env)

    monitor._retrain_worker("drift")      # 不得抛出

    st = kv_get(_RETRAIN_KEY)
    assert st is not None
    assert st["status"] == "failed", f"普通异常应记 failed: {st}"
    assert st["error"].startswith("RuntimeError"), st
    assert "finished_ts" in st and "finished_at" in st, f"终态未落库: {st}"

    assert published and published[0][0] == "monitor", f"普通异常应仍发事件: {published}"
    assert "自动重训失败" in published[0][1]
    assert LOG_PREFIX not in read(), "普通异常不应走 resilience 兜底分支"


# ================================================== 4) 成功路径：语义完全不变
def test_retrain_worker_success_path_semantics_unchanged(
        log_capture, retrain_env, monkeypatch):
    """成功路径：status=``done`` + model_version/promoted/valid_rank_ic/gate_reason
    逐字段不变 + 事件发布 + KV 终态落库（钉住「别顺手改正常路径」）。"""
    _log_file, read = log_capture
    published: list = []
    monkeypatch.setattr("app.core.events.publish_threadsafe",
                        lambda topic, message: published.append((topic, message)))
    monkeypatch.setattr(monitor, "pd", _StubPandas())
    monkeypatch.setattr(monitor, "_compute_dataset_version", lambda: "ds_test")
    monkeypatch.setattr("app.ml.train_lgbm.train_lgbm",
                        lambda df, **kw: {"version": "20260919_000000_monitor",
                                          "valid_rank_ic": 0.0512,
                                          "n_symbols": 100, "valid_days": 250})
    monkeypatch.setattr("app.ml.registry.get_model",
                        lambda name, version: {"name": name, "version": version})
    monkeypatch.setattr("app.ml.registry.get_production", lambda *_a, **_k: {"version": "prod"})
    monkeypatch.setattr("app.ml.registry.evaluate_candidate",
                        lambda cand, prod, policy: type("D", (), {"reason": "rank_ic_ok"})())
    monkeypatch.setattr("app.ml.registry.promote_model",
                        lambda *a, **k: {"promoted": True})

    _seed_one_part(retrain_env)
    monitor._retrain_worker("drift")

    st = kv_get(_RETRAIN_KEY)
    assert st is not None
    assert st["status"] == "done", f"成功路径 status 被改坏: {st}"
    assert st["model_version"] == "20260919_000000_monitor", st
    assert st["promoted"] is True, st
    assert st["valid_rank_ic"] == 0.0512, st
    assert st["gate_reason"] == "rank_ic_ok", st
    assert st["trigger"] == "drift" and "finished_ts" in st, st

    assert published and published[0][0] == "monitor", f"成功应发事件: {published}"
    assert "自动重训完成" in published[0][1]
    assert LOG_PREFIX not in read(), "成功路径不应走 resilience 兜底分支"
