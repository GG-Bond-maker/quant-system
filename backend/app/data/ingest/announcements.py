"""公告事件标签（P1-6）：巨潮 cninfo 全市场公告 -> 关键词规则分类 -> announcements.parquet。

情绪标签为【可解释的关键词规则】（非随机/非模型黑箱）：
    positive: 回购 / 增持 / 业绩预增 / 中标 / 分红
    negative: 减持 / 质押 / 业绩预亏 / 处罚 / 立案
    neutral : 其余
PIT 红线：pub_date 是唯一可见时间轴，T 日模型只能看到 pub_date <= T 的公告。

取值源选型（2026-09-30 修复「近期事件停在 2024-06」）
------------------------------------------------------
- **回补/首次取数用巨潮 cninfo**（``fetch_announcements_range``）：``ak.
  stock_zh_a_disclosure_report_cninfo(symbol="", start_date=, end_date=)`` 支持
  **全市场 × 任意历史区间** 一次拉取，是本项目唯一可行的历史回补路径。
- **东财 ``ak.stock_notice_report`` 不适合回补**（故 ``fetch_announcements`` 已被
  取代，仅保留作单标的/兼容用途）：其 ``date`` 是**单披露日全市场快照**，
  给不出历史全量；且 ``symbol`` 是**封闭枚举**（只能传 ``"全部"/"财务报告"`` 等
  分类，传 ``"回购公告"`` 会 ``KeyError``），无法按标的/时间段回溯。
"""
from __future__ import annotations

from datetime import date

import polars as pl

from ...domain.a_share_rules import code_to_symbol

POSITIVE = ("回购", "增持", "业绩预增", "预增", "中标", "分红", "净利润增长")
NEGATIVE = ("减持", "质押", "业绩预亏", "预亏", "处罚", "立案", "诉讼", "冻结")
EVENT_TYPES = ("股权激励", "减持", "回购", "业绩预告")


def classify(title: str) -> tuple[str, str]:
    """(event_type, sentiment) —— 纯函数、可解释、可单测。"""
    event = "其他"
    if "激励" in title:
        event = "股权激励"
    elif "减持" in title:
        event = "减持"
    elif "回购" in title:
        event = "回购"
    elif any(k in title for k in ("业绩预告", "预增", "预亏", "业绩")):
        event = "业绩预告"
    if any(k in title for k in NEGATIVE):
        return event, "negative"
    if any(k in title for k in POSITIVE):
        return event, "positive"
    return event, "neutral"


def _build_frame(raw: "pl.DataFrame", source: str) -> pl.DataFrame:
    """把已规范化为 ``title`` / ``pub_date`` / ``symbol`` / ``url`` 列的表打标并定序。

    共用给单标的（``fetch_announcements``）与区间全市场（``fetch_announcements_range``）
    两条路径，保证落盘 schema / 列序 / 打标规则**同源**（避免两处漂移）。
    """
    out = raw
    tags = [classify(str(t)) for t in out["title"].to_list()]
    out = out.with_columns([
        pl.Series("type", [t[0] for t in tags], dtype=pl.String),
        pl.Series("sentiment", [t[1] for t in tags], dtype=pl.String),
    ])
    if "url" not in out.columns:
        out = out.with_columns(pl.lit(None, dtype=pl.String).alias("url"))
    out = out.with_columns(pl.lit(source, dtype=pl.String).alias("source"))
    # 统一列序（存储事实源口径，见 data/announcements.py 模块 docstring）
    return out.select(["symbol", "pub_date", "title", "type", "sentiment", "source", "url"])


