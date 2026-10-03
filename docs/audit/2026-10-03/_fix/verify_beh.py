"""独立行为验证（Phase Verification）—— 只读，不修改生产源码。

覆盖：修复 2（report 交易日门禁 / startup_catchup）、修复 4（
last_completed_trade_date_or_last）、修复 5（sync partial_fail 终态）、
修复 6（validate all_empty 告警）、修复 3（etf diff 数值正确）。

输出全部打到 stdout，由调用方重定向到日志文件（本会话 Bash stdout 捕获不可用）。
"""
from __future__ import annotations

import json
import sys
from contextlib import contextmanager
from datetime import date, datetime

sys.path.insert(0, "backend")

RESULTS: list[tuple[str, bool, str]] = []


def check(name: str, cond: bool, detail: str = "") -> None:
    RESULTS.append((name, bool(cond), detail))
    print(f"[{'PASS' if cond else 'FAIL'}] {name}  :: {detail}")


@contextmanager
def capture_loguru(level: str = "WARNING"):
    from loguru import logger
    msgs: list[str] = []
    sid = logger.add(lambda m: msgs.append(str(m)), level=level)
    try:
        yield msgs
    finally:
        logger.remove(sid)


print("=" * 90)
print("#2a report.build_daily_report 交易日门禁")
print("=" * 90)
import app.api.v1.report as report  # noqa: E402

try:
    r_live = report.build_daily_report()
    print("  live build_daily_report()['date'] =", r_live["date"], "(今天=2026-10-03 周六)")
except Exception as e:  # noqa: BLE001
    print("  live call raised:", type(e).__name__, e)

# 冻结 now=2026-09-27 20:00（周日）→ 期望"最近已收盘交易日"
import app.domain.calendar as domcal  # noqa: E402
_orig_dt = domcal.datetime


class _FrozenDT(datetime):
    @classmethod
    def now(cls, tz=None):
        return cls(2026, 9, 27, 20, 0, 0)


domcal.datetime = _FrozenDT
try:
    r_frozen = report.build_daily_report()
    got = r_frozen["date"]
finally:
    domcal.datetime = _orig_dt
print("  frozen now=2026-09-27(周日) 20:00 -> report['date'] =", got)
check("2a frozen(09-27) 返回 09-24（09-25 为中秋假期，非交易日）",
      got == "2026-09-24", f"got={got}")
check("2a frozen(09-27) == 任务书预期的 2026-09-25", got == "2026-09-25",
      f"got={got}（若 FAIL 说明任务书预期与交易日历不符）")

# 空日历 → 必须退回 date.today() 且打 WARNING，不得抛异常
import app.data.calendar_store as calstore  # noqa: E402
from app.domain.calendar import build_calendar  # noqa: E402
_orig_gc = calstore.get_calendar
calstore.get_calendar = lambda: build_calendar([])  # 空日历
try:
    with capture_loguru() as logs:
        try:
            r_empty = report.build_daily_report()
            raised = None
        except Exception as e:  # noqa: BLE001
            r_empty, raised = None, e
finally:
    calstore.get_calendar = _orig_gc
print("  空日历: raised =", raised)
print("  空日历: report['date'] =", (r_empty or {}).get("date"), "| today =", date.today().isoformat())
hit_warn = any("交易日历不可用" in m for m in logs)
print("  空日历 WARNING 命中 =", hit_warn, "| 样例:", [m.strip() for m in logs][:2])
check("2a 空日历不抛异常", raised is None, f"raised={raised!r}")
check("2a 空日历退回 date.today()", (r_empty or {}).get("date") == date.today().isoformat(),
      f"date={(r_empty or {}).get('date')}")
check("2a 空日历打 WARNING", hit_warn, f"logs={len(logs)}")

print()
print("=" * 90)
print("#4 parquet_store.last_completed_trade_date_or_last")
print("=" * 90)
import app.data.parquet_store as ps  # noqa: E402
from app.domain.calendar import last_completed_trade_day  # noqa: E402
from app.data.calendar_store import get_calendar  # noqa: E402

