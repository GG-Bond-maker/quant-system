"""临时验证脚本（修复 2）：证明 AI 日报默认日取「最近已收盘交易日」而非自然日。

验收口径（**已按 QA 复核更正**）：在 2026-09-27（周日）调用 build_daily_report()
（day=None）时，rep["date"] 必须为 **2026-09-24**（真实交易日历给出的上一交易日），
不得为 2026-09-27。

为什么是 09-24 而不是 09-25（首发验收值曾写错）：
    真实 ``trade_calendar`` 中 **2026-09-25（周五）不是交易日**（2026 中秋假期），
    09-26(六)/09-27(日) 亦非交易日 ⇒ 09-27 之前的最近交易日只能是 **09-24**。
    ⚠️ 本脚本**不再自建"把 09-25 当交易日"的假日历**（那是自证式验收，会掩盖真实
    行为），改为直接读取**真实日历** ``get_calendar()``。

运行：cd backend && .venv/Scripts/python.exe ../docs/audit/2026-10-03/_fix/verify_report_date.py
"""
from __future__ import annotations

import os
import sys
import tempfile
from datetime import date, datetime as _dt

# 让 `app` 可导入（脚本放在 docs 下，需指向 backend）
_HERE = os.path.dirname(os.path.abspath(__file__))
_BACKEND = os.path.abspath(os.path.join(_HERE, "..", "..", "..", "..", "backend"))
sys.path.insert(0, _BACKEND)

# 只把行情数据根指向空目录（避免读取真实 parquet 造成偶发耗时）；
# ⚠️ 不触碰 SQLITE_URL —— 交易日历必须来自**真实** trade_calendar 表。
os.environ["DATA_ROOT"] = tempfile.mkdtemp(prefix="aqp_verify_")

import app.domain.calendar as calmod  # noqa: E402
import app.data.calendar_store as calstore  # noqa: E402
from app.domain.calendar import build_calendar, last_completed_trade_day  # noqa: E402

# 0) 读取**真实**交易日历（来自 data/sqlite/aqp.db 的 trade_calendar 表）
_REAL_CAL = calstore.get_calendar()
print(f"[cal]    真实日历交易日数 = {len(_REAL_CAL)}")
assert len(_REAL_CAL) > 0, "真实日历为空 —— 无法验证（检查 SQLITE_URL / trade_calendar）"
# 佐证：09-25 不在日历中（2026 中秋假期），这正是"09-27 ⇒ 09-24"而非 09-25 的原因
_in_25 = date(2026, 9, 25) in _REAL_CAL.trade_days
print(f"[cal]    2026-09-25 in cal = {_in_25}（应为 False）")
assert _in_25 is False, "2026-09-25 不应是交易日（若为 True，说明日历/日期有误）"
for _d in (date(2026, 9, 24), date(2026, 9, 28), date(2026, 9, 30), date(2026, 10, 8)):
    assert _d in _REAL_CAL.trade_days, f"真实日历缺少预期交易日 {_d}"
print("[cal]    预期交易日 09-24 / 09-28 / 09-30 / 10-08 均在日历中")


class _FrozenDatetime(_dt):
    """把 domain.calendar 的 datetime.now() 冻结到 2026-09-27 20:00（周日）。"""

    @classmethod
    def now(cls, tz=None):  # noqa: ANN001
        return _dt(2026, 9, 27, 20, 0, 0)


calmod.datetime = _FrozenDatetime  # type: ignore[assignment]

# 1) 直接验证纯函数：周日晚 ⇒ 上一交易日 09-24（而非自然日 09-27，也非 09-25）
_got = last_completed_trade_day(_REAL_CAL)
print(f"[direct] last_completed_trade_day(real_cal, 2026-09-27 20:00) = {_got}")
assert _got == date(2026, 9, 24), f"期望 2026-09-24，实际 {_got}"

# 2) 端到端：让 build_daily_report(day=None) 走默认日路径（内部用真实 get_calendar）
import app.api.v1.report as rep  # noqa: E402

_r = rep.build_daily_report()
print(f"[e2e]    build_daily_report()['date'] = {_r['date']}")
assert _r["date"] == "2026-09-24", (
    f"期望 2026-09-24，实际 {_r['date']}（不得为 2026-09-27，也不得为 09-25）")

# 3) 兜底路径：日历为空时退回 date.today()（不得抛 500）
#    ⚠️ 这里注入的是**空日历**（documented 降级路径），不是"假日历"；
#    验证完立即还原真实 get_calendar。
_real_get = calstore.get_calendar
try:
    calstore.get_calendar = lambda: build_calendar([])  # type: ignore[assignment]
    _r2 = rep.build_daily_report()
    print(f"[fallback] 空日历 ⇒ build_daily_report()['date'] = {_r2['date']}")
    assert _r2["date"] == date.today().isoformat(), (
        "空日历兜底应退回自然日 date.today()")
finally:
    calstore.get_calendar = _real_get  # type: ignore[assignment]

print("\nALL CHECKS PASSED: 日报默认日 = 最近已收盘交易日（2026-09-27 周日 ⇒ 2026-09-24）")
