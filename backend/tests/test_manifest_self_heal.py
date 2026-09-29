"""缺陷 C（P0）：parquet manifest 一致性自愈。

背景：``DATA_ROOT/.manifest.json`` 只在写路径 ``_manifest_record`` 增量登记，
凡绕过 ``write_partition`` 的旁路写入（离线重建 / 扩容 / repair 脚本）都不感知，
且此前**仅在 dataset 键缺失时**才全量重扫 —— 一旦键存在就永不修复。实测生产
``daily_bar_hfq`` 磁盘有 2500 只（目录全部 rows>0），manifest 只登记 1729 只，
于是 ``read_all_symbols`` 恒返 1729，771 只真实标的被静默排除在特征构建之外
（铁证：features 09-11 行数 = 1729，与 manifest 计数吻合）。

本测试固化修复后的自愈契约：
1. manifest 条目 < 磁盘 symbol= 目录 → 强制重扫一次、回写 manifest、并留 WARNING；
2. **每个 dataset 每进程至多重扫一次** ← ⚠️ 这条原契约已被 2026-09-21
   审计 P1-41 **推翻**：一次性预算导致"首次旁路写自愈后，之后新增的 symbol
   永久静默缺失且不再 WARNING"。现改为**增长驱动 + 30s 节流**：
   目录数比上次核对时增长 ⇒ 再重扫；目录数不变而仍 `N_man<N_dir`
   （合法空目录 rows=0）⇒ 不重扫（防刷屏 / 防逐只扩容时反复全扫 footer）。
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
    monkeypatch.setattr(ps, "_audited", {})
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
    """⚠️ 原契约已被 P1-41 推翻，此处保留为**反向回归**：新增目录必须再自愈。

    原始断言是"再新增一只磁盘目录…不应再次重扫（`_audited` 预算）"，
    而那正是审计认定的缺陷（后续新增 symbol 永久静默缺失）。现在断言相反行为：
    目录数增长 ⇒ 重扫；`manifest_invalidate()` 仍是权威重置手段。
    """
    root = _isolate(monkeypatch, tmp_path)
    monkeypatch.setattr(ps, "_AUDIT_MIN_INTERVAL", 0.0)
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

    # 再新增一只磁盘目录：目录数增长 ⇒ P1-41 要求**再次**自愈（原契约在此处是错的）
    _write_symbol(root, "daily_bar", "300003.SZ", 1)
    got = ps.list_symbols_with_data("daily_bar")
    assert got == {"600001.SH", "000002.SZ", "300003.SZ"}, (
        "新增旁路写未被自愈 ⇒ P1-41 回归")
    assert calls["n"] == 2

    # 显式失效后账本重置，可再次权威全扫
    ps.manifest_invalidate()
    ps.list_symbols_with_data("daily_bar")
    assert calls["n"] == 3


def test_new_bypass_write_after_audit_is_still_healed(tmp_path, monkeypatch):
    """P1-41：**首次自愈之后**的旁路写必须仍能被自愈（原实现永久静默缺失）。

    原行为（缺陷）：`_audited` 是 set，首次不一致后记入集合 ⇒ 再新增的磁盘目录
    永不重扫、永不 WARNING，标的被静默排除在标的池之外。
    新行为：目录数相对上次核对**增长**即再重扫（受 30s 节流，测试里把节流清零）。
    """
    root = _isolate(monkeypatch, tmp_path)
    fake = _FakeLogger()
    monkeypatch.setattr(ps, "logger", fake)
    monkeypatch.setattr(ps, "_AUDIT_MIN_INTERVAL", 0.0)  # 关掉节流，专注语义

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
    assert calls["n"] == 1

    # 第一次自愈之后，又出现一只旁路写入 —— 必须再次自愈，而不是永久缺失
    _write_symbol(root, "daily_bar", "300003.SZ", 1)
    got = ps.list_symbols_with_data("daily_bar")
    assert got == {"600001.SH", "000002.SZ", "300003.SZ"}, (
        f"首次自愈后的新增旁路写未被修复 —— 这正是 P1-41；实际返回 {got}")
    assert calls["n"] == 2, "目录数增长应触发第二次重扫"
    assert sum("强制重扫" in w for w in fake.warnings) >= 2, "第二次自愈也必须留痕"

    # 目录数不再增长（无新旁路写）⇒ 不得反复重扫（防刷屏/防开销）
    ps.list_symbols_with_data("daily_bar")
    assert calls["n"] == 2, "目录数未增长时不应重复重扫"


def test_empty_dirs_do_not_cause_repeat_rescan(tmp_path, monkeypatch):
    """合法空目录（rows=0）造成的 N_man<N_dir 不得让重扫反复触发。"""
    root = _isolate(monkeypatch, tmp_path)
    monkeypatch.setattr(ps, "_AUDIT_MIN_INTERVAL", 0.0)
    _write_symbol(root, "daily_bar", "600001.SH", 3)
    (root / "daily_bar" / "symbol=000002.SZ").mkdir(parents=True)  # 空目录
    (root / ".manifest.json").write_text(
        json.dumps({"daily_bar": {"600001.SH": {"rows": 3}}}), encoding="utf-8")

    calls = {"n": 0}
    orig_scan = ps._manifest_scan_dataset

    def counting_scan(dataset: str) -> dict[str, dict]:
        calls["n"] += 1
        return orig_scan(dataset)

    monkeypatch.setattr(ps, "_manifest_scan_dataset", counting_scan)

    for _ in range(5):
        assert ps.list_symbols_with_data("daily_bar") == {"600001.SH"}
    assert calls["n"] == 1, f"空目录导致重复重扫（calls={calls['n']}）"


def test_audit_throttle_defers_but_does_not_cancel_heal(tmp_path, monkeypatch):
    """30s 节流只**推迟**自愈：窗口过后仍会补上（不得退回"永久静默"）。"""
    root = _isolate(monkeypatch, tmp_path)
    _write_symbol(root, "daily_bar", "600001.SH", 3)
    _write_symbol(root, "daily_bar", "000002.SZ", 2)
    (root / ".manifest.json").write_text(
        json.dumps({"daily_bar": {"600001.SH": {"rows": 3}}}), encoding="utf-8")

    # 第一次不一致：正常自愈，同时把"已核对目录数=2"记入账本
    assert ps.list_symbols_with_data("daily_bar") == {"600001.SH", "000002.SZ"}

    # 之后又出现一只旁路目录，但节流窗口未过 ⇒ 本次先跳过（推迟，不是取消）
    _write_symbol(root, "daily_bar", "300003.SZ", 1)
    monkeypatch.setattr(ps, "_AUDIT_MIN_INTERVAL", 10_000.0)
    assert ps.list_symbols_with_data("daily_bar") == {"600001.SH", "000002.SZ"}, (
        "窗口内应被节流")

    # 关掉窗口（等价于时间流逝）⇒ 增长判据仍在（账本 2 < 磁盘 3），自愈补上
    monkeypatch.setattr(ps, "_AUDIT_MIN_INTERVAL", 0.0)
    got = ps.list_symbols_with_data("daily_bar")
    assert got == {"600001.SH", "000002.SZ", "300003.SZ"}, (
        "节流不得变成永久静默（P1-41 回归）")


def test_empty_symbol_dir_excluded_by_skip_empty(tmp_path, monkeypatch):
    """空目录（rows=0）不得被 skip_empty=True 返回；skip_empty=False 才可见。"""
    root = _isolate(monkeypatch, tmp_path)
    _write_symbol(root, "daily_bar", "600001.SH", 3)
    (root / "daily_bar" / "symbol=000002.SZ").mkdir(parents=True)  # 空目录

    assert ps.list_symbols_with_data("daily_bar") == {"600001.SH"}
    assert ps.list_symbols_with_data("daily_bar", skip_empty=False) == {
        "600001.SH", "000002.SZ"}
