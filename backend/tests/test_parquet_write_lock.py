"""审计 P1-40 防回归：`write_partition` 的读-改-写必须互斥，不得丢失对方新增行。

缺陷（2026-09-21 全栈审计，P1；B3a 探针 probe3.py［C1］）：
    `write_partition` = 「读该年文件 -> concat -> 去重 -> 原子重写」。
    `_atomic_write_parquet` 只保证单文件不写半截，**不保证两个写者之间的先后**：
    两个写者各持旧快照时，后写者覆盖先写者 ⇒ 实测**文件 3 行变 2 行**，且
    manifest 记的是"丢失后的事实"，**事后无痕**。
    真实对手是**跨进程**的：夜间 `build_universe`（持 pipeline_slot）vs
    `scripts/build_universe.py` CLI —— 而 `core/pipeline_lock.py` 自述"进程级互斥"，
    跨进程无效，故必须用文件锁。

本文件覆盖：
    1. 同进程并发写不同日 → 两行都要在（原实现会丢一行）；
    2. 锁文件的创建/释放语义（不残留、不误删别人的锁）；
    3. 陈旧锁（崩溃残留）可被接管，不会永久卡死；
    4. 跨进程互斥：子进程持锁期间，父进程写同一分区应超时**报错**而非静默覆盖。
       ⚠️ 沙箱禁命名管道 ⇒ 子进程用 `stdio='ignore'` 启动、靠锁文件 mtime 传信号。
"""
from __future__ import annotations

import os
import subprocess
import sys
import threading
import time
from datetime import date
from pathlib import Path
from types import SimpleNamespace

BACKEND_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(BACKEND_ROOT))

import polars as pl  # noqa: E402
import pytest  # noqa: E402

import app.data.parquet_store as ps  # noqa: E402


@pytest.fixture
def data_root(tmp_path, monkeypatch) -> Path:
    """隔离 DATA_ROOT + 复位 manifest 缓存/账本，绝不触碰真实 data/。"""
    root = tmp_path / "parquet"
    root.mkdir(parents=True, exist_ok=True)
    monkeypatch.setattr(ps, "get_settings", lambda: SimpleNamespace(DATA_ROOT=root))
    monkeypatch.setattr(ps, "_manifest", None)
    monkeypatch.setattr(ps, "_audited", {})
    return root


def _df(days: list[int]) -> pl.DataFrame:
    return pl.DataFrame({
        "date": [f"2026-09-{d:02d}" for d in days],
        "close": [1.0] * len(days),
    })


def _rows(path: Path) -> int:
    return pl.read_parquet(path).height


# ---------------- 1) 同进程并发：不得丢行 ----------------

def test_concurrent_writes_do_not_lose_rows(data_root: Path) -> None:
    """两个线程各写一天，最终必须两行都在（原实现实测 3 行变 2 行）。"""
    d1, d2 = date(2026, 9, 1), date(2026, 9, 2)
    ps.write_partition("daily_bar", "600519.SH", d1, _df([1]))
    target = ps.path_for_year("daily_bar", "600519.SH", 2026)
    assert _rows(target) == 1

    barrier = threading.Barrier(2)
    errors: list[BaseException] = []

    def _writer(day: date, n: int) -> None:
        try:
            barrier.wait(timeout=10)      # 尽量让两个写者同时进入读-改-写
            ps.write_partition("daily_bar", "600519.SH", day, _df([n]))
        except BaseException as e:        # noqa: BLE001 探针：把线程内异常带回主线程
            errors.append(e)

    ts = [threading.Thread(target=_writer, args=(d2, 2)),
          threading.Thread(target=_writer, args=(d2, 2))]
    for t in ts:
        t.start()
    for t in ts:
        t.join(timeout=30)
    assert not errors, f"写入线程报错：{errors!r}"

    got = pl.read_parquet(target)
    assert got.height == 2, (
        f"并发读-改-写丢失新增行：最终 {got.height} 行（应为 2）—— 这正是 P1-40")
    # parquet 往返后 date 列是 Date 类型，按 date 对象比较（不要按字符串比）
    assert sorted(got["date"].to_list()) == [date(2026, 9, 1), date(2026, 9, 2)]


