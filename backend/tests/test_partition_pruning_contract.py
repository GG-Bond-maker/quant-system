"""分区裁剪契约测试（G-4，2026-10-02）。

## 背景

审计 **G-4**：「批量读分区前按业务日期跨度裁剪（无条件 glob = 10 倍成本）；
⚠️ **只裁起点安全**，**终点必须留足前瞻窗口**，否则末日样本被截成 null、**静默丢样本**。」

**实测定位**：`/research/lab/yearly`（`ml_lab_yearly`）原先无条件 glob
`daily_bar/symbol=*/year=*.parquet` —— 全部年份 × 全部标的，而下游只用到与 predictions
日期相交的那些行。同仓另两处都是对的（`market.py:199` 按年裁剪、
`market.py:624-628` 带 `horizon*3+10` 前瞻窗口裁剪）⇒ 它是**唯一的漏网者**。

## 为什么这样测

🔴 裁剪是**纯优化**，必须能证明"裁前/裁后语义一致"。但该端点有 `n < 500` 的最小样本门槛，
小夹具无法端到端跑出输出 ⇒ 改为**把裁剪逻辑抽成纯函数**（`_bar_year_window` /
`_prune_bar_files`，只依赖日期与文件名，不碰文件系统）并直接单测，
**重点覆盖年末边界** —— 那正是"终点不留前瞻窗口"会**静默丢样本**的位置。
"""
from __future__ import annotations

from datetime import date
from pathlib import Path

from app.api.v1.research import (
    _BAR_FORWARD_WINDOW_DAYS,
    _bar_year_window,
    _prune_bar_files,
)


def _f(symbol: str, year: int) -> Path:
    return Path(f"daily_bar/symbol={symbol}/year={year}.snappy.parquet")


class TestBarYearWindow:
    def test_常规区间同年(self):
        assert _bar_year_window(date(2022, 3, 1), date(2022, 9, 30)) == (2022, 2022)

    def test_跨年区间(self):
        assert _bar_year_window(date(2022, 11, 1), date(2023, 2, 1)) == (2022, 2023)

    def test_年末边界必须外扩到次年(self):
        """🔴 `pred_max = 12-30` 时 `shift(-1)` 需要次年首个交易日 ⇒ `y_to` 必须是次年。

        若把终点卡在 `pred_max` 当年，该年最后一个交易日的样本会因 `ret=null`
        被下游 `drop_nulls("ret")` **静默丢弃**（不报错，结果偏小）。
        """
        y_from, y_to = _bar_year_window(date(2022, 1, 4), date(2022, 12, 30))
        assert y_from == 2022
        assert y_to == 2023, "年末样本会静默丢失（前瞻窗口未外扩）"

    def test_前瞻窗口天数可调(self):
        d = date(2022, 12, 30)
        assert _bar_year_window(d, d, fwd_days=0)[1] == 2022
        assert _bar_year_window(d, d, fwd_days=1)[1] == 2022   # 12-31 仍在当年
        assert _bar_year_window(d, d, fwd_days=2)[1] == 2023   # 跨到次年
        assert _BAR_FORWARD_WINDOW_DAYS > 1, "前瞻窗口必须 > 1 天（要覆盖下一个交易日）"


class TestPruneBarFiles:
    FILES = [_f("600000.SH", y) for y in (2020, 2021, 2022, 2023, 2024)]

    def test_闭区间包含两端(self):
        kept = {f.name for f in _prune_bar_files(self.FILES, 2021, 2023)}
        assert kept == {f"year={y}.snappy.parquet" for y in (2021, 2022, 2023)}

    def test_单年(self):
        kept = _prune_bar_files(self.FILES, 2022, 2022)
        assert len(kept) == 1
        assert kept[0].name == "year=2022.snappy.parquet"

    def test_对照_裁剪确实减少了分区数(self):
        """与"不裁剪"对照 —— 证明裁剪真的省了分区（而非空转）。"""
        pruned = _prune_bar_files(self.FILES, 2022, 2023)
        assert len(pruned) < len(self.FILES)
        assert len(self.FILES) - len(pruned) == 3      # 2020 / 2021 / 2024 被裁掉

    def test_无命中返回空(self):
        assert _prune_bar_files(self.FILES, 2010, 2015) == []

    def test_文件名解析对真实glob结果稳健(self):
        """`glob('year=*.parquet')` 会命中 `year=2022.snappy.parquet`，年份取前 4 位。"""
        f = Path("daily_bar/symbol=000001.SZ/year=2022.snappy.parquet")
        assert _prune_bar_files([f], 2022, 2022) == [f]
        assert _prune_bar_files([f], 2023, 2023) == []
