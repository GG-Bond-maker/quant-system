"""
数据质量校验（AQP data，纯校验逻辑 + 集中阈值）。

设计原则：
1. **阈值集中**：所有阈值只在 ``QCThresholds`` 定义，严禁散落到各业务文件硬编码；
2. **可审计**：每个问题产出一条 ``QCIssue``（dataset/symbol/date/kind/detail），
   便于生成修复报告与进入 quarantine 决策；
3. **分级**：``severity = error | warn``。
   error = 必须拒绝写入 / 必须隔离；warn = 记录但不阻断；
4. **先隔离后修复**：本模块只负责"检出"，不做静默删除。

覆盖的检查项（对应修复阶段要求）：
    schema_drift   列集合 / 列顺序 / dtype 与 canonical 不一致
    null           关键列出现 null
    duplicate      同一 (symbol, date) 重复
    ohlc           high < low / close 超出 [low, high] / 价格 <= 0 / 成交量为负
    daily_return   单日收益异常（ST / 新股分别放宽）
    factor_jump    复权因子（hfq/raw）单日突变
    calendar       日期不在交易日历内（合成数据 / 错误回补的强特征）
    year_density   单年行数明显偏离交易日数量（合成数据特征）
"""
from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass, field, replace
from datetime import date
from pathlib import Path
from typing import Any, Literal

import polars as pl
from loguru import logger

Severity = Literal["error", "warn"]

# ---------------- 集中阈值（唯一真源） ----------------
@dataclass(frozen=True)
class QCThresholds:
    """数据质量阈值。修改阈值只改这里。

    阈值取值依据（A 股现行制度）：
    - 主板 ±10% / ST ±5% / 创业板·科创板 ±20% / 北交所 ±30%；
      上市前 5 个交易日与北交所首日不设限。故非 ST/新股上限取 0.22（留 10% 缓冲，
      覆盖 ±20% 板的整数化误差），ST 取 0.12。
    - 复权因子单日变化主要来自分红送转；10 送 10 会使因子翻倍。
      经验上单日 >25% 已属极端，用于捕捉"raw 误写入 hfq"（因子瞬变到 1.0）这类事故。
    """

    # 单日收益
    max_abs_daily_return: float = 0.22
    st_max_abs_daily_return: float = 0.12
    new_issue_exempt_days: int = 5  # 上市前 N 个交易日豁免

    # 复权因子
    max_factor_jump: float = 0.25
    factor_near_one_tol: float = 1e-6

    # OHLC
    require_positive_price: bool = True
    require_ohlc_bounds: bool = True
    require_non_negative_volume: bool = True

    # 完整性
    max_null_ratio: float = 0.0
    allow_duplicate_dates: bool = False

    # 年度密度（合成数据检测）：A 股一年约 240~245 个交易日
    min_year_rows: int = 180
    max_year_rows: int = 250

    # 交易日日历校验（合成数据常含周末/节假日）
    check_calendar: bool = True


DEFAULT_THRESHOLDS = QCThresholds()

# 增量写入门禁：单日/短区间写入时，"年度行数密度"必然不达标（一个分区只有 1 行），
# 因此门禁只跑 schema / null / dup / OHLC / 收益连续性，不跑密度检查。
WRITE_GATE_THRESHOLDS = replace(
    DEFAULT_THRESHOLDS, min_year_rows=0, max_year_rows=1_000_000)

# ---------------- canonical schema（列顺序固定） ----------------
# 说明：'pct' 可由 close/prev_close 推导，不入库；'source' 记录数据来源便于追踪。
# 元素类型取 ``type[pl.DataType]`` 而非实例：pl.Date / pl.Float64 是类对象，
# 若按实例注解，mypy 会对每一项都报 list-item 不兼容。
SchemaSpec = list[tuple[str, "type[pl.DataType]"]]
CANONICAL_SCHEMA: dict[str, SchemaSpec] = {
    "daily_bar": [
        ("date", pl.Date), ("open", pl.Float64), ("high", pl.Float64),
        ("low", pl.Float64), ("close", pl.Float64), ("volume", pl.Float64),
        ("amount", pl.Float64), ("turnover", pl.Float64),
        ("code", pl.String), ("symbol", pl.String), ("source", pl.String),
    ],
}
# 复权口径与 raw 同构（仅 OHLC 被调整，volume/amount 保持原口径）
CANONICAL_SCHEMA["daily_bar_hfq"] = list(CANONICAL_SCHEMA["daily_bar"])
CANONICAL_SCHEMA["daily_bar_qfq"] = list(CANONICAL_SCHEMA["daily_bar"])

