"""数据初始化共享任务（供 scripts/bootstrap.py 与 python -m app.data.ingest 复用）。"""
from __future__ import annotations

import sqlite3
from collections.abc import Callable
from datetime import date

import pandas as pd
import polars as pl
from loguru import logger
from sqlalchemy import update as sqlalchemy_update
from sqlalchemy.dialects.sqlite import insert as sqlite_insert

from ...core.config import get_settings
from ...db.models import Instrument, TradeCalendar
from ...db.session import get_session_factory
from ...domain.a_share_rules import code_to_symbol
from ..parquet_store import write_partition
from ..quality import (
    WRITE_GATE_THRESHOLDS,
    QCIssue,
    check_duplicates,
    check_nulls,
    check_ohlc,
    has_schema_drift,
    normalize_schema,
)


async def upsert_calendar(cal: pd.DataFrame) -> int:
    """交易日历 upsert 到 SQLite。"""
    factory = get_session_factory()
    rows = cal.to_dict("records")
    ins = sqlite_insert(TradeCalendar)
    stmt = ins.on_conflict_do_update(
        index_elements=["trade_date"],
        set_={c: ins.excluded[c] for c in ("is_sh", "is_sz", "is_bj", "week")},
    )
    async with factory() as sess:
        await sess.execute(stmt, rows)
        await sess.commit()
    return len(rows)


async def upsert_instruments(pdf: pl.DataFrame) -> int:
    """证券列表 upsert 到 SQLite。"""
    factory = get_session_factory()
    rows = pdf.to_dicts()
    ins = sqlite_insert(Instrument)
    stmt = ins.on_conflict_do_update(
        index_elements=["symbol"],
        set_={c: ins.excluded[c] for c in ("code", "name", "market", "instrument_type", "is_st")},
    )
    async with factory() as sess:
        await sess.execute(stmt, rows)
        await sess.commit()
    return len(rows)


async def upsert_delist_dates(pdf: pl.DataFrame) -> int:
    """退市名单回填 instrument.delist_date（Task 6）。

    :param pdf: 列 code(6位)/delist_date(date)；只更新已存在的 instrument 行。
    """
    factory = get_session_factory()
    n = 0
    async with factory() as sess:
        for rec in pdf.iter_rows(named=True):
            if rec.get("delist_date") is None:
                continue
            try:
                sym = code_to_symbol(str(rec["code"]))
            except ValueError:
                continue
            await sess.execute(
                sqlalchemy_update(Instrument)
                .where(Instrument.symbol == sym)
                .values(delist_date=rec["delist_date"]))
            n += 1
        await sess.commit()
    return n


def symbol_of(code: str) -> str:
    return code_to_symbol(code)


def write_daily_bars(code: str, df: pl.DataFrame, adjusts: tuple[str, ...] = ("",)) -> int:
    """把【已按指定口径抓取】的日线增量写入对应数据集。

    ⚠️ CRIT-002 根因修复（原实现有两个致命缺陷）：

    1. 原实现把调用方传入的**同一份 df** 写到 ``adjusts`` 里的每个目录，
       而 ``adjust`` 只用于拼目录名 —— 结果是 raw 价格被写进 daily_bar_hfq，
       复权因子瞬间塌缩为 1.0。现在**一个 df 只允许一个口径**，
       多口径必须走 :func:`fetch_and_write_daily_bars`（按口径分别抓取）。

    2. 原实现用 ``write_year_batch`` 整年覆盖，每日增量会把该年其它日期全部抹掉
       （实测 000001.SZ / 300750.SZ / 600519.SH 的 2026 年 1~5 月数据就是这样丢的）。
       现在一律走 ``write_partition`` 增量合并（按 date 去重，保留新值）。

    3. 写入前跑质量门禁（:data:`WRITE_GATE_THRESHOLDS`），error 级别问题直接拒绝写入。
    """
    if df.is_empty():
        return 0
    if len(adjusts) != 1:
        raise ValueError(
            f"write_daily_bars 一次只接受一个复权口径（收到 {adjusts}）："
            "单个 DataFrame 不可能同时是两种复权结果，"
            "多口径请改用 fetch_and_write_daily_bars()"
        )
    adjust = adjusts[0]
    dataset = "daily_bar" if adjust == "" else f"daily_bar_{adjust}"
    sym = symbol_of(code)
    pdf = normalize_schema(
        df.with_columns(pl.col("date").cast(pl.Date)), dataset, symbol=sym)

    issues = validate_write_gate(pdf, dataset, sym)
    if issues:
        raise ValueError(
            f"数据质量门禁拒绝写入 {dataset}/{sym}："
            + " | ".join(f"{i.kind}: {i.detail}" for i in issues[:3]))

    total = 0
    for year in sorted({d.year for d in pdf["date"].to_list()}):
        part = pdf.filter(pl.col("date").dt.year() == year)
        write_partition(dataset, sym, date(year, 1, 1), part)
        total += part.height
    return total


