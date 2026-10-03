"""backfill_incremental —— 增量补齐日线行情至「最近一个已完成交易日」。

定位
----
只做一件事的运维入口：把 ``daily_bar``（不复权）与 ``daily_bar_hfq``（后复权）
两个口径的行情，按标的**增量**补齐到 :func:`last_completed_trade_day` 给出的
目标交易日。

它**复用**生产同步路径的既有实现（``fetch_and_write_daily_bars`` ＋
``write_partition`` 的按 date 去重合并 ＋ ``_symbol_last_date_in`` 的跳过判据），
不重新发明抓取逻辑，因此与数据中心「一键同步（incremental）」**同口径**；
差别只是把入口从 HTTP 挪到 CLI，便于无人值守长跑、断点续传与事后审计。

范围（Scope）
-------------
- **标的池**：``read_all_symbols("daily_bar")``（跳过空目录标的）。
- **口径**：raw（``daily_bar``）与 hfq（``daily_bar_hfq``）。
  qfq **不直抓** —— 它锚定最新价，增量写会造成基准漂移；统一由
  ``repair.build_qfq_dataset`` 从本地 raw+hfq 离线推导。
- **单标的区间**：``[next_trade_day(min(last_raw, last_hfq)), target]``。
  两侧都 ``>= target`` 则**跳过**（幂等：零网络请求）。
  取两侧较早者作起点，保证「raw 最新但 hfq 落后」的标的也被一并回补。
- **目标日**：``last_completed_trade_day(get_calendar())`` —— 盘前/盘中/非交易日
  一律回退到上一个交易日，**绝不把盘中快照写进日线**（否则次日
  ``last >= target`` 会判定已最新，残缺数据被永久保留）。

数据来源
--------
``AQP_DAILY_SOURCE_PRIORITY`` 定义的降级链，默认 ``eastmoney,sina,baostock``：

    eastmoney ``stock_zh_a_hist`` → sina ``stock_zh_a_daily`` → baostock

实测（2026-10-02 本机）：东财 ``push2his.eastmoney.com`` **不可达**
（``RemoteProtocolError: Server disconnected``），链路自动降级到新浪，返回的
8 个交易日与本地交易日历**逐日对齐**，且 ``amount`` 非空（腾讯源不提供成交额）。

频率
----
- **调度频率**：生产由 ``services.sync_service.auto_sync_scheduler`` 每 60s 检查
  一次、到 ``data/.auto_sync.json`` 配置的时间点触发一轮 ``incremental``。
  本脚本是**手动/运维**入口，不参与调度。
- **请求频率**：受 ``AKSHARE_RATE_LIMIT``（默认 1.2s ＋ ≤30% 抖动）**全局限速**，
  由 ``akshare_adapter._throttle()`` 的进程级锁保证。因此本脚本刻意**单线程
  串行** —— 限速是全局的，多线程不会提升吞吐，只会放大被源站限流的风险。

去重
----
写入走 ``parquet_store.write_partition``：读该年既有文件 → concat →
``unique(subset=["date"], keep="last")`` → 原子重写（``_rw_lock`` 保护）。
由此得到两条保证：

1. 重复抓取同一区间**幂等**，不会产生重复行；
2. 重跑时以**新抓到的值覆盖**同日旧值（``keep="last"``）。

断点续传
--------
状态落 ``data/.backfill_incremental_state.json``（原子写）：

    {"target": "2026-09-30", "completed": [...], "failed": [...],
     "rows_written": 0, "updated_at": "..."}

- 每处理完一只即尝试更新（写盘节流 5s，硬崩溃最多丢 5s 进度）；
- 重跑时**仅当 ``target`` 相同**才跳过 ``completed`` —— 目标日变了必须重算，
  否则新交易日的行情会被旧续传记录挡住（与 ``_resume_skip_set`` 防的
  「跨口径跳过」是同一类缺陷）；
- ``--no-resume`` 强制忽略续传记录，从头重跑（仍幂等，安全）。

用法（``cd backend`` 后）
-------------------------
    ./.venv/Scripts/python.exe scripts/backfill_incremental.py --dry-run
    ./.venv/Scripts/python.exe scripts/backfill_incremental.py --limit 20
    ./.venv/Scripts/python.exe scripts/backfill_incremental.py
    ./.venv/Scripts/python.exe scripts/backfill_incremental.py --codes 600519,000001
    ./.venv/Scripts/python.exe scripts/backfill_incremental.py --no-resume
"""
from __future__ import annotations

