# -*- coding: utf-8 -*-
"""变异反证辅助探针（Task B）——不依赖 pytest / tmp_path，直接 import-and-call。

用途：对第 2b 条（evening_routine.startup_catchup 交易日门禁）在**无测试覆盖**时，
仍能用行为差异证明"修复真实生效 / 缺陷形态确实不同"。本脚本只读运行，不写任何生产数据。
"""
from __future__ import annotations

import asyncio
import datetime as _dt
import sys

sys.path.insert(0, ".")

import app.jobs.evening_routine as er  # noqa: E402
import app.api.v1.report as report_mod  # noqa: E402
import app.ml.monitor as monitor  # noqa: E402
from app.data.calendar_store import get_calendar  # noqa: E402
from app.domain.calendar import is_trade_day  # noqa: E402

calls = {"gen": 0}


async def main() -> None:
    # 跳过 monitor 分支（返回非 None 快照）
    monitor.get_health_snapshot = lambda: {"ok": True}  # type: ignore[assignment]
    # 让日报"缺失"，从而进入日报补跑分支
    report_mod.get_latest_report = lambda: None  # type: ignore[assignment]

    def _gen() -> dict:
        calls["gen"] += 1
        return {}

    report_mod.generate_and_store_report = _gen  # type: ignore[assignment]

    # asyncio.to_thread 同步执行，避免线程与事件循环的时序噪音
    async def _to_thread(fn, *a, **k):  # noqa: ANN001
        return fn(*a, **k)

    er.asyncio.to_thread = _to_thread  # type: ignore[assignment]

    today = _dt.date.today()
    trade = is_trade_day(today, get_calendar())
    await er.startup_catchup()
    print(f"today={today} is_trade_day={trade} generate_calls={calls['gen']}")


if __name__ == "__main__":
    asyncio.run(main())
