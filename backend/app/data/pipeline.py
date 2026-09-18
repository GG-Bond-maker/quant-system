"""data 层数据原子步骤（Task 15 整改 A-P1-6b）。

编排引擎（STEP_FUNCTIONS / run_pipeline / CLI）已上移 app/orchestrator.py——
data 层只保留不依赖 ml 的原子步骤，供 orchestrator 注册组装。
本模块禁止 import app.ml（守卫：tests/test_no_data_to_ml_import.py）。

原子步骤：
    step_update_daily  拉取各复权口径行情 -> 质量门禁 -> 增量原子写入
    step_validate      当日落库数据综合校验（fail-fast）+ 昨有今无检测（带容差）
    step_rebuild_qfq   由 raw+hfq 推导重建 qfq（qfq = hfq / F_last）
"""
from __future__ import annotations

from datetime import date

from loguru import logger

from ..core.config import get_settings
from .ingest.validate import validate_daily_bar


def step_update_daily(trade_date: date, codes: list[str]) -> str:
    """拉取 trade_date 当日各复权口径行情 -> 校验 -> 增量原子写入 Parquet。

    CRIT-002 修复：改为 ``fetch_and_write_daily_bars``，按口径分别抓取，
    并走质量门禁 + 增量合并（不再整年覆盖、不再把 raw 写进 hfq）。
    """
    from .ingest.tasks import fetch_and_write_daily_bars

    ds = trade_date.strftime("%Y-%m-%d")
    total = 0
    failed: list[str] = []
    for code in codes:
        try:
            n, failed_adj = fetch_and_write_daily_bars(code, ds, ds, adjusts=("", "hfq"))
        except Exception as e:
            logger.warning(f"[pipeline] {code} {ds} 拉取失败(跳过): {e!r}")
            failed.append(code)
            continue
        if failed_adj:
            logger.warning(f"[pipeline] {code} {ds} 口径 {failed_adj} 抓取失败")
            failed.append(code)
        if n == 0 and not failed_adj:
            logger.warning(f"[pipeline] {code} {ds} 无数据（可能停牌/退市）")
            failed.append(code)
            continue
        total += n
    detail = f"rows={total}"
    if failed:
        detail += f" failed={len(failed)}({','.join(failed[:5])})"
    return detail


# 「昨有今无」容差（**纯比例，不用绝对下限**）。
# A 股每天都有少量「昨日正常交易、今日临时停牌」的标的，属完全正常现象；若任一
# missing 即致命，晚间例行几乎每晚都会被误判失败。
# ⚠️ 不得使用绝对下限：小代码集（如手动重跑默认 3 只）的 n_expected 只有个位数，
# 绝对下限会恒 >= n_expected ⇒ 连 100% 丢失也被静默放行（相对旧「任一缺失即 raise」
# 是退化）。纯比例下 missing == n_expected 时 ratio=1.0 > 阈值 ⇒ 任何规模的 100%
# 丢失恒致命；市场级 n_expected≈2167 时阈值≈108，可容纳日常 ~20 只临时停牌。
VALIDATE_MISSING_TOLERANCE_RATIO = 0.05
# 日历不可用时的降级门槛：无法用「上一交易日」校准整日丢失，退化为「当日覆盖率」
# 判据——覆盖率低于此值即视为疑似整日/大面积丢失而致命（否则会静默放行）。
VALIDATE_MIN_COVERAGE_FALLBACK = 0.5


