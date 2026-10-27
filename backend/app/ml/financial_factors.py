"""PIT financial features: actual disclosure only, one-day availability lag.

The production v3 feature builder consumes this block. Scheduled disclosure dates
never make numeric reports visible. Event-level revisions and year-on-year
comparisons use only versions disclosed by that event.
"""
from __future__ import annotations

import json
import os
from collections.abc import Callable, Iterable
from datetime import date, datetime
from typing import Any

import numpy as np
import pandas as pd
from loguru import logger

# 数据注入点：生产用 data 层的 PIT 读取，测试可直接注入 DataFrame。
# 返回 DataFrame[symbol, period, announce_date, revenue, net_profit, roe, roa,
# eps, is_proxy_announce]；``announce_date`` / ``period`` 可为 date 或 str。
FinancialSource = Callable[[str, date], Any]

ON = "1"

# 财务因子块的前缀。统一前缀的原因：①``apply_propagate`` 会把
# ``factor_columns(df)`` 全量传播成 ``g1_*``（graph.py:290 的 ``cols`` 参数），
# 不加前缀会让 g1_ 列数再翻倍；②``factor_columns`` 无白名单，任何桶级聚合
# （如按行业取中位数）都必须显式 ``startswith("f_")`` 才能只取财务列，
# 避免与 ``fv_``（features_v2 的价量列）混同。
FACTOR_PREFIX = "f_"
#: 三类财务因子列名（roe / 营收同比 / 营收规模），共 3 列。
FINANCIAL_FACTOR_COLUMNS: tuple[str, ...] = (
    "f_roe", "f_revenue_yoy", "f_log_revenue",
    "f_eps", "f_bps", "f_gross_margin", "f_net_margin", "f_debt_ratio", "f_roe_change",
)
#: 面板日期列转 datetime 后比对 asof（date 与 str 混用会抛 TypeError）
_ASOF_DATE_FORMAT = "%Y-%m-%d"


def financial_factors_enabled() -> bool:
    """财务因子块是否启用（环境变量 ``FEATURE_FINANCIAL``，默认关闭）。

    ⚠️ 默认关闭是**数据源现状**决定的，不是保守：见模块 docstring 的
    "oracle 陷阱"。打开前请先跑 ``tests/test_financial_factors.py`` 的
    oracle 守卫确认真实公告日已生效。
    """
    return os.environ.get("FEATURE_FINANCIAL", "1") == ON


# ---------------- 数据源 ----------------
def _default_source(symbol: str, asof: date) -> pd.DataFrame:
    """默认数据源：``load_financials_asof``（PIT：announce_date <= asof）。

    ``asof`` 取面板最大日期而非构建当天：面板是历史宽表（可能含多年），
    必须让"最晚一期财报"也能在正确的 K 线日之后生效；用 today 会让全部
    历史日期都能看见最新财报（= 前视），是比"晚 1 天"严重得多的问题。
    """
    from ..data.ingest.financials import load_financials_asof

    df = load_financials_asof(symbol, asof)
    if df is None or len(df) == 0:
        return pd.DataFrame(columns=["period", "announce_date", "revenue",
                                     "roe", "is_proxy_announce"])
    return df.to_pandas()


def _normalize(fin: pd.DataFrame) -> pd.DataFrame:
    """统一数据源口径：日期转 datetime、数值转 float、按 (announce, period) 升序。

    返回列：``announce_date`` / ``period``(NA 表示报告期缺失) / ``revenue`` /
    ``roe``。缺列（mock 数据源只给部分字段）时补 NaN，不静默改语义。
    """
    out = pd.DataFrame(index=fin.index)
    out["announce_date"] = pd.to_datetime(fin["announce_date"], errors="coerce")
    if "period" in fin.columns:
        out["period"] = pd.to_datetime(fin["period"], errors="coerce")
    else:
        out["period"] = pd.NaT
    # 统一到 datetime64[ns]：面板日期来自 parquet（datetime64[ms] / Date），
    # 与 to_datetime 默认的 ns 分辨率不一致会让 merge_asof 直接抛 MergeError
    # （真实数据实测，而非理论可能）。
    for col in ("announce_date", "period"):
        if out[col].dtype != "datetime64[ns]":
            out[col] = out[col].astype("datetime64[ns]")
    for col in ("revenue", "roe", "eps", "bps", "gross_margin", "net_profit", "total_assets", "total_liability"):
        src = fin[col] if col in fin.columns else pd.Series(np.nan, index=fin.index)
        out[col] = pd.to_numeric(src, errors="coerce").replace([np.inf, -np.inf], np.nan)
    # 公告日缺失的行无法判定可见性 ⇒ 整行丢弃（保守：宁可缺失不可猜）
    n_bad = int(out["announce_date"].isna().sum())
    if n_bad:
        logger.warning(f"[fin_factors] 丢弃 {n_bad} 行 announce_date 缺失的财报记录")
        out = out[out["announce_date"].notna()]
    if "is_proxy_announce" in fin:
        out = out.loc[~fin.loc[out.index, "is_proxy_announce"].fillna(True).astype(bool)]
    out["announce_date"] += pd.Timedelta(days=1)
    # period 缺失/非法时置为哨兵 NaT（不影响 asof 取数，仅同比会得 NaN）
    return out.sort_values(["announce_date", "period"], na_position="first")


