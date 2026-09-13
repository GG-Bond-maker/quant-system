"""公告文本管线（1-2 FinLLM 数据前置：先把"路"修好，数据到位即通）。

数据流：
    公告文档 (symbol, date, title, content, source)
      → 清洗/分块
      → 情绪打分：规则 A 股词库打底（离线可用）；LLM_PROVIDER 配置后可选增强
      → 按 (symbol, date) 聚合为日频因子 sentiment ∈ [-1,1]（附 n_docs/method 披露）
      → DATA_ROOT/text_features/version=sentiment_v1/year=YYYY.parquet

导入路径（当前无网络抓取源，遵守"先不抓数"）：
- API 导入：POST /datacenter/text/import（JSON 文档批量，人工导出/第三方落地均可）
- 抓取适配器：register_source() 预留接口，akshare 等源接入后由 sync 触发

防未来函数：公告日 T 的信息在训练/推理侧经 attach_text_features 消费，
可用日 = T + lag_days（默认 1），由因子表日期整体位移承担——因此样本在
T 日看不到 T 日公告，T+1 起可见。注意这与「merge_asof allow_exact_matches
=False」不同：不要在调用侧再设该参数，否则可用日会被推到 T+2。
"""
from __future__ import annotations

import hashlib
import json
import math
import re
from datetime import date

import polars as pl
from loguru import logger

from ..core.config import get_settings
from ..domain.a_share_rules import code_to_symbol
from .parquet_store import atomic_write_parquet

DOCS_DATASET = "announcements_docs"
FACTOR_DIR = "text_features"
FACTOR_VERSION = "sentiment_v1"


# ---------------- 文档存储 ----------------
def _docs_dir():
    return get_settings().DATA_ROOT / DOCS_DATASET


def _norm_symbol(sym: str) -> str:
    s = sym.strip().upper()
    if re.fullmatch(r"\d{6}", s):
        try:
            return code_to_symbol(s)
        except ValueError:
            return s
    return s


def _load_docs() -> pl.DataFrame:
    files = sorted(_docs_dir().glob("year=*.parquet"))
    if not files:
        return pl.DataFrame(schema={"symbol": pl.String, "date": pl.Date,
                                    "title": pl.String, "content": pl.String,
                                    "source": pl.String, "doc_id": pl.String})
    return pl.concat([pl.read_parquet(f) for f in files], how="diagonal_relaxed")


def import_documents(docs: list[dict], source: str = "manual") -> dict:
    """批量导入公告文档（幂等：doc_id = hash(symbol|date|title) 去重）。"""
    if not docs:
        return {"imported": 0, "duplicates": 0, "invalid": 0}
    seen: set[str] = set()
    rows: list[dict] = []
    invalid = 0
    batch_dups = 0
    for d in docs:
        try:
            sym = _norm_symbol(str(d["symbol"]))
            dt = d["date"]
            dt = dt if isinstance(dt, date) else date.fromisoformat(str(dt)[:10])
            title = str(d.get("title", "")).strip()
            content = str(d.get("content", "")).strip()
            if not title and not content:
                invalid += 1
                continue
            doc_id = hashlib.sha1(f"{sym}|{dt}|{title}".encode()).hexdigest()[:16]
            if doc_id in seen:
                batch_dups += 1
                continue
            seen.add(doc_id)
            rows.append({"symbol": sym, "date": dt, "title": title,
                         "content": content[:8000], "source": source,
                         "doc_id": doc_id})
        except (KeyError, ValueError, TypeError):
            invalid += 1
    if not rows:
        return {"imported": 0, "duplicates": batch_dups, "invalid": invalid}

    existing = _load_docs()
    old_ids = set(existing["doc_id"].to_list()) if existing.height else set()
    new = [r for r in rows if r["doc_id"] not in old_ids]
    df = pl.DataFrame(new)
    out_dir = _docs_dir()
    merged = pl.concat([existing, df], how="diagonal_relaxed") \
        if existing.height else df
    merged = merged.with_columns(pl.col("date").dt.year().alias("year"))
    for g in merged.partition_by("year"):
        out_dir.mkdir(parents=True, exist_ok=True)
        atomic_write_parquet(out_dir / f"year={int(g['year'][0])}.parquet",
                             g.drop("year").sort("date"))
    return {"imported": len(new),
            "duplicates": batch_dups + (len(rows) - len(new)),
            "invalid": invalid, "total": merged.height}