PRICE_COLS = ("open", "high", "low", "close")
KEY_COLS = ("date", "open", "high", "low", "close", "volume", "symbol")


# ---------------- 问题记录 ----------------
@dataclass(frozen=True)
class QCIssue:
    dataset: str
    symbol: str
    kind: str
    severity: Severity
    detail: str
    n_rows: int = 0
    sample_dates: tuple[str, ...] = ()

    def as_row(self) -> dict[str, Any]:
        return {
            "dataset": self.dataset, "symbol": self.symbol, "kind": self.kind,
            "severity": self.severity, "detail": self.detail,
            "n_rows": self.n_rows, "sample_dates": ",".join(self.sample_dates[:5]),
        }


@dataclass
class QCReport:
    dataset: str
    issues: list[QCIssue] = field(default_factory=list)
    n_symbols: int = 0
    n_files: int = 0
    n_rows: int = 0

    def add(self, issue: QCIssue) -> None:
        self.issues.append(issue)

    @property
    def errors(self) -> list[QCIssue]:
        return [i for i in self.issues if i.severity == "error"]

    @property
    def warnings(self) -> list[QCIssue]:
        return [i for i in self.issues if i.severity == "warn"]

    def by_kind(self) -> dict[str, int]:
        out: dict[str, int] = {}
        for i in self.issues:
            out[i.kind] = out.get(i.kind, 0) + i.n_rows
        return out


# ---------------- schema 归一 ----------------
def canonical_columns(dataset: str) -> list[str]:
    return [c for c, _ in CANONICAL_SCHEMA.get(dataset, CANONICAL_SCHEMA["daily_bar"])]


def canonical_dtypes(dataset: str) -> dict[str, Any]:
    """列 -> dtype（dtype 是类对象，供 cast/normalize 使用）。"""
    return dict(CANONICAL_SCHEMA.get(dataset, CANONICAL_SCHEMA["daily_bar"]))


def schema_signature(df: pl.DataFrame) -> tuple[str, ...]:
    return tuple(df.columns)


def has_schema_drift(df: pl.DataFrame, dataset: str) -> bool:
    return schema_signature(df) != tuple(canonical_columns(dataset))


def normalize_schema(
    df: pl.DataFrame,
    dataset: str,
    symbol: str | None = None,
    source: str = "unknown",
) -> pl.DataFrame:
    """把任意来源分区归一到 canonical schema（固定列顺序 + 固定 dtype）。

    - 缺失列补 null；多余列（如合成数据里的 ``pct``）直接丢弃；
    - ``date`` 统一转 pl.Date；
    - ``symbol``/``code``/``source`` 缺失时按 symbol 推导。
    """
    dtypes = canonical_dtypes(dataset)
    cols = list(dtypes)

    if "date" in df.columns and df.schema["date"] != pl.Date:
        df = df.with_columns(pl.col("date").cast(pl.Date, strict=False))

    if symbol is not None:
        if "symbol" not in df.columns:
            df = df.with_columns(pl.lit(symbol).alias("symbol"))
        if "code" not in df.columns:
            df = df.with_columns(pl.lit(symbol.split(".")[0]).alias("code"))
    if "source" not in df.columns:
        df = df.with_columns(pl.lit(source).alias("source"))

    missing = [pl.lit(None, dtypes[c]).alias(c) for c in cols if c not in df.columns]
    if missing:
        df = df.with_columns(missing)

    casted = [pl.col(c).cast(dtypes[c], strict=False).alias(c) for c in cols]
    return df.select(casted)