def _revenue_yoy(vis: pd.DataFrame) -> np.ndarray:
    """营收同比：本期 / 去年同期(报告期减 1 年) - 1。

    以**报告期**（而非公告日）判定"去年同期"：只接受恰好早一个自然年的
    同季数据，缺项/跨年错配 => NaN（宁缺勿错）。
    """
    period = vis["period"]
    rev_year = period.dt.year
    want_year = (rev_year - 1).astype("Int64")
    # 目标行的自然键：(want_year, 本期月日)。月日必须一致才认作"去年同期"。
    key_cur = pd.MultiIndex.from_arrays([want_year, period.dt.month, period.dt.day])
    key_all = pd.MultiIndex.from_arrays([rev_year, period.dt.month, period.dt.day])
    lookup: dict[tuple, float] = {}
    for k, v in zip(key_all, vis["revenue"].to_numpy(dtype="float64"), strict=True):
        if not any(pd.isna(x) for x in k):
            lookup[(int(k[0]), int(k[1]), int(k[2]))] = float(v)
    prev = np.array([lookup.get(tuple(k), np.nan) if not any(pd.isna(x) for x in k)
                     else np.nan for k in key_cur], dtype="float64")
    cur = vis["revenue"].to_numpy(dtype="float64")
    # 分母 <= 0（巨亏/异常）或本期 <= 0：比值无经济含义，置 NaN
    ok = (prev > 0) & (cur > 0) & np.isfinite(prev) & np.isfinite(cur)
    yoy = np.where(ok, cur / np.where(ok, prev, 1.0) - 1.0, np.nan)
    # 单期异常（|yoy|>50 倍）视为数据噪声，置 NaN 而非截断（不造数）
    yoy = np.where(np.abs(yoy) > 50.0, np.nan, yoy)
    return yoy


