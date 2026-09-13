"""交易日历纯函数模块（AQP domain，零 IO）。

架构约定（P0 修复后）：
    API / lifespan
        ↓
    app/data/calendar_store.py   （唯一允许触碰 SQLite 的日历数据访问层）
        ↓
    CalendarData（不可变数据快照）
        ↓
    本模块：纯函数日期计算（无任何 import sqlite3 / sqlalchemy / pathlib IO）

所有函数都显式接收 ``CalendarData``，杜绝隐式全局状态与隐藏 IO。
"""
from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass
from datetime import date, datetime, timedelta


@dataclass(frozen=True)
class CalendarData:
    """不可变交易日历快照（由 data 层构建，domain 层只读消费）。"""

    trade_days: frozenset[date]

    def __len__(self) -> int:
        return len(self.trade_days)


def build_calendar(days: Iterable[date]) -> CalendarData:
    """从交易日集合构建不可变快照（纯函数）。"""
    return CalendarData(trade_days=frozenset(days))


_EMPTY = CalendarData(trade_days=frozenset())


def is_trade_day(d: date, cal: CalendarData = _EMPTY) -> bool:
    """判断 d 是否为交易日（周末提前过滤，集合成员判断兜底）。"""
    if d.weekday() >= 5:
        return False
    return d in cal.trade_days


def prev_trade_day(d: date, cal: CalendarData = _EMPTY) -> date:
    """往前找最近一个交易日，最多找 20 天。"""
    cur = d - timedelta(days=1)
    for _ in range(20):
        if is_trade_day(cur, cal):
            return cur
        cur -= timedelta(days=1)
    raise ValueError(f"无法在 20 天内找到 d={d} 之前的交易日（日历为空或区间不足）")


def next_trade_day(d: date, cal: CalendarData = _EMPTY) -> date:
    """往后找最近一个交易日，最多找 20 天。"""
    cur = d + timedelta(days=1)
    for _ in range(20):
        if is_trade_day(cur, cal):
            return cur
        cur += timedelta(days=1)
    raise ValueError(f"无法在 20 天内找到 d={d} 之后的交易日（日历为空或区间不足）")


# 收盘缓冲：A 股 15:00 收盘，数据源日线的当日 K 线通常 15:30 后稳定
MARKET_CLOSE_CUTOFF = (15, 30)


def last_completed_trade_day(cal: CalendarData = _EMPTY,
                             now: datetime | None = None) -> date:
    """最近一个**已收盘**的交易日（同步/例行任务取目标交易日用）。

    - 今天为交易日且已过收盘缓冲（15:30）→ 返回今天；
    - 否则（盘前/盘中/非交易日）→ 返回上一个交易日。

    ⚠️ 盘中**绝不能**返回今天：日线接口的"今天"是盘中快照，一旦同步入库，
    次日 ``last >= target`` 会判定该标的已最新，残缺数据将被永久保留。

    ⚠️ 与 :func:`prev_trade_day` 的区别：后者恒定返回今天之前的交易日，
    用它做同步目标日会造成"当天行情永远要等次日同步"的一天滞后。
    """
    now = now or datetime.now()
    today = now.date()
    hh, mm = MARKET_CLOSE_CUTOFF
    if is_trade_day(today, cal) and (now.hour, now.minute) >= (hh, mm):
        return today
    return prev_trade_day(today, cal)


def range_trade_days(start: date, end: date, cal: CalendarData = _EMPTY) -> list[date]:
    """闭区间 [start, end] 内的交易日列表，升序。"""
    out: list[date] = []
    cur = start
    while cur <= end:
        if is_trade_day(cur, cal):
            out.append(cur)
        cur += timedelta(days=1)
    return out
