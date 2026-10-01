"""财务报表（PIT）：真实披露日（巨潮预约披露）+ 全市场财务数值（东财业绩报表）。

EODHD 财务表（P1-4 重写，2026-10-01 修复 PIT 缺陷）。

## 为什么重写
旧实现（sina ``stock_financial_analysis_indicator``）有两个致命问题：
1. **逐标的**：一次只能拉一只股票，全市场 5000+ 标的不可行；
2. **无公告日**：接口只给报告期（period），只能伪造 ``period + 45 天`` 当前视
   （``is_proxy_announce=True``）——45 天只是经验值，对提前披露或延迟披露的标的
   都会产生偏差（早披露 ⇒ 前视；晚披露 ⇒ 信息损失）。

## 新方案：披露日与数值分离，各自取最可靠的源
- **披露日** ← ``ak.stock_report_disclosure(market='沪深京', period='2024年报')``
  巨潮**预约披露**表，含 ``首次预约 / 初次变更 / 二次变更 / 三次变更 / 实际披露``。
  **按报告期整市场一次拉取**（~5400 行、1.3~3.1s），比逐标的快 3 个数量级。
  实测交叉验证：600519 贵州茅台 2024年报 → ``实际披露=2025-04-03``，与巨潮公告
  列表「贵州茅台2024年年度报告 2025-04-03」完全一致。
- **财务数值** ← ``ak.stock_yjbb_em(date=YYYYMMDD)`` 东财业绩报表（16 列，
  含每股收益/营业总收入/净利润/每股净资产/净资产收益率/销售毛利率）。
  同样按报告期整市场拉取（与披露表同构，join 成本极低）。

## PIT 红线：绝不使用 ``stock_yjbb_em`` 的「最新公告日期」
该列是**未来函数** —— 它是「该报告期财报截至现在的最后一次公告日期」，对同一
报告期会随新公告（更正/摘要/英文版）不断前移。实测 600519 各报告期返回
2025-04-30 / 2025-08-13 / 2025-10-30 / 2026-04-17 / 2026-04-25，均为真实披露日
**+1 年**（2024 年报：真实 2025-04-03，该列给 2026-04-17）。若采用会引入长达
12 个月的前视偏差，比旧的 +45 天代理值**更糟**。
⇒ 本模块只取 ``stock_yjbb_em`` 的**数值列**，日期一律以 ``stock_report_disclosure``
为准；join 键为 ``(股票代码, 报告期)``。

## announce_date 取值规则（PIT 语义）
- ``实际披露`` 非空 → 用它，``is_proxy_announce=False``，``announce_basis='actual'``
- 否则 → 用 ``首次预约``（事前即可知），``is_proxy_announce=True``，
  ``announce_basis='scheduled'``

⚠️ 时点 T 重放时，「实际披露」是**事后**才知道的；未披露期必须回落到事前可知的
「首次预约」，否则同样是未来函数。``scheduled`` 分支的正确定义见
``load_financials_asof`` 的双分支实现。
"""
from __future__ import annotations

import asyncio
from datetime import date, datetime

import pandas as pd
import polars as pl
from loguru import logger
from sqlalchemy.dialects.sqlite import insert as sqlite_insert

from ...db.models import FinancialReport
from ...db.session import get_session_factory

#: period 词表 → 报告期。**必须**按 akshare 源码硬编码字面量拼接：
#: 一季 / 半年报 / 三季（注意是「三季」不是「三季报」，用错会 KeyError）/ 年报。
PERIOD_SPECS: dict[str, tuple[int, int]] = {
    "一季": (3, 31),
    "半年报": (6, 30),
    "三季": (9, 30),
    "年报": (12, 31),
}

#: 默认拉取的年份（覆盖 3 年滚动回测窗口）
DEFAULT_YEARS: tuple[int, ...] = (2023, 2024, 2025)

SOURCE = "akshare_gildata"


