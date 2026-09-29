"""2026-09-26 收口回归：stats_cache 代际守卫 + 写入路径必须失效统计缓存。

覆盖两件事（对应 team-lead 指定的收口范围）：
1. **代际守卫**（``app/services/stats_cache.py``）：当一次 ``cached()`` 计算**在飞**期间
   发生了 ``invalidate_stats_cache()``，该次计算算出的旧值**不得被写回缓存**（否则
   会把"失效"变成空操作，并把旧快照固化到 TTL=1800s）。对照：无失效干扰时正常写回。
2. **写入路径失效**（``orchestrator`` / ``text_ingest`` / ``cross_section``）：这三个
   此前"写数据却不失效缓存"的路径，现在必须在写完后调用 ``invalidate_stats_cache()``。
"""
from __future__ import annotations

import threading
from datetime import date

import polars as pl

from app.services import stats_cache as sc


# ---------------------------------------------------------------------------
# 1) 代际守卫
# ---------------------------------------------------------------------------
def test_generation_guard_skips_writeback_after_invalidate():
    """fn() 执行期间发生失效 ⇒ 值不写回缓存；下一次调用必须重算。"""
    entered = threading.Event()
    release = threading.Event()
    calls = {"n": 0}

    def fn():
        calls["n"] += 1
        entered.set()
        release.wait(5)      # 卡住，制造"在飞"窗口
        return "V1"

    got: dict = {}

    def worker():
        got["val"] = sc.cached("gen_guard_key", 1800, fn)

    t = threading.Thread(target=worker)
    t.start()
    assert entered.wait(5), "fn 未在超时内启动"
    # fn 仍在跑 → 此刻失效（模拟同步/流水线写库后 invalidate）
    sc.invalidate_stats_cache()
    release.set()
    t.join(5)

    assert got.get("val") == "V1", "调用方应仍拿到本次刚算出的值"
    with sc._CACHE_LOCK:
        assert "gen_guard_key" not in sc._cache, (
            "失效期间算出的旧值被写回了缓存（代际守卫失效）")

    # 下一次调用找不到缓存 ⇒ 必须再次执行 fn
    before = calls["n"]
    sc.cached("gen_guard_key", 1800, fn)   # release 已置位，立即返回
    assert calls["n"] == before + 1, "失效后下一次调用未重算（旧值仍被当作有效缓存）"


def test_normal_writeback_without_invalidate():
    """无失效干扰 ⇒ 代际不变 ⇒ 正常写回，第二次命中缓存不再执行 fn。"""
    calls = {"n": 0}

    def fn():
        calls["n"] += 1
        return 42

    assert sc.cached("norm_key", 1800, fn) == 42
    assert sc.cached("norm_key", 1800, fn) == 42
    assert calls["n"] == 1, "无失效时未命中缓存的第二次调用不应重算"


def test_invalidate_bumps_generation():
    """invalidate_stats_cache 必须自增代际（守卫的另一半）。"""
    with sc._CACHE_LOCK:
        before = sc._generation
    sc.invalidate_stats_cache()
    with sc._CACHE_LOCK:
        after = sc._generation
    assert after == before + 1


# ---------------------------------------------------------------------------
# 2) 写入路径必须调用 invalidate_stats_cache
# ---------------------------------------------------------------------------
def _spy(monkeypatch, module):
    """把 module 命名空间里的 invalidate_stats_cache 换成计数器。"""
    hits = {"n": 0}

    def _counter():
        hits["n"] += 1

    monkeypatch.setattr(module, "invalidate_stats_cache", _counter)
    return hits


class _FakeSettings:
    """把模块内的 get_settings() 钉到本用例私有 DATA_ROOT。

    为什么必须隔离：这些写入路径会**遍历整个 DATA_ROOT**（build_mirror 扫描
    daily_bar 下全部源文件；import_documents 读 announcements_docs）。测试会话共享
    同一个 DATA_ROOT，其它用例留下的、schema 各异的残留 parquet 会污染本用例
    （实测：全量跑时 build_mirror 因其它用例写的无 symbol 列文件而 ColumnNotFoundError，
    单跑却通过）。钉到 tmp_path 后与用例顺序无关。
    """

    def __init__(self, root):
        self.DATA_ROOT = root


def _isolate(monkeypatch, module, tmp_path):
    monkeypatch.setattr(module, "get_settings", lambda: _FakeSettings(tmp_path))
    return tmp_path