def step_validate(trade_date: date, codes: list[str]) -> str:
    """对当日已落库数据执行综合校验（fail-fast）+ 检测「昨有今无」式整日数据丢失。

    语义（缺陷 3 / 3b，见 docs/audit-2026-09-17/FIX-SPEC-sync-integrity.md §4）：
    - 当日有数据 → 走 :func:`validate_daily_bar`，坏 bar 记 ``quality_errs``
      （**始终致命**，严格性不放松）；
    - 当日无数据 → **才**读上一交易日那一行：昨日有 → ``missing_errs``（昨有今无）；
      昨日也无 → 停牌/未上市/退市 → 跳过（只计 ``n_suspended``）；
    - ``missing_errs`` 仅在超过 ``VALIDATE_MISSING_TOLERANCE_RATIO * n_expected``
      时致命，且**只用纯比例、无绝对下限**——保证任何规模（含 3 只小代码集）的
      100% 丢失恒致命，同时市场级不误杀日常临时停牌股；
    - 日历不可用（``get_calendar``/``prev_trade_day`` 抛错）→ 降级：``prev=None``、
      改用「当日覆盖率」判据（< ``VALIDATE_MIN_COVERAGE_FALLBACK`` 即致命），
      返回串标记 ``degraded=1`` 且记 WARNING，**绝不静默、绝不崩步**。

    返回串：正常日为 ``… missing_vs_prev=<只数> degraded=0``；降级日为
    ``… missing_vs_prev=n/a coverage=<比率> degraded=1``（降级时未校准「昨有今无」，
    用 ``n/a`` 明示无意义，避免被误读为「丢失=0」）。

    日历只对「缺当日」的 code 读取 prev 日那一行（正常日约 350 只），避免 2× 全量读。
    """
    from ..domain.a_share_rules import code_to_symbol
    from ..domain.calendar import prev_trade_day
    from .calendar_store import get_calendar
    from .parquet_store import read_symbol_dataset

    # 1) 上一交易日（日历可用性降级，不得让整步崩掉）
    prev: date | None = None
    try:
        prev = prev_trade_day(trade_date, get_calendar())
    except Exception as e:  # noqa: BLE001 - 日历不可用属可降级情形
        logger.warning(f"[pipeline] validate: 日历不可用，改用覆盖率降级判据: {e!r}")
    degraded = prev is None

    quality_errs: list[str] = []   # 坏 bar —— 始终致命
    missing_errs: list[str] = []   # 昨有今无 —— 超阈值才致命
    n_ok = 0
    n_suspended = 0

    for code in codes:
        sym = code_to_symbol(code)
        df = read_symbol_dataset("daily_bar", sym, start=trade_date, end=trade_date)
        if not df.is_empty():
            ok, errs = validate_daily_bar(df)
            if ok:
                n_ok += 1
            else:
                quality_errs.append(f"{code}: {'; '.join(errs)}")
            continue
        # 当日无数据：仅此时才读上一交易日那一行
        if prev is None:
            n_suspended += 1          # 日历不可用：无法校准，降级为覆盖率判据（见下）
            continue
        prev_df = read_symbol_dataset("daily_bar", sym, start=prev, end=prev)
        if prev_df.is_empty():
            n_suspended += 1          # 停牌/未上市/退市 → 正常跳过
        else:
            missing_errs.append(code)  # 昨有今无 → 可疑

    # 2) 坏 bar 始终致命（严格性不许放松）
    if quality_errs:
        raise ValueError("validate failed: " + " | ".join(quality_errs[:5]))

    if degraded:
        # 3b) 日历不可用 → 降级为「当日覆盖率」判据（不得静默放行整日丢失）
        coverage = n_ok / max(1, len(codes))
        if coverage < VALIDATE_MIN_COVERAGE_FALLBACK:
            raise ValueError(
                f"validate failed: 日历不可用且当日覆盖率 {coverage:.0%} < "
                f"{VALIDATE_MIN_COVERAGE_FALLBACK:.0%}，疑似整日/大面积数据丢失"
                f"（无法用上一交易日校准）")
    else:
        # 3) 「昨有今无」仅在超过**纯比例**容差时才致命
        n_expected = n_ok + len(missing_errs)
        tolerance = VALIDATE_MISSING_TOLERANCE_RATIO * n_expected
        if len(missing_errs) > tolerance:
            raise ValueError(
                f"validate failed: 昨有今无 {len(missing_errs)}/{n_expected} 只"
                f"（阈值 {VALIDATE_MISSING_TOLERANCE_RATIO:.0%}）: "
                + ", ".join(missing_errs[:5]))
        if missing_errs:
            logger.warning(
                f"[pipeline] validate: {len(missing_errs)} 只昨有今无"
                f"（阈值 {tolerance:.1f} 内，放行）: {missing_errs[:10]}")

    # 返回串区分「正常」与「日历不可用降级」两种口径：
    #   - 正常：missing_vs_prev=<昨有今无只数> degraded=0
    #   - 降级：missing_vs_prev=n/a（未校准，无意义）+ coverage=<当日覆盖率> degraded=1
    # 避免把降级时的 n_ok 覆盖率误读成「昨有今无=0」（旧串在降级下会误导值班/告警解析）。
    if degraded:
        coverage = n_ok / max(1, len(codes))
        return (f"validated={n_ok}/{len(codes)} suspended={n_suspended} "
                f"missing_vs_prev=n/a coverage={coverage:.2f} degraded=1")
    return (f"validated={n_ok}/{len(codes)} suspended={n_suspended} "
            f"missing_vs_prev={len(missing_errs)} degraded=0")


def step_rebuild_qfq(trade_date: date, codes: list[str]) -> str:
    """由本地 raw+hfq 全量重建 qfq（Task 3 整改：qfq 不再直接抓取）。

    qfq = hfq / F_last（锚定最新一日，见 repair.build_qfq_dataset）。
    每晚全量重建为纯本地推导（无网络），保证除权后整条序列同基准，
    根治增量抓取造成的 qfq 基准漂移（量化专项审查 P0-3）。
    """
    from .parquet_store import read_all_symbols
    from .repair import build_qfq_dataset

    root = get_settings().DATA_ROOT
    symbols = read_all_symbols("daily_bar_hfq")
    built = 0
    for sym in symbols:
        if build_qfq_dataset(root, sym) > 0:
            built += 1
    return f"symbols={built}/{len(symbols)}"