import argparse
import json
import socket
import sys
import time
from collections import Counter
from datetime import date
from pathlib import Path

BACKEND_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(BACKEND_ROOT))

from loguru import logger  # noqa: E402

from app.core.config import get_settings  # noqa: E402
from app.core.logging import setup_logging  # noqa: E402
from app.data.calendar_store import get_calendar  # noqa: E402
from app.data.ingest.akshare_adapter import daily_source_priority  # noqa: E402
from app.data.ingest.tasks import fetch_and_write_daily_bars  # noqa: E402
from app.data.parquet_store import read_all_symbols  # noqa: E402
from app.domain.calendar import last_completed_trade_day, next_trade_day  # noqa: E402
# 复用生产同步的「库内最后交易日」判据（含最新年份分区 + 最多回退 3 年兜底）。
# 刻意 import 同一个函数而非另写一份：跳过判据一旦分叉，CLI 与生产同步就会
# 对「谁该抓」给出不同答案（正是本项目反复踩过的「同型写法分叉」缺陷）。
from app.services.sync_service import _symbol_last_date_in  # noqa: E402

_STATE_NAME = ".backfill_incremental_state.json"
_STATE_FLUSH_MIN_INTERVAL = 5.0


# ---------------------------------------------------------------------------
# 状态文件（断点续传）
# ---------------------------------------------------------------------------
def _state_path() -> Path:
    return get_settings().DATA_ROOT / _STATE_NAME


def _load_state() -> dict:
    p = _state_path()
    if not p.exists():
        return {}
    try:
        data = json.loads(p.read_text(encoding="utf-8"))
        return data if isinstance(data, dict) else {}
    except Exception as e:  # noqa: BLE001 损坏则从头开始（重跑幂等，安全）
        logger.warning(f"[backfill] 续传状态不可读，按空处理: {e!r}")
        return {}


def _save_state(state: dict) -> None:
    p = _state_path()
    try:
        p.parent.mkdir(parents=True, exist_ok=True)
        tmp = p.with_name(p.name + ".tmp")
        tmp.write_text(json.dumps(state, ensure_ascii=False, indent=1),
                       encoding="utf-8")
        tmp.replace(p)
    except Exception as e:  # noqa: BLE001 状态写失败不影响抓取本身
        logger.warning(f"[backfill] 续传状态写盘失败: {e!r}")