def fetch_and_write_daily_bars(
    code: str,
    start: str,
    end: str,
    adjusts: tuple[str, ...] = ("", "hfq"),
    fetcher: Callable[[str, str, str, str], pl.DataFrame] | None = None,
) -> tuple[int, list[str]]:
    """按复权口径**分别抓取**并增量写入（CRIT-002 的正确入口）。

    ⚠️ 关键：akshare 的 hfq/qfq 是"以最新数据为基准重算整条序列"，
    因此必须**一次性抓取完整历史**再分区，绝不能逐年抓取后拼接
    （逐年抓取会让每年的复权基准不同，年界处产生假的复权因子跳变）。

    ⚠️ Task 3（整改 P0-3）：默认口径**不含 qfq**——qfq 锚定最新价，除权后
    新抓的数据已重新定基而存量行保留旧基准，增量写会让同一 symbol 前后
    两段不可比（基准漂移）。qfq 统一由 repair.build_qfq_dataset 从本地
    raw+hfq 推导（qfq = hfq / F_last），pipeline 的 rebuild_qfq 步骤每晚
    全量重建。如确需直抓 qfq，显式传 adjusts=("", "hfq", "qfq") 并自担
    漂移风险。

    :param fetcher: 依赖注入，默认 ``akshare_adapter.fetch_daily_bar``；
        测试可注入假数据（签名 ``(code, start, end, adjust) -> pl.DataFrame``）。
    :return: (写入总行数, 抓取故障的口径列表)。空数据（停牌/退市）不算故障；
        调用方据 failed_adjusts 区分"完成"与"故障"（Task 9 断点续传语义）。
    """
    if fetcher is None:
        from .akshare_adapter import fetch_daily_bar as _f

        fetcher = _f
    total = 0
    failed: list[str] = []
    for adjust in adjusts:
        try:
            df = fetcher(code, start, end, adjust)
        except Exception as e:
            logger.error(f"[ingest] {code} adjust={adjust!r} 抓取失败: {e!r}")
            failed.append(adjust)
            continue
        if df is None or df.is_empty():
            logger.warning(f"[ingest] {code} adjust={adjust!r} 无数据（停牌/退市/网络）")
            continue
        try:
            total += write_daily_bars(code, df, adjusts=(adjust,))
        except ValueError as e:
            # 门禁拒绝 —— 明确记录并继续其它口径，绝不静默写入脏数据
            logger.error(f"[ingest] {code} adjust={adjust!r} 被质量门禁拒绝: {e}")
            failed.append(adjust)
    return total, failed


def validate_write_gate(
    df: pl.DataFrame, dataset: str, symbol: str,
) -> list[QCIssue]:
    """增量写入前的质量门禁：只跑与单批次相关的检查，返回 error 级问题。"""
    th = WRITE_GATE_THRESHOLDS
    issues: list[QCIssue] = []
    if has_schema_drift(df, dataset):
        issues.append(QCIssue(dataset, symbol, "schema_drift", "error",
                              f"列与 canonical 不符：{list(df.columns)}", len(df)))
    issues += check_nulls(df, dataset, symbol, th)
    issues += check_duplicates(df, dataset, symbol, th)
    issues += check_ohlc(df, dataset, symbol, th)
    return [i for i in issues if i.severity == "error"]


def init_sqlite_file() -> None:
    """确保 SQLite 文件存在且 WAL 已开启（脚本场景的幂等自检）。"""
    s = get_settings()
    db_path = s.SQLITE_PATH
    db_path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(db_path)
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA synchronous=NORMAL")
    conn.close()
    logger.info(f"sqlite ready: {db_path}")