def fetch_announcements_all_market(day: str) -> pl.DataFrame:
    """拉取**单个披露日 × 全市场**公告（东财 ``stock_notice_report(symbol="全部")``）。

    与 :func:`fetch_announcements` 的区别：后者按**单只标的**过滤（``symbol`` 需为
    真实代码），本函数**不做标的过滤**，直接保留该披露日全市场所有公告，并把每行的
    真实代码经 :func:`domain.a_share_rules.code_to_symbol` 标准化为 ``symbol`` 列。

    为什么需要它：``step_sync_announcements`` 要的是「近期全市场公告」用于个股
    「近期事件」块；若误用 ``fetch_announcements("__all__", ...)``，会因
    ``raw_code == "__all__"`` 永不匹配而**静默返回空表**（2026-09-30 实测踩坑）。

    Args:
        day: 披露日，``YYYY-MM-DD`` 或 ``YYYYMMDD``。

    Returns:
        列序 ``[symbol, pub_date, title, type, sentiment, source, url]``；
        无数据 → 空表。非 6 位数字代码的行被丢弃（无法映射交易所后缀）。
    """
    import akshare as ak

    raw = ak.stock_notice_report(symbol="全部", date=day.replace("-", ""))
    if raw is None or raw.empty:
        return pl.DataFrame()
    rename = {"公告标题": "title", "公告日期": "pub_date", "公告类型": "category",
              "代码": "raw_code", "网址": "url", "公告链接": "url"}
    raw = raw.rename(columns={k: v for k, v in rename.items() if k in raw.columns})
    if "title" not in raw.columns:
        raise ValueError(f"公告字段漂移: {list(raw.columns)[:8]}")
    if "raw_code" not in raw.columns:
        raise ValueError(f"公告字段漂移（缺代码列）: {list(raw.columns)[:8]}")

    raw = raw.copy()
    # 真实公告日期（PIT 红线：绝不覆盖成入参 day）。
    raw["pub_date"] = (raw["pub_date"].astype(str).str.slice(0, 10)
                       if "pub_date" in raw.columns else None)
    raw["raw_code"] = raw["raw_code"].astype(str).str.replace(r"\D", "", regex=True).str.zfill(6)
    raw = raw[raw["raw_code"].str.match(r"^\d{6}$", na=False)]
    if raw.empty:
        return pl.DataFrame()

    cols = [c for c in ("raw_code", "pub_date", "title", "url") if c in raw.columns]
    out = pl.from_pandas(raw[cols])
    out = out.with_columns(
        pl.col("pub_date").cast(pl.String, strict=False).str.slice(0, 10)
        .str.to_date(strict=False).alias("pub_date"),
        pl.col("raw_code").cast(pl.String, strict=False),
    )
    out = out.with_columns(
        pl.col("raw_code").map_elements(code_to_symbol, return_dtype=pl.String).alias("symbol")
    ).drop("raw_code")
    out = _build_frame(out, "eastmoney")
    # 同一披露日内同一公告可能重复（接口偶发重页），按 4 元组去重。
    return out.unique(subset=["symbol", "title", "pub_date", "url"], keep="last")