# ---------------------------------------------------------------------------
# 范围判定（plan）
# ---------------------------------------------------------------------------
def build_plan(symbols: list[str], target: date, cal,
               *, new_symbol_from: date | None = None) -> dict:
    """逐标的判定「是否需要抓、从哪天抓」。

    返回 ``{"pending": [(sym, last_raw, last_hfq, start)], "up_to_date": [...],
    "no_data": [...], "start_hist": Counter, "hfq_missing": [...]}``。

    ``pending`` 的区间起点取 raw/hfq **两侧较早者**的次一交易日，因此
    「raw 已最新、hfq 落后」的标的不会被误判为已完成（缺口不静默固化）。

    :param new_symbol_from: **库内无数据**标的的全历史抓取起点。
        - ``None``（默认）：库内无数据 ⇒ 归 ``no_data`` 并跳过。
          这是「补齐已有池的尾部缺口」语义 —— 全无数据的标的通常是停牌/退市/
          源不覆盖，每轮重试都是白耗外呼预算。
        - 给定日期（如 ``2022-01-01``）：库内无数据 ⇒ **进 pending**，从该日起
          抓全历史。这是「**扩充标的池**」语义 —— 新标的本来就该是"库内无数据"，
          沿用默认语义会让它们被整批跳过（这正是"扩充"与"补齐"最容易混淆之处）。
    """
    pending: list[tuple[str, date | None, date | None, date]] = []
    up_to_date: list[str] = []
    no_data: list[str] = []
    new_symbols: list[str] = []
    hfq_missing: list[str] = []
    start_hist: Counter[str] = Counter()

    for sym in symbols:
        last = _symbol_last_date_in("daily_bar", sym, target.year)
        last_hfq = _symbol_last_date_in("daily_bar_hfq", sym, target.year)
        if last is None and last_hfq is None:
            if new_symbol_from is None:
                no_data.append(sym)
                continue
            start = _first_trade_day_on_or_after(new_symbol_from, cal)
            if start > target:
                start = target
            new_symbols.append(sym)
            pending.append((sym, None, None, start))
            start_hist[start.isoformat()] += 1
            continue
        if (last is not None and last >= target
                and last_hfq is not None and last_hfq >= target):
            up_to_date.append(sym)
            continue
        if last_hfq is None:
            hfq_missing.append(sym)
        ref = min(d for d in (last, last_hfq) if d is not None)
        try:
            start = next_trade_day(ref, cal)
        except ValueError:
            # 日历数据不足（刷新失败降级）：退化为单日拉取
            start = target
        if start > target:
            # 仅当「一侧已达目标、另一侧完全无数据」时出现：退化为目标日单日，
            # 至少把缺口起点补上，避免发出区间倒置的请求（必然空返回）。
            start = target
        pending.append((sym, last, last_hfq, start))
        start_hist[start.isoformat()] += 1

    return {"pending": pending, "up_to_date": up_to_date, "no_data": no_data,
            "new_symbols": new_symbols,
            "hfq_missing": hfq_missing, "start_hist": start_hist}


def _first_trade_day_on_or_after(d: date, cal) -> date:
    """取 ``>= d`` 的第一个交易日（``d`` 本身是交易日则返回 ``d``）。

    ⚠️ 与 :func:`next_trade_day` 的区别是**含端点**：`next_trade_day` 从 `d+1` 起找，
    用它做"全历史起点"会平白丢掉起点当天那一根 K 线（若起点恰是交易日）。
    """
    from app.domain.calendar import is_trade_day
    if is_trade_day(d, cal):
        return d
    return next_trade_day(d, cal)


def _print_plan(plan: dict, target: date, priority: list[str]) -> None:
    pend = plan["pending"]
    print("=" * 72)
    print(f"增量补齐范围（目标交易日 = {target.isoformat()}）")
    print("=" * 72)
    print(f"  数据源降级链      : {' → '.join(priority)}")
    print(f"  标的池（daily_bar）: {len(plan['up_to_date']) + len(pend) + len(plan['no_data'])}")
    print(f"  ├ 已最新（跳过）  : {len(plan['up_to_date'])}")
    print(f"  ├ 待补齐          : {len(pend)}")
    print(f"  └ 库内无数据      : {len(plan['no_data'])}")
    print(f"  其中 hfq 侧缺失   : {len(plan['hfq_missing'])}")
    print()
    if plan["start_hist"]:
        print("  待补标的的区间起点分布（Top 10）：")
        for d, n in plan["start_hist"].most_common(10):
            print(f"    {d} 起  {n:5d} 只")
        print()
    # 每只标的固定 2 次外呼（raw + hfq），受全局限速约束
    calls = len(pend) * 2
    rate = get_settings().AKSHARE_RATE_LIMIT
    est = calls * rate * 1.15 / 60.0
    print(f"  预计网络调用      : {calls} 次（{len(pend)} 只 × 2 口径）")
    print(f"  全局限速          : {rate}s/次（+≤30% 抖动）")
    print(f"  预计耗时          : ≈ {est:.0f} 分钟（单线程串行，实际取决于源站）")
    print("=" * 72)