def period_label(year: int, kind: str) -> str:
    """拼接巨潮 period 参数，如 ``2024年报`` / ``2024三季``。"""
    if kind not in PERIOD_SPECS:
        raise ValueError(f"非法报告期类型 {kind!r}，合法值：{list(PERIOD_SPECS)}")
    return f"{year}{kind}"


def period_end_date(year: int, kind: str) -> date:
    """报告期词表 → 报告期末日期（提前返回，无需 await 拉取）。"""
    m, d = PERIOD_SPECS[kind]
    return date(year, m, d)


def _to_date(series: pd.Series) -> pd.Series:
    """统一转 ``datetime.date``；NaT → None（保留「未披露」语义，不可 dropna）。"""
    dt = pd.to_datetime(series, errors="coerce")
    return dt.dt.date.where(dt.notna(), None)


def _norm_code(series: pd.Series) -> pd.Series:
    """6 位裸码 → 带交易所后缀的标准 symbol（600519 → 600519.SH）。"""
    code = series.astype(str).str.strip().str.zfill(6)

    def _suffix(c: str) -> str:
        if c.startswith(("60", "68", "51", "58", "11")):
            return f"{c}.SH"
        if c.startswith(("4", "8", "92")):  # 北交所
            return f"{c}.BJ"
        return f"{c}.SZ"

    return code.map(_suffix)


def _fetch_disclosure(period: str) -> pd.DataFrame:
    """拉取某一报告期的巨潮预约披露表（失败抛异常，由调用方降级处理）。"""
    import akshare as ak

    df = ak.stock_report_disclosure(market="沪深京", period=period)
    if df is None or df.empty:
        return pd.DataFrame()
    return df


def _fetch_indicators(period_end: date) -> pd.DataFrame:
    """拉取某报告期的东财业绩报表（仅用数值列，日期列一律丢弃）。"""
    import akshare as ak

    try:
        df = ak.stock_yjbb_em(date=period_end.strftime("%Y%m%d"))
    except Exception as e:  # noqa: BLE001 - 数值源失败不应中断整体
        logger.warning(f"[financials] 业绩报表 {period_end} 拉取失败，仅保留披露日：{e!r}")
        return pd.DataFrame()
    if df is None or df.empty:
        return pd.DataFrame()
    return df


def _build_period_frame(
    year: int, kind: str, disc: pd.DataFrame, ind: pd.DataFrame
) -> pd.DataFrame:
    """把「披露表」与「业绩报表」按 (代码,报告期) join，输出单期标准行。"""
    p_end = period_end_date(year, kind)

    if disc.empty:
        return pd.DataFrame()

    out = pd.DataFrame({
        "symbol": _norm_code(disc["股票代码"]),
        "period": p_end,
        "scheduled": _to_date(disc["首次预约"]),
        "actual": _to_date(disc["实际披露"]),
    })
    out = out.dropna(subset=["scheduled"])  # 首次预约实测 100% 非空
    out = out.drop_duplicates(subset=["symbol", "period"], keep="first")

    # --- 财务数值（可选）：以裸码 join，只取数值列 ---
    if not ind.empty:
        code = ind["股票代码"].astype(str).str.strip().str.zfill(6)
        vals = pd.DataFrame({"code": code, "period": p_end})
        col_map = {
            "每股收益": "eps",
            "营业总收入-营业总收入": "revenue",
            "净利润-净利润": "net_profit",
            "净资产收益率": "roe",
            "销售毛利率": "gross_margin",
            "每股净资产": "bps",
        }
        for src, dst in col_map.items():
            vals[dst] = (pd.to_numeric(ind[src], errors="coerce")
                         if src in ind.columns else None)
        vals = vals.drop_duplicates(subset=["code", "period"], keep="first")
        out["code"] = out["symbol"].str.split(".").str[0]
        out = out.merge(vals, on=["code", "period"], how="left").drop(columns=["code"])

    for c in ("eps", "revenue", "net_profit", "roe", "gross_margin", "bps"):
        if c not in out.columns:
            out[c] = None

    # --- announce_date：实际披露优先，缺失回落首次预约 ---
    has_actual = out["actual"].notna()
    out["announce_date"] = out["actual"].where(has_actual, out["scheduled"])
    out["is_proxy_announce"] = ~has_actual
    out["announce_basis"] = has_actual.map({True: "actual", False: "scheduled"})
    out["source"] = SOURCE
    return out