def fetch_announcements(symbol: str, start: str, end: str) -> pl.DataFrame:
    """⚠️ 已被 :func:`fetch_announcements_range` 取代，仅供单标的场景/兼容测试；
    历史回补请用 ``fetch_announcements_range``（东财源做不到历史全量，见模块 docstring）。

    拉取**单只**标的公告（东方财富 ``stock_notice_report``），应用可解释规则打标。

    本次修复的 3 个 bug（2026-09-30）：
        1. 原 ``:51`` 用**裸 6 位码匹配公告标题** —— A 股公告标题基本不含自己的代码，
           实测命中率 0.0%。改为匹配**代码列**（兼容列名 ``代码``，缺失时退化为标题匹配）。
        2. 原 ``:54-55`` 把 ``pub_date`` 覆盖成入参 ``start`` —— 破坏 PIT 红线。
           改为使用接口返回的**真实公告日期**列。
        3. 原 ``rename`` 丢掉了 ``代码`` 列。此处显式保留。

    Args:
        symbol: 标准标的（如 ``600519.SH``），用于过滤与打标。
        start: 拉取日（``YYYY-MM-DD`` 或 ``YYYYMMDD``）。
        end: 结束日（东财单日快照口径下与 ``start`` 同义，仅为签名兼容保留）。

    Returns:
        列序 ``[symbol, pub_date, title, type, sentiment, source, url]``；无数据 → 空表。
    """
    import akshare as ak

    code = symbol.split(".")[0]
    raw = ak.stock_notice_report(symbol="全部", date=start.replace("-", ""))
    if raw is None or raw.empty:
        return pl.DataFrame()
    rename = {"公告标题": "title", "公告日期": "pub_date", "公告类型": "category",
              "代码": "raw_code", "网址": "url", "公告链接": "url"}
    raw = raw.rename(columns={k: v for k, v in rename.items() if k in raw.columns})
    if "title" not in raw.columns:
        raise ValueError(f"公告字段漂移: {list(raw.columns)[:8]}")
    # bug1 修复：优先按代码列匹配（标题不含自身代码，裸码匹配命中率为 0）；
    # 仅在代码列缺失时退化为标题包含匹配（保留旧行为作兜底）。
    if "raw_code" in raw.columns:
        raw = raw[raw["raw_code"].astype(str).str.replace(r"\D", "", regex=True)
                  .str.zfill(6) == code].copy()
    else:
        raw = raw[raw["title"].astype(str).str.contains(code, na=False)].copy()
    if raw.empty:
        return pl.DataFrame()
    # bug2 修复：使用接口返回的真实公告日期，绝不覆盖成入参 start（PIT 红线）。
    if "pub_date" in raw.columns:
        raw["pub_date"] = raw["pub_date"].astype(str).str.slice(0, 10)
    else:
        raw["pub_date"] = None
    raw["symbol"] = symbol
    out = pl.from_pandas(raw[[c for c in ("symbol", "pub_date", "title", "url") if c in raw.columns]])
    # pub_date：str -> pl.Date（容错，解析失败为 null，不造数）
    if "pub_date" in out.columns:
        out = out.with_columns(
            pl.col("pub_date").cast(pl.String, strict=False).str.slice(0, 10)
            .str.to_date(strict=False).alias("pub_date")
        )
    else:
        out = out.with_columns(pl.lit(None, dtype=pl.Date).alias("pub_date"))
    out = _build_frame(out, "eastmoney")
    return out.unique(subset=["symbol", "title", "pub_date"], keep="last")