# ---------------------------------------------------------------------------
# 执行
# ---------------------------------------------------------------------------
def run(plan: dict, target: date, state: dict, *, verify_each: bool) -> dict:
    # ⚠️ 变量名 `ds` 曾导致一次真实误判：它表示 **date string（目标交易日）**，
    # 而 `fetch_and_write_daily_bars(code, start, end, adjusts)` 的第 3 个位置参数
    # 正是 `end`。名字看着像 "data source"，会让人以为「把数据源列表传给了 end」，
    # 进而去"修"一个并不存在的 bug（对照实验证实：真传 list 会三源全 AttributeError）。
    # 已改名为 target_str，避免下一个读代码的人重蹈。
    target_str = target.isoformat()
    pend = plan["pending"]
    completed: set[str] = set(state.get("completed", []))
    failed: set[str] = set(state.get("failed", []))
    rows_written = int(state.get("rows_written", 0))
    last_flush = 0.0
    t_run = time.monotonic()

    def _flush(force: bool = False) -> None:
        nonlocal last_flush
        now = time.monotonic()
        if not force and now - last_flush < _STATE_FLUSH_MIN_INTERVAL:
            return
        last_flush = now
        _save_state({
            "target": target_str,
            "completed": sorted(completed),
            "failed": sorted(failed),
            "rows_written": rows_written,
            "updated_at": time.strftime("%Y-%m-%d %H:%M:%S"),
        })

    n_ok = n_fail = n_empty = n_still_stale = 0
    for i, (sym, last, last_hfq, start) in enumerate(pend, 1):
        code = sym.split(".")[0]
        try:
            n, failed_adj = fetch_and_write_daily_bars(
                code, start.isoformat(), target_str, adjusts=("", "hfq"))
        except Exception as e:  # noqa: BLE001 逐标的容错：单只失败不中断整批
            failed.add(sym)
            completed.discard(sym)  # 不变式：completed / failed 互斥
            n_fail += 1
            logger.warning(f"[backfill] {sym} 抓取异常: {e!r}")
            _flush()
            continue

        # 落库后回读校验：两侧是否真的都到了目标日。
        # 这一步是「抓取成功」与「数据真的进库且到位」的分界线 —— 只看
        # fetch_and_write 的返回值会把「写入被门禁拒绝」误判成成功。
        if verify_each:
            v_raw = _symbol_last_date_in("daily_bar", sym, target.year)
            v_hfq = _symbol_last_date_in("daily_bar_hfq", sym, target.year)
            ok_raw = v_raw is not None and v_raw >= target
            ok_hfq = v_hfq is not None and v_hfq >= target
            if not (ok_raw and ok_hfq):
                n_still_stale += 1
                failed.add(sym)
                completed.discard(sym)
                logger.warning(
                    f"[backfill] {sym} 写入后仍落后: raw={v_raw} hfq={v_hfq} "
                    f"（目标 {target_str}），已记入 failed 可重跑")
                _flush()
                continue

        if n > 0 and not failed_adj:
            completed.add(sym)
            failed.discard(sym)
            rows_written += n
            n_ok += 1
        elif n > 0 or failed_adj:
            failed.add(sym)
            completed.discard(sym)
            n_fail += 1
        else:
            # 全口径无数据 = 停牌 / 退市 / 源不覆盖 ⇒ 不算故障，不重试
            completed.add(sym)
            failed.discard(sym)
            n_empty += 1

        if i % 25 == 0 or i == len(pend):
            elapsed = time.monotonic() - t_run
            speed = i / elapsed if elapsed > 0 else 0.0
            eta = (len(pend) - i) / speed / 60.0 if speed > 0 else float("inf")
            logger.info(
                f"[backfill] 进度 {i}/{len(pend)} | ok={n_ok} fail={n_fail} "
                f"empty={n_empty} rows={rows_written} | "
                f"{speed:.2f} 只/s | 剩余 ≈{eta:.0f} 分钟")
        _flush()

    _flush(force=True)
    return {
        "target": target_str,
        "planned": len(pend),
        "ok": n_ok, "failed": n_fail, "empty": n_empty,
        "still_stale": n_still_stale,
        "skipped_up_to_date": len(plan["up_to_date"]),
        "no_data": len(plan["no_data"]),
        "rows_written": rows_written,
        "elapsed_sec": round(time.monotonic() - t_run, 1),
        "failed_symbols": sorted(failed)[:200],
    }


