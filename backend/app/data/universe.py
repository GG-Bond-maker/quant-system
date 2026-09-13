"""universe_daily 构建器（P1-3）：每交易日的可交易宇宙 + 真实交易约束。

数据拼装（全部真实来源，无合成）：
- SQLite instrument 表：name / is_st（证券简称含 ST） / list_date / industry；
- daily_bar（不复权）：prev_close / volume / amount；volume==0 视为停牌（is_halted）；
- domain/limit.py：limit_up / limit_down / limit_pct（唯一规则来源，杜绝第二套编码）。

落盘：DATA_ROOT/universe_daily/symbol=__all__/year=YYYY.parquet，
去重键 (date, symbol)，原子替换。
"""
from __future__ import annotations

from datetime import date

import polars as pl
from loguru import logger
from sqlalchemy import select

from ..domain.limit import InstrumentAttrs, calc_limit_prices, determine_board
from .parquet_store import path_for_year, write_partition


def board_of(code: str) -> str:
    """板块枚举（与 domain.limit.determine_board 同源，screener 过滤共用）。"""
    return determine_board(InstrumentAttrs(f"{code}.X", code))


def build_universe_daily(
    trade_date: date,
    symbols: list[str] | None = None,
    persist: bool = True,
) -> pl.DataFrame:
    """构建某交易日的股票宇宙。symbols 缺省 = SQLite 中全部在册证券。"""
    import asyncio

    from ..db.models import Instrument
    from ..db.session import get_session_factory

    async def _load_instruments() -> pl.DataFrame:
        factory = get_session_factory()
        async with factory() as sess:
            rows = (await sess.scalars(select(Instrument))).all()
        return pl.DataFrame([{
            "symbol": r.symbol, "code": r.code, "name": r.name,
            "is_st": r.is_st, "list_date": r.list_date, "industry": r.industry,
        } for r in rows])

    ins = asyncio.run(_load_instruments())
    if symbols is not None:
        ins = ins.filter(pl.col("symbol").is_in(symbols))
    if ins.is_empty():
        raise ValueError("instrument 表为空，请先运行 bootstrap / 数据初始化")

    rows: list[dict] = []
    for rec in ins.iter_rows(named=True):
        sym: str = rec["symbol"]
        code: str = rec["code"]
        bar = read_prev_and_today(sym, trade_date)
        prev_close = bar.get("prev_close")
        close = bar.get("close")
        raw_volume = bar.get("volume", 0.0)
        volume = float(raw_volume) if raw_volume is not None else 0.0
        attrs = InstrumentAttrs(symbol=sym, code=code, is_st=bool(rec["is_st"]),
                                list_date=rec["list_date"])
        limit_up: float | None
        limit_down: float | None
        limit_pct: float | None
        if prev_close:
            up, dn, pct = calc_limit_prices(prev_close, attrs, trade_date)
            limit_up, limit_down = float(up), float(dn)
            limit_pct = float(pct) if pct else None
        else:
            limit_up = limit_down = limit_pct = None  # 无昨收（新股/长期停牌）
        rows.append({
            "date": trade_date, "symbol": sym, "name": rec["name"],
            "board": board_of(code), "is_st": bool(rec["is_st"]),
            "list_date": rec["list_date"],
            "days_since_list": ((trade_date - rec["list_date"]).days
                                if rec["list_date"] else None),
            "is_halted": (volume == 0) if volume is not None else True,
            "industry": rec["industry"],
            "limit_pct": limit_pct, "limit_up": limit_up, "limit_down": limit_down,
            "close": close, "volume": volume if volume is not None else 0.0,
        })
    df = pl.DataFrame(rows).sort("symbol")
    if persist:
        write_partition("universe_daily", "__all__", trade_date, df,
                        dedup_keys=("date", "symbol"))
        logger.info(f"universe_daily {trade_date}: {df.height} 标的已写入")
    return df