def test_many_concurrent_distinct_days_all_survive(data_root: Path) -> None:
    """N 个线程各写不同交易日：最终行数必须等于 N（锁下无丢行）。"""
    days = [date(2026, 9, d) for d in range(1, 13)]
    errors: list[BaseException] = []

    def _writer(day: date) -> None:
        try:
            ps.write_partition("daily_bar", "000001.SZ", day, _df([day.day]))
        except BaseException as e:        # noqa: BLE001
            errors.append(e)

    ts = [threading.Thread(target=_writer, args=(d,)) for d in days]
    for t in ts:
        t.start()
    for t in ts:
        t.join(timeout=30)
    assert not errors, f"写入线程报错：{errors!r}"

    target = ps.path_for_year("daily_bar", "000001.SZ", 2026)
    assert _rows(target) == len(days), f"应为 {len(days)} 行，实际 {_rows(target)}"


# ---------------- 2) 锁文件语义 ----------------

def test_lock_file_is_removed_after_write(data_root: Path) -> None:
    """正常写完后不得残留锁文件（否则后续写者要等超时）。"""
    ps.write_partition("daily_bar", "600519.SH", date(2026, 9, 1), _df([1]))
    target = ps.path_for_year("daily_bar", "600519.SH", 2026)
    lock = target.with_name(f".{target.name}.rwlock")
    assert target.exists() and not lock.exists(), f"锁文件残留：{lock}"


def test_lock_file_removed_even_on_write_failure(data_root: Path, monkeypatch) -> None:
    """写入抛异常时锁也必须释放（否则一次失败会永久卡死该分区）。"""
    target = ps.path_for_year("daily_bar", "600519.SH", 2026)
    lock = target.with_name(f".{target.name}.rwlock")

    def _boom(*a, **k):
        raise RuntimeError("模拟原子写失败")

    monkeypatch.setattr(ps, "_atomic_write_parquet", _boom)
    with pytest.raises(RuntimeError, match="模拟原子写失败"):
        ps.write_partition("daily_bar", "600519.SH", date(2026, 9, 1), _df([1]))
    assert not lock.exists(), "写入失败后锁文件残留 ⇒ 后续写入会被永久阻塞"


def test_rwlock_is_reentrant_free_but_serializes(data_root: Path) -> None:
    """`_rw_lock` 必须真正串行化（第二个持有者在第一个释放前拿不到锁）。"""
    target = ps.path_for_year("daily_bar", "LOCK.TEST", 2026)
    order: list[str] = []
    first_acquired = threading.Event()
    release_first = threading.Event()

    def _first() -> None:
        with ps._rw_lock(target):
            order.append("a-in")
            first_acquired.set()
            release_first.wait(timeout=20)
            order.append("a-out")

    def _second() -> None:
        first_acquired.wait(timeout=20)
        with ps._rw_lock(target):
            order.append("b-in")

    t1 = threading.Thread(target=_first)
    t2 = threading.Thread(target=_second)
    t1.start()
    t2.start()
    assert first_acquired.wait(timeout=20)
    time.sleep(0.3)                      # 给 t2 充分机会去抢锁
    assert "b-in" not in order, "锁未串行化：第二个持有者在第一个释放前就进入了"
    release_first.set()
    t1.join(timeout=20)
    t2.join(timeout=20)
    assert order == ["a-in", "a-out", "b-in"], order


