"""P1（2026-10-01）：`_sectors_from_local` 的统计日**不得**被单只标的的离群行劫持。

## 缺陷本体（实测复现）

`_sectors_from_local` 原实现用 `last_day = df["date"].max()` 选统计日 —— 与
`_heat_from_local` 的缺陷 A **完全同型**（同一天被独立发现两次）。

实测覆盖度（`daily_bar` 2026 分区）：

| 日期 | 有数据的标的数 |
|------|--------------|
| 2026-09-29 | **1** |
| 2026-09-28 | 25 |
| 2026-09-18 | 995 |
| **2026-09-17** | **2492**（真实全市场） |

⇒ 接口实际返回：

```
status=ok  items 数 = 1
{'name': '酒店旅游', 'pct': 1.56, 'leader': '全新好', 'leader_pct': None, 'count': 1}
note = 实时板块源不可用，按本地样本 1 只聚合
```

一个**只有 1 项**的"热门板块榜"没有排序意义 —— 而且它来自**单只股票**。

## 修复判据

与 `_heat_from_local` **共用** `_pick_stat_day`（单一事实来源），并如实披露
`data_date`/`coverage_symbols`/`latest_date`。修复后：**items 12 项 / 覆盖 2492 只**。

## 本文件断言什么

1. 离群标的**不能**改变统计日，聚合样本不得退化成 1；
2. 稀疏尾巴被跳过时**必须披露**（`data_date` ≠ `latest_date` + note 含"已跳过"）；
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

# 固定"今天"，让 `year={当前年}` 的 glob 不随真实日历漂移。
FIXED_TODAY = date(2026, 6, 2)


@pytest.fixture()
def data_root(tmp_path, monkeypatch):
    monkeypatch.setattr(get_settings(), "DATA_ROOT", tmp_path)
    # `_sectors_from_local` 只读 `year={today.year}` 的分区 ⇒ 必须固定"今天"，
    # 否则测试会在跨年后静默变成"无数据"而不再是"断言失败"。
    monkeypatch.setattr(market_api, "today_trade_date_or_last", lambda: FIXED_TODAY)
    return tmp_path


def _write_bars(root: Path, symbol: str, days: list[date], closes: list[float]) -> None:
    d = root / "daily_bar" / f"symbol={symbol}"
    d.mkdir(parents=True, exist_ok=True)
    pl.DataFrame({"symbol": [symbol] * len(days), "date": days, "close": closes}) \
        .write_parquet(d / f"year={days[0].year}.snappy.parquet")


def _seed_normal(root: Path, n: int) -> None:
    """n 只标的，最后交易日 2026-06-01（前一日 05-29 供算涨跌幅）。"""
    for i in range(n):
        _write_bars(root, f"0000{i:02d}.SZ",
                    [date(2026, 5, 29), date(2026, 6, 1)], [10.0, 10.2])


def test_sectors_stat_day_not_hijacked_by_outlier(data_root):
    """离群标的多一行 06-02 ⇒ 统计日仍须是 06-01，聚合样本不得退化成 1。"""
    _seed_normal(data_root, 20)
    _write_bars(data_root, "000007.SZ",
                [date(2026, 5, 29), date(2026, 6, 1), date(2026, 6, 2)],
                [10.0, 10.2, 10.5])

    out = market_api._sectors_from_local()

    assert out["status"] == "ok", out
    assert out["coverage_symbols"] == 20, (
        f"聚合样本应为 20 只，实为 {out['coverage_symbols']} —— "
        "统计日疑似被 000007.SZ 的离群行劫持"
    )
    assert out["data_date"] == "2026-06-01", out
    assert out["latest_date"] == "2026-06-02", out
    assert sum(i["count"] for i in out["items"]) == 20, out["items"]
    assert "已跳过" in out["note"], out["note"]


def test_sectors_discloses_skip(data_root):
    """稀疏尾巴被跳过时，note 必须如实说明（不得静默）。"""
    _seed_normal(data_root, 12)
    _write_bars(data_root, "000007.SZ",
                [date(2026, 5, 29), date(2026, 6, 1), date(2026, 6, 2)],
                [10.0, 10.2, 10.5])

    out = market_api._sectors_from_local()
    assert "2026-06-01" in out["note"]
    assert "12 只标的" in out["note"], out["note"]
    assert "2026-06-02" in out["note"], out["note"]


def test_sectors_normal_case_no_skip_note(data_root):
    """反向断言：覆盖均匀时取最新日，且**不**出现"已跳过"（防过度修正）。"""
    _seed_normal(data_root, 15)

    out = market_api._sectors_from_local()

    assert out["data_date"] == "2026-06-01", out
    assert out["latest_date"] == "2026-06-01", out
    assert out["coverage_symbols"] == 15, out
    assert "已跳过" not in out["note"], out["note"]
