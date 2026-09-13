"""Task 10（整改计划 A-P1-5a）：流水线产物原子写。

背景：features/predictions/screener/text 因子等写点直接 to_parquet，进程
中断会留下截断文件；经 read_parquet_columns 路径消费时坏文件被静默跳过
→ 数据静默缩水（审核报告 P1-5）。
"""
from __future__ import annotations

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
