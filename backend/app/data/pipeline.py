"""data 层数据原子步骤（Task 15 整改 A-P1-6b）。

编排引擎（STEP_FUNCTIONS / run_pipeline / CLI）已上移 app/orchestrator.py——
data 层只保留不依赖 ml 的原子步骤，供 orchestrator 注册组装。
本模块禁止 import app.ml（守卫：tests/test_no_data_to_ml_import.py）。

原子步骤：
    step_update_daily  拉取各复权口径行情 -> 质量门禁 -> 增量原子写入
    step_validate      当日落库数据综合校验（fail-fast）
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


def step_validate(trade_date: date, codes: list[str]) -> str:
    """对当日已落库数据执行综合校验（fail-fast）。"""
    from ..domain.a_share_rules import code_to_symbol
    from .parquet_store import read_symbol_dataset

    errs_all: list[str] = []
    for code in codes:
        df = read_symbol_dataset("daily_bar", code_to_symbol(code),
                                 start=trade_date, end=trade_date)
        if df.is_empty():
            errs_all.append(f"{code}: 当日无数据")
            continue
        ok, errs = validate_daily_bar(df)
        if not ok:
            errs_all.append(f"{code}: {'; '.join(errs)}")
    if errs_all:
        raise ValueError("validate failed: " + " | ".join(errs_all[:5]))
    return f"validated={len(codes)}"


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


