"""公告事件标签（P1-6）：sina 公告接口 -> 关键词规则分类 -> announcements.parquet。

情绪标签为【可解释的关键词规则】（非随机/非模型黑箱）：
    positive: 回购 / 增持 / 业绩预增 / 中标 / 分红
    negative: 减持 / 质押 / 业绩预亏 / 处罚 / 立案
    neutral : 其余
PIT 红线：pub_date 是唯一可见时间轴，T 日模型只能看到 pub_date <= T 的公告。
"""
from __future__ import annotations

from datetime import date

import polars as pl

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


def fetch_announcements(symbol: str, start: str, end: str) -> pl.DataFrame:
    """拉取单只标的公告（sina stock_notice_report），应用可解释规则打标。"""
    import akshare as ak

    code = symbol.split(".")[0]
    # akshare 公告接口按日期分页拉全市场，此处按 symbol 过滤
    raw = ak.stock_notice_report(symbol="全部", date=start.replace("-", ""))
    if raw is None or raw.empty:
        return pl.DataFrame()
    rename = {"公告标题": "title", "公告日期": "pub_date", "公告类型": "category"}
    raw = raw.rename(columns={k: v for k, v in rename.items() if k in raw.columns})
    if "title" not in raw.columns:
        raise ValueError(f"公告字段漂移: {list(raw.columns)[:8]}")
    raw = raw[raw["title"].astype(str).str.contains(code, na=False)].copy()
    if raw.empty:
        return pl.DataFrame()
    raw["pub_date"] = None  # sina 接口以拉取日期为公告日口径（当日公告）
    raw["pub_date"] = date.fromisoformat(start)
    raw["symbol"] = symbol
    tags = [classify(str(t)) for t in raw["title"]]
    raw["type"] = [t[0] for t in tags]
    raw["sentiment"] = [t[1] for t in tags]
    raw["source"] = "akshare"
    keep = ["symbol", "pub_date", "title", "type", "sentiment", "source"]
    if "url" in raw.columns:
        keep.append("url")
    else:
        raw["url"] = None
        keep.append("url")
    out = pl.from_pandas(raw[keep]).unique(subset=["symbol", "title"], keep="last")
    return out


def save_announcements(df: pl.DataFrame, trade_date: date) -> int:
    """增量合并写 announcements.parquet（按年分区，(symbol,title) 去重，原子替换）。"""
    from ..parquet_store import write_partition

    if df.is_empty():
        return 0
    write_partition("announcements", "__all__", trade_date, df,
                    dedup_keys=("symbol", "title"))
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
