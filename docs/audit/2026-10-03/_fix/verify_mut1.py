# -*- coding: utf-8 -*-
"""变异/行为辅助探针（Task B）——修复 1：auto_sync_scheduler 交易日门禁。

不依赖 pytest / tmp_path，直接 import-and-call。只读运行，不写生产数据。
2026-10-03 为周六（非交易日）⇒ 有门禁时不得触发 auto sync。
"""
from __future__ import annotations

import asyncio
import datetime as _dt
import sys

sys.path.insert(0, ".")

import app.services.sync_service as ss  # noqa: E402
from app.data.calendar_store import get_calendar  # noqa: E402
from app.domain.calendar import is_trade_day  # noqa: E402

calls = {"bg": 0, "record": 0}


async def main() -> None:
    ss._load_auto_sync = lambda: {"enabled": True, "time": "00:00"}  # type: ignore[assignment]
    ss._auto_sync_today_done = lambda: False  # type: ignore[assignment]

    def _rec() -> None:
        calls["record"] += 1

    def _bg(mode, resume=False):  # noqa: ANN001
        calls["bg"] += 1

    ss._record_auto_sync = _rec  # type: ignore[assignment]
    ss._start_sync_bg = _bg  # type: ignore[assignment]
    ss._sync.running = False

    async def _to_thread(fn, *a, **k):  # noqa: ANN001
        return fn(*a, **k)

    async def _sleep(_):  # noqa: ANN001
        raise asyncio.CancelledError()

    ss.asyncio.to_thread = _to_thread  # type: ignore[assignment]
    ss.asyncio.sleep = _sleep  # type: ignore[assignment]

    today = _dt.date.today()
    trade = is_trade_day(today, get_calendar())
    try:
        await ss.auto_sync_scheduler()
    except asyncio.CancelledError:
        pass
    print(f"today={today} is_trade_day={trade} "
          f"start_sync_bg_calls={calls['bg']} record_auto_sync={calls['record']}")


if __name__ == "__main__":
    asyncio.run(main())