def _build_symbol_frame(fin: pd.DataFrame, dates: pd.Series) -> pd.DataFrame:
    """单只标的：asof join 到 ``dates``（已升序去重的 K 线日）。"""
    empty = pd.DataFrame(
        {c: np.full(len(dates), np.nan, dtype="float64")
         for c in FINANCIAL_FACTOR_COLUMNS}, index=dates.index)
    if fin.empty or len(dates) == 0:
        return empty

    vis = _normalize(fin)
    if vis.empty:
        return empty
    known = {}
    events = []
    last_roe = np.nan
    for _, record in vis.iterrows():
        known[record["period"]] = record
        periods = [period for period in known if not pd.isna(period)]
        current = known[max(periods)] if periods else record
        event = current.copy()
        event["announce_date"] = record["announce_date"]
        previous = known.get(current["period"] - pd.DateOffset(years=1)) if not pd.isna(current["period"]) else None
        event[FACTOR_PREFIX + "revenue_yoy"] = (
            current["revenue"] / previous["revenue"] - 1
            if previous is not None and previous["revenue"] > 0 and current["revenue"] > 0 else np.nan)
        event["roe_change"] = current["roe"] - last_roe
        last_roe = current["roe"]
        events.append(event)
    vis = pd.DataFrame(events)

    # ---- asof join：对每根 K 线日 d 取 announce_date <= d 的最后一期 ----
    # merge_asof 要求两侧同序；同 announce_date 多期时取序列里靠后的（period
    # 升序 ⇒ 报告期最新），"同日多期"不会随机取到旧的一期。
    left = pd.DataFrame({"date": pd.to_datetime(dates.to_numpy())}).sort_values("date")
    if left["date"].dtype != "datetime64[ns]":
        left["date"] = left["date"].astype("datetime64[ns]")
    merged = pd.merge_asof(
        left, vis,
        left_on="date", right_on="announce_date", direction="backward",
        allow_exact_matches=True,
    )
    merged = merged.drop_duplicates(subset=["date"], keep="last")
    merged = merged.set_index("date").reindex(pd.to_datetime(dates.to_numpy()))

    revenue = merged["revenue"].to_numpy(dtype="float64")
    # log 规模：营收 <= 0（异常）或缺失 => NaN（log 非正数无定义）
    log_rev = np.where(np.isfinite(revenue) & (revenue > 0),
                       np.log(np.where(revenue > 0, revenue, 1.0)), np.nan)

    return pd.DataFrame(
        {
            "f_roe": merged["roe"].to_numpy(dtype="float64"),
            "f_revenue_yoy": merged[FACTOR_PREFIX + "revenue_yoy"]
            .to_numpy(dtype="float64"),
            "f_log_revenue": log_rev,
            "f_eps": merged["eps"].to_numpy(dtype="float64"),
            "f_bps": merged["bps"].to_numpy(dtype="float64"),
            "f_gross_margin": merged["gross_margin"].to_numpy(dtype="float64"),
            "f_net_margin": (merged["net_profit"] / merged["revenue"].where(merged["revenue"] > 0)).to_numpy(dtype="float64"),
            "f_debt_ratio": (merged["total_liability"] / merged["total_assets"].where(merged["total_assets"] > 0)).to_numpy(dtype="float64"),
            "f_roe_change": merged["roe_change"].to_numpy(dtype="float64"),
        },
        index=dates.index,
    )


def _iter_groups(panel: pd.DataFrame) -> Iterable[tuple[str, pd.Series]]:
    """按 symbol 分组，返回 (symbol, 升序且 index 保留原位的 date Series)。"""
    for sym, g in panel.groupby("symbol", sort=False):
        dates = pd.to_datetime(g["date"])
        yield str(sym), dates.sort_values()


def build_financial_factors(
    panel: pd.DataFrame,
    *,
    source: FinancialSource | None = None,
    enabled: bool | None = None,
) -> pd.DataFrame:
    """为因子面板追加财务因子列（``f_roe`` / ``f_revenue_yoy`` / ``f_log_revenue``）。

    实测覆盖率（2026-10-01，真实库 10776 行 / 面板 1148 交易日）：

    ===============  ==================  ==========================================
    因子             实测有值比例        当前瓶颈
    ===============  ==================  ==========================================
    ``f_roe``        ~33%（仅尾部有值）  财报同步只覆盖 2024Q4 起 ⇒ 面板前 2/3 为空
    ``f_log_revenue``~33%（同上）        同 ``f_roe``
    ``f_revenue_yoy``**0%（恒 NaN）**    库里只有 2024-12-31 / 2025-06-30 两个报告期，
                                         **不存在**去年同期（2024-06-30）⇒ 分母恒缺失
    ===============  ==================  ==========================================

    ⚠️ 列名稳定存在、值为 NaN（与 ``apply_propagate`` 的"无数据补 NaN 列"同一纪律），
    下游按列名取用不会 KeyError。

    Args:
        panel: ``build_factors`` 产出面板，须含 ``symbol`` / ``date``。
        source: 数据源 ``(symbol, asof) -> DataFrame``；缺省走
            ``load_financials_asof``（PIT）。测试注入手工 DataFrame。
        enabled: 显式覆盖开关；缺省读 ``FEATURE_FINANCIAL`` 环境变量。

    Returns:
        与 ``panel`` **同长度同索引**的 DataFrame，列 = FINANCIAL_FACTOR_COLUMNS。
        未启用 / 无数据时逐列全 NaN。
    """
    cols = list(FINANCIAL_FACTOR_COLUMNS)
    if panel is None or len(panel) == 0:
        return pd.DataFrame(columns=cols)
    missing = {"symbol", "date"} - set(panel.columns)
    if missing:
        raise ValueError(f"财务因子面板缺失列: {sorted(missing)}")

    is_on = financial_factors_enabled() if enabled is None else enabled
    if not is_on:
        logger.debug("[fin_factors] FEATURE_FINANCIAL 未启用 ⇒ 全 NaN 列（列名稳定）")
        return pd.DataFrame({c: np.nan for c in cols}, index=panel.index)

    src: FinancialSource = source or _default_source
    asof_max = pd.to_datetime(panel["date"]).max().date()
    parts: list[pd.DataFrame] = []
    n_cov = 0
    for sym, dates in _iter_groups(panel):
        try:
            fin = src(sym, asof_max)
        except Exception as e:  # noqa: BLE001 单只失败不拖垮整表（与 build_factors 一致）
            logger.warning(f"[fin_factors] 读取失败 symbol={sym}: {e!r}")
            fin = pd.DataFrame()
        if fin is None:
            fin = pd.DataFrame()
        blk = _build_symbol_frame(fin, dates)
        if blk[cols].notna().any().any():
            n_cov += 1
        parts.append(blk)

    out = pd.concat(parts) if parts else pd.DataFrame(columns=cols)
    out = out.reindex(panel.index)
    n_any = int(out[cols].notna().any(axis=1).sum())
    logger.info(f"[fin_factors] 财务因子构建完成: symbols={len(parts)}, "
                f"有值标的={n_cov}, 有值行={n_any}/{len(out)}")
    if n_any == 0 and len(out):
        # 生产全 NaN 几乎必然是 oracle 陷阱（代理公告日）而非"确实没财报"
        logger.warning(
            "[fin_factors] 财务因子全为 NaN：若 financial_report 有数据，"
            "说明 announce_date 仍为「报告期+45 天」代理值 —— A 股法定截止日 4/30 "
            "= Q1 报告期+30 天，公告日在结构上晚于截止日 ⇒ asof join 永不命中"
            "（oracle 陷阱，见 financial_factors.py docstring）")
    return out