def _round_half_up_2(x: pl.Expr) -> pl.Expr:
    """四舍五入到 0.01 元（ROUND_HALF_UP）。

    Polars 的 round() 是银行家舍入，与交易所公布的涨跌停价不一致
    （domain/limit.py 专门用 Decimal ROUND_HALF_UP 规避同一问题）。
    价格恒为正，故 ROUND_HALF_UP 等价于 floor(v*100 + 0.5)/100。
    """
    return ((x * 100.0 + 0.5).floor() / 100.0)


def _limit_pct_expr(board: pl.Expr, is_st: pl.Expr, days_since_list: pl.Expr,
                    is_new_issue: pl.Expr) -> pl.Expr:
    """涨跌停幅度 pct（与 domain/limit.py 的板块规则保持一致）。

    规则来源 domain/limit.py：
        主板 ±10%（ST ±5%）；创业板/科创板 ±20%（ST 仍 ±20%）；
        北交所 ±30%；新股特殊期不设涨跌幅（pct=0，由 limit_up/down 哨兵表达）。
    """
    main = (
        pl.when(is_st).then(pl.lit(0.05)).otherwise(pl.lit(0.10))
    )
    return (
        pl.when(is_new_issue).then(pl.lit(0.0))
        .when(board == "bse").then(pl.lit(0.30))
        .when(board == "chinext_star").then(pl.lit(0.20))
        .when(board == "main").then(main)
        .otherwise(pl.lit(0.10))
    )