# ---------------- 清洗 / 分块 ----------------
_WS_RE = re.compile(r"[ \t\r\f\v]+")
_CTRL_RE = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f]")


def clean_text(text: str) -> str:
    """最小清洗：控制字符剔除、空白归一、去首尾。"""
    return _WS_RE.sub(" ", _CTRL_RE.sub("", text or "")).strip()


def chunk_text(text: str, max_chars: int = 500, overlap: int = 50) -> list[str]:
    """定长滑窗分块（供 LLM 长文打分；标题类短文自然只有 1 块）。"""
    text = clean_text(text)
    if not text:
        return []
    if len(text) <= max_chars:
        return [text]
    step = max(1, max_chars - overlap)
    return [text[i:i + max_chars] for i in range(0, len(text), step)]


# ---------------- 情绪打分 ----------------
# A 股公告高频情绪词（极简词库打底；LLM 配置后作为增强路径）
_LEXICON: dict[str, float] = {
    "增长": 1, "增长至": 1, "超预期": 2, "预增": 2, "扭亏": 2, "盈利": 1,
    "中标": 1.5, "签约": 1, "回购": 1.5, "增持": 1.5, "分红": 1, "派息": 1,
    "获批": 1.5, "专利": 1, "订单": 1, "扩产": 1, "涨停": 1, "利好": 2,
    "亏损": -2, "预减": -2, "下滑": -1.5, "下降": -1, "回落": -1,
    "处罚": -2, "违规": -2, "立案": -2, "质押": -1, "减持": -1.5,
    "退市": -3, "诉讼": -1.5, "违约": -2, "债务逾期": -2, "利空": -2,
    "暂停上市": -3, "警示": -1.5, "问询": -1,
}


# 否定词（出现在情绪词前 → 情绪取反）：A 股公告里"解除质押""未减持"
# "不下滑"都是常见表述，不做否定识别会把利好判成利空。
_NEGATIONS = ("尚未", "没有", "未", "不", "无", "免于", "解除", "撤销", "撤消",
              "终止", "不予", "否认", "不存在", "不存在于")
# 词按长度降序做有序交替：保证"增长至"整体命中一次，而不是被"增长"
# 与"增长至"两条重叠词条各计一次（原实现会让"增长至"拿到 2 倍权重）。
_TERM_RE = re.compile("|".join(
    re.escape(w) for w in sorted(_LEXICON, key=len, reverse=True)))


def _lexicon_score(text: str, lexicon: dict[str, float],
                   term_re: re.Pattern[str], scale: float) -> float:
    """通用词库打分：最长匹配不重叠 + 否定词取反（前 3 字窗口）+
    同词封顶 3 次 + tanh(score/scale) 归一；无命中返回 0（中性）。
    """
    if not text:
        return 0.0
    score = 0.0
    hits = 0
    counts: dict[str, int] = {}
    for m in term_re.finditer(text):
        w = m.group(0)
        if counts.get(w, 0) >= 3:
            continue
        counts[w] = counts.get(w, 0) + 1
        wgt = lexicon[w]
        prefix = text[max(0, m.start() - 3):m.start()]
        if any(neg in prefix for neg in _NEGATIONS):
            wgt = -wgt
        score += wgt
        hits += 1
    return round(math.tanh(score / scale), 4) if hits else 0.0


def rule_sentiment(text: str) -> float:
    """规则词库打分 → tanh(加权和/3) ∈ [-1,1]；无命中返回 0（中性）。

    口径（自研，离线必出，LLM 配置后仅作回退）：
    - 最长匹配优先、不重复计重叠词条；
    - 情绪词前 3 字内出现否定词 → 该词权重取反；
    - 同一词条最多计入 3 次（防公告模板刷词）；
    - 除以 3 后 tanh 归一，属尺度假设而非概率，仅供排序类因子使用。
    """
    return _lexicon_score(text, _LEXICON, _TERM_RE, 3.0)


# ---------------- 业绩超预期因子（1-2 深化：独立于情绪的超预期事件强度） ----------------
# 口径（数据真实性红线）：本因子度量「公告文本中业绩表述的方向与强度」，
# 是词库口径的事件强度 ∈ [-1,1]，**不是**超预期概率——平台无卖方一致预期
# (consensus) 数据源，任何"概率"都无从谈起。待一致预期数据接入后才能升级。
_SURPRISE_LEXICON: dict[str, float] = {
    "大超预期": 3.0, "超预期": 2.5, "好于预期": 2.0, "高于预期": 2.0,
    "扭亏为盈": 2.5, "扭亏": 2.0, "预增": 2.0, "业绩预增": 2.5,
    "不及预期": -2.5, "低于预期": -2.0, "差于预期": -2.0,
    "业绩变脸": -2.5, "下修": -2.0, "预亏": -2.0, "预减": -2.0,
}
_SURPRISE_RE = re.compile("|".join(
    re.escape(w) for w in sorted(_SURPRISE_LEXICON, key=len, reverse=True)))


