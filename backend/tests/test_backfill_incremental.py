"""`scripts/backfill_incremental.py` 的**范围判定**契约（2026-10-02 增量补齐）。

只测 ``build_plan()`` —— 它是整条补齐链路的**唯一分叉点**：判错就会要么漏补
（缺口静默固化），要么重复抓（白耗 1.2s/次 的全局限速预算）。

🔴 核心判据是「**起点取 raw/hfq 两侧较早者**」。写这条测试的直接动因是实测
发现 ``300061.SZ`` 出现 ``raw=2026-09-30 / hfq=2026-09-18`` 的**单侧到位**状态：
若只看 ``daily_bar`` 就会把它判成"已最新"并永久跳过，而 ``daily_bar_hfq`` 是
标签/特征/评估的唯一价格基准（``_LABEL_PRICE_BASIS="hfq"``）。
"""
from __future__ import annotations

import sys
from datetime import date
from pathlib import Path

import pytest

BACKEND_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(BACKEND_ROOT / "scripts"))

import backfill_incremental as bf  # noqa: E402
from app.domain.calendar import build_calendar  # noqa: E402

TARGET = date(2026, 9, 30)

# 含两个缺口起点所需的前一交易日；9/19-9/20 是周末（故意不进日历）。
# ⚠️ 必须含一个**目标日之后**的交易日（10-08）：否则 `next_trade_day(target)`
# 会在 20 天窗口内找不到而抛 ValueError，让"区间倒置保护"那条用例
# 走成降级路径、**测不到真正要测的守卫**。
CAL = build_calendar([
    date(2026, 9, 17), date(2026, 9, 18),
    date(2026, 9, 21), date(2026, 9, 22), date(2026, 9, 23),
    date(2026, 9, 24), date(2026, 9, 25),
    date(2026, 9, 28), date(2026, 9, 29), date(2026, 9, 30),
    date(2026, 10, 8), date(2026, 10, 9),
])


@pytest.fixture()
def stub(monkeypatch):
    """把「读磁盘最后交易日」换成查表；未登记的 symbol 视为库内无数据。"""
    table: dict[tuple[str, str], date | None] = {}

    def _fake(dataset: str, sym: str, ref_year: int) -> date | None:
        return table.get((dataset, sym))

    monkeypatch.setattr(bf, "_symbol_last_date_in", _fake)
    return table


def _plan(symbols: list[str]) -> dict:
    return bf.build_plan(symbols, TARGET, CAL)


def test_both_sides_current_is_up_to_date(stub):
    """两侧都达目标日 ⇒ 跳过（幂等空跑，不发任何请求）。"""
    stub[("daily_bar", "600519.SH")] = TARGET
    stub[("daily_bar_hfq", "600519.SH")] = TARGET
    p = _plan(["600519.SH"])
    assert p["up_to_date"] == ["600519.SH"]
    assert p["pending"] == []


def test_raw_current_but_hfq_behind_is_pending_from_hfq(stub):
    """🔴 核心回归：raw 已到目标日、hfq 落后 ⇒ **必须**待补，且起点按 hfq 算。

    这正是 ``300061.SZ`` 的实测形态。若起点误按 raw 算，会得到
    ``next_trade_day(2026-09-30)`` = 未来日 ⇒ 区间倒置、必然空返回，
    且缺口被"抓过了"的名义掩盖。
    """
    stub[("daily_bar", "300061.SZ")] = TARGET
    stub[("daily_bar_hfq", "300061.SZ")] = date(2026, 9, 18)
    p = _plan(["300061.SZ"])
    assert p["up_to_date"] == []
    assert len(p["pending"]) == 1
    sym, last, last_hfq, start = p["pending"][0]
    assert (sym, last, last_hfq) == ("300061.SZ", TARGET, date(2026, 9, 18))
    assert start == date(2026, 9, 21), "起点应为 hfq 末日的次一交易日"


def test_hfq_current_but_raw_behind_symmetric(stub):
    """反方向同样成立（起点按 raw 算）——证明取的是 min 而非"固定优先某一侧"。"""
    stub[("daily_bar", "000001.SZ")] = date(2026, 9, 18)
    stub[("daily_bar_hfq", "000001.SZ")] = TARGET
    p = _plan(["000001.SZ"])
    assert p["up_to_date"] == []
    assert p["pending"][0][3] == date(2026, 9, 21)