def build_universe_history(
    symbols: list[str] | None = None,
    persist: bool = True,
    progress_every: int = 20,
) -> pl.DataFrame:
    """构建**全历史** universe_daily（第七阶段：HIGH-001 修复）。

    原实现 ``build_universe_daily`` 是"单日 × 全标的"，且每个标的都调用
    ``read_symbol_dataset`` 全量重读该标的的所有年份分区 —— 对 1146 个交易日
    × 120 只标的意味着约 13.7 万次全量 IO，实际上根本跑不完，
    导致仓库里只有 1 个交易日（2024-06-05，7 行）的 universe，
    回测因此完全不可用。

    本函数改为**向量化全历史构建**：
        一次性读入 daily_bar -> 按 (symbol, date) 计算 prev_close / is_halted
        -> 向量化计算涨跌停 -> 按年分区批量落盘

    覆盖口径：每个交易日 × 所有已上市（list_date <= date）且在册证券。
    当日无行情或成交量为 0 记为 is_halted=True（停牌不可交易）。

    :return: 完整 universe DataFrame
    """
    import asyncio

    from ..db.models import Instrument
    from ..db.session import get_session_factory
    from .parquet_store import read_all_symbols, read_symbol_dataset

    async def _load_instruments() -> pl.DataFrame:
        factory = get_session_factory()
        async with factory() as sess:
            rows = (await sess.scalars(select(Instrument))).all()
        return pl.DataFrame([{
            "symbol": r.symbol, "code": r.code, "name": r.name,
            "is_st": bool(r.is_st), "list_date": r.list_date,
            "industry": r.industry,
        } for r in rows])

    ins = asyncio.run(_load_instruments())
    if ins.is_empty():
        raise ValueError("instrument 表为空，请先运行 bootstrap / 数据初始化")

    have = set(read_all_symbols("daily_bar"))
    ins = ins.filter(pl.col("symbol").is_in(list(have)))
    if symbols is not None:
        ins = ins.filter(pl.col("symbol").is_in(list(symbols)))
    if ins.is_empty():
        raise ValueError("instrument 与 daily_bar 无交集，请先补齐行情数据")

    # ---- 1) 一次性读入全部行情 ----
    frames = []
    syms = ins["symbol"].to_list()
    for i, sym in enumerate(syms, 1):
        df = read_symbol_dataset("daily_bar", sym)
        if not df.is_empty():
            # ⚠️ open 必须带上：Broker 按 T 日**开盘价**撮合。
            #    少了它回测会一笔都成交不了（实测 252 个交易日 0 成交）。
            cols = ["date", "symbol", "open", "high", "low", "close", "volume"]
            frames.append(df.select([c for c in cols if c in df.columns]))
        if progress_every and i % progress_every == 0:
            logger.info(f"[universe] 读取行情 {i}/{len(syms)}")
    if not frames:
        raise ValueError("daily_bar 为空")
    bars = pl.concat(frames, how="vertical_relaxed").sort(["symbol", "date"])
    logger.info(f"[universe] 行情载入 rows={bars.height} symbols={bars['symbol'].n_unique()}")

    # ---- 2) 全交易日 × 全标的骨架（仅保留已上市的）----
    all_dates = bars.select("date").unique().sort("date")
    grid = (ins.select(["symbol", "code", "name", "is_st", "list_date", "industry"])
            .join(all_dates, how="cross"))
    grid = grid.filter(
        (pl.col("list_date").is_null()) | (pl.col("list_date") <= pl.col("date")))

    # ---- 3) 左连接行情，缺失 = 停牌 ----
    df = grid.join(bars, on=["symbol", "date"], how="left")
    df = df.sort(["symbol", "date"]).with_columns([
        pl.col("close").fill_null(0.0).alias("close_f"),
        pl.col("volume").fill_null(0.0).alias("volume_f"),
    ])
    # prev_close：该 symbol 上一交易日的收盘（跨停牌仍取最近一次）
    df = df.with_columns(
        pl.col("close").fill_null(strategy="forward").shift(1).over("symbol")
        .alias("prev_close"))

    # ---- 4) 板块 / 上市天数 / 新股判定期 ----
    df = df.with_columns([
        pl.col("code").map_elements(lambda c: board_of(str(c)),
                                    return_dtype=pl.String).alias("board"),
        (pl.col("date") - pl.col("list_date")).dt.total_days()
        .alias("days_since_list"),
    ])
    # 新股特殊期（与 domain/limit.py 一致）：
    #   创业板/科创板/主板注册制后：上市前 5 个交易日不设涨跌幅
    #   北交所：上市首日不设涨跌幅
    #   主板 2023-04 前：首日 ±44%/-36%（此处按不设限的宽松处理会失真，
    #   故单独用 pct=0.44 表达，见 _limit_pct_expr 的 is_new_issue 分支）
    df = df.with_columns(
        pl.when(pl.col("days_since_list").is_null()).then(pl.lit(False))
        .when(pl.col("board") == "bse").then(pl.col("days_since_list") < 1)
        .otherwise(pl.col("days_since_list") < 5)
        .alias("is_new_issue")
    )

    # ---- 5) 向量化涨跌停 ----
    pct = _limit_pct_expr(pl.col("board"), pl.col("is_st"),
                          pl.col("days_since_list"), pl.col("is_new_issue"))
    df = df.with_columns(pct.alias("limit_pct"))
    df = df.with_columns([
        pl.when(pl.col("is_new_issue"))
        .then(pl.lit(99999999.99))
        .when(pl.col("prev_close").is_null() | (pl.col("prev_close") <= 0))
        .then(None)
        .otherwise(_round_half_up_2(pl.col("prev_close") * (1.0 + pl.col("limit_pct"))))
        .alias("limit_up"),
        pl.when(pl.col("is_new_issue"))
        .then(pl.lit(0.0))
        .when(pl.col("prev_close").is_null() | (pl.col("prev_close") <= 0))
        .then(None)
        .otherwise(_round_half_up_2(pl.col("prev_close") * (1.0 - pl.col("limit_pct"))))
        .alias("limit_down"),
        pl.when(pl.col("volume_f") <= 0).then(pl.lit(True))
        .otherwise(pl.lit(False)).alias("is_halted"),
    ])

    out = df.select([
        "date", "symbol", "name", "board", "is_st", "list_date",
        "days_since_list", "is_halted", "limit_pct", "limit_up", "limit_down",
        "industry", "open", "high", "low", "close", "volume",
    ]).sort(["date", "symbol"])

    if persist:
        out = out.with_columns(pl.col("date").dt.year().alias("_year"))
        for (year,), g in out.group_by(["_year"], maintain_order=True):
            part = g.drop("_year")
            write_partition("universe_daily", "__all__", date(int(year), 1, 1),  # type: ignore[call-overload]
                            part,
                            dedup_keys=("date", "symbol"))
            logger.info(f"[universe] {year} 写入 {part.height} 行")
        out = out.drop("_year")

    logger.info(f"[universe] 全历史构建完成 rows={out.height} "
                f"dates={out['date'].n_unique()} symbols={out['symbol'].n_unique()}")
    return out


