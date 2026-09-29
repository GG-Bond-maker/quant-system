"""交易日历数据访问层（AQP data）。

架构边界（P0 修复）：本模块是【唯一】允许为日历触碰 SQLite 的地方；
domain/calendar.py 只消费本模块产出的不可变 ``CalendarData`` 快照。

- load_from_db_sync()      ：sqlite3 直连（脚本 / 同步 fallback）；
- load_from_db()           ：SQLAlchemy 异步（FastAPI lifespan）；
- get_calendar()           ：进程级缓存读取（为空时同步加载兜底）；
- refresh_calendar_cache() ：lifespan 启动时预加载；
- set_calendar()           ：测试注入。
"""
from __future__ import annotations

import sqlite3
from datetime import date

from loguru import logger

from ..core.config import get_settings
from ..domain.calendar import CalendarData, build_calendar

_CACHE: CalendarData | None = None


def _rows_to_calendar(rows: list[date]) -> CalendarData:
    return build_calendar(rows)


def load_from_db_sync() -> CalendarData:
    """同步从 SQLite 加载交易日历（sqlite3 直连，任何上下文可安全调用）。"""
    s = get_settings()
    db_path = s.SQLITE_PATH
    if not db_path.exists():
        logger.warning(f"SQLite 文件不存在: {db_path}，交易日历加载跳过")
        return build_calendar([])
    try:
        conn = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True, timeout=30)
        rows = conn.execute("SELECT trade_date FROM trade_calendar").fetchall()
        conn.close()
        cal = _rows_to_calendar([date.fromisoformat(r[0]) for r in rows])
        logger.info(f"交易日历同步加载完成: {len(cal)} 个交易日")
        return cal
    except Exception as e:
        logger.warning(f"交易日历同步加载失败: {e}")
        return build_calendar([])


async def load_from_db() -> CalendarData:
    """异步从 SQLite 加载交易日历（SQLAlchemy，FastAPI 场景）。"""
    from sqlalchemy import select

    from ..db.models import TradeCalendar
    from ..db.session import get_session_factory

    factory = get_session_factory()
    async with factory() as sess:
        res = await sess.scalars(select(TradeCalendar.trade_date))
        cal = _rows_to_calendar(list(res.all()))
    logger.info(f"交易日历异步加载完成: {len(cal)} 个交易日")
    return cal


def get_calendar() -> CalendarData:
    """读取进程级缓存；为空时同步兜底加载。"""
    global _CACHE
    if _CACHE is None:
        _CACHE = load_from_db_sync()
    return _CACHE


async def refresh_calendar_cache() -> CalendarData:
    """lifespan 启动钩子调用：异步加载并刷新进程级缓存。"""
    global _CACHE
    _CACHE = await load_from_db()
    return _CACHE


def set_calendar(cal: CalendarData) -> None:
    """测试/脚本注入用。"""
    global _CACHE
    _CACHE = cal
