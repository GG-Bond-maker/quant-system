"""数据中心统计的性能修复回归（2026-09-26）。

真 bug：/data 页长期显示
``部分数据面板加载失败：请求超时，请稍后重试；请求超时，请稍后重试``

根因两条（互补，缺一仍会超时）：

1. ``_scan_file`` 对**每个文件开了三次** —— ``pl.read_parquet_schema`` 探可读性、
   ``pq.ParquetFile(...).metadata.num_rows`` 取行数、``pl.read_parquet`` 再解码
   ``date`` 列求 min/max。``daily_bar`` / ``_qfq`` / ``_hfq`` 三兄弟共 **3.3 万个
   文件** ⇒ 约 10 万次 parquet 打开；实测冷算 ``datasets`` **74s**、``quality``
   **86s**，而前端这两个端点用的是**默认 15s** 超时 ⇒ 必然超时。
2. ``stats_cache.cached`` 是"锁内查、锁外算"，**没有 single-flight**，而 docstring
   却自称"并发下只算一次" ⇒ 页面并行发起的多个请求各自跑一遍全量扫描，互相抢 IO。

修法：``_scan_file`` 改为**一个** pyarrow 句柄 + **优先读 row group statistics**
（零解码），统计不可用时才回退解码列；``cached`` 加 per-key 闸门；两个端点的 TTL
从 120s/300s 拉到 1800s（同步写库后由 ``invalidate_stats_cache()`` 立即失效，
故长 TTL 不会让用户看到陈旧统计）；前端超时放宽到 90s。

测试纪律：写**真实 parquet 文件**，不 monkeypatch 被测逻辑本身；只在需要观测
"是否走了回退路径"时替换回退函数（那是观测点，不是被测逻辑）。
"""
from __future__ import annotations

import sys
import threading
import time
from datetime import date
from pathlib import Path

import polars as pl
import pytest

BACKEND_ROOT = Path(__file__).resolve().parents[1]
if str(BACKEND_ROOT) not in sys.path:
    sys.path.insert(0, str(BACKEND_ROOT))

from app.api.v1 import datacenter as dc  # noqa: E402
from app.services import stats_cache as sc  # noqa: E402


def _write(df: pl.DataFrame, path: Path, **kw) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    df.write_parquet(path, **kw)
    return path


@pytest.fixture()
def clean_cache():
    """清空统计缓存的进程级状态（含 single-flight 闸门登记表），避免跨用例串味。"""
    sc._cache.clear()
    sc._inflight.clear()
    yield sc
    sc._cache.clear()
    sc._inflight.clear()


# ---------------------------------------------------------------------------
# A. _scan_file 必须优先走 footer 统计（零解码）
# ---------------------------------------------------------------------------
def test_scan_file_prefers_footer_statistics(tmp_path: Path, monkeypatch) -> None:
    """统计可用时**不得**解码日期列 —— 直接把回退函数打成"一调就失败"来钉死这条。

    修法前每个文件都要 ``pl.read_parquet(columns=[date_col])``；正是这一步 × 3.3 万
    个文件构成了 74s 冷扫的主体。
    """
    # 故意乱序：统计给的是 row group 的 min/max，与顺序无关
    days = [date(2024, 12, 31), date(2024, 3, 1), date(2024, 5, 20)]
    f = _write(pl.DataFrame({"date": days, "v": [1, 2, 3]}), tmp_path / "a.parquet")

    def _forbidden(*_a, **_kw):
        raise AssertionError("统计可用时不允许走解码回退路径")

    monkeypatch.setattr(dc, "_date_range_from_column", _forbidden)

    assert dc._scan_file(f, "date") == (3, date(2024, 3, 1), date(2024, 12, 31))


def test_scan_file_falls_back_when_statistics_absent(tmp_path: Path, monkeypatch) -> None:
    """统计缺失（``statistics=False`` 写出的文件）时必须回退解码，且结果正确。"""
    days = [date(2024, 7, 1), date(2024, 2, 29), date(2024, 11, 5)]
    f = _write(pl.DataFrame({"date": days, "v": [1, 2, 3]}),
               tmp_path / "nostats.parquet", statistics=False)

    used: list[int] = []
    original = dc._date_range_from_column

    def _spy(*a, **kw):
        used.append(1)
        return original(*a, **kw)

    monkeypatch.setattr(dc, "_date_range_from_column", _spy)

    assert dc._scan_file(f, "date") == (3, date(2024, 2, 29), date(2024, 11, 5))
    assert used, "统计缺失时**必须**回退解码列（否则区间会静默为 None）"


