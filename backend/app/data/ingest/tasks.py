"""数据初始化共享任务（供 scripts/bootstrap.py 与 python -m app.data.ingest 复用）。"""
from __future__ import annotations

import json
import sqlite3
from collections.abc import Callable
from datetime import date, datetime
from pathlib import Path

import pandas as pd
import polars as pl
from loguru import logger
from sqlalchemy import func, select
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

    :param pdf: 列 code(6位)/delist_date(date)；可选 list_date(date)——仅当
        ``instrument.list_date`` 为空时才回填（**绝不覆盖**既有值，包括
        ``scripts/enrich_instruments.py`` 用本地行情最早日近似出的值）。
        只更新已存在的 instrument 行，不新增。
    :return: 被写入 ``delist_date`` 的行数。

    幂等：同一份名单重复执行结果相同（UPDATE 而非 INSERT）。
    """
    factory = get_session_factory()
    has_list_date = "list_date" in pdf.columns
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
            if has_list_date and rec.get("list_date") is not None:
                await sess.execute(
                    sqlalchemy_update(Instrument)
                    .where(Instrument.symbol == sym,
                           Instrument.list_date.is_(None))
                    .values(list_date=rec["list_date"]))
        await sess.commit()
    return n


# ---------- 退市回填接线（审计 §8.2 第 6 项 / P1-4 / B5-14） ----------
def delist_status_path() -> Path:
    """退市回填**状态/覆盖度**落盘路径（披露单一来源）。

    为什么单独落盘：覆盖率为 0、部分缺失、或源不可用时，调用方（回测 note、
    人工排障）必须能看出「退市信息不可用/部分可用」，而不是默认「没有退市股」。
    与 `universe_daily` 等派生产物同根（``DATA_ROOT``），故随 DATA_ROOT 一起隔离。
    """
    return get_settings().DATA_ROOT / "delist" / "sync_status.json"


def _write_delist_status(status: dict) -> None:
    """原子落盘状态 JSON；**任何失败只告警**，绝不影响回填本身。"""
    try:
        target = delist_status_path()
        target.parent.mkdir(parents=True, exist_ok=True)
        tmp = target.with_name(target.name + ".tmp")
        tmp.write_text(json.dumps(status, ensure_ascii=False, indent=1, default=str),
                       encoding="utf-8")
        tmp.replace(target)
    except Exception as e:  # noqa: BLE001 披露落盘不得反噬数据步骤
        logger.warning(f"[delist] 状态落盘失败（披露降级，不影响回填）：{e!r}")


async def _existing_symbols() -> set[str]:
    factory = get_session_factory()
    async with factory() as sess:
        return set((await sess.scalars(select(Instrument.symbol))).all())


async def _delist_coverage_counts() -> tuple[int, int]:
    """(instrument 总数, delist_date 非空数)——只读真实计数，不推测。"""
    factory = get_session_factory()
    async with factory() as sess:
        total = (await sess.execute(
            select(func.count()).select_from(Instrument))).scalar_one()
        with_date = (await sess.execute(
            select(func.count()).select_from(Instrument)
            .where(Instrument.delist_date.is_not(None)))).scalar_one()
    return int(total or 0), int(with_date or 0)


async def enrich_delist_dates(fetcher: Callable[[], pl.DataFrame] | None = None,
                              apply: bool = True) -> dict:
    """拉取退市名单并回填 ``instrument.delist_date``（+ 补空 ``list_date``）。

    这是审计 §8.2 第 6 项的**接线本体**：此前唯一调用方是手工脚本
    ``scripts/enrich_delist.py``，没有任何流水线/定时任务调用 ⇒ ``instrument.
    delist_date`` 实测 **0/5552**，回测面板里的退市剔除结构性空转。

    **优雅降级**（本函数**永不抛异常**，否则会把整条晚间流水线掐断）：
    - 源不可达 / akshare 未安装 / 接口列变更 ⇒ ``availability="unavailable"``；
    - 源返回空表 ⇒ ``availability="unavailable"`` 且 ``reason="empty_source"``
      （**空 ≠ 没有退市股**，必须与"成功但名单为空"区分开）；
    - 源可用但与本地 instrument 无交集 ⇒ ``availability="no_local_match"``
      （回填无人可填，覆盖率不会增长，同样要披露）；
    - 有交集 ⇒ ``availability="ok"``。

    :param fetcher: 依赖注入的取数函数（默认 ``akshare_adapter.fetch_delist_list``，
    惰性 import ⇒ 进程启动不加载 akshare）；测试注入假源，**绝不发真实网络请求**。
    :param apply: False = 只取数/算覆盖度，不写库、不落状态（dry-run）。
    :return: 状态/覆盖度字典（同时落盘到 :func:`delist_status_path`）。
    """
    total0, with0 = await _delist_coverage_counts()
    status: dict = {
        "as_of": datetime.now().isoformat(timespec="seconds"),
        "source_name": "akshare_delist",
        "source": None,
        "availability": "unavailable",
        "reason": None,
        "n_source_rows": None,
        "n_matched": 0,
        "n_updated": 0,
        "n_instruments": total0,
        "n_with_delist_date": with0,
        "coverage_pct": round(with0 / total0 * 100, 2) if total0 else 0.0,
        "applied": apply,
    }
    try:
        if fetcher is None:
            from .akshare_adapter import fetch_delist_list

            fetcher = fetch_delist_list
        src = fetcher()
        if src is not None and not isinstance(src, pl.DataFrame):
            # 容错：注入/替换的取数函数若返回 pandas，也统一成 polars
            # （失败同样落到下面的降级分支，不穿透到调用方）
            src = pl.from_pandas(src)
    except Exception as e:  # noqa: BLE001 无源/源故障 ⇒ 降级披露，不阻断流水线
        status["reason"] = f"{type(e).__name__}: {e}"[:300]
        logger.warning(f"[delist] 退市名单源不可用（优雅降级，"
                       f"delist_date 覆盖率不变={status['coverage_pct']}%）："
                       f"{status['reason']}")
        if apply:
            _write_delist_status(status)
        return status

    if src is None or src.height == 0:
        status["reason"] = "empty_source"
        logger.warning("[delist] 退市名单源返回**空表**：不可据此认为『没有退市股』"
                       "（接口变更/被限流/临时故障都可能返回空），"
                       "delist_date 覆盖率不变")
        if apply:
            _write_delist_status(status)
        return status

    status["source"] = (str(src["source"][0] or "unknown")
                        if "source" in src.columns else "unknown")
    status["n_source_rows"] = int(src.height)
    have = await _existing_symbols()
    rows: list[dict] = []
    for rec in src.iter_rows(named=True):
        try:
            sym = code_to_symbol(str(rec["code"]))
        except ValueError:
            continue
        if sym not in have or rec.get("delist_date") is None:
            continue
        row = {"code": str(rec["code"]), "delist_date": rec["delist_date"]}
        if rec.get("list_date") is not None:
            row["list_date"] = rec["list_date"]
        rows.append(row)
    status["n_matched"] = len(rows)

    if rows and apply:
        status["n_updated"] = await upsert_delist_dates(pl.DataFrame(rows))
    status["availability"] = "ok" if rows else "no_local_match"
    if not rows:
        # 让披露能直接点名状态（否则回测 note 里会出现 reason=None）
        status["reason"] = "no_local_match"
    total, with_date = await _delist_coverage_counts()
    status["n_instruments"] = total
    status["n_with_delist_date"] = with_date
    status["coverage_pct"] = round(with_date / total * 100, 2) if total else 0.0
    if status["availability"] != "ok":
        logger.warning(f"[delist] 退市名单与本地 instrument 交集为 0"
                       f"（源 {status['n_source_rows']} 条）⇒ 覆盖率仍为 "
                       f"{status['coverage_pct']}%，退市剔除继续空转")
    else:
        logger.info(f"[delist] 回填 {status['n_updated']} 条；delist_date 覆盖 "
                    f"{with_date}/{total} = {status['coverage_pct']}%")
    if apply:
        _write_delist_status(status)
    return status


def symbol_of(code: str) -> str:
    return code_to_symbol(code)


# 只允许「整只标的重取」写入的兜底源：其复权算法与主源（东财/新浪）**不同**，
# 混列会造成假的复权因子跳变。2026-09-26 多源降级改造引入（唯一成员：baostock）。
_FALLBACK_ONLY_SOURCES: frozenset[str] = frozenset({"baostock"})


def _guard_cross_source(dataset: str, sym: str, incoming: pl.DataFrame) -> None:
    """跨源混列守卫：兜底源不得写进**已有其它源数据**的 symbol 序列。

    背景：BaoStock 用「涨跌幅复权法」，东财/新浪各用自家的除权因子法。同一
    symbol 的 ``daily_bar_hfq``/``qfq`` 序列若由两个源的行拼接，会在切换日出现
    **假的复权因子跳变**——这正是 :func:`fetch_and_write_daily_bars` 文档里
    早已明令禁止的「基准漂移」，而 ``write_partition`` 是按 ``date`` 去重合并
    （保留新值），会**静默覆盖**旧源的行。

    守卫采用最保守策略「整只标的重取」：**兜底源的数据只允许写入本地尚无该
    标的行的 dataset**；本地已有**其它**源的行则拒绝写入并上报，交由人工裁决
    是否整只重取，**绝不静默混列**。本地已有行且同为兜底源时（重跑同一源）放行。

    :raises ValueError: 兜底源试图写进本地已有其它源数据的 symbol。调用方
        （``fetch_and_write_daily_bars``）按「该口径抓取失败」处理并记录。
    """
    if incoming.is_empty() or "source" not in incoming.columns:
        return
    incoming_srcs = {s for s in incoming["source"].unique().to_list() if s}
    if not (incoming_srcs & _FALLBACK_ONLY_SOURCES):
        return  # 主源写主源，不受此守卫约束（保持既有行为）
    from ..parquet_store import read_symbol_dataset

    existing = read_symbol_dataset(dataset, sym, columns=["date", "source"])
    if existing.is_empty():
        return  # 整只标的本地无数据 -> 允许兜底源整段写入
    existing_srcs = (
        {s for s in existing["source"].unique().to_list() if s}
        if "source" in existing.columns else set()
    )
    offending = sorted(existing_srcs - incoming_srcs)
    if not offending:
        return  # 存量亦为同一兜底源（重跑）-> 放行
    raise ValueError(
        f"跨源混列被拒绝 {dataset}/{sym}：兜底源 {sorted(incoming_srcs)} 不得写入"
        f"已有 {offending} 数据的序列（复权基准不同会制造假跳变）；"
        f"存量 {existing.height} 行保持不变，如需切换请人工「整只标的重取」")


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

    # CRIT-003（2026-09-26 多源降级改造）：跨源混列守卫 —— 兜底源(BaoStock)不得写进
    # 已有其它源数据的序列，否则复权基准跳变 + 静默覆盖（详见 _guard_cross_source）。
    _guard_cross_source(dataset, sym, pdf)

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
    conn = sqlite3.connect(db_path, timeout=30)
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA synchronous=NORMAL")
    conn.close()
    logger.info(f"sqlite ready: {db_path}")