# ---------------- 单项检查 ----------------
def check_nulls(df: pl.DataFrame, dataset: str, symbol: str,
                th: QCThresholds) -> list[QCIssue]:
    out: list[QCIssue] = []
    for c in KEY_COLS:
        if c not in df.columns:
            continue
        n = int(df[c].null_count())
        if n:
            ratio = n / max(len(df), 1)
            out.append(QCIssue(dataset, symbol, "null",
                               "error" if ratio > th.max_null_ratio else "warn",
                               f"列 {c} 有 {n} 个 null（占比 {ratio:.2%}）", n))
    return out


def check_duplicates(df: pl.DataFrame, dataset: str, symbol: str,
                     th: QCThresholds) -> list[QCIssue]:
    if th.allow_duplicate_dates or "date" not in df.columns:
        return []
    n = len(df) - int(df["date"].n_unique())
    if n > 0:
        dups = (df.group_by("date").len().filter(pl.col("len") > 1)
                .sort("date")["date"].to_list()[:5])
        return [QCIssue(dataset, symbol, "duplicate", "error",
                        f"{n} 个重复交易日", n,
                        tuple(str(d) for d in dups))]
    return []


def check_ohlc(df: pl.DataFrame, dataset: str, symbol: str,
               th: QCThresholds) -> list[QCIssue]:
    out: list[QCIssue] = []
    if not all(c in df.columns for c in PRICE_COLS):
        return out

    bad_positive = 0
    if th.require_positive_price:
        expr = pl.lit(False)
        for c in PRICE_COLS:
            expr = expr | (pl.col(c) <= 0)
        bad_positive = int(df.select(expr.alias("bad"))["bad"].sum() or 0)
    if bad_positive:
        out.append(QCIssue(dataset, symbol, "ohlc", "error",
                           f"{bad_positive} 行存在非正价格", bad_positive))

    if th.require_ohlc_bounds:
        bad_bounds = int(
            df.select(
                ((pl.col("high") < pl.col("low"))
                 | (pl.col("close") > pl.col("high") + 1e-6)
                 | (pl.col("close") < pl.col("low") - 1e-6)
                 | (pl.col("open") > pl.col("high") + 1e-6)
                 | (pl.col("open") < pl.col("low") - 1e-6)
                 ).alias("bad"))["bad"].sum() or 0)
        if bad_bounds:
            out.append(QCIssue(dataset, symbol, "ohlc", "error",
                               f"{bad_bounds} 行 OHLC 边界不自洽", bad_bounds))

    if th.require_non_negative_volume and "volume" in df.columns:
        bad_vol = int(df.select((pl.col("volume") < 0).alias("b"))["b"].sum() or 0)
        if bad_vol:
            out.append(QCIssue(dataset, symbol, "ohlc", "error",
                               f"{bad_vol} 行成交量为负", bad_vol))
    return out


def compute_factor_series(raw: pl.DataFrame, adj: pl.DataFrame) -> pl.DataFrame:
    """复权因子序列 date/raw_close/adj_close/factor/factor_chg。"""
    m = (raw.select(["date", "close"]).rename({"close": "raw_close"})
         .join(adj.select(["date", "close"]).rename({"close": "adj_close"}),
               on="date", how="inner")
         .filter(pl.col("raw_close") > 0).sort("date")
         .with_columns((pl.col("adj_close") / pl.col("raw_close")).alias("factor")))
    return m.with_columns(
        (pl.col("factor") / pl.col("factor").shift(1) - 1.0).alias("factor_chg"))


def detect_ex_div_dates(raw: pl.DataFrame, adj: pl.DataFrame,
                        th: QCThresholds) -> set[date]:
    """识别除权除息日（送股/转增/配股导致 raw 价格跳空）。

    判据：复权因子显著变化，且**后复权收益正常** —— 说明调整已正确补偿，
    raw 的跳空是真实公司行为，不是数据损坏。

    ⚠️ 这个判据很关键：直接对 raw 价格做涨跌幅异常检测会把所有除权日误报为损坏
    （实测 000028.SZ 2023-06-01 raw −27%、hfq 仅 −3.7%，是合法 10 送转）。
    """
    if raw.is_empty() or adj.is_empty():
        return set()
    m = compute_factor_series(raw, adj)
    m = m.with_columns(
        (pl.col("adj_close") / pl.col("adj_close").shift(1) - 1.0).abs().alias("_adj_r"))
    hit = m.filter((pl.col("factor_chg").abs() > 1e-6)
                   & (pl.col("_adj_r") <= th.max_abs_daily_return))
    return set(hit["date"].to_list())