_cal = get_calendar()
print("  日历交易日数 =", len(_cal))
# 底层纯函数（确定性，带 now 参数）
for now, exp in [(datetime(2026, 9, 30, 10, 0), "2026-09-29"),
                 (datetime(2026, 9, 30, 16, 0), "2026-09-30"),
                 (datetime(2026, 9, 27, 16, 0), "2026-09-24")]:
    got = last_completed_trade_day(_cal, now=now).isoformat()
    check(f"4 last_completed_trade_day(now={now:%Y-%m-%d %H:%M})", got == exp,
          f"got={got} exp={exp}")

# wrapper：冻结 domain.calendar.datetime 控制"现在"
for now, exp in [(datetime(2026, 9, 30, 10, 0), "2026-09-29"),   # 交易日盘中
                 (datetime(2026, 9, 30, 16, 0), "2026-09-30")]:  # 交易日收盘后
    class _FDT(datetime):
        @classmethod
        def now(cls, tz=None):
            return now
    domcal.datetime = _FDT
    try:
        got = ps.last_completed_trade_date_or_last().isoformat()
    finally:
        domcal.datetime = _orig_dt
    check(f"4 wrapper(now={now:%Y-%m-%d %H:%M})", got == exp, f"got={got} exp={exp}")

# 空日历降级
calstore.get_calendar = lambda: build_calendar([])
try:
    with capture_loguru() as logs:
        got = ps.last_completed_trade_date_or_last().isoformat()
finally:
    calstore.get_calendar = _orig_gc
exp_fb = ps._weekend_rewind(date.today()).isoformat()
hit = any("交易日历为空" in m for m in logs)
print(f"  空日历 wrapper -> {got}（_weekend_rewind(today)={exp_fb}）WARNING={hit}")
check("4 空日历降级到 _weekend_rewind 且打 WARNING", got == exp_fb and hit,
      f"got={got} exp={exp_fb} warn={hit}")

print()
print("=" * 90)
print("#5 _sync_worker partial_fail 终态判定（边界两侧）")
print("=" * 90)
import app.services.sync_service as ss  # noqa: E402
from app.core.pipeline_lock import pipeline_slot  # noqa: E402
from contextlib import nullcontext  # noqa: E402

_ss_pipeline_slot = ss.pipeline_slot if hasattr(ss, "pipeline_slot") else None


def run_worker(n_total: int, n_failed: int, done: int | None = None) -> dict:
    recorded: dict = {}
    import app.core.pipeline_lock as plock  # noqa: E402
    _orig_slot = plock.pipeline_slot
    plock.pipeline_slot = lambda name, **k: nullcontext()
    ss._record_sync_job = lambda mode, status, dur, err=None: recorded.update(
        mode=mode, status=status, err=err)
    ss._persist_sync_state = lambda *a, **k: None
    s = ss._sync
    s.completed.clear(); s.failed.clear(); s.logs.clear(); s.cancel_event.clear()
    s.error = None; s.task_id = None; s.rows_written = 0
    symbols = [f"6000{i:02d}.SZ" for i in range(n_total)]
    s.total = n_total
    s.done = 0

    def runner(syms, resume=False):
        with ss._sync.lock:
            s.done = (done if done is not None else n_total)
            s.failed = set(syms[:n_failed])
            s.completed = set(syms[n_failed:])
            s.rows_written = 5
    ss._run_incremental = lambda syms, resume=False: runner(syms)
    try:
        ss._sync_worker("incremental", symbols, resume=False)
    finally:
        plock.pipeline_slot = _orig_slot
    return recorded


r_50 = run_worker(10, 5)   # 5/10 = 50% (边界)
r_60 = run_worker(10, 6)   # 60%
r_40 = run_worker(10, 4)   # 40%
r_10 = run_worker(10, 1)   # 10%
print("  50%  ->", r_50.get("status"), "| err:", r_50.get("err"))
print("  60%  ->", r_60.get("status"), "| err:", r_60.get("err"))
print("  40%  ->", r_40.get("status"), "| err:", r_40.get("err"))
print("  10%  ->", r_10.get("status"))
check("5 failed/total=50%(边界) ⇒ FAILED", r_50.get("status") == "FAILED", r_50.get("status"))
check("5 50% 的 err 含 PARTIAL", "PARTIAL" in (r_50.get("err") or ""), r_50.get("err"))
check("5 failed/total=60% ⇒ FAILED", r_60.get("status") == "FAILED", r_60.get("status"))
check("5 failed/total=40%(<0.5) ⇒ SUCCESS", r_40.get("status") == "SUCCESS", r_40.get("status"))
check("5 failed/total=10% ⇒ SUCCESS", r_10.get("status") == "SUCCESS", r_10.get("status"))