def test_import_documents_invalidates(monkeypatch, tmp_path):
    from app.data import text_ingest

    _isolate(monkeypatch, text_ingest, tmp_path)
    hits = _spy(monkeypatch, text_ingest)
    res = text_ingest.import_documents(
        [{"symbol": "600519", "date": "2024-01-02", "title": "业绩增长", "content": "预计扭亏"}],
        source="test")
    assert res["imported"] == 1
    assert hits["n"] >= 1, "import_documents 写库后未失效统计缓存"


def test_score_and_build_factor_invalidates(monkeypatch, tmp_path):
    from app.data import text_ingest

    _isolate(monkeypatch, text_ingest, tmp_path)
    # 先种一份文档（该 import 自身也会失效，故随后只测 build 的调用）
    text_ingest.import_documents(
        [{"symbol": "600519", "date": "2024-01-02", "title": "业绩增长", "content": "预计扭亏"}],
        source="test")
    hits = _spy(monkeypatch, text_ingest)
    res = text_ingest.score_and_build_factor()
    assert res["ok"] is True
    assert hits["n"] >= 1, "score_and_build_factor 写库后未失效统计缓存"


def test_build_mirror_invalidates(monkeypatch, tmp_path):
    from app.data import cross_section

    _isolate(monkeypatch, cross_section, tmp_path)
    # 种一个含 date/symbol 的源分区，使 build_mirror 真的产出镜像（built>0）
    root = tmp_path / "daily_bar"
    f = root / "symbol=600519.SH" / "year=2024.parquet"
    f.parent.mkdir(parents=True, exist_ok=True)
    pl.DataFrame({
        "date": [date(2024, 1, 2)],
        "symbol": ["600519.SH"],
        "close": [10.0],
    }).write_parquet(f)

    hits = _spy(monkeypatch, cross_section)
    res = cross_section.build_mirror("daily_bar")
    assert res["built"] >= 1, "镜像未产出，本用例失去意义"
    assert hits["n"] >= 1, "build_mirror 写库后未失效统计缓存"


def test_run_pipeline_invalidates(monkeypatch):
    """run_pipeline 每成功一步即失效（fail-fast ⇒ 逐步骤放，覆盖部分成功）。"""
    from app import orchestrator as orch

    hits = _spy(monkeypatch, orch)
    monkeypatch.setattr(orch, "is_trade_day_checked", lambda d: True)
    monkeypatch.setattr(orch, "STEP_FUNCTIONS", {"stub": lambda d, c: "ok"})

    class _Job:
        id = 1
        status = "PENDING"
        current_step = None
        error_message = None
        traceback = None
        finished_at = None
        duration_ms = 0

    async def _fake_create(trade_date, job_type):
        return _Job(), True

    async def _fake_finish(*a, **k):
        return None

    monkeypatch.setattr(orch, "_create_or_get_job_async", _fake_create)
    monkeypatch.setattr(orch, "_finish_job_async", _fake_finish)

    job, executed = orch._run_pipeline_impl(date(2024, 1, 2), ["600519"], steps=["stub"])
    assert executed is True
    assert hits["n"] >= 1, "run_pipeline 步骤成功后未失效统计缓存"


def test_run_pipeline_does_not_invalidate_on_step_failure(monkeypatch):
    """失败的步骤不得触发失效（"没写成功就不该失效"）。"""
    from app import orchestrator as orch

    hits = _spy(monkeypatch, orch)
    monkeypatch.setattr(orch, "is_trade_day_checked", lambda d: True)

    def _boom(d, c):
        raise RuntimeError("step failed")

    monkeypatch.setattr(orch, "STEP_FUNCTIONS", {"stub": _boom})
    monkeypatch.setattr(orch, "notify_failure", lambda msg: None)

    class _Job:
        id = 1
        status = "PENDING"
        current_step = None
        error_message = None
        traceback = None
        finished_at = None
        duration_ms = 0

    async def _fake_create(trade_date, job_type):
        return _Job(), True

    async def _fake_finish(*a, **k):
        return None

    monkeypatch.setattr(orch, "_create_or_get_job_async", _fake_create)
    monkeypatch.setattr(orch, "_finish_job_async", _fake_finish)

    job, executed = orch._run_pipeline_impl(date(2024, 1, 2), ["600519"], steps=["stub"])
    assert executed is True
    assert job.status == "FAILED"
    assert hits["n"] == 0, "步骤失败路径不应失效缓存"