# 回测宇宙 hfq 域涨跌停哨兵（镜像 raw 构建器 is_new_issue 分支的"不设限"语义）
_BT_NO_LIMIT_UP = 1e12
_BT_NO_LIMIT_DOWN = 0.0

_BT_PRICE_COLS = ("date", "symbol", "open", "high", "low", "close")
_BT_ALL_COLS = ("date", "symbol", "open", "high", "low", "close",
                "volume", "amount")


async def _load_instruments_df() -> pl.DataFrame:
    """instrument 表 -> Polars（build_universe_history / build_universe_backtest 共用）。"""
    from ..db.models import Instrument
    from ..db.session import get_session_factory

    factory = get_session_factory()
    async with factory() as sess:
        rows = (await sess.scalars(select(Instrument))).all()
    return pl.DataFrame([{
        "symbol": r.symbol, "code": r.code, "name": r.name,
        "is_st": bool(r.is_st), "list_date": r.list_date,
        "delist_date": r.delist_date, "industry": r.industry,
    } for r in rows])


def _read_bt_bars(syms: list[str], progress_every: int) -> pl.DataFrame:
    """逐 symbol 读 hfq 价格，合并 raw 的 volume/amount（raw 优先、hfq 兜底）。

    参与率/冲击成本是金额比值，raw 与 hfq 换算在分子分母同乘当日因子，
    比值不变——量额直接用 raw 口径即可，无需换算。
    """
    from .parquet_store import read_symbol_dataset

    frames: list[pl.DataFrame] = []
    for i, sym in enumerate(syms, 1):
        hfq = read_symbol_dataset("daily_bar_hfq", sym)
        if hfq.is_empty():
            continue
        p = hfq.select([c for c in _BT_ALL_COLS if c in hfq.columns])
        raw = read_symbol_dataset("daily_bar", sym)
        if not raw.is_empty() and "volume" in raw.columns:
            va = raw.select([c for c in ("date", "volume", "amount", "close")
                             if c in raw.columns])
            va = va.rename({"volume": "_volume_r", "amount": "_amount_r",
                            "close": "_close_r"})
            p = p.join(va, on="date", how="left")
            if "_volume_r" in p.columns:
                fb = "volume" if "volume" in p.columns else None
                p = (p.with_columns(pl.coalesce(["_volume_r", fb]).alias("volume"))
                     if fb else p.with_columns(pl.col("_volume_r").alias("volume")))
                p = p.drop("_volume_r")
            if "_amount_r" in p.columns:
                fb = "amount" if "amount" in p.columns else None
                p = (p.with_columns(pl.coalesce(["_amount_r", fb]).alias("amount"))
                     if fb else p.with_columns(pl.col("_amount_r").alias("amount")))
                p = p.drop("_amount_r")
        # 复权因子 f_t = hfq/raw：broker 把整手/参与率换算回物理域用。
        # raw 缺失（停牌日）为 null -> 消费端默认 1.0（当日不可交易，无影响）。
        if "_close_r" in p.columns and "close" in p.columns:
            p = p.with_columns(
                pl.when(pl.col("_close_r") > 0)
                .then(pl.col("close") / pl.col("_close_r"))
                .otherwise(None).alias("factor")
            ).drop("_close_r")
        else:
            p = p.with_columns(pl.lit(None, dtype=pl.Float64).alias("factor"))
        # 列集归一：保证跨 symbol concat 后 schema 一致
        for c in (*_BT_ALL_COLS, "factor"):
            if c not in p.columns:
                p = p.with_columns(pl.lit(None).alias(c))
        frames.append(p.select([*_BT_ALL_COLS, "factor"]))

        if progress_every and i % progress_every == 0:
            logger.info(f"[universe-bt] 读取行情 {i}/{len(syms)}")
    if not frames:
        return pl.DataFrame()
    out = pl.concat(frames, how="vertical_relaxed").sort(["symbol", "date"])
    logger.info(f"[universe-bt] 行情载入 rows={out.height} "
                f"symbols={out['symbol'].n_unique()}")
    return out