def check_daily_return(
    df: pl.DataFrame,
    dataset: str,
    symbol: str,
    th: QCThresholds,
    is_st: bool = False,
    list_date: date | None = None,
    exempt_dates: set[date] | frozenset[date] | None = None,
) -> list[QCIssue]:
    """单日收益异常。ST、上市前 N 日、除权除息日分别放宽/豁免。

    :param exempt_dates: 已确认的除权除息日集合（raw 口径跳空属正常公司行为）。
    """
    if "close" not in df.columns or len(df) < 2:
        return []
    d = df.sort("date").with_columns(
        (pl.col("close") / pl.col("close").shift(1) - 1.0).abs().alias("_r"))
    if list_date is not None:
        d = d.with_columns(
            (pl.col("date") - list_date).dt.total_days().alias("_days"))
        d = d.with_columns(
            pl.when(pl.col("_days") >= th.new_issue_exempt_days)
            .then(pl.col("_r")).otherwise(None).alias("_r"))
    if exempt_dates:
        d = d.with_columns(
            pl.when(pl.col("date").is_in(list(exempt_dates)))
            .then(None).otherwise(pl.col("_r")).alias("_r"))
    limit = th.st_max_abs_daily_return if is_st else th.max_abs_daily_return
    bad = d.filter(pl.col("_r") > limit)
    if bad.is_empty():
        return []
    dates = tuple(str(x) for x in bad["date"].to_list()[:5])
    vals = [f"{v:.1%}" for v in bad["_r"].to_list()[:5]]
    return [QCIssue(dataset, symbol, "daily_return", "error",
                    f"{len(bad)} 个交易日涨跌超阈值 {limit:.0%}（ST={is_st}）"
                    f" 样例 {list(zip(dates, vals))}", len(bad), dates)]


def check_adjusted_continuity(
    adj: pl.DataFrame,
    dataset: str,
    symbol: str,
    th: QCThresholds,
    is_st: bool = False,
    list_date: date | None = None,
) -> list[QCIssue]:
    """后复权序列连续性：复权后的单日收益不应出现制度外跳变。

    这是**真正**的损坏判据 —— 复权已消除公司行为影响，
    若 hfq 收益仍超阈值，只可能是数据本身错了（合成数据 / raw 误写入 hfq / 基准漂移）。
    """
    return check_daily_return(adj, dataset, symbol, th, is_st, list_date,
                              exempt_dates=None)


def check_factor_jump(
    raw: pl.DataFrame,
    adj: pl.DataFrame,
    dataset: str,
    symbol: str,
    th: QCThresholds,
) -> list[QCIssue]:
    """复权因子（adj.close / raw.close）单日突变检测。

    主要捕捉两类事故：
    - raw 数据被误写入 hfq/qfq 分区 → 因子瞬变到 1.0；
    - 复权基准在不同批次间不一致（akshare 逐年抓取 hfq 的经典陷阱）。
    """
    if raw.is_empty() or adj.is_empty():
        return []
    m = compute_factor_series(raw, adj)
    if m.height < 2:
        return []
    m = m.with_columns(
        (pl.col("adj_close") / pl.col("adj_close").shift(1) - 1.0).abs().alias("_adj_r"))
    bad = m.filter(pl.col("factor_chg").abs() > th.max_factor_jump)
    if bad.is_empty():
        return []

    # 关键分级：复权后的收益是否仍然异常？
    #   正常 -> 合法公司行为（送转/配股），warn 留痕；
    #   异常 -> 复权未能补偿，是真损坏（raw 误写入 hfq / 基准漂移 / 合成数据），error。
    broken = bad.filter(pl.col("_adj_r") > th.max_abs_daily_return)
    legit = bad.filter(pl.col("_adj_r") <= th.max_abs_daily_return)
    out: list[QCIssue] = []
    if len(legit):
        # 两个 detail 结构不同（3 元组 vs 2 元组），故统一标注为 list[tuple[str, ...]]
        detail: list[tuple] = [
            (str(r["date"]), round(float(r["factor"]), 4))
            for r in legit.head(5).iter_rows(named=True)]
        out.append(QCIssue(
            dataset, symbol, "factor_jump", "warn",
            f"{len(legit)} 处因子突变但复权收益正常 -> 判定为除权除息（合法）{detail}",
            len(legit), tuple(str(x) for x in legit["date"].to_list()[:5])))
    if len(broken):
        detail = [(str(r["date"]), round(float(r["factor"]), 4),
                   f"adj_ret={r['_adj_r']:.1%}")
                  for r in broken.head(5).iter_rows(named=True)]
        out.append(QCIssue(
            dataset, symbol, "factor_jump", "error",
            f"{len(broken)} 处因子突变且复权收益异常 -> 判定为数据损坏{detail}",
            len(broken), tuple(str(x) for x in broken["date"].to_list()[:5])))
    return out