def fetch_announcements_range(start: str, end: str) -> pl.DataFrame:
    """拉取**全市场 × [start, end] 区间**公告（巨潮 cninfo），打标并定序。

    为什么用巨潮而非东财（本函数的立身之本）：东财 ``stock_notice_report`` 是
    **单披露日全市场快照**、``symbol`` 是**封闭枚举**，给不出历史全量；只有巨潮
    ``stock_zh_a_disclosure_report_cninfo`` 支持"全市场 × 任意历史区间"一次拉取，
    是本项目唯一可行的历史回补路径。

    Args:
        start: 起始日，``YYYY-MM-DD`` 或 ``YYYYMMDD``。
        end: 结束日，同格式。

    Returns:
        列序 ``[symbol, pub_date, title, type, sentiment, source, url]`` 的 DataFrame；
        取不到数据 → ``pl.DataFrame()``（0 列 0 行，调用方据此跳过写入）。

    Notes:
        - cninfo 返回列名：``代码 / 简称 / 公告标题 / 公告时间 / 公告链接``；
          ``公告时间`` 是**含时分秒的字符串时间戳**，``pub_date`` 取其前 10 字符。
        - ``symbol=""``（空串）表示**全市场**（akshare 默认值是 ``"000001"``，
          故必须显式传空串，否则只会拿到平安银行一只）。
        - 6 位裸码经 :func:`domain.a_share_rules.code_to_symbol` 补交易所后缀
          （项目唯一事实来源，复用而非自建规则）。
    """
    import akshare as ak

    s = start.replace("-", "")
    e = end.replace("-", "")
    raw = ak.stock_zh_a_disclosure_report_cninfo(
        symbol="", market="沪深京", start_date=s, end_date=e)
    if raw is None or raw.empty:
        return pl.DataFrame()
    df = raw.rename(columns={"代码": "raw_code", "公告标题": "title",
                             "公告时间": "pub_dt", "公告链接": "url"})
    if "title" not in df.columns or "raw_code" not in df.columns:
        raise ValueError(f"公告字段漂移: {list(df.columns)[:8]}")
    df = df[["raw_code", "title", "pub_dt", "url"]].copy()

    out = pl.from_pandas(df)
    # pub_date：从含时分秒的 pub_dt 取前 10 字符容错转 Date。
    out = out.with_columns(
        pl.col("pub_dt").cast(pl.String, strict=False).str.slice(0, 10)
        .str.to_date(strict=False).alias("pub_date")
    )
    # symbol：6 位裸码 -> 标准代码（复用领域层唯一事实来源；非 6 位数字的行丢弃）。
    out = out.with_columns(
        pl.col("raw_code").cast(pl.String, strict=False).str.strip_chars().alias("raw_code")
    )
    out = out.filter(pl.col("raw_code").is_not_null()
                     & (pl.col("raw_code").str.len_chars() == 6)
                     & pl.col("raw_code").str.contains(r"^\d{6}$"))
    out = out.with_columns(
        pl.col("raw_code").map_elements(code_to_symbol, return_dtype=pl.String).alias("symbol")
    )
    out = out.with_columns(
        pl.col("pub_date").cast(pl.Date, strict=False),
        pl.col("url").cast(pl.String, strict=False),
        pl.col("title").cast(pl.String, strict=False),
    )
    out = _build_frame(out, "cninfo")
    # 区间内同一公告可能因分页重复，按 4 元组去重。
    return out.unique(subset=["symbol", "title", "pub_date", "url"], keep="last")


def save_announcements(df: pl.DataFrame, trade_date: date) -> int:
    """增量合并写 announcements.parquet（按年分区，4 元组去重，原子替换）。

    去重键 ``(symbol, title, pub_date, url)``（2026-09-30 修复）：原为 ``(symbol, title)``
    **不含 pub_date** ⇒ 同标的同标题的不同日期公告（年度报告摘要、按月披露的回购进展
    公告等）会被 ``unique(keep="last")`` **压成一条**，历史回补必然残缺。

    写入前把去重键中的字符串列（``url`` 等）强制为 ``pl.String``：全 ``None`` 的列在
    polars 里 dtype 是 ``pl.Null``，而 ``write_partition`` 会 ``sort`` 去重键
    —— polars 1.6.0 对 Null dtype 排序抛 ``pyo3_runtime.PanicException``（BaseException，
    穿透普通 except）。显式转型是写入边界的防御，兼容旧调用方传入的 Null 列。
    """
    from ..parquet_store import write_partition

    if df.is_empty():
        return 0
    for col in ("symbol", "title", "url"):
        if col in df.columns and df.schema[col] in (pl.Null,):
            df = df.with_columns(pl.col(col).cast(pl.String, strict=False))
    write_partition("announcements", "__all__", trade_date, df,
                    dedup_keys=("symbol", "title", "pub_date", "url"))
    return df.height


def load_announcements_asof(asof: date) -> pl.DataFrame:
    """PIT 读取：pub_date <= asof。"""
    from ...core.config import get_settings

    base = get_settings().DATA_ROOT / "announcements" / "symbol=__all__"
    files = sorted(base.glob("year=*.parquet"))
    dfs = []
    for f in files:
        d = pl.read_parquet(f)
        if "pub_date" in d.columns:
            d = d.filter(pl.col("pub_date") <= asof)
            dfs.append(d)
    return pl.concat(dfs) if dfs else pl.DataFrame()