print()
print("=" * 90)
print("#6 step_validate all_empty 告警（>500 与 <500 规模）")
print("=" * 90)
from app.data.pipeline import step_validate  # noqa: E402
from app.data.parquet_store import read_all_symbols  # noqa: E402
from app.domain.a_share_rules import normalize_code  # noqa: E402

full = list(dict.fromkeys(normalize_code(c) for c in read_all_symbols("daily_bar")))
print("  全市场 codes =", len(full))

# 用 2026-10-09（交易日，prev=10-08，两者均无数据）构造"整日全灭"
for label, codes in [(">500 (600只)", full[:600]), ("<500 (100只)", full[:100])]:
    with capture_loguru() as logs:
        try:
            ret = step_validate(date(2026, 10, 9), codes)
            raised = None
        except Exception as e:  # noqa: BLE001
            ret, raised = None, e
    hit = any("全部无数据" in m for m in logs)
    print(f"  [{label}] ret={ret} raised={type(raised).__name__ if raised else None} warn={hit}")
    check(f"6 {label} 触发 all_empty WARNING", hit, f"warn={hit}")
    if len(codes) < 500:
        check(f"6 {label} 返回串含 all_empty=1", "all_empty=1" in (ret or ""), ret)
    else:
        check(f"6 {label} 市场硬门禁 raise", raised is not None and "全市场覆盖率异常" in str(raised),
              str(raised)[:60])

# 正常日 all_empty=0
with capture_loguru() as logs:
    ret_norm = step_validate(date(2026, 9, 30), full[:100])
print("  正常日(09-30) 返回串 =", ret_norm)
check("6 正常日 all_empty=0", "all_empty=0" in ret_norm, ret_norm)

print()
print("=" * 90)
print("#3 _build_tracking_block diff 数值正确性（手算校验）")
print("=" * 90)
import app.api.v1.etf as apietf  # noqa: E402
import app.data.etf as dataetf  # noqa: E402

# 构造：ETF 累计 0.5%->1.0%（对应 close 100.5->101.0，base=100），指数持平
_dates = [f"2026-06-{d:02d}" for d in range(1, 22)]  # 21 天
etf_bars = [{"date": _dates[0], "close": 100.0}]
for i in range(1, 20):
    etf_bars.append({"date": _dates[i], "close": 100.4})
etf_bars.append({"date": _dates[19], "close": 100.5})  # 累计 +0.5%
etf_bars.append({"date": _dates[20], "close": 101.0})  # 累计 +1.0%
idx_bars = [{"date": d, "close": 200.0} for d in _dates]

dataetf.resolve_tracking_index = lambda name, ti: ("sh", "000300")
dataetf.fetch_kline = lambda market, code, limit=252: etf_bars
dataetf.fetch_index_kline = lambda market, code, limit=252: idx_bars
dataetf._market_for_code = lambda country, code: "sh"

res = apietf._build_tracking_block("510300", "cn", {"name": "x", "tracking_index": "000300"})
pts = res.get("points") or []
print("  status =", res.get("status"), "| n_points =", len(pts))
print("  first point =", pts[0] if pts else None)
print("  last  point =", pts[-1] if pts else None)
check("3 points 含 diff 字段", bool(pts) and "diff" in pts[0], str(pts[:1]))
check("3 首点 diff 为 None", bool(pts) and pts[0].get("diff") is None, str(pts[:1]))
if pts:
    last = pts[-1].get("diff")
    # 手算：(101.0/100.5-1) - (200/200-1) = 0.004975 → 0.4975
    check("3 末点 diff ≈ +0.4975%（非 +100%）", last is not None and abs(last - 0.4975) < 1e-6,
          f"got={last}")

# 旧前端公式对照（应明显不同，证明修复方向）
old = (1.0 / 0.5 - 1) * 100
print(f"  旧前端公式 (1.0/0.5-1)*100 = {old}  ← 错误值（应为日收益差 {last}）")

print()
print("=" * 90)
print("汇总")
print("=" * 90)
n_pass = sum(1 for _, ok, _ in RESULTS if ok)
print(f"PASS {n_pass}/{len(RESULTS)}")
for name, ok, detail in RESULTS:
    if not ok:
        print(f"  FAIL: {name} :: {detail}")
