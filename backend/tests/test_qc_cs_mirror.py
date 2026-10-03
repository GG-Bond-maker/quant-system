"""``scripts/quality_check_full_market.py::cs_mirror_survey`` 的可证伪单测。

背景（2026-10-02 实测缺陷）
--------------------------
``app.data.cross_section.mirror_status()`` 的 ``lag_days`` **只比日期集合**，
⇒ 「镜像文件**存在**、但只有 1 行」的**残缺截面**被判为「已覆盖」而**完全不报**。
实测 ``cs/daily_bar/date=20260928.parquet`` 只有 **1 行**（源同日 5206 只）、
``cs/daily_bar_hfq`` **2 行** ⇒ ``/screener/stocks`` 读到的截面近乎为空。

本测试锁定新判据的**灵敏度**与**特异性**：
- 灵敏度：残缺截面（行数远低于中位数）**必须**被计入 ``truncated_dates``；
- 特异性：全部健康的镜像**必须**报 0（不得误报，否则门禁变成噪音源）。
"""
from __future__ import annotations

import importlib.util
import os
import sys
from pathlib import Path

import polars as pl
import pytest

BACKEND_ROOT = Path(__file__).resolve().parents[1]
if str(BACKEND_ROOT) not in sys.path:
    sys.path.insert(0, str(BACKEND_ROOT))

# 🔴 允许被证伪脚本重定向到**副本**（与 test_expand_universe_full_market.py 同法）：
# 对原文件做「注入→跑测试→还原」时，宿主对同一命令的**重复执行**会把缺陷写回生产文件。
_SCRIPT = Path(
    os.environ.get("AQP_QC_SCRIPT")
    or (BACKEND_ROOT / "scripts" / "quality_check_full_market.py")
)


def _load_qc():
    name = "qc_full_market_under_test"
    spec = importlib.util.spec_from_file_location(name, _SCRIPT)
    assert spec is not None and spec.loader is not None
    mod = importlib.util.module_from_spec(spec)
    sys.modules[name] = mod
    spec.loader.exec_module(mod)
    return mod


qc = _load_qc()


def _write_cs(root: Path, ds: str, date_str: str, n_rows: int) -> None:
    d = root / "cs" / ds / f"year={date_str[:4]}"
    d.mkdir(parents=True, exist_ok=True)
    df = pl.DataFrame({"date": [date_str] * n_rows,
                       "symbol": [f"{i:06d}.SZ" for i in range(n_rows)]})
    df.write_parquet(d / f"date={date_str}.parquet")


@pytest.fixture()
def cs_root(tmp_path, monkeypatch):
    """把质检模块的 DATA_ROOT 指向临时目录（``cs_mirror_survey`` 读的是模块级常量）。"""
    monkeypatch.setattr(qc, "DATA_ROOT", tmp_path)
    return tmp_path


def test_truncated_cross_section_is_flagged(cs_root):
    """灵敏度：某日行数远低于中位数 ⇒ 必须进 ``truncated_dates``。

    构造：3 个健康日（各 200 行）+ 1 个残缺日（**1 行**，复刻 20260928 实测）。
    阈值 = ``max(10, 0.5 × 中位数)`` = 100 ⇒ 1 行必须被抓住，200 行不得被误判。
    """
    for d in ("20260924", "20260925", "20260928", "20260929"):
        _write_cs(cs_root, "daily_bar", d, 200)
    _write_cs(cs_root, "daily_bar", "20260930", 1)      # ← 残缺（1 行）

    out = qc.cs_mirror_survey()["daily_bar"]
    assert out["median_rows"] == 200
    assert out["truncated_dates"] == 1, f"残缺日未被抓住：{out}"
    assert out["truncated_sample"] == [("20260930", 1)]
    assert out["last"] == "20260930"


def test_healthy_mirror_is_not_flagged(cs_root):
    """特异性：全部健康 ⇒ ``truncated_dates == 0``（不得误报）。"""
    for d in ("20260924", "20260925", "20260928", "20260929", "20260930"):
        _write_cs(cs_root, "daily_bar", d, 180)
    out = qc.cs_mirror_survey()["daily_bar"]
    assert out["truncated_dates"] == 0, f"健康镜像被误报：{out}"
    assert out["last"] == "20260930"


def test_all_three_calibers_are_surveyed(cs_root):
    """三个口径都要普查（只查一个会重演「判定面窄于不变量」的老坑）。"""
    _write_cs(cs_root, "daily_bar", "20260930", 100)
    _write_cs(cs_root, "daily_bar_hfq", "20260930", 100)
    _write_cs(cs_root, "daily_bar_qfq", "20260930", 100)
    out = qc.cs_mirror_survey()
    assert set(out) == {"daily_bar", "daily_bar_hfq", "daily_bar_qfq"}
    assert all(v["dates"] == 1 for v in out.values())


def test_empty_mirror_does_not_crash(cs_root):
    """镜像不存在时返回全 0 结构（不得抛异常打死整轮质检）。"""
    out = qc.cs_mirror_survey()
    assert out["daily_bar"] == {"dates": 0, "last": None, "median_rows": 0,
                                "truncated_dates": 0, "truncated_sample": [],
                                "read_errors": 0}