def rule_surprise(text: str) -> float:
    """业绩超预期事件强度 → tanh(加权和/2) ∈ [-1,1]；无业绩表述返回 0。

    与 sentiment 并列的独立日频因子（同 docstring 口径：最长匹配 + 否定词
    取反 + 同词封顶 3 次）。「未超预期」「不下修」由否定词规则处理。
    """
    return _lexicon_score(text, _SURPRISE_LEXICON, _SURPRISE_RE, 2.0)


def llm_sentiment(texts: list[str]) -> list[float] | None:
    """LLM 批量打分（可选增强）；解析失败返回 None，调用方回退规则打分。

    每批 ≤20 条，输出 JSON 数组逐条 [-1,1]，越界截断。
    """
    from ..core.llm import chat, llm_enabled

    if not llm_enabled() or not texts:
        return None
    lines = "\n".join(f"{i + 1}. {clean_text(t)[:300]}" for i, t in enumerate(texts))
    try:
        content = chat([
            {"role": "system", "content":
                "你是 A 股公告情绪标注器。对每条公告输出 [-1,1] 的情绪分"
                "（-1 极度利空，0 中性，1 极度利好）。只输出 JSON 数组，"
                "长度与输入条数一致，不要任何解释。"},
            {"role": "user", "content": lines}])
        arr = json.loads(content[content.find("["):content.rfind("]") + 1])
        if not isinstance(arr, list) or len(arr) != len(texts):
            return None
        return [max(-1.0, min(1.0, float(x))) for x in arr]
    except Exception as e:  # noqa: BLE001 任何解析/网络问题都回退规则
        logger.warning(f"[text] llm_sentiment 回退规则词库: {type(e).__name__}")
        return None


# ---------------- 因子构建 ----------------
def score_and_build_factor(batch_size: int = 20) -> dict:
    """对全部文档打分并聚合为日频情绪因子（幂等覆盖写）。"""
    docs = _load_docs()
    if docs.is_empty():
        return {"ok": False, "error": "无公告文档（先 POST /datacenter/text/import 导入）"}

    titles = [f"{t} {c}" for t, c in zip(docs["title"].to_list(),
                                         docs["content"].to_list())]
    scores: list[float] = []
    method = "rule_lexicon"
    from ..core.llm import llm_enabled

    if llm_enabled():
        llm_scores: list[float | None] = []
        for i in range(0, len(titles), batch_size):
            batch = llm_sentiment(titles[i:i + batch_size])
            llm_scores.extend(batch if batch is not None
                              else [None] * len(titles[i:i + batch_size]))
        if all(s is not None for s in llm_scores):
            scores = [float(s) for s in llm_scores]  # type: ignore[arg-type]
            method = "llm"
    if not scores:
        scores = [rule_sentiment(t) for t in titles]

    out = docs.select(["symbol", "date"]).with_columns([
        pl.Series("sentiment", scores),
        # surprise 恒为词库口径（即使 sentiment 走 LLM）——口径见 text_status basis
        pl.Series("surprise", [rule_surprise(t) for t in titles]),
        pl.lit(1).alias("n_docs"),
        pl.lit(method).alias("method"),
    ])
    agg = (out.group_by(["symbol", "date", "method"])
           .agg([pl.col("sentiment").mean().round(4).alias("sentiment"),
                 pl.col("surprise").mean().round(4).alias("surprise"),
                 pl.col("n_docs").sum().alias("n_docs")])
           .sort(["date", "symbol"]))
    out_dir = get_settings().DATA_ROOT / FACTOR_DIR / f"version={FACTOR_VERSION}"
    out_dir.mkdir(parents=True, exist_ok=True)
    agg = agg.with_columns(pl.col("date").dt.year().alias("year"))
    for g in agg.partition_by("year"):
        atomic_write_parquet(out_dir / f"year={int(g['year'][0])}.parquet",
                             g.drop("year").sort(["date", "symbol"]))
    return {"ok": True, "method": method, "rows": agg.height,
            "dates": agg["date"].n_unique(), "symbols": agg["symbol"].n_unique(),
            "dir": str(out_dir)}