def test_both_behind_uses_earlier_side(stub):
    """两侧都落后 ⇒ 起点按**较早者**算，一次抓齐缺口。"""
    stub[("daily_bar", "000002.SZ")] = date(2026, 9, 18)
    stub[("daily_bar_hfq", "000002.SZ")] = date(2026, 9, 21)
    p = _plan(["000002.SZ"])
    assert p["pending"][0][3] == date(2026, 9, 21), "应取 09-18 的次一交易日"


def test_no_data_at_all_is_not_pending(stub):
    """两侧全无 ⇒ 归 no_data，**不**进 pending。

    这类标的通常是新股未上市 / 已退市；把它们塞进 pending 会让每轮补齐都
    空跑一遍外呼，且永远"补不上"，污染失败率统计。
    """
    p = _plan(["999999.SZ"])
    assert p["no_data"] == ["999999.SZ"]
    assert p["pending"] == [] and p["up_to_date"] == []


def test_hfq_missing_is_tracked_but_still_pending(stub):
    """单侧完全无数据（hfq 缺失）⇒ 记入 hfq_missing **且**仍进 pending。

    必须同时满足：① 不能因为"有一侧没数据"就跳过（那会把缺口固化）；
    ② 要单独统计，便于运维区分"数据源不提供该口径"与"抓取失败"。
    """
    stub[("daily_bar", "000003.SZ")] = date(2026, 9, 18)
    stub[("daily_bar_hfq", "000003.SZ")] = None
    p = _plan(["000003.SZ"])
    assert p["hfq_missing"] == ["000003.SZ"]
    assert len(p["pending"]) == 1
    assert p["pending"][0][3] == date(2026, 9, 21)


def test_start_never_exceeds_target(stub):
    """区间倒置保护：起点**绝不**超过目标日（否则请求必然空返回）。

    构造：一侧恰好停在目标日、另一侧完全无数据 ⇒ ``min`` 取到目标日本身
    ⇒ ``next_trade_day(target)`` 落在目标日**之后**。若不守卫，就会发出
    ``beg > end`` 的区间请求（源端必然空返回，且缺口被"抓过了"的名义掩盖）。
    """
    stub[("daily_bar", "000004.SZ")] = TARGET
    stub[("daily_bar_hfq", "000004.SZ")] = None
    p = _plan(["000004.SZ"])
    assert len(p["pending"]) == 1
    assert p["pending"][0][3] == TARGET, "应退化为目标日单日，而非目标日之后"
    # 反证：若不守卫，起点会是 2026-10-08
    assert bf.next_trade_day(TARGET, CAL) == date(2026, 10, 8)


def test_calendar_exhausted_falls_back_to_target(stub, monkeypatch):
    """日历数据不足（``next_trade_day`` 抛 ValueError）⇒ 退化为目标日单日。

    降级路径也必须有覆盖 —— 本项目判据「有降级分支 ≠ 降级分支能跑」：
    无覆盖的分支会积累死代码级 bug。
    """
    stub[("daily_bar", "000005.SZ")] = date(2026, 9, 18)
    stub[("daily_bar_hfq", "000005.SZ")] = date(2026, 9, 18)

    def _boom(ref, cal):
        raise ValueError("日历数据不足")

    monkeypatch.setattr(bf, "next_trade_day", _boom)
    p = _plan(["000005.SZ"])
    assert p["pending"][0][3] == TARGET


def test_start_hist_counts_by_start_date(stub):
    """``start_hist`` 按起点日期分桶（dry-run 报告靠它披露范围）。"""
    for i, d in enumerate([date(2026, 9, 18), date(2026, 9, 18), date(2026, 9, 21)]):
        sym = f"00000{i}.SZ"
        stub[("daily_bar", sym)] = d
        stub[("daily_bar_hfq", sym)] = d
    p = _plan(["000000.SZ", "000001.SZ", "000002.SZ"])
    assert p["start_hist"]["2026-09-21"] == 2
    assert p["start_hist"]["2026-09-22"] == 1