def financial_factors_provenance() -> str:
    """财务因子的口径摘要（随特征血缘落盘，供事后追溯）。

    只登记**诊断统计**（行数、代理比例、因子覆盖率）与口径常量，不副本体。
    原因：代理比例是判断"oracle 陷阱是否已解除"的唯一硬信号，
    事后追"这批特征是不是喂了脏数据"必须能在不重跑流水线的前提下复查。
    """
    import sqlite3

    from ..core.config import get_settings
    from ..data.ingest.financials import SOURCE as _FIN_SOURCE

    meta: dict[str, Any] = {
        "factor_columns": list(FINANCIAL_FACTOR_COLUMNS),
        "enabled": financial_factors_enabled(),
        "pit_rule": "announce_date <= kline_date（同日可见）",
        # 2026-10-01 重写：不再有 period+45d 代理日；announce_date 取自巨潮
        # 预约披露（actual=实际披露 / scheduled=首次预约回落），故代理天数
        # 概念已废弃，保留字段为 None 以兼容既有 meta 消费者。
        "proxy_announce_days": None,
        "announce_source": _FIN_SOURCE,
        "oracle_risk_rule": "announce_date > 季末+30d(Q1)/+60d(H1)/+90d(Q3)/+120d(FY) 视为 oracle",
        "generated_at": datetime.now().isoformat(timespec="seconds"),
    }
    try:
        conn = sqlite3.connect(get_settings().SQLITE_PATH, timeout=30)
        try:
            rows = conn.execute(
                "SELECT period, announce_date FROM financial_report").fetchall()
        finally:
            conn.close()
        n = len(rows)
        n_oracle = 0
        for period, announce in rows:
            try:
                p = date.fromisoformat(str(period)[:10])
                a = date.fromisoformat(str(announce)[:10])
            except ValueError:
                continue
            # 法定披露截止日：Q1/H1/Q3/FY 分别 +30/+60/+90/+120 天
            deadline = {3: 30, 6: 60, 9: 90, 12: 120}.get(p.month)
            if deadline is None:
                continue
            if (a - p).days > deadline:
                n_oracle += 1
        meta["rows"] = n
        meta["oracle_rows"] = n_oracle
        meta["oracle_ratio"] = round(n_oracle / n, 4) if n else None
    except Exception as e:  # noqa: BLE001 血缘信息尽力而为，不阻断流水线
        meta["error"] = f"{type(e).__name__}: {e}"
    return json.dumps(meta, ensure_ascii=False)
