"""启动例行：回收陈旧 ``*.tmp``（原子写残留）。

背景：``_atomic_write_parquet`` / ``_manifest_flush_locked`` 的 ``finally`` 在进程被
``TerminateProcess`` 强杀时来不及跑 ⇒ 残留 ``*.tmp``（实测生产 8 个、最早 621h 前）。

本文件验证 :func:`app.data.parquet_store.reclaim_stale_tmp_files` 的：
  ① 递归扫描（含隐藏 ``.manifest.*.tmp`` 与深层分区目录）；
  ② 按 mtime 年龄判、阈值内不动；``.rwlock`` 不在范围；
  ③ 返回结构化计数（scanned/removed/kept/failed/bytes_freed/removed_sample）；
  ④ 单文件删除失败不中断整体。

⚠️ 隔离：本测试会**造 + 删**文件，故用 ``data_root`` fixture 把 ``DATA_ROOT``
monkeypatch 到 pytest 的 ``tmp_path`` —— 否则**裸跑**（不经 conftest 重定向）时会落到
**生产** ``data/parquet``。
"""
from __future__ import annotations

import os
import sys
import time
import types
from pathlib import Path

import pytest

BACKEND_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(BACKEND_ROOT))

import app.data.parquet_store as ps  # noqa: E402


@pytest.fixture
def data_root(tmp_path, monkeypatch):
    """把 ``reclaim_stale_tmp_files`` 读的 ``DATA_ROOT`` 指到 ``tmp_path/parquet``。

    ⚠️ 必须 monkeypatch ``parquet_store.get_settings``（而非仅依赖 conftest）：
    ``reclaim_stale_tmp_files`` 通过 ``get_settings().DATA_ROOT`` 取根目录，裸跑时
    该值指向生产 ``data/parquet``，本测试的造/删会污染生产目录。
    """
    root = tmp_path / "parquet"
    root.mkdir(parents=True, exist_ok=True)
    monkeypatch.setattr(ps, "get_settings",
                        lambda: types.SimpleNamespace(DATA_ROOT=root))
    return root


def _make_tmp(path: Path, age_hours: float, size: int = 10) -> Path:
    """造一个指定年龄（小时）与大小的文件（用 mtime 模拟陈旧）。"""
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(b"x" * size)
    ts = time.time() - age_hours * 3600.0
    os.utime(path, (ts, ts))
    return path


def test_reclaim_removes_only_stale_covers_hidden_and_nested(data_root):
    old_nested = _make_tmp(data_root / "daily_bar" / "symbol=X.SZ" /
                           "year=2024.snappy.abcd1234.tmp", 30)
    old_hidden = _make_tmp(data_root / ".manifest.deadbeef.tmp", 40, size=100)
    fresh = _make_tmp(data_root / "daily_bar" / "symbol=Y.SZ" /
                      "year=2025.snappy.eeee1111.tmp", 0.5)
    not_tmp = data_root / "keep.parquet"
    not_tmp.write_bytes(b"z")
    rwlock = _make_tmp(data_root / ".x.parquet.rwlock", 999, size=3)  # 不在范围

    res = ps.reclaim_stale_tmp_files(max_age_hours=24.0, dry_run=False)

    # 结构化计数 + 内部一致性
    for k in ("scanned", "removed", "kept", "failed", "bytes_freed"):
        assert k in res and isinstance(res[k], int), res
    assert res["removed"] + res["kept"] + res["failed"] == res["scanned"], res
    assert res["failed"] == 0, res
    assert res["removed"] >= 2, res
    assert res["bytes_freed"] >= 110, res

    # 陈旧（含隐藏 + 深层）被删；阈值内 / 非 tmp / rwlock 保留
    assert not old_nested.exists(), "深层陈旧 tmp 未回收"
    assert not old_hidden.exists(), "隐藏 .manifest.*.tmp 未回收"
    assert fresh.exists(), "阈值内的新鲜 tmp 被误删"
    assert not_tmp.exists(), "非 tmp 文件被误删"
    assert rwlock.exists(), ".rwlock 不在回收范围，被误删"
    assert set(res["removed_sample"]) >= {str(old_nested), str(old_hidden)}


def test_dry_run_reports_without_deleting(data_root):
    f = _make_tmp(data_root / "sub" / "a.deadbeef.tmp", 48, size=20)
    res = ps.reclaim_stale_tmp_files(max_age_hours=24.0, dry_run=True)
    assert res["dry_run"] is True
    assert res["removed"] >= 1
    assert res["bytes_freed"] >= 20
    assert f.exists(), "dry_run 不得删除任何文件"
    ps.reclaim_stale_tmp_files(max_age_hours=24.0, dry_run=False)  # 收尾清理


def test_single_file_failure_does_not_abort(data_root, monkeypatch):
    good = _make_tmp(data_root / "a.good1111.tmp", 48, size=5)
    bad = _make_tmp(data_root / "b.bad2222.tmp", 48, size=7)
    real_unlink = Path.unlink

    def fake_unlink(self, *a, **k):  # noqa: ANN001, ANN002, ANN003
        if self.name == "b.bad2222.tmp":
            raise PermissionError(13, "拒绝访问。")
        return real_unlink(self, *a, **k)

    monkeypatch.setattr(Path, "unlink", fake_unlink)
    res = ps.reclaim_stale_tmp_files(max_age_hours=24.0, dry_run=False)

    assert res["failed"] >= 1, res
    assert bad.exists(), "删除失败的文件不应消失"
    assert not good.exists(), "一个文件失败不应中断整批"
    assert any("b.bad2222.tmp" in s["path"] for s in res["failed_sample"])
    # 收尾：撤销 monkeypatch 后清掉残留（tmp_path 亦会被 pytest 自动清理）
    monkeypatch.undo()
    ps.reclaim_stale_tmp_files(max_age_hours=24.0, dry_run=False)