def test_scan_file_missing_date_column_returns_none_range(tmp_path: Path) -> None:
    """映射的日期列不存在（如 announcements 没有 date 列）：行数有效、区间为 None。"""
    f = _write(pl.DataFrame({"pub_date": [date(2024, 1, 2)], "title": ["x"]}),
               tmp_path / "ann.parquet")

    assert dc._scan_file(f, "date") == (1, None, None)


def test_scan_file_all_null_dates_returns_none_range(tmp_path: Path) -> None:
    """日期列全空：行数有效、区间为 None（不得抛、不得给假区间）。"""
    f = _write(pl.DataFrame({"date": [None, None]}, schema={"date": pl.Date}),
               tmp_path / "null.parquet")

    rows, lo, hi = dc._scan_file(f, "date")
    assert rows == 2
    assert lo is None and hi is None


def test_scan_file_corrupt_file_still_raises(tmp_path: Path) -> None:
    """可读性探针契约不变：损坏文件必须**抛异常**（不得被吞成 0 行）。

    这是"静默给出错误信息"的那条红线：吞成 0 就无法区分「空文件」与「坏文件」，
    调用方失去告警依据。
    """
    f = tmp_path / "bad.parquet"
    f.parent.mkdir(parents=True, exist_ok=True)
    f.write_bytes(b"definitely not a parquet file")

    with pytest.raises(Exception):
        dc._scan_file(f, "date")


# ---------------------------------------------------------------------------
# B. cached() 必须有 single-flight
# ---------------------------------------------------------------------------
def test_cached_single_flight_computes_once(clean_cache) -> None:
    """同一 key 的并发冷启动只调用一次 ``fn``（修前：N 个线程 = N 次全量扫描）。"""
    gate = threading.Event()
    calls: list[int] = []
    results: list[str] = []

    def slow() -> str:
        calls.append(1)
        gate.wait(3.0)  # 卡住首次计算，让其余线程都聚到闸门上
        return "v"

    def worker() -> None:
        results.append(clean_cache.cached("k", 60, slow))

    threads = [threading.Thread(target=worker) for _ in range(8)]
    for t in threads:
        t.start()
    time.sleep(0.2)  # 给所有线程进入"等闸门"状态的机会
    gate.set()
    for t in threads:
        t.join(5)

    assert results == ["v"] * 8
    assert len(calls) == 1, f"fn 被调用了 {len(calls)} 次 —— 没有 single-flight"


def test_cached_ttl_hit_semantics_unchanged(clean_cache) -> None:
    """契约不变：TTL 内命中不重算；ttl=0 必然过期重算。"""
    calls: list[int] = []

    def fn() -> int:
        calls.append(1)
        return len(calls)

    assert clean_cache.cached("k", 60, fn) == 1
    assert clean_cache.cached("k", 60, fn) == 1, "TTL 内应命中缓存"
    assert len(calls) == 1

    assert clean_cache.cached("k", 0, fn) == 2, "ttl=0 应视为已过期"
    assert len(calls) == 2


def test_cached_exception_propagates_and_leaves_no_gate(clean_cache) -> None:
    """异常语义不变：向上传播、**不写缓存**，且闸门登记必须清干净（否则泄漏）。"""
    calls: list[int] = []

    def boom() -> str:
        calls.append(1)
        raise RuntimeError("boom")

    with pytest.raises(RuntimeError):
        clean_cache.cached("k", 60, boom)
    assert clean_cache._inflight == {}, "异常路径未清理闸门 ⇒ _inflight 泄漏"

    with pytest.raises(RuntimeError):
        clean_cache.cached("k", 60, boom)
    assert len(calls) == 2, "失败结果不得被缓存"


def test_cached_different_keys_do_not_block_each_other(clean_cache) -> None:
    """不同 key 各自独立：慢 key 不得把其它 key 也串行化。"""
    started = threading.Event()
    release = threading.Event()

    def slow() -> str:
        started.set()
        release.wait(3.0)
        return "slow"

    out: list[str] = []
    t = threading.Thread(target=lambda: out.append(clean_cache.cached("slow", 60, slow)))
    t.start()
    assert started.wait(3.0)

    # 慢 key 仍在算，另一个 key 必须能立刻拿到自己的值
    assert clean_cache.cached("fast", 60, lambda: "fast") == "fast"

    release.set()
    t.join(5)
    assert out == ["slow"]


# ---------------------------------------------------------------------------
# C. TTL 与前端超时的配比（防回归：TTL 被改回短值 ⇒ 每几分钟撞一次冷扫）
# ---------------------------------------------------------------------------
def test_datacenter_scan_ttls_are_long_enough() -> None:
    """``datasets`` / ``quality`` 的 TTL 必须远大于前端超时，冷扫才只在启动/同步后发生。"""
    assert dc._TTL_DATASETS >= 1800, dc._TTL_DATASETS
    assert dc._TTL_QUALITY >= 1800, dc._TTL_QUALITY