def fetch_financials(
    period: str | list[str] | None = None,
    symbol: str | None = None,
    years: tuple[int, ...] = DEFAULT_YEARS,
) -> pl.DataFrame:
    """拉取全市场财务 PIT 记录（按报告期）。

    Args:
        period: 单个 period 词（如 ``"2024年报"``）或词列表；缺省则展开
            ``years`` × {一季, 半年报, 三季, 年报} 共 4N 期。
        symbol: **兼容参数**（旧签名 ``fetch_financials(symbol, start_year)``）。
            传入时在返回结果上按 symbol 过滤——但依然会整市场拉取。
            逐标的过滤会让全市场同步变慢 3 个数量级，故仅作兼容保留，新代码
            请勿传入。
        years: 当 ``period`` 为 None 时使用的年份列表。

    Returns:
        列：symbol, period, announce_date, is_proxy_announce, announce_basis,
        revenue, net_profit, roe, eps, gross_margin, bps, source。

    **单期失败不中断整体**：任一报告期拉取/解析异常只记日志并计入
    ``failed``，其余期照常返回。
    """
    if period is None:
        periods = [period_label(y, k) for y in years for k in PERIOD_SPECS]
    elif isinstance(period, str):
        periods = [period]
    else:
        periods = list(period)

    frames: list[pd.DataFrame] = []
    failed: list[str] = []
    for p in periods:
        # period 形如 "2024年报" → (2024, "年报")
        try:
            year_s, kind = p[:4], p[4:]
            year = int(year_s)
            p_end = period_end_date(year, kind)
        except (ValueError, KeyError) as e:
            logger.error(f"[financials] 非法 period {p!r}：{e!r}")
            failed.append(p)
            continue
        try:
            disc = _fetch_disclosure(p)
            ind = _fetch_indicators(p_end)
            n = 0 if disc.empty else len(disc)
            has_actual = 0 if disc.empty else int(
                _to_date(disc["实际披露"]).notna().sum())
            f = _build_period_frame(year, kind, disc, ind)
            logger.info(
                f"[financials] {p}: 披露表={n} 行 实际披露={has_actual} "
                f"业绩报表={'空' if ind.empty else len(ind)} 行 → 入库候选={len(f)}")
            frames.append(f)
        except Exception as e:  # noqa: BLE001 - 单期失败不得中断整体（明确要求）
            logger.error(f"[financials] {p} 拉取失败，跳过该期：{e!r}")
            failed.append(p)

    if failed:
        logger.warning(f"[financials] 共 {len(failed)} 期失败：{failed}")

    frames = [f for f in frames if not f.empty]
    if not frames:
        return pl.DataFrame()
    df = pd.concat(frames, ignore_index=True)

    if symbol:
        df = df[df["symbol"] == symbol]
        if df.empty:
            logger.warning(f"[financials] 过滤 symbol={symbol} 后无记录")

    return pl.from_pandas(df)