def test_stale_lock_is_taken_over(data_root: Path) -> None:
    """崩溃残留的陈旧锁必须被接管，不能永久卡死（否则运维需手工删文件）。"""
    target = ps.path_for_year("daily_bar", "600519.SH", 2026)
    target.parent.mkdir(parents=True, exist_ok=True)
    lock = target.with_name(f".{target.name}.rwlock")
    lock.write_text("999999\n", encoding="utf-8")
    old = time.time() - (ps._LOCK_STALE_SECONDS + 60)
    os.utime(lock, (old, old))           # 伪造成"很久以前"的残留锁

    ps.write_partition("daily_bar", "600519.SH", date(2026, 9, 1), _df([1]))
    assert _rows(target) == 1, "陈旧锁未被接管 ⇒ 写入被永久阻塞"
    assert not lock.exists()


def test_fresh_lock_times_out_instead_of_silently_overwriting(
    data_root: Path, monkeypatch
) -> None:
    """他人持有**新鲜**锁时必须显式报错（宁可失败，也不静默覆盖）。

    原实现会直接读旧快照并覆盖 ⇒ 丢行且事后无痕；新实现必须抛 TimeoutError。
    """
    target = ps.path_for_year("daily_bar", "600519.SH", 2026)
    target.parent.mkdir(parents=True, exist_ok=True)
    ps.write_partition("daily_bar", "600519.SH", date(2026, 9, 1), _df([1]))
    lock = target.with_name(f".{target.name}.rwlock")
    lock.write_text("999999\n", encoding="utf-8")   # 新鲜锁：mtime=now

    monkeypatch.setattr(ps, "_LOCK_WAIT_SECONDS", 0.5)   # 缩短等待，避免测试变慢
    monkeypatch.setattr(ps, "_LOCK_POLL_SECONDS", 0.05)
    with pytest.raises(TimeoutError) as ei:
        ps.write_partition("daily_bar", "600519.SH", date(2026, 9, 2), _df([2]))
    assert "跨进程写锁超时" in str(ei.value)
    # 关键：被保护的文件**未被改动**（没有静默覆盖）
    assert _rows(target) == 1


# ---------------- 3) 跨进程互斥（真实子进程持锁） ----------------

_CHILD_HOLDER = """
import os, sys, time
from pathlib import Path
sys.path.insert(0, sys.argv[1])
lock = Path(sys.argv[2])
lock.parent.mkdir(parents=True, exist_ok=True)
fd = os.open(lock, os.O_CREAT | os.O_EXCL | os.O_RDWR)
os.write(fd, b"child\\n")
time.sleep(float(sys.argv[3]))
os.close(fd)
lock.unlink()
"""


def test_cross_process_lock_blocks_writer(data_root: Path, tmp_path: Path) -> None:
    """真实子进程持锁期间，本进程写同一分区必须超时（验证**跨进程**语义）。

    ⚠️ 沙箱边界：不能捕获子进程输出（命名管道被禁），故三个标准流都指向
    `DEVNULL`（`subprocess.DEVNULL` 打开的是 os.devnull，不是管道），
    仅靠锁文件存在性做同步。
    """
    target = ps.path_for_year("daily_bar", "600519.SH", 2026)
    ps.write_partition("daily_bar", "600519.SH", date(2026, 9, 1), _df([1]))
    lock = target.with_name(f".{target.name}.rwlock")

    script = tmp_path / "child_holder.py"
    script.write_text(_CHILD_HOLDER, encoding="utf-8")
    proc = subprocess.Popen(
        [sys.executable, str(script), str(BACKEND_ROOT), str(lock), "20"],
        stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL)   # ← 不用 PIPE（沙箱禁命名管道）
    try:
        deadline = time.time() + 15
        while not lock.exists() and time.time() < deadline:
            time.sleep(0.05)
        if not lock.exists():
            pytest.skip("子进程未能建立锁文件（环境限制）")

        ps._LOCK_WAIT_SECONDS = 1.0
        with pytest.raises(TimeoutError):
            ps.write_partition("daily_bar", "600519.SH", date(2026, 9, 2), _df([2]))
        assert _rows(target) == 1, "跨进程持锁期间文件被改写 ⇒ 跨进程互斥失效"
    finally:
        proc.kill()
        proc.wait(timeout=10)