def check_calendar(
    df: pl.DataFrame,
    dataset: str,
    symbol: str,
    trade_days: frozenset[date] | set[date],
    th: QCThresholds,
) -> list[QCIssue]:
    """日期不在交易日历内 —— 合成/错误回补数据的强特征。"""
    if not th.check_calendar or "date" not in df.columns or not trade_days:
        return []
    bad = df.filter(~pl.col("date").is_in(list(trade_days)))
    if bad.is_empty():
        return []
    dates = tuple(str(x) for x in bad["date"].to_list()[:5])
    return [QCIssue(dataset, symbol, "calendar", "error",
                    f"{len(bad)} 个日期不在交易日历内（疑似合成数据）",
                    len(bad), dates)]


def check_year_density(
    df: pl.DataFrame, dataset: str, symbol: str, year: int, th: QCThresholds,
) -> list[QCIssue]:
    n = len(df)
    if n < th.min_year_rows:
        return [QCIssue(dataset, symbol, "year_density", "warn",
                        f"{year} 年仅 {n} 行（少于 {th.min_year_rows}，可能缺失/停牌）", n)]
    if n > th.max_year_rows:
        return [QCIssue(dataset, symbol, "year_density", "error",
                        f"{year} 年有 {n} 行（超过 {th.max_year_rows}，"
                        f"疑似含非交易日的合成数据）", n)]
    return []


# ---------------- 分区级 / 数据集级扫描 ----------------
def validate_partition(
    df: pl.DataFrame,
    dataset: str,
    symbol: str,
    year: int,
    th: QCThresholds = DEFAULT_THRESHOLDS,
    trade_days: frozenset[date] | set[date] | None = None,
    is_st: bool = False,
    list_date: date | None = None,
) -> list[QCIssue]:
    """对单个 (dataset, symbol, year) 分区执行全部检查。"""
    issues: list[QCIssue] = []
    if has_schema_drift(df, dataset):
        issues.append(QCIssue(
            dataset, symbol, "schema_drift", "error",
            f"列顺序/列集与 canonical 不符：{list(df.columns)} "
            f"!= {canonical_columns(dataset)}", len(df)))
    issues += check_nulls(df, dataset, symbol, th)
    issues += check_duplicates(df, dataset, symbol, th)
    issues += check_ohlc(df, dataset, symbol, th)
    issues += check_daily_return(df, dataset, symbol, th, is_st, list_date)
    issues += check_year_density(df, dataset, symbol, year, th)
    if trade_days is not None and th.check_calendar:
        issues += check_calendar(df, dataset, symbol, trade_days, th)
    return issues


def read_symbol_all(
    root: Path, dataset: str, symbol: str, normalize: bool = True,
) -> pl.DataFrame:
    """读取某标的在 dataset 下的全部分区，按需归一 schema 后按 date 升序拼接。"""
    base = root / dataset / f"symbol={symbol}"
    if not base.exists():
        return pl.DataFrame()
    frames = []
    for f in sorted(base.glob("year=*.parquet")):
        df = pl.read_parquet(f)
        if normalize:
            df = normalize_schema(df, dataset, symbol=symbol)
        frames.append(df)
    if not frames:
        return pl.DataFrame()
    return pl.concat(frames, how="vertical_relaxed").sort("date")