def main() -> int:
    ap = argparse.ArgumentParser(prog="backfill_incremental.py")
    ap.add_argument("--dry-run", action="store_true",
                    help="只输出范围报告，不抓取、不写盘")
    ap.add_argument("--limit", type=int, default=0,
                    help="只处理前 N 只（冒烟用；不影响续传记录语义）")
    ap.add_argument("--codes", default=None,
                    help="逗号分隔的 6 位代码，限定标的（调试用）")
    ap.add_argument("--no-resume", action="store_true",
                    help="忽略续传记录，从头重跑（仍幂等）")
    ap.add_argument("--no-verify-each", action="store_true",
                    help="关闭逐只写后回读校验（更快，但不证明数据真的到位）")
    args = ap.parse_args()

    setup_logging()
    s = get_settings()
    priority = daily_source_priority()

    # 🔴 必须复刻应用 lifespan 的 socket 兜底超时（见 app/main.py:59-62）。
    # 本 CLI **不走 ASGI lifespan**，若不在这里设，akshare/requests 内部那些**未显式
    # 指定 timeout** 的 socket 调用就会无限等待 —— 对端挂死即让整个补齐进程永久阻塞
    # 在 socket read 上（实测过一次：日志在某只标的的 hfq 抓取后静止不动，进程既不报错
    # 也不推进）。「无 timeout 的第三方 HTTP = 挂死之源」是本项目的既有判据。
    _socket_timeout = float(s.SOCKET_DEFAULT_TIMEOUT_SECONDS)
    if _socket_timeout > 0:
        socket.setdefaulttimeout(_socket_timeout)
        logger.info(f"[backfill] socket 兜底超时 = {_socket_timeout}s")

    cal = get_calendar()
    target = last_completed_trade_day(cal)

    if args.codes:
        codes = [c.strip() for c in args.codes.split(",") if c.strip()]
        symbols = [f"{c}.{_suffix(c)}" for c in codes]
    else:
        symbols = read_all_symbols("daily_bar")

    logger.info(f"[backfill] 目标交易日={target} 标的池={len(symbols)} "
                f"数据源={' → '.join(priority)} 数据根={s.DATA_ROOT}")

    # ---- 续传记录：仅当 target 相同才复用（目标日变了必须重算）----
    state = {} if args.no_resume else _load_state()
    resumed_from = 0
    if state.get("target") == target.isoformat() and state.get("completed"):
        done = set(state["completed"])
        before = len(symbols)
        symbols = [x for x in symbols if x not in done]
        resumed_from = before - len(symbols)
        logger.info(f"[backfill] 续传：跳过上次已完成 {resumed_from} 只"
                    f"（target={target} 一致）")
    elif state and state.get("target") != target.isoformat():
        logger.info(f"[backfill] 目标日已变化（{state.get('target')} → {target}），"
                    f"忽略旧续传记录并重算范围")

    plan = build_plan(symbols, target, cal)
    if args.limit:
        plan["pending"] = plan["pending"][: args.limit]
    _print_plan(plan, target, priority)

    if args.dry_run:
        return 0
    if not plan["pending"]:
        print("✅ 无待补标的：全部已达目标交易日（幂等空跑）")
        _save_state({"target": target.isoformat(), "completed": [], "failed": [],
                     "rows_written": 0,
                     "updated_at": time.strftime("%Y-%m-%d %H:%M:%S")})
        return 0

    summary = run(plan, target, state, verify_each=not args.no_verify_each)
    summary["resumed_skipped"] = resumed_from

    print()
    print("=" * 72)
    print("增量补齐结果")
    print("=" * 72)
    for k in ("target", "planned", "ok", "failed", "empty", "still_stale",
              "skipped_up_to_date", "no_data", "rows_written", "elapsed_sec",
              "resumed_skipped"):
        print(f"  {k:20s}: {summary[k]}")
    if summary["failed_symbols"]:
        print(f"  失败标的（前 20）: {summary['failed_symbols'][:20]}")
    print("=" * 72)
    print("JSON:" + json.dumps(summary, ensure_ascii=False))
    return 0 if summary["failed"] == 0 else 1


def _suffix(code: str) -> str:
    if code.startswith(("6", "9")):
        return "SH"
    if code.startswith(("0", "2", "3")):
        return "SZ"
    return "BJ"


if __name__ == "__main__":
    raise SystemExit(main())
