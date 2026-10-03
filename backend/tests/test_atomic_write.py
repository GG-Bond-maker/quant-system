"""Task 10（整改计划 A-P1-5a）：流水线产物原子写。

背景：features/predictions/screener/text 因子等写点直接 to_parquet，进程
中断会留下截断文件；经 read_parquet_columns 路径消费时坏文件被静默跳过
→ 数据静默缩水（审核报告 P1-5）。
"""
from __future__ import annotations

import os
import sys
from pathlib import Path

BACKEND_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(BACKEND_ROOT))

import pandas as pd  # noqa: E402
import polars as pl  # noqa: E402
import pytest  # noqa: E402

from app.data.parquet_store import atomic_write_parquet  # noqa: E402


def test_atomic_write_leaves_no_partial_file_on_crash(tmp_path, monkeypatch):
    """写入中途崩溃：目标路径无半写文件、tmp 清理干净。"""
    target = tmp_path / "year=2024.parquet"
    df = pl.DataFrame({"a": [1, 2]})

    def boom(*a, **k):
        raise OSError("disk full")

    monkeypatch.setattr(pl.DataFrame, "write_parquet", boom)
    with pytest.raises(OSError):
        atomic_write_parquet(target, df)
    assert not target.exists()
    assert list(tmp_path.glob("*.tmp")) == []


def test_atomic_write_replaces_existing(tmp_path):
    """正常路径：新写覆盖旧文件，内容完整可读。"""
    target = tmp_path / "year=2024.parquet"
    atomic_write_parquet(target, pl.DataFrame({"a": [1]}))
    atomic_write_parquet(target, pl.DataFrame({"a": [1, 2, 3]}))
    assert pl.read_parquet(target)["a"].to_list() == [1, 2, 3]


def test_atomic_write_accepts_pandas(tmp_path):
    """pandas DataFrame 输入：自动转换（流水线写点多为 pandas）。"""
    target = tmp_path / "out.parquet"
    atomic_write_parquet(target, pd.DataFrame({"a": [1, 2]}))
    assert pl.read_parquet(target).height == 2


def test_no_bypass_parquet_writes():
    """守卫：app/ 与 scripts/ 中禁止裸 to_parquet/write_parquet 旁路写。

    所有 parquet 落盘必须经 parquet_store（原子写 + 统一压缩）。
    """
    allowed = {"parquet_store.py"}
    bad: list[str] = []
    for base in ("app", "scripts"):
        for py in (BACKEND_ROOT / base).rglob("*.py"):
            if py.name in allowed:
                continue
            text = py.read_text(encoding="utf-8")
            for lineno, line in enumerate(text.splitlines(), 1):
                if (".to_parquet(" in line or ".write_parquet(" in line) \
                        and not line.lstrip().startswith("#"):
                    bad.append(f"{py.relative_to(BACKEND_ROOT)}:{lineno}")
    assert bad == [], f"发现旁路 parquet 写点（应走 atomic_write_parquet）: {bad}"


# --------------------------------------------------------------------------- #
# Windows 瞬时文件锁重试（2026-10-02 事故）
#
# 事故：`--steps rebuild` 的 `rebuild_qfq` 在第 657/5220 只标的抛
# `PermissionError(13, '拒绝访问。')` ⇒ 步骤 FAILED + `break` ⇒ **其后 5 个步骤
# 全部未执行**（含 build_cs_mirror）。受控复现：事后对同一只标的单独重算**成功**
# ⇒ 该错误是**瞬时**的（杀软/索引器短暂持有目标句柄）。
#
# 两条判据缺一不可：
#   ① 瞬时失败必须被重试吸收（否则单只抖动中止整条链）；
#   ② 持久失败必须**原样抛出**（否则真权限问题被吞成静默跳过 —— 比崩溃更糟）。
# --------------------------------------------------------------------------- #
def _patch_replace(monkeypatch, outcomes: list):
    """把 ``os.replace`` 换成一串预设结局（``None``=成功，异常实例=抛出）。

    ⚠️ 成功分支**必须调用真实现**：``ps.os`` 就是全局 ``os`` 模块，
    只 return None 会让目标文件根本不存在（首版即踩此坑，报 FileNotFoundError）。
    """
    import app.data.parquet_store as ps

    real_replace = os.replace
    calls = {"n": 0}

    def fake(src, dst):
        i = calls["n"]
        calls["n"] += 1
        outcome = outcomes[i] if i < len(outcomes) else None
        if outcome is not None:
            raise outcome
        return real_replace(src, dst)

    monkeypatch.setattr(ps.os, "replace", fake)
    return calls


def test_transient_permission_error_is_retried(tmp_path, monkeypatch):
    """① 瞬时 ``PermissionError`` 必须被重试吸收，写入最终成功且内容完整。"""
    target = tmp_path / "year=2024.parquet"
    calls = _patch_replace(monkeypatch, [
        PermissionError(13, "拒绝访问。"),
        PermissionError(13, "拒绝访问。"),
        None,                                  # 第三次成功
    ])
    atomic_write_parquet(target, pl.DataFrame({"a": [1, 2, 3]}))
    assert calls["n"] == 3, f"应重试到第 3 次，实际 {calls['n']} 次"
    assert pl.read_parquet(target)["a"].to_list() == [1, 2, 3]
    assert list(tmp_path.glob("*.tmp")) == []


def test_persistent_permission_error_is_not_swallowed(tmp_path, monkeypatch):
    """② 重试耗尽后必须**原样抛出** —— 绝不静默跳过（红线：不得用跳过冒充成功）。"""
    target = tmp_path / "year=2024.parquet"
    from app.data.parquet_store import _REPLACE_RETRIES

    calls = _patch_replace(monkeypatch, [PermissionError(13, "拒绝访问。")] * 99)
    with pytest.raises(PermissionError):
        atomic_write_parquet(target, pl.DataFrame({"a": [1]}))
    assert calls["n"] == _REPLACE_RETRIES, \
        f"应恰好重试 {_REPLACE_RETRIES} 次，实际 {calls['n']} 次"
    assert not target.exists(), "失败后不得留下目标文件"
    assert list(tmp_path.glob("*.tmp")) == [], "失败后必须清理 tmp"