def scan_dataset(
    root: Path,
    dataset: str,
    th: QCThresholds = DEFAULT_THRESHOLDS,
    trade_days: frozenset[date] | set[date] | None = None,
    symbols: Iterable[str] | None = None,
    raw_dataset: str = "daily_bar",
) -> QCReport:
    """扫描整个 dataset 目录，返回 QCReport（不修改任何数据）。

    跨数据集联动：
    - 扫 ``daily_bar``（raw）时，用 hfq 推导除权除息日并豁免其 raw 跳空；
    - 扫 ``daily_bar_hfq`` 时，改用 ``check_adjusted_continuity``（复权后仍跳变才是真损坏）；
    - 两者都会跑 factor_jump 交叉校验。
    """
    report = QCReport(dataset=dataset)
    base = root / dataset
    if not base.exists():
        return report
    dirs = sorted(d for d in base.iterdir() if d.is_dir() and d.name.startswith("symbol="))
    if symbols is not None:
        wanted = set(symbols)
        dirs = [d for d in dirs if d.name.split("=", 1)[1] in wanted]
    report.n_symbols = len(dirs)

    for d in dirs:
        sym = d.name.split("=", 1)[1]
        cur = read_symbol_all(root, dataset, sym)
        if cur.is_empty():
            continue
        other_ds = "daily_bar" if dataset != "daily_bar" else "daily_bar_hfq"
        other = read_symbol_all(root, other_ds, sym)
        raw, adj = (cur, other) if dataset == "daily_bar" else (other, cur)

        ex_div = (detect_ex_div_dates(raw, adj, th)
                  if not raw.is_empty() and not adj.is_empty() else set())

        for f in sorted(d.glob("year=*.parquet")):
            try:
                year = int(f.name.split("=")[1].split(".")[0])
            except (IndexError, ValueError):
                continue
            df = pl.read_parquet(f)
            report.n_files += 1
            report.n_rows += len(df)
            issues = _partition_issues(df, dataset, sym, year, th, trade_days, ex_div)
            report.issues.extend(issues)

        if not raw.is_empty() and not adj.is_empty():
            report.issues.extend(check_factor_jump(raw, adj, dataset, sym, th))
    return report


def _partition_issues(
    df: pl.DataFrame,
    dataset: str,
    symbol: str,
    year: int,
    th: QCThresholds,
    trade_days: frozenset[date] | set[date] | None,
    ex_div: set[date],
) -> list[QCIssue]:
    """单分区检查：schema / null / dup / ohlc / 收益连续性 / 密度 / 日历。"""
    issues: list[QCIssue] = []
    if has_schema_drift(df, dataset):
        issues.append(QCIssue(
            dataset, symbol, "schema_drift", "error",
            f"列顺序/列集与 canonical 不符：{list(df.columns)}", len(df)))
    issues += check_nulls(df, dataset, symbol, th)
    issues += check_duplicates(df, dataset, symbol, th)
    issues += check_ohlc(df, dataset, symbol, th)
    if dataset == "daily_bar":
        issues += check_daily_return(df, dataset, symbol, th, exempt_dates=ex_div)
    else:
        issues += check_adjusted_continuity(df, dataset, symbol, th)
    issues += check_year_density(df, dataset, symbol, year, th)
    if trade_days is not None and th.check_calendar:
        issues += check_calendar(df, dataset, symbol, trade_days, th)
    return issues


def load_trade_days(sqlite_path: Path) -> frozenset[date]:
    """从 SQLite trade_calendar 表加载交易日集合；失败返回空集合（降级为不校验）。"""
    import sqlite3

    if not sqlite_path.exists():
        return frozenset()
    try:
        con = sqlite3.connect(sqlite_path)
        rows = con.execute("SELECT trade_date FROM trade_calendar").fetchall()
        con.close()
    except Exception as e:  # 日历不可用 -> 降级，不阻断
        logger.warning(f"trade_calendar 不可用，跳过日历校验: {e!r}")
        return frozenset()
    out: set[date] = set()
    for (v,) in rows:
        try:
            out.add(date.fromisoformat(str(v)[:10]))
        except ValueError:
            continue
    return frozenset(out)