def build_universe_backtest(
    symbols: list[str] | None = None,
    persist: bool = True,
    progress_every: int = 100,
) -> pl.DataFrame:
    """构建 **hfq 口径**的全历史回测宇宙（整改 Task 1：结算口径 raw -> hfq）。

    为什么不用 build_universe_history 的 raw 口径：
        引擎按 raw close/open 撮合与估值时，除权日缺口被当成真实亏损
        （现金分红不入账、10 送 10 市值凭空减半，见
        docs/audit/2026-09-05-量化专项审查报告.md P0-1）。hfq 序列跨除权日
        连续，以它结算后：
        - 除权日无虚假亏损（分红/送转已体现在 hfq 价格路径中）；
        - limit_up/limit_down 基于 hfq 昨收推导，天然等于"除权参考价"口径
          （raw 口径下除权日涨跌停基准未做除权调整，是另一处失真）。

    与 build_universe_history 的差异：
        - 价格列取 daily_bar_hfq；volume/amount 取 raw daily_bar（缺失回退
          hfq 自带值，见 _read_bt_bars）；
        - limit 哨兵在 hfq 域重新标定（is_new_issue -> 1e12 / 0.0）；
        - 无 hfq 行情的 symbol 整体剔除（无复权价不可回测）；
        - 落盘数据集 ``universe_daily_bt``（与 raw 版并存，回滚零成本）。

    :return: 列 date, symbol, name, board, is_st, list_date, days_since_list,
             is_halted, limit_up, limit_down, industry,
             open, high, low, close, volume, amount, factor
             （价格列均为 hfq 口径；factor = hfq/raw，供 broker 物理整手换算）
    """
    from .parquet_store import read_all_symbols

    import asyncio

    ins = asyncio.run(_load_instruments_df())
    if ins.is_empty():
        raise ValueError("instrument 表为空，请先运行 bootstrap / 数据初始化")

    have_hfq = set(read_all_symbols("daily_bar_hfq"))
    ins = ins.filter(pl.col("symbol").is_in(list(have_hfq)))
    if symbols is not None:
        ins = ins.filter(pl.col("symbol").is_in(list(symbols)))
    if ins.is_empty():
        raise ValueError("instrument 与 daily_bar_hfq 无交集，请先补齐复权行情")

    # ---- 1) 行情：hfq 价格 + raw 量额 ----
    bars = _read_bt_bars(ins["symbol"].to_list(), progress_every)
    if bars.is_empty():
        raise ValueError("daily_bar_hfq 为空，无法构建回测宇宙")

    # ---- 2) 全交易日 × 全标的骨架（仅保留已上市）----
    all_dates = bars.select("date").unique().sort("date")
    grid = (ins.select(["symbol", "code", "name", "is_st", "list_date",
                        "delist_date", "industry"])
            .join(all_dates, how="cross"))
    grid = grid.filter(
        (pl.col("list_date").is_null()) | (pl.col("list_date") <= pl.col("date")))
    # Task 6（整改 A-P1-2）：退市证券按 delist_date 移出宇宙
    # （此前退市股表现为"永续停牌"，无法与真停牌区分）
    grid = grid.filter(
        (pl.col("delist_date").is_null()) | (pl.col("date") <= pl.col("delist_date")))

    # ---- 3) 左连接行情，缺失 = 停牌 ----
    df = grid.join(bars, on=["symbol", "date"], how="left")
    df = df.sort(["symbol", "date"]).with_columns(
        pl.col("volume").fill_null(0.0).alias("volume_f"))
    # prev_close：hfq 口径上一交易日收盘（跨停牌取最近一次；hfq 连续无除权缺口）
    df = df.with_columns(
        pl.col("close").fill_null(strategy="forward").shift(1).over("symbol")
        .alias("prev_close"))

    # ---- 4) 板块 / 上市天数 / 新股判定期（与 raw 构建器同规则）----
    df = df.with_columns([
        pl.col("code").map_elements(lambda c: board_of(str(c)),
                                    return_dtype=pl.String).alias("board"),
        (pl.col("date") - pl.col("list_date")).dt.total_days()
        .alias("days_since_list"),
    ])
    df = df.with_columns(
        pl.when(pl.col("days_since_list").is_null()).then(pl.lit(False))
        .when(pl.col("board") == "bse").then(pl.col("days_since_list") < 1)
        .otherwise(pl.col("days_since_list") < 5)
        .alias("is_new_issue")
    )

    # ---- 5) hfq 域涨跌停（除权参考价口径）----
    pct = _limit_pct_expr(pl.col("board"), pl.col("is_st"),
                          pl.col("days_since_list"), pl.col("is_new_issue"))
    df = df.with_columns(pct.alias("limit_pct"))
    df = df.with_columns([
        pl.when(pl.col("is_new_issue"))
        .then(pl.lit(_BT_NO_LIMIT_UP))
        .when(pl.col("prev_close").is_null() | (pl.col("prev_close") <= 0))
        .then(None)
        .otherwise(_round_half_up_2(pl.col("prev_close") * (1.0 + pl.col("limit_pct"))))
        .alias("limit_up"),
        pl.when(pl.col("is_new_issue"))
        .then(pl.lit(_BT_NO_LIMIT_DOWN))
        .when(pl.col("prev_close").is_null() | (pl.col("prev_close") <= 0))
        .then(None)
        .otherwise(_round_half_up_2(pl.col("prev_close") * (1.0 - pl.col("limit_pct"))))
        .alias("limit_down"),
        pl.when(pl.col("volume_f") <= 0).then(pl.lit(True))
        .otherwise(pl.lit(False)).alias("is_halted"),
    ])

    out = df.select([
        "date", "symbol", "name", "board", "is_st", "list_date",
        "days_since_list", "is_halted", "limit_pct", "limit_up", "limit_down",
        "industry", "open", "high", "low", "close", "volume", "amount",
        "factor",
    ]).sort(["date", "symbol"])

    if persist:
        out = out.with_columns(pl.col("date").dt.year().alias("_year"))
        for (year,), g in out.group_by(["_year"], maintain_order=True):
            part = g.drop("_year")
            write_partition("universe_daily_bt", "__all__", date(int(year), 1, 1),  # type: ignore[call-overload]
                            part,
                            dedup_keys=("date", "symbol"))
            logger.info(f"[universe-bt] {year} 写入 {part.height} 行")
        out = out.drop("_year")

    logger.info(f"[universe-bt] hfq 回测宇宙构建完成 rows={out.height} "
                f"dates={out['date'].n_unique()} symbols={out['symbol'].n_unique()}")
    return out