def text_status() -> dict:
    """文本数据状态（数据中心卡消费）。"""
    from ..core.llm import llm_enabled

    docs = _load_docs()
    s = get_settings()
    factor_dir = s.DATA_ROOT / FACTOR_DIR / f"version={FACTOR_VERSION}"
    factor_files = sorted(factor_dir.glob("year=*.parquet")) if factor_dir.exists() else []
    factor_rows = sum(pl.read_parquet(f).height for f in factor_files)
    surprise_active = 0
    if factor_files:
        fac = pl.concat([pl.read_parquet(f) for f in factor_files],
                        how="diagonal_relaxed")
        if "surprise" in fac.columns:
            surprise_active = int((fac["surprise"].abs() > 0).sum())
    return {
        "docs": docs.height,
        "doc_dates": (docs["date"].n_unique() if docs.height else 0),
        "doc_symbols": (docs["symbol"].n_unique() if docs.height else 0),
        "doc_last": (str(docs["date"].max()) if docs.height else None),
        "factor_rows": factor_rows,
        "factor_files": len(factor_files),
        "surprise_active_rows": surprise_active,
        "llm_enabled": llm_enabled(),
        # 口径披露（平台数据真实性红线）：sentiment 为自研规则/LLM 情绪分，
        # 非官方评级；surprise 为业绩表述事件强度（词库口径），**非超预期
        # 概率**——无一致预期数据源，任何概率表述都属造数；挂接时 T+1 可用、
        # 30 自然日过期即 NaN
        "basis": ("sentiment ∈ [-1,1]，规则词库或 LLM 打分（见 factor 的 method 字段）；"
                  "surprise ∈ [-1,1]，业绩表述事件强度（恒为规则词库口径，"
                  "非超预期概率——无一致预期数据源）；"
                  "挂接口径：T+1 可用、距最近公告 >30 自然日为 NaN（事件型信号，不前向填充）"),
        "note": "当前无抓取源（先不抓数）：经 /datacenter/text/import 导入文档后即可构建情绪因子",
    }


def attach_text_features(df, lag_days: int = 1, stale_days: int = 30):
    """训练/研究侧消费入口：按 symbol merge_asof 挂接情绪因子。

    防未来函数口径（实现细节，勿在调用侧另行处理）：
    - T 日公告的**可用日 = T + lag_days**（默认 1），实现方式是把因子表
      日期整体位移 lag_days 后再 merge_asof——样本在 T 日看不到 T 日公告。
      等价于「allow_exact_matches=False 再 lag 1 天」，不要两者叠加。
    - **stale_days（默认 30 个自然日）**：距最近一次公告超过该窗口的样本
      情绪为 NaN（过期信息不向前填充）。这是主动口径而非缺陷——公告情绪
      是事件型信号，30 天后继续沿用会把一次性利好变成永久属性。
    - 无文档覆盖的样本为 NaN，不造数。
    - surprise（业绩表述事件强度）与 sentiment 同口径挂接；旧版本因子表
      无该列时补 NaN（不造数）。

    df: pd.DataFrame(symbol, date, ...) → 追加 sentiment / surprise / n_docs 列。
    """
    import numpy as np
    import pandas as pd

    factor_dir = get_settings().DATA_ROOT / FACTOR_DIR / f"version={FACTOR_VERSION}"
    files = sorted(factor_dir.glob("year=*.parquet"))
    if not files or df.empty:
        return df.assign(sentiment=np.nan, surprise=np.nan, n_docs=np.nan)
    fac = pl.concat([pl.read_parquet(f) for f in files], how="diagonal_relaxed") \
        .drop("method")
    if "surprise" not in fac.columns:      # 旧版本因子表兼容
        fac = fac.with_columns(pl.lit(None, dtype=pl.Float64).alias("surprise"))
    fac_pd = fac.to_pandas()
    # T 日公告 T+lag_days 起可用：因子表日期整体位移（见 docstring）
    fac_pd["date"] = pd.to_datetime(fac_pd["date"]) + pd.Timedelta(days=lag_days)
    out = df.copy()
    out["date"] = pd.to_datetime(out["date"])
    merged = pd.merge_asof(
        out.sort_values("date"), fac_pd.sort_values("date"),
        on="date", by="symbol", tolerance=pd.Timedelta(days=stale_days))
    return merged
