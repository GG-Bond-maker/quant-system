"""P1（2026-10-01）：`_build_ai_stats` 读 hfq 必须**按预测日跨度裁剪**，且裁剪不改变语义。

## 缺陷本体（实测）

`_build_ai_stats` 为评估推荐质量，会 `read_parquet_columns` 读**全部** hfq 分区：

- 实测 **10924 个分区**，跨 **2022~2026**；
- 而 `predictions` 最早为 **2024-06-05** ⇒ **2022+2023 共 3515 个分区（32%）**
  永远 join 不上，纯浪费。

实测耗时：`_build_ai_stats` **7.72s → 2.41s**；`_build_daily` 墙钟 **10.10s → 4.66s**。

## 正确性证据（对照实验，非本文件）

`scripts/dev_verify_ai_stats_year_trim.py` 用文件级 swap 跑「裁剪 vs 全量」两次，
**11 个输出字段完全一致**（`rank_ic=-0.0457` / `top_k_precision=0.6625` / `n_days=4` …）
⇒ 裁剪是**纯优化**。

## 本文件断言什么

1. **只读需要的年份** —— 预测日落在 2025 时，2023/2024 的 hfq 分区**不得**被读；
2. **前瞻窗口不被截断** —— 终点年份必须覆盖 `max(pred.date) + horizon*3+10 天`，
   否则末日样本的 `future_close` 会被截成 null（静默丢样本）；
3. **预测为空时不炸**（早退 unavailable，不抛 `AttributeError`）。
"""

from __future__ import annotations

import sys
from datetime import date
from pathlib import Path

import polars as pl
import pytest

BACKEND_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(BACKEND_ROOT))

from app.api.v1 import market as market_api  # noqa: E402
from app.core.config import get_settings  # noqa: E402


def _write_hfq(root: Path, symbol: str, year: int) -> None:
    d = root / "daily_bar_hfq" / f"symbol={symbol}"
    d.mkdir(parents=True, exist_ok=True)
    pl.DataFrame({
        "symbol": [symbol] * 3,
        "date": [date(year, 6, 1), date(year, 6, 2), date(year, 6, 3)],
        "close": [1.0, 1.1, 1.2],
    }).write_parquet(d / f"year={year}.snappy.parquet")


def _write_pred(root: Path, day: str) -> None:
    d = root / "predictions"
    d.mkdir(parents=True, exist_ok=True)
    pl.DataFrame({
        "date": [date(int(day[:4]), int(day[4:6]), int(day[6:8]))],
        "symbol": ["600519.SH"],
        "pred_score": [0.9],
        "model_version": ["v1"],
        "feature_version": ["f1"],
        "label_horizon": [5],
    }).write_parquet(d / f"date={day}.parquet")


@pytest.fixture()
def data_root(tmp_path, monkeypatch):
    monkeypatch.setattr(get_settings(), "DATA_ROOT", tmp_path)
    return tmp_path


@pytest.fixture()
def captured(monkeypatch):
    """捕获 `read_parquet_columns` 收到的文件列表，并返回空帧让流程早退。"""
    seen: list[list[Path]] = []

    def _fake(files, columns=None, **kw):  # noqa: ANN001, ANN002, ANN003
        seen.append([Path(f) for f in files])
        return pl.DataFrame({"symbol": [], "date": [], "close": []},
                            schema={"symbol": pl.String, "date": pl.Date,
                                    "close": pl.Float64})

    import app.data.parquet_store as ps
    monkeypatch.setattr(ps, "read_parquet_columns", _fake)
    return seen


def _years_seen(seen: list[list[Path]]) -> set[int]:
    out: set[int] = set()
    for batch in seen:
        for f in batch:
            out.add(int(f.name.split("year=")[1][:4]))
    return out


def test_only_needed_years_are_read(data_root, captured):
    """预测日全在 2025 ⇒ 只应读 2025（2023/2024 永远 join 不上）。"""
    for y in (2023, 2024, 2025):
        _write_hfq(data_root, "600519.SH", y)
    _write_pred(data_root, "20250601")

    market_api._build_ai_stats()

    assert captured, "未观察到任何 parquet 读取 —— 裁剪逻辑可能被改动"
    assert _years_seen(captured) == {2025}, (
        f"读了 {_years_seen(captured)}，应只读 {{2025}}"
        f"（预测日跨度只覆盖 2025）"
    )


def test_forward_window_year_is_not_truncated(data_root, captured):
    """预测日在年末 ⇒ 终点年份必须延伸到下一年，否则前瞻窗口被截断。"""
    for y in (2024, 2025, 2026):
        _write_hfq(data_root, "600519.SH", y)
    _write_pred(data_root, "20251220")  # +5*3+10=25 天 ⇒ 跨到 2026

    market_api._build_ai_stats()

    years = _years_seen(captured)
    assert 2025 in years, f"起点年份缺失：{years}"
    assert 2026 in years, (
        f"终点年份被截断（只读了 {years}）⇒ 末日样本的 future_close 会变 null，"
        "静默丢掉最新一段评估样本"
    )
    assert 2024 not in years, f"起点之前的年份不该读：{years}"


def test_empty_predictions_do_not_crash(data_root, captured):
    """predictions 目录为空 ⇒ 早退 unavailable，不得抛异常。"""
    _write_hfq(data_root, "600519.SH", 2025)
    out = market_api._build_ai_stats()
    assert out["status"] == "unavailable"
    assert not captured, "无预测时不该读行情"