def save_financials(df: pl.DataFrame) -> int:
    """写入 financial_report（幂等 upsert）。

    ``on_conflict`` 语义：唯一键 ``(symbol, period, announce_date)`` 命中时
    **更新**（而非 do_nothing）——同一报告期在「预约期」先落 ``scheduled`` 行，
    实际披露后 ``announce_date`` 变化会新增一行，但若运营商再次同步同一份数据，
    数值列（revenue/net_profit/...）应被最新值覆盖，避免旧快照残留。
    同时 ``announce_basis`` 等新增列一并纳入 update 集合。
    """
    rows = df.to_dicts()
    if not rows:
        return 0
    ins = sqlite_insert(FinancialReport)
    update_cols = [
        "is_proxy_announce", "announce_basis", "revenue", "net_profit",
        "total_assets", "total_liability", "roe", "roa", "eps",
        "gross_margin", "bps", "source",
    ]
    stmt = ins.on_conflict_do_update(
        index_elements=["symbol", "period", "announce_date"],
        set_={c: getattr(ins.excluded, c) for c in update_cols},
    )

    async def _go() -> None:
        factory = get_session_factory()
        async with factory() as sess:
            await sess.execute(stmt, rows)
            await sess.commit()

    asyncio.run(_go())
    return len(rows)


def load_financials_asof(symbol: str, asof: date) -> pl.DataFrame:
    """PIT 读取：仅 ``announce_date <= asof`` 的记录可见（未来财报绝对不可见）。

    双分支语义（关键，勿合并成单条件）：
    - ``is_proxy_announce=0``（``announce_basis='actual'``）：``announce_date``
      即真实披露日，``<= asof`` 直接可见。
    - ``is_proxy_announce=1``（``announce_basis='scheduled'``）：``announce_date``
      是**预约日**，在预约日当天财报尚未真正披露 ⇒ 必须再确认在 ``asof`` 之前
      该期是否已有实际披露记录；若已有（说明实际披露早于预约），以实际披露为准。
    """
    import sqlite3

    from ...core.config import get_settings

    conn = sqlite3.connect(get_settings().SQLITE_PATH, timeout=30)
    try:
        rows = conn.execute(
            "SELECT f.period, f.announce_date, f.revenue, f.net_profit, f.roe, "
            "f.roa, f.eps, f.is_proxy_announce, f.announce_basis, f.gross_margin, f.bps "
            "FROM financial_report f "
            "WHERE f.symbol = ? AND f.announce_date <= ? "
            "  AND (f.is_proxy_announce = 0 OR NOT EXISTS ( "
            "        SELECT 1 FROM financial_report a "
            "        WHERE a.symbol = f.symbol AND a.period = f.period "
            "          AND a.is_proxy_announce = 0 AND a.announce_date <= ?)) "
            "ORDER BY f.period",
            (symbol, asof.isoformat(), asof.isoformat())).fetchall()
    finally:
        conn.close()
    if not rows:
        return pl.DataFrame()
    df = pl.DataFrame(rows, orient="row", schema=[
        "period", "announce_date", "revenue", "net_profit", "roe", "roa", "eps",
        "is_proxy_announce", "announce_basis", "gross_margin", "bps"])
    return df.with_columns(pl.col("period").str.to_date(),
                           pl.col("announce_date").str.to_date())


def coverage_summary(df: pl.DataFrame) -> dict:
    """数据质量摘要（announce_basis 分布 / 数值覆盖 / 期间范围）。

    因为真正的数据缺口只有落库后才能发现（接口静默返回空表是本项目已踩过的
    坑：``fetch_announcements`` 曾静默 0 行），这里提供显式体检入口。
    """
    if df.is_empty():
        logger.error("[financials] coverage_summary 收到空表 —— 疑似上游静默失败")
        return {"rows": 0, "periods": 0, "actual_ratio": 0.0}

    n = df.height
    actual = df.filter(~pl.col("is_proxy_announce")).height
    out = {
        "rows": n,
        "symbols": df["symbol"].n_unique(),
        "periods": df["period"].n_unique(),
        "actual_ratio": round(actual / n, 4),
        "period_min": str(df["period"].min()),
        "period_max": str(df["period"].max()),
    }
    for col in ("revenue", "net_profit", "roe", "eps", "gross_margin", "bps"):
        if col in df.columns:
            out[f"{col}_fill"] = round(df[col].is_not_null().sum() / n, 4)
    out["_ts"] = datetime.now().isoformat(timespec="seconds")
    logger.info(f"[financials] coverage: {out}")
    return out
