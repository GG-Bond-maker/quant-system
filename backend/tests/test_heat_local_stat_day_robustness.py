"""P2（2026-10-01）：`_heat_from_local` 的统计日**不得**被单只标的的离群行劫持。

## 缺陷本体（实测复现）

`_heat_from_local` 原实现用 `last_day = df["date"].max()` 选统计日。该判据会被
**单只标的的离群行**劫持：

- 实证数据：2499 只标的中 2492 只最后交易日为 `2026-09-17`
  （当日成交额 **15770.6 亿** = 真实全市场量级）；
- 但 `000007.SZ` 多出一行 `2026-09-29` ⇒ `max()` 取到 09-29；
- 结果 `last` 只剩 **1 只标的** ⇒ 接口报：
  - `total_amount_yi = 0.8`（**误差约 2 万倍**）
  - `up = 1, down = 0`（"红盘 1 / 绿盘 0" 冒充全市场涨跌分布）

这是项目红线禁止的「**退化样本冒充全市场**」。它尤其危险，因为
**0.8 亿是一个"真实但退化"的和**，不会被任何"0 冒充不可得"类守卫拦住
（既不是 0、也不是 null，量纲也合法）。

## 修复判据

统计日 = 「**最近且覆盖充分**」的交易日：
阈值取近 `_COVER_LOOKBACK` 个交易日**最大覆盖数的一半**（相对判据，
不硬编码标的池规模）。被跳过的稀疏日期经 `note`/`coverage_symbols`/`data_date` 如实披露。

## 本文件断言什么

1. 离群标的**不能**改变统计日（核心回归）；
2. 稀疏尾巴被跳过时**必须披露**（不得静默）；
3. 无异常时行为不变（取最新日、无"已跳过"字样）—— 反向断言，防过度修正。
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


def _write_year_partition(root: Path, symbol: str, rows: list[tuple[date, float, float]]) -> None:
    """把 (date, close, amount) 列表写成 `daily_bar/symbol=*/year=2026.snappy.parquet`。"""
    d = root / "daily_bar" / f"symbol={symbol}"
    d.mkdir(parents=True, exist_ok=True)
    pl.DataFrame(
        {
            "symbol": [symbol] * len(rows),
            "date": [r[0] for r in rows],
            "close": [r[1] for r in rows],
            "amount": [r[2] for r in rows],
        },
        schema={"symbol": pl.String, "date": pl.Date, "close": pl.Float64, "amount": pl.Float64},
    ).write_parquet(d / "year=2026.snappy.parquet")


def _seed_uniform(root: Path, n: int, days: list[date], amount: float = 1e8) -> None:
    for i in range(n):
        _write_year_partition(
            root, f"60{i:04d}.SH",
            [(d, 10.0 + j * 0.1, amount) for j, d in enumerate(days)],
        )


@pytest.fixture
def data_root(tmp_path, monkeypatch) -> Path:
    """把 `DATA_ROOT` 重定向到**本测试私有**目录。

    ⚠️ 必须做：conftest 的隔离是**会话级**的（同一个临时 DATA_ROOT 给所有测试用），
    若直接往里写 parquet，前一个测试写的分区会残留到后一个测试，
    导致 `coverage` 累加（实测 15 变成 17、5 变成 12）——测试间互相污染。
    """
    monkeypatch.setattr(get_settings(), "DATA_ROOT", tmp_path)
    return tmp_path


# ---------------- 1. 核心回归：离群标的不得劫持统计日 ----------------

def test_outlier_symbol_does_not_hijack_stat_day(data_root: Path):
    """**缺陷本体**：单只标的的离群行不得把统计日拉到稀疏的一天。"""
    normal_days = [date(2026, 9, 10), date(2026, 9, 11)]
    _seed_uniform(data_root, 20, normal_days, amount=1e8)
    # 离群：只在 2026-09-20 有一行（模拟源侧单只标的更新超前）
    _write_year_partition(data_root, "000007.SZ", [(date(2026, 9, 20), 5.0, 1e6)])

    out = market_api._heat_from_local(date(2026, 9, 11))

    assert out["status"] == "ok", out
    # 统计日必须是覆盖充分的那天，而不是离群标的所在的 09-20
    assert out["data_date"] == "2026-09-11", f"统计日被离群标的劫持：{out}"
    assert out["coverage_symbols"] == 20, f"覆盖标的数应为 20，实为 {out['coverage_symbols']}"
    # 涨跌家数必须是 20 只的量级，而不是 1 只
    assert out["up"] + out["down"] + out["flat"] == 20, out
    # 成交额必须是 20 只的合计（20 亿），而不是离群那只的 0.01 亿
    assert out["total_amount_yi"] == pytest.approx(20.0), out


def test_sparse_tail_is_skipped_and_disclosed(data_root: Path):
    """尾部稀疏（补数未完成）时必须回退到完整日，且**如实披露**跳过事实。"""
    _seed_uniform(data_root, 20, [date(2026, 9, 10), date(2026, 9, 11)])
    # 稀疏尾巴：仅 2 只标的更新到 09-12（低于 50% 覆盖阈值）
    for i in range(2):
        _write_year_partition(
            data_root, f"30{i:04d}.SZ",
            [(date(2026, 9, 10), 8.0, 1e7), (date(2026, 9, 11), 8.1, 1e7),
             (date(2026, 9, 12), 8.2, 1e7)],
        )

    out = market_api._heat_from_local(date(2026, 9, 12))

    assert out["data_date"] == "2026-09-11", out
    assert out["latest_date"] == "2026-09-12", "必须报出被跳过的最新日期"
    assert "已跳过" in out["note"], f"跳过事实必须披露，实为：{out['note']}"
    assert "2026-09-12" in out["note"], out["note"]


# ---------------- 2. 反向断言：无异常时行为不变 ----------------

def test_normal_case_picks_latest_day_without_skip_note(data_root: Path):
    """所有标的覆盖一致时应取最新日，且**不得**出现"已跳过"字样（防过度修正）。"""
    days = [date(2026, 9, 10), date(2026, 9, 11), date(2026, 9, 12)]
    _seed_uniform(data_root, 15, days, amount=2e8)

    out = market_api._heat_from_local(date(2026, 9, 12))

    assert out["data_date"] == "2026-09-12", out
    assert out["coverage_symbols"] == 15, out
    assert "已跳过" not in out["note"], out["note"]
    assert out["total_amount_yi"] == pytest.approx(30.0), out  # 15 只 × 2 亿


def test_single_day_data_does_not_crash(data_root: Path):
    """仅一天数据（无前值 ⇒ pct 全空）应走 unavailable，而不是报 ok+全 0 桶。"""
    _seed_uniform(data_root, 5, [date(2026, 9, 11)])

    out = market_api._heat_from_local(date(2026, 9, 11))

    assert out["status"] == "unavailable", out
    assert "reason" in out


def test_all_null_amount_reports_none_not_zero(data_root: Path):
    """成交额整列为 null ⇒ 必须回 `None`（前端渲染「—」），不得 0 冒充。"""
    days = [date(2026, 9, 10), date(2026, 9, 11)]
    for i in range(6):
        d = data_root / "daily_bar" / f"symbol=70{i:04d}.SH"
        d.mkdir(parents=True, exist_ok=True)
        pl.DataFrame(
            {
                "symbol": [f"70{i:04d}.SH"] * 2,
                "date": days,
                "close": [10.0, 10.2],
                "amount": [None, None],
            },
            schema={
                "symbol": pl.String, "date": pl.Date,
                "close": pl.Float64, "amount": pl.Float64,
            },
        ).write_parquet(d / "year=2026.snappy.parquet")

    out = market_api._heat_from_local(date(2026, 9, 11))

    assert out["status"] == "ok", out
    assert out["total_amount_yi"] is None, f"全 null 成交额必须回 None，实为 {out['total_amount_yi']!r}"
