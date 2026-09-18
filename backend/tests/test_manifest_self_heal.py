"""缺陷 C（P0）：parquet manifest 一致性自愈。

背景：``DATA_ROOT/.manifest.json`` 只在写路径 ``_manifest_record`` 增量登记，
凡绕过 ``write_partition`` 的旁路写入（离线重建 / 扩容 / repair 脚本）都不感知，
且此前**仅在 dataset 键缺失时**才全量重扫 —— 一旦键存在就永不修复。实测生产
``daily_bar_hfq`` 磁盘有 2500 只（目录全部 rows>0），manifest 只登记 1729 只，
于是 ``read_all_symbols`` 恒返 1729，771 只真实标的被静默排除在特征构建之外
（铁证：features 09-11 行数 = 1729，与 manifest 计数吻合）。

本测试固化修复后的自愈契约：
1. manifest 条目 < 磁盘 symbol= 目录 → 强制重扫一次、回写 manifest、并留 WARNING；
2. 每个 dataset 每进程至多重扫一次（``_audited`` 预算），不刷屏；
3. ``skip_empty=True`` 仍过滤 rows=0 的空目录。

隔离：把 ``DATA_ROOT`` monkeypatch 到独立 tmp_path，并复位模块级 manifest 缓存
与 ``_audited``；绝不触碰真实 ``data/``。
"""
from __future__ import annotations

import json
import sys
from pathlib import Path
from types import SimpleNamespace

BACKEND_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(BACKEND_ROOT))

import polars as pl  # noqa: E402

import app.data.parquet_store as ps  # noqa: E402


class _FakeLogger:
    """最小 logger 替身：只记录 warning 文案，用于断言「留痕」。"""

    def __init__(self) -> None:
        self.warnings: list[str] = []

    def warning(self, message: str, *args: object, **kwargs: object) -> None:
        self.warnings.append(str(message))

    def info(self, *args: object, **kwargs: object) -> None:  # pragma: no cover
        pass

    def debug(self, *args: object, **kwargs: object) -> None:  # pragma: no cover
        pass


def _write_symbol(root: Path, dataset: str, symbol: str, rows: int) -> None:
    """在磁盘上造一个 symbol 目录（rows 行为空目录时传 0）。"""
    d = root / dataset / f"symbol={symbol}"
    d.mkdir(parents=True, exist_ok=True)
    if rows == 0:
        return
    pl.DataFrame({
        "date": [f"2026-09-{i + 1:02d}" for i in range(rows)],
        "close": [1.0] * rows,
    }).write_parquet(d / "year=2026.snappy.parquet")


def _isolate(monkeypatch, tmp_path: Path) -> Path:
    """把 DATA_ROOT 指向独立临时目录并复位模块级缓存，返回 DATA_ROOT。"""
    root = tmp_path / "parquet"
    root.mkdir(parents=True, exist_ok=True)
    monkeypatch.setattr(ps, "get_settings", lambda: SimpleNamespace(DATA_ROOT=root))
    monkeypatch.setattr(ps, "_manifest", None)
    monkeypatch.setattr(ps, "_audited", set())
    return root


def test_manifest_self_heals_when_disk_has_more_symbols(tmp_path, monkeypatch):
    """manifest 少登记 → 强制重扫 + 回写 + WARNING 留痕（不得静默）。"""
    root = _isolate(monkeypatch, tmp_path)
    fake = _FakeLogger()
    monkeypatch.setattr(ps, "logger", fake)

    _write_symbol(root, "daily_bar", "600001.SH", 3)
    _write_symbol(root, "daily_bar", "000002.SZ", 2)  # 磁盘有数据但未登记
    (root / ".manifest.json").write_text(
        json.dumps({"daily_bar": {"600001.SH": {"rows": 3}}}), encoding="utf-8")

    got = ps.list_symbols_with_data("daily_bar")

    assert got == {"600001.SH", "000002.SZ"}, f"自愈失败，仍返回 {got}"
    # manifest 文件被回写补全
    on_disk = json.loads((root / ".manifest.json").read_text(encoding="utf-8"))
    assert set(on_disk["daily_bar"]) == {"600001.SH", "000002.SZ"}
    # 留痕：WARNING 被记录，杜绝静默降级
    assert any("强制重扫" in w for w in fake.warnings), "缺少自愈 WARNING 留痕"


def test_manifest_rescans_at_most_once_per_dataset(tmp_path, monkeypatch):
    """每 dataset 每进程至多重扫一次；显式失效后预算重置可再自愈。"""
    root = _isolate(monkeypatch, tmp_path)
    _write_symbol(root, "daily_bar", "600001.SH", 3)
    _write_symbol(root, "daily_bar", "000002.SZ", 2)
    (root / ".manifest.json").write_text(
        json.dumps({"daily_bar": {"600001.SH": {"rows": 3}}}), encoding="utf-8")

    calls = {"n": 0}
    orig_scan = ps._manifest_scan_dataset

    def counting_scan(dataset: str) -> dict[str, dict]:
        calls["n"] += 1
        return orig_scan(dataset)

    monkeypatch.setattr(ps, "_manifest_scan_dataset", counting_scan)

    assert ps.list_symbols_with_data("daily_bar") == {"600001.SH", "000002.SZ"}
    assert calls["n"] == 1, "首次不一致应触发一次重扫"

    # 再新增一只磁盘目录：本进程该 dataset 已审计过，不应再次重扫
    _write_symbol(root, "daily_bar", "300003.SZ", 1)
    ps.list_symbols_with_data("daily_bar")
    assert calls["n"] == 1, "同一 dataset 每进程应至多重扫一次（_audited 预算）"

    # 显式失效（旁路写后的标准动作）后预算重置，可再次权威全扫
    ps.manifest_invalidate()
    got = ps.list_symbols_with_data("daily_bar")
    assert got == {"600001.SH", "000002.SZ", "300003.SZ"}
    assert calls["n"] == 2


def test_empty_symbol_dir_excluded_by_skip_empty(tmp_path, monkeypatch):
    """空目录（rows=0）不得被 skip_empty=True 返回；skip_empty=False 才可见。"""
    root = _isolate(monkeypatch, tmp_path)
    _write_symbol(root, "daily_bar", "600001.SH", 3)
    (root / "daily_bar" / "symbol=000002.SZ").mkdir(parents=True)  # 空目录

    assert ps.list_symbols_with_data("daily_bar") == {"600001.SH"}
    assert ps.list_symbols_with_data("daily_bar", skip_empty=False) == {
        "600001.SH", "000002.SZ"}