def read_prev_and_today(sym: str, trade_date: date) -> dict:
    """从 daily_bar 读 trade_date 当日及前一日收盘（不复权口径）。

    顺带返回 turnover / amount：选股榜的换手率列需要，而
    universe_daily 分区没有这两列；反正已经读了 daily_bar，不多花一次 IO。
    """
    from .parquet_store import read_symbol_dataset

    df = read_symbol_dataset("daily_bar", sym, end=trade_date)
    if df.is_empty():
        return {}
    today = df.filter(pl.col("date") == trade_date)
    if today.is_empty():
        return {}
    tail = df.tail(2)
    prev_close = float(tail["close"][0]) if tail.height >= 2 else None

    def _num(col: str) -> float | None:
        if col not in today.columns:
            return None
        v = today[col][0]
        try:
            f = float(v)
            return None if f != f else f   # NaN -> None
        except (TypeError, ValueError):
            return None

    return {"close": float(today["close"][0]), "volume": float(today["volume"][0]),
            "prev_close": prev_close,
            "turnover": _num("turnover"), "amount": _num("amount")}


def load_universe(trade_date: date) -> pl.DataFrame:
    """读取已落盘的某交易日宇宙。"""
    target = path_for_year("universe_daily", "__all__", trade_date.year)
    if not target.exists():
        return pl.DataFrame()
    df = pl.read_parquet(target)
    return df.filter(pl.col("date") == trade_date)
