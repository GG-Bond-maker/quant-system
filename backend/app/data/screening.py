"""选股过滤与快照（L2-1，Sprint3）。

数据层单一事实源：universe join / ST·停牌过滤 / 板块过滤 / score 降序的口径
只在这里实现，API 实时路径（api/v1/screener.py）与盘后快照写入
（pipeline.step_screener_dump）共同消费——两路径数字必须一致。

另含「选股中心 · 股票列表」的全市场行构建口径（有效截面日选择 / 本地日终行
/ 实时快照合并 / 筛选 / 服务端排序），见文件下半部分——同样只在这里实现，
路由层保持薄。

快照存储（SQLite，与主库同文件）：
- screener_snapshot 行表：每 board 物化 top-200，含富化列（close/pct 等），
  读取路径单次 SELECT 组装响应（<200ms 验收的关键）；
- screener_snapshot_stats 聚合表：pool_size/total + stats_json（完整 _stats）。
写入幂等（INSERT OR REPLACE）。
"""
from __future__ import annotations

import json
import sqlite3
from datetime import date as date_cls
from pathlib import Path
from typing import Any

import polars as pl
from loguru import logger

from ..core.config import get_settings
from ..core.errors import ERR_DATA_EMPTY, ERR_PARAMS, AQPException

BOARDS = ("all", "main", "chinext_star", "bse")
SNAPSHOT_TOP_K = 200
STRATEGY = "alpha_basic_v1"

# ---- 信号强度「相对分位」口径（缺陷 7，2026-09-18）----
# 旧口径用**绝对分数阈值**（score>=0.3→strong / >=0.1→neutral / else weak），与模型
# 分数尺度强耦合：生产模型换成 demean/repaired 版后分数尺度极小
# （实测 board=all max pred_score=0.039818、avg=0.003317），全部 < 0.1 ⇒ 全市场恒为
# weak、零信息量。新口径改为「与绝对分数无关的相对分位」：以**板块池前
# SIGNAL_REFERENCE_DEPTH 名**为参考总体，按名次分位切三档 ⇒ 换模型/换分数尺度不再失效。
SIGNAL_REFERENCE_DEPTH = 200   # 信号强度参考总体深度（与 SNAPSHOT_TOP_K 对齐）
SIGNAL_STRONG_CUT = 0.20       # 参考总体前 20% → strong
SIGNAL_NEUTRAL_CUT = 0.50      # 前 20%~50% → neutral，其余 weak

# [AQP 改名历史债务] 存量快照 screener_snapshot.risk 列可能仍是旧枚举 low/mid/high。
# 旧语义 score>=0.3→low(=高分=强信号)，与新语义相反，故翻译表是**反转映射**：
# low→strong, mid→neutral, high→weak。新值 strong/neutral/weak 原样通过（幂等、向前兼容）。
_LEGACY_SIGNAL_MAP = {"low": "strong", "mid": "neutral", "high": "weak"}


def _translate_signal_strength(raw: str | None) -> str | None:
    """读路径把存量 risk 列旧枚举翻译为新 signal_strength；新值/未知值原样返回。"""
    if raw is None:
        return None
    return _LEGACY_SIGNAL_MAP.get(raw, raw)


def instrument_info() -> dict[str, tuple[str | None, str | None]]:
    """SQLite instrument 表：symbol -> (name, industry)（名称/行业兜底源）。"""
    path = get_settings().SQLITE_PATH
    if not path.exists():
        return {}
    con = sqlite3.connect(path)
    try:
        rows = con.execute("SELECT symbol, name, industry FROM instrument").fetchall()
    finally:
        con.close()
    return {r[0]: (r[1], r[2]) for r in rows}


def filter_universe(pred: pl.DataFrame, trade_date: str, board: str,
                    *, require_universe: bool = False) -> tuple[pl.DataFrame, int]:
    """universe join + ST/停牌过滤 + 板块过滤 + pred_score 降序（**未截断**）。

    Returns:
        ``(df, pool_size)``：pool_size 为截断前的股票池规模。

    Raises:
        AQPException: board 非法；``require_universe=True`` 且当日无股票池快照。

    ⚠️ 分区路径**必须**走 :func:`parquet_store.path_for_year`（唯一正确来源）。
    历史缺陷（2026-09-14 修复）：此前手拼
    ``.../universe_daily/symbol=__all__/year=YYYY.parquet``，而磁盘真实文件名是
    ``year=YYYY.snappy.parquet``（见 ``parquet_store.path_for_year``），于是
    ``uni_path.exists()`` **恒为 False**：universe join 静默失效 = ST/停牌不过滤
    （未校验的榜单被当成已校验结果），且 ``require_universe=True`` 的调用方
    （``market._build_recommend`` / ``alerts._load_latest_predictions``）永久拿到
    ``ERR_DATA_EMPTY`` —— 每日推荐榜不可用、score_topk 预警静默跳过。
    """
    if board != "all" and board not in BOARDS:
        raise AQPException(ERR_PARAMS, f"board 仅支持 {sorted(set(BOARDS) | {'all'})}")

    from .parquet_store import path_for_year

    universe = pl.DataFrame()
    uni_path = path_for_year("universe_daily", "__all__", trade_date[:4])
    if uni_path.exists():
        universe = pl.read_parquet(uni_path)
        if universe.schema["date"] != pl.Date:
            universe = universe.with_columns(pl.col("date").str.to_date())
        universe = universe.filter(pl.col("date") == date_cls.fromisoformat(trade_date))
    if require_universe and universe.is_empty():
        raise AQPException(ERR_DATA_EMPTY, f"{trade_date} 无可交易股票池快照")

    df = pred
    if universe.height:
        join_cols = [c for c in ("symbol", "name", "industry", "board", "is_st",
                                 "is_halted", "close", "limit_pct")
                     if c in universe.columns]
        df = df.join(universe.select(join_cols), on="symbol", how="left")
        df = df.filter((pl.col("is_st") != True) & (pl.col("is_halted") != True))  # noqa: E712
    if board != "all":
        if universe.height:
            df = df.filter(pl.col("board") == board)
        else:
            from .universe import board_of

            df = df.filter(pl.col("symbol").map_elements(
                lambda s: board_of(s.split(".")[0]) == board, return_dtype=pl.Boolean))
    pool_size = df.height
    return df.sort("pred_score", descending=True), pool_size


def signal_strength_by_rank(n: int) -> list[str]:
    """按「相对分位」生成 n 个信号强度标签（位置 0 = 分数最高 = 最强）。

    ``pct = i / n``；``pct < SIGNAL_STRONG_CUT`` → ``strong``；
    ``pct < SIGNAL_NEUTRAL_CUT`` → ``neutral``；否则 ``weak``。
    与**任何绝对分数阈值**无关 ⇒ 换模型 / 换分数尺度都不会失效。``n <= 0`` 返回 ``[]``。

    Args:
        n: 参考总体规模（通常是板块池前 ``SIGNAL_REFERENCE_DEPTH`` 名）。

    Returns:
        长度 n 的标签列表，index 越小（分数越高）档位越强；序列单调
        （不允许 weak 出现在 strong 之前）。
    """
    if n <= 0:
        return []
    out: list[str] = []
    for i in range(n):
        pct = i / n
        if pct < SIGNAL_STRONG_CUT:
            out.append("strong")
        elif pct < SIGNAL_NEUTRAL_CUT:
            out.append("neutral")
        else:
            out.append("weak")
    return out


def signal_strength_reference_map(pred: pl.DataFrame, trade_date: str,
                                  *,
                                  boards: "list[str] | tuple[str, ...] | None" = None,
                                  ) -> dict[str, str]:
    """以「板块池前 ``SIGNAL_REFERENCE_DEPTH`` 的相对分位」为总体，返回 ``symbol -> 标签``。

    自选股没有「榜单」总体，故以与榜单**完全一致**的口径取总体：对 ``boards``
    （缺省 ``("main", "chinext_star", "bse")``）逐板块调 :func:`filter_universe`
    取板块池，取前 ``SIGNAL_REFERENCE_DEPTH`` 名按 :func:`signal_strength_by_rank`
    分档写入 ``symbol -> label``；未被任何板块覆盖的 symbol 回落到 ``all`` 榜的标签
    （universe 缺 board 列时 :func:`filter_universe` 内部有 ``board_of`` 兜底）。

    与 :func:`enrich_items` 共用同一分档函数与同一参考深度 ⇒ 同一标的在「自选股
    路径」与「榜单路径」得到**同一标签**（守住本模块「两路径数字必须一致」契约）。

    Args:
        pred: 当日预测结果（含 ``symbol``/``pred_score``，``date`` 为 trade_date）。
        trade_date: 交易日（``YYYY-MM-DD``）。
        boards: 需要构建总体标签的细分板块（``keyword-only``，缺省三大板块）；
            调用方（自选股路径）可只用实际出现的板块以省 IO，**不改变分档口径**。

    Returns:
        ``symbol -> "strong"|"neutral"|"weak"``；pred 为空或缺 ``symbol``/``pred_score``
        列时返回 ``{}``（非抛异常 —— 调用方自选股路径要求本函数对空/缺列**永不抛**）。
    """
    # [AQP B-1 修复 2026-09-18] 空/缺列时**返回空 dict 而非抛异常**：filter_universe
    # 依赖 symbol + pred_score 列（缺列会在 sort/join 处抛 ColumnNotFoundError）。自选股
    # 路径的调用方把「空 map」当作「全部降级」，无需在此区分原因（留痕由调用方负责）。
    if pred.is_empty() or not {"symbol", "pred_score"}.issubset(pred.columns):
        return {}
    board_list = tuple(boards) if boards is not None else ("main", "chinext_star", "bse")
    out: dict[str, str] = {}
    for board in board_list:                      # ≤3 次板块调用（非逐 symbol）
        df, _ = filter_universe(pred, trade_date, board)
        reference = df.head(SIGNAL_REFERENCE_DEPTH)
        labels = signal_strength_by_rank(reference.height)
        for idx, r in enumerate(reference.iter_rows(named=True)):
            out[str(r["symbol"])] = labels[idx]
    # 回落：未被上述任一分板块覆盖的 symbol 用 all 榜口径（setdefault 不覆盖已有值）
    df_all, _ = filter_universe(pred, trade_date, "all")
    reference_all = df_all.head(SIGNAL_REFERENCE_DEPTH)
    labels_all = signal_strength_by_rank(reference_all.height)
    for idx, r in enumerate(reference_all.iter_rows(named=True)):
        out.setdefault(str(r["symbol"]), labels_all[idx])
    return out


def enrich_items(df: pl.DataFrame, top_k: int) -> list[dict]:
    """榜单行富化：名称/行业兜底 + daily_bar 最新收盘/涨跌/换手（原 _build_items 口径）。"""
    from .universe import read_prev_and_today

    ins_info = instrument_info()
    # 信号强度参考总体 = 板块池前 SIGNAL_REFERENCE_DEPTH 名（df 是 filter_universe 返回的
    # **已按 pred_score 降序的整板块池**，未截断）。**关键不变式**：参考深度恒为 200、
    # 不随 top_k 变化 ⇒ 实时榜（top_k=50）与盘后快照（top_k=200）对同一标的给出同一
    # 标签（本模块 docstring 的「两路径数字必须一致」契约）。
    reference = df.head(SIGNAL_REFERENCE_DEPTH)
    labels = signal_strength_by_rank(reference.height)
    items: list[dict] = []
    for idx, r in enumerate(df.head(top_k).iter_rows(named=True)):
        sym = r["symbol"]
        ins_name, ins_industry = ins_info.get(sym, (None, None))
        close = float(r["close"]) if r.get("close") is not None else None
        pct: float | None = None
        turnover = None
        amount = None
        bar = read_prev_and_today(sym, date_cls.fromisoformat(str(r["date"])[:10]))
        if bar.get("close") is not None:
            close = float(bar["close"])
            prev_close = bar.get("prev_close")
            if prev_close:
                pct = round((close / prev_close - 1) * 100, 2)
        if bar.get("turnover") is not None:
            turnover = round(bar["turnover"] * 100, 2)
        amount = bar.get("amount")
        score = round(float(r["pred_score"]), 6)
        items.append({
            "symbol": sym,
            "name": r.get("name") or ins_name or None,
            "industry": r.get("industry") or ins_industry or None,
            "close": close,
            "pct": pct,
            # 换手率为小数口径（0.012 = 1.2%），前端换算百分比展示
            "turnover": turnover,
            "amount": amount,
            "limit_pct": float(r["limit_pct"]) if r.get("limit_pct") is not None else None,
            "score": score,
            # [AQP 缺陷 7 修正] 信号强度改为**相对分位**（见 signal_strength_by_rank）。
            # 为什么不能用绝对阈值：生产模型 max pred_score=0.0398 < 0.1 ⇒ 全市场恒为
            # weak，零信息量；且阈值与模型分数尺度强耦合，每次换模型都可能失效。
            # 现按榜单内名次分位（前 20% strong / 20%~50% neutral / 其余 weak），
            # 与分数绝对值无关。index 越靠前档位越强，超出参考深度者回退 weak。
            "signal_strength": labels[idx] if idx < len(labels) else "weak",
        })
    return items


def compute_stats(items: list[dict], pool_size: int) -> dict:
    """榜单聚合指标（原 api._stats 口径）：total=截断后，pool_size=截断前。"""
    pcts = [i["pct"] for i in items if i["pct"] is not None]
    scores = [i["score"] for i in items if i["score"] is not None]
    industries = [i["industry"] for i in items if i["industry"]]
    up = sum(1 for p in pcts if p > 0)
    counts: dict[str, int] = {}
    for ind in industries:
        counts[ind] = counts.get(ind, 0) + 1
    top_ratio: float | None = None
    if industries and counts:
        top_ratio = round(max(counts.values()) / len(industries) * 100, 2)
    return {
        "total": len(items),
        "pool_size": pool_size,
        "win_rate": round(up / len(pcts) * 100, 2) if pcts else None,
        "avg_pct": round(sum(pcts) / len(pcts), 4) if pcts else None,
        "avg_score": round(sum(scores) / len(scores), 6) if scores else None,
        # [AQP 改名] high_risk → strong_signal：统计 signal_strength == "strong" 的标的数
        "strong_signal": sum(1 for i in items if i.get("signal_strength") == "strong"),
        "industry_count": len(counts),
        "top_industry_ratio": top_ratio,
    }


# ---------------- 快照写入（盘后流水线） ----------------
def write_screener_snapshot(trade_date: str, pred: pl.DataFrame,
                            strategy: str = STRATEGY,
                            top_k: int = SNAPSHOT_TOP_K) -> str:
    """四板块物化 top-200 快照（幂等 INSERT OR REPLACE；写入即含富化列）。

    Args:
        trade_date: 快照交易日（YYYY-MM-DD，须与 pred 的 date 一致）。
        pred: 当日预测结果（columns: date/symbol/pred_score[...]）。
        strategy: 策略标识。
        top_k: 每板块物化行数。

    Returns:
        "boards=4 rows=N" 形式的摘要。
    """
    if pred.height and str(pred["date"].max())[:10] != trade_date:
        # !r 避免 bytes 标量被 f-string 渲染成 b'...' 的歧义写法
        raise ValueError(f"pred 日期 {pred['date'].max()!r} 与快照日 {trade_date} 不一致")

    s = get_settings()
    total_rows = 0
    con = sqlite3.connect(s.SQLITE_PATH, timeout=30)
    try:
        con.execute("PRAGMA busy_timeout=30000")
        for board in BOARDS:
            df, pool_size = filter_universe(pred, trade_date, board)
            items = enrich_items(df, top_k)
            stats = compute_stats(items, pool_size)
            con.executemany(
                "INSERT OR REPLACE INTO screener_snapshot "
                "(date, strategy, board, rank, symbol, name, industry, pred_score, "
                " model_version, close, pct, turnover, amount, limit_pct, risk) "
                "VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                [(trade_date, strategy, board, i, it["symbol"], it["name"],
                  it["industry"], it["score"],
                  r.get("model_version"), it["close"], it["pct"], it["turnover"],
                  it["amount"], it["limit_pct"], it["signal_strength"])
                 for i, (it, r) in enumerate(zip(items, df.head(top_k).iter_rows(named=True)),
                                             start=1)])
            # 注：screener_snapshot 表的 risk 列为历史遗留命名，现实际存储
            # signal_strength 取值（strong/neutral/weak），读路径重命名为 signal_strength
            # 回传（见 load_screener_snapshot）。未改列名以避免迁移生产 SQLite。
            con.execute(
                "INSERT OR REPLACE INTO screener_snapshot_stats "
                "(date, strategy, board, pool_size, total, trade_date, stats_json) "
                "VALUES (?,?,?,?,?,?,?)",
                (trade_date, strategy, board, pool_size, len(items), trade_date,
                 json.dumps(stats, ensure_ascii=False)))
            total_rows += len(items)
        con.commit()
    finally:
        con.close()
    logger.info(f"[screener_snapshot] {trade_date} boards={len(BOARDS)} rows={total_rows}")
    return f"boards={len(BOARDS)} rows={total_rows}"


# ---------------- 快照读取（API 路径） ----------------
def _latest_pred_date() -> str | None:
    """predictions 最新分区日期（只看文件名，不读内容）。"""
    files = sorted((get_settings().DATA_ROOT / "predictions").glob("date=*.parquet"))
    if not files:
        return None
    stem = files[-1].stem.replace("date=", "")
    return f"{stem[:4]}-{stem[4:6]}-{stem[6:8]}" if len(stem) == 8 else None


def load_screener_snapshot(trade_date: str | None, strategy: str, board: str,
                           top_k: int) -> dict | None:
    """读快照组装响应；不可用（无快照/日期过期/top_k 超物化）时返回 None 回落实时。

    - trade_date=None：取最新快照日期，但必须等于 predictions 最新分区日期
      （流水线跑完才会写入快照，日期错位说明快照过期，宁可回退实时也不给旧榜）；
    - top_k 超过物化行数且 pool_size 更大（>200 池被截断）→ 回落实时。
    """
    s = get_settings()
    if not s.SQLITE_PATH.exists():
        return None
    con = sqlite3.connect(f"file:{s.SQLITE_PATH}?mode=ro", uri=True)
    try:
        con.row_factory = sqlite3.Row
        if trade_date is None:
            want = _latest_pred_date()
            row = con.execute(
                "SELECT date FROM screener_snapshot_stats WHERE strategy=? AND board=? "
                "ORDER BY date DESC LIMIT 1", (strategy, board)).fetchone()
            if row is None or want is None or row["date"] != want:
                return None
            snap_date = row["date"]
        else:
            snap_date = trade_date

        stat = con.execute(
            "SELECT * FROM screener_snapshot_stats WHERE date=? AND strategy=? AND board=?",
            (snap_date, strategy, board)).fetchone()
        if stat is None or not stat["stats_json"]:
            return None
        pool_size = stat["pool_size"] or 0
        rows = con.execute(
            "SELECT * FROM screener_snapshot WHERE date=? AND strategy=? AND board=? "
            "ORDER BY rank ASC", (snap_date, strategy, board)).fetchall()
        if not rows:
            return None
        if top_k > len(rows) and pool_size > len(rows):
            return None  # 请求超过物化窗口且池确实更大

        items = [{
            "symbol": r["symbol"], "name": r["name"], "industry": r["industry"],
            "close": r["close"], "pct": r["pct"], "turnover": r["turnover"],
            "amount": r["amount"], "limit_pct": r["limit_pct"],
            # [AQP 改名] 读 risk 列原样透传会带出旧枚举 low/mid/high；翻译为 signal_strength。
            "signal_strength": _translate_signal_strength(r["risk"]),
            "score": r["pred_score"],
        } for r in rows[:top_k]]

        # stats 按**请求 top_k 口径**现算（与实时路径 _screen 完全一致）：
        # 物化行是 top-200，直接用其 stats_json 会与 count/top_k 错位
        stats = compute_stats(items, pool_size)

        prev_stats: dict | None = None
        prev_date: str | None = None
        prev_row = con.execute(
            "SELECT date, pool_size FROM screener_snapshot_stats WHERE strategy=? "
            "AND board=? AND date<? ORDER BY date DESC LIMIT 1",
            (strategy, board, snap_date)).fetchone()
        if prev_row is not None:
            prev_rows = con.execute(
                "SELECT * FROM screener_snapshot WHERE date=? AND strategy=? AND "
                "board=? ORDER BY rank ASC",
                (prev_row["date"], strategy, board)).fetchall()
            if prev_rows:
                prev_items = [{
                    "pct": r["pct"], "score": r["pred_score"],
                    "industry": r["industry"],
                    # [AQP 改名] prev 路径同样翻译存量 risk 列旧枚举
                    "signal_strength": _translate_signal_strength(r["risk"]),
                } for r in prev_rows[:top_k]]
                prev_date = prev_row["date"]
                prev_stats = compute_stats(prev_items, prev_row["pool_size"] or 0)

        return {
            "date": snap_date, "strategy": strategy, "top_k": top_k, "board": board,
            "count": len(items), "items": items,
            "stats": {"today": stats, "prev": prev_stats, "prev_date": prev_date},
            "from_snapshot": True,
        }
    except Exception as e:  # noqa: BLE001 快照读失败一律回落实时路径
        logger.warning(f"[screener_snapshot] load failed, fallback realtime: {e!r}")
        return None
    finally:
        con.close()


# ============================================================================
# 选股中心 · 股票列表（全市场在册证券，2026-09-13）
#
# 与上方「score 榜单」完全解耦：这里不读 predictions、不算 score，只做
# 「全市场在册证券 × 最新有效截面 × 外部实时快照」的一张宽表，供前端做
# 服务端排序 + 分页。全部业务逻辑沉淀在本模块，api/v1/screener.py 只做
# 参数校验、缓存编排与分页切片。
# ============================================================================

# 「有效截面日」判定阈值：全市场在册证券约 1157 只，晚间增量同步会把新交易日
# 的**部分**标的先落盘（实测 2026-09-07 分区仅 19 行，而 09-03 / 09-04 为
# 1157 / 1156 行）。若直接取最大日期，页面只剩 19 行 —— 看似「全市场」实则
# 残缺。500 ≈ 在册数的四成：既能识别「截面没落全」，也不会因市场真实缩容
# （或标的池被主动裁剪到几百只）而误判。
MIN_VALID_SECTION_ROWS = 500
# 往回探测的日期上限：镜像里若存在大量残缺分区，避免无限回溯
SECTION_DATE_SCAN_LIMIT = 15
# 外部行情覆盖率红线：低于该比例 → 整表切本地日终口径（见 apply_quote_snapshot）
MIN_QUOTE_COVERAGE = 0.5
# 股票列表排序白名单（"" = 后端默认：code 升序）
STOCK_SORT_FIELDS: tuple[str, ...] = (
    "code", "name", "industry", "board", "close", "pct", "amount",
    "turnover", "total_cap_yi", "float_cap_yi",
)
# 数值排序列：统一 cast 到 Float64（全 null 列在 polars 里是 Null dtype，
# 不 cast 会让 nulls_last 排序行为不可控）
_STOCK_NUMERIC_SORT_FIELDS = frozenset(
    {"close", "pct", "amount", "turnover", "total_cap_yi", "float_cap_yi"})
# 股票列表行的完整字段契约（响应 items 的键集，顺序即前端列顺序）
STOCK_ROW_FIELDS: tuple[str, ...] = (
    "symbol", "name", "industry", "board", "close", "pct", "amount",
    "amount_yi", "total_cap_yi", "float_cap_yi", "turnover", "is_st",
    "is_halted", "quote_status",
)


def _opt_float(v: Any) -> float | None:
    """任意标量 -> ``float | None``（NaN / 不可解析一律 None，**绝不 0 兜底**）。

    数据真实性红线：取不到就是 None，禁止用 0 或推算值填充。
    """
    if v is None:
        return None
    try:
        f = float(v)
    except (TypeError, ValueError):
        return None
    return None if f != f else f  # noqa: PLR0124  NaN -> None（NaN 唯一不等于自身）


def _cs_path(dataset: str, d: date_cls) -> Path:
    """截面镜像单文件路径（与 cross_section._mirror_dir 同布局，只读用途）。"""
    root = get_settings().DATA_ROOT / "cs" / dataset
    return root / f"year={d.year}" / f"date={d.strftime('%Y%m%d')}.parquet"


def section_dates(dataset: str = "daily_bar") -> list[date_cls]:
    """截面镜像里已存在的全部日期（升序）。只读目录名，不打开任何文件。

    镜像布局见 ``data/cross_section.py``：
    ``DATA_ROOT/cs/<dataset>/year=YYYY/date=YYYYMMDD.parquet``。
    """
    root = get_settings().DATA_ROOT / "cs" / dataset
    out: list[date_cls] = []
    if not root.exists():
        return out
    for f in root.glob("year=*/date=*.parquet"):
        stem = f.stem.split("=")[-1]
        try:
            out.append(date_cls(int(stem[:4]), int(stem[4:6]), int(stem[6:8])))
        except ValueError:
            continue
    return sorted(out)


def _section_row_count(d: date_cls) -> int:
    """单个截面日的行数（优先读 parquet 元数据，失败回退全读；坏文件记 0）。"""
    path = _cs_path("daily_bar", d)
    if not path.exists():
        return 0
    try:
        return int(pl.scan_parquet(path).select(pl.len()).collect().item() or 0)
    except Exception:  # noqa: BLE001 元数据不可用（旧格式/损坏）-> 退回全读
        try:
            return int(pl.read_parquet(path).height)
        except Exception as e:  # noqa: BLE001 坏分区视为 0 行，继续回退
            logger.warning(f"[stock-list] 截面 {d} 不可读: {e!r}")
            return 0


def latest_valid_section_date(min_rows: int | None = None) -> date_cls | None:
    """从最新截面日往回找第一个「行数 ≥ min_rows」的有效截面日。

    背景（必读，否则会踩坑）：晚间增量同步先把新交易日的**部分**标的落盘，
    实测 2026-09-07 分区只有 19 行，而 09-03 / 09-04 是 1157 / 1156 行。
    若直接取最大日期，页面只剩 19 行。故必须按行数阈值往回回溯。

    Args:
        min_rows: 有效截面判定阈值；缺省取模块常量 ``MIN_VALID_SECTION_ROWS``
            （测试可 monkeypatch 该常量）。

    Returns:
        有效截面日；镜像为空返回 ``None``。若回溯窗口内**没有任何**日期达到
        阈值（极端：镜像里只有残缺分区），退回最新的非空分区 —— 宁可展示
        残缺截面，也不返回空列表（列表不空是本接口的可用性红线）。
    """
    threshold = MIN_VALID_SECTION_ROWS if min_rows is None else min_rows
    dates = section_dates()[-SECTION_DATE_SCAN_LIMIT:]
    newest_non_empty: date_cls | None = None
    for d in reversed(dates):
        n = _section_row_count(d)
        if n >= threshold:
            return d
        if newest_non_empty is None and n > 0:
            newest_non_empty = d
    return newest_non_empty


def _prev_valid_section_date(d: date_cls,
                             min_rows: int | None = None) -> date_cls | None:
    """严格早于 ``d`` 的最近一个有效截面日（涨跌幅的分母来源）。

    与 :func:`latest_valid_section_date` 同阈值：只有「完整截面」才适合做
    前收基准，否则残缺前收会让绝大多数标的的 pct 变 null。
    """
    threshold = MIN_VALID_SECTION_ROWS if min_rows is None else min_rows
    candidates = [x for x in section_dates() if x < d][-SECTION_DATE_SCAN_LIMIT:]
    for cand in reversed(candidates):
        if _section_row_count(cand) >= threshold:
            return cand
    return None


def instrument_dims() -> dict[str, dict[str, Any]]:
    """SQLite instrument 表：``symbol -> {name, industry, is_st}``（兜底源）。

    universe_daily 缺失某只标的时用它补名称/行业/ST 标记；三者都取不到就是
    None —— 不猜、不补默认值。
    """
    path = get_settings().SQLITE_PATH
    if not path.exists():
        return {}
    con = sqlite3.connect(path)
    rows: list[tuple] = []
    has_is_st = True
    try:
        rows = con.execute(
            "SELECT symbol, name, industry, is_st FROM instrument").fetchall()
    except sqlite3.OperationalError:  # 老库无 is_st 列
        has_is_st = False
        try:
            rows = con.execute(
                "SELECT symbol, name, industry FROM instrument").fetchall()
        except sqlite3.Error as e:  # 表不存在 / 库损坏 -> 兜底为空，不阻断列表
            logger.warning(f"[stock-list] instrument 表不可用: {e!r}")
            rows = []
    finally:
        con.close()
    out: dict[str, dict[str, Any]] = {}
    for r in rows:
        out[r[0]] = {
            "name": r[1],
            "industry": r[2],
            "is_st": bool(r[3]) if (has_is_st and r[3] is not None) else None,
        }
    return out


def _local_basis_fields(trade_date: str | None) -> dict[str, str]:
    """本地日终口径的列来源说明（前端在表头 tooltip 展示）。"""
    day = trade_date or "未知日期"
    return {
        "close": f"本地日终收盘（{day}）",
        "pct": f"{day} 收盘 / 前一有效截面收盘 - 1",
        "amount": "本地日终成交额（元）",
        "total_cap_yi": "不可用：本地无市值数据（不推算、不填 0）",
        "float_cap_yi": "不可用：本地无市值数据（不推算、不填 0）",
    }


def _realtime_basis_fields(source: str) -> dict[str, str]:
    """外部实时口径的列来源说明。"""
    label = {"tencent": "腾讯实时快照", "sina": "新浪实时快照"}.get(
        source, f"{source} 实时快照")
    return {
        "close": label,
        "pct": label,
        "amount": "盘中累计成交额（元）",
        "total_cap_yi": "总市值（亿）",
        "float_cap_yi": "流通市值（亿）",
    }


def _empty_market_payload(basis: str, reason: str) -> dict:
    """无本地截面时的空载荷（HTTP 仍 200，列表为空但不报错）。"""
    return {
        "rows": [],
        "trade_date": None,
        "as_of": None,
        "basis": basis,
        "source": "local",
        "degraded": True,
        "quote_coverage": None,
        "basis_desc": f"本地无日终截面数据：{reason}",
        "basis_fields": _local_basis_fields(None),
    }


def build_market_stock_rows(basis: str = "auto") -> dict:
    """构建「全市场在册证券」的本地日终口径行（**不含**外部行情）。

    这是数据层的单一事实源：有效截面日选择、名称/行业/板块/ST 兜底、
    涨跌幅分母、单位换算全部只在这里实现。外部实时快照由
    :func:`apply_quote_snapshot` 叠加，分页/排序由
    :func:`filter_stock_rows` / :func:`sort_stock_rows` 完成。

    单位约定（前端契约，勿改）：
        close / amount = 元；amount_yi / total_cap_yi / float_cap_yi = 亿元；
        pct / turnover = 百分比（本地截面的 turnover 是小数，这里 ×100）。

    Args:
        basis: ``auto|realtime|daily``，仅回显到响应，不影响本地行构建。

    Returns:
        ``{rows, trade_date, as_of, basis, source, degraded, quote_coverage,
        basis_desc, basis_fields}``；``rows`` 为行 dict 列表（键集 = 契约）。
        两列市值恒 ``None`` —— 本地没有任何市值数据，绝不推算。
    """
    from .cross_section import read_cross_section
    from .universe import board_of, load_universe

    d = latest_valid_section_date()
    if d is None:
        return _empty_market_payload(basis, "镜像 cs/daily_bar 下无可用截面分区")

    cs = read_cross_section("daily_bar", d)
    if cs.is_empty() or "symbol" not in cs.columns:
        return _empty_market_payload(basis, f"截面 {d} 为空或缺少 symbol 列")

    prev_d = _prev_valid_section_date(d)
    prev_close: dict[str, float] = {}
    if prev_d is not None:
        prev = read_cross_section("daily_bar", prev_d)
        if not prev.is_empty() and "symbol" in prev.columns and "close" in prev.columns:
            for r in prev.select(["symbol", "close"]).iter_rows(named=True):
                c = _opt_float(r.get("close"))
                if c is not None and r.get("symbol"):
                    prev_close[str(r["symbol"])] = c

    # 名称/行业/板块/ST/停牌：universe_daily 优先，缺失回退 SQLite instrument
    uni_dims: dict[str, dict[str, Any]] = {}
    uni = load_universe(d)
    if not uni.is_empty():
        keep = [c for c in ("symbol", "name", "industry", "board", "is_st",
                            "is_halted") if c in uni.columns]
        for r in uni.select(keep).iter_rows(named=True):
            uni_dims[str(r["symbol"])] = {
                "name": r.get("name"),
                "industry": r.get("industry"),
                "board": r.get("board"),
                "is_st": r.get("is_st"),
                "is_halted": r.get("is_halted"),
            }
    ins_dims = instrument_dims()

    rows: list[dict] = []
    for r in cs.iter_rows(named=True):
        sym = r.get("symbol")
        if not sym:
            continue
        sym = str(sym)
        code = str(r.get("code") or sym.split(".")[0])
        dim = uni_dims.get(sym) or {}
        ins = ins_dims.get(sym) or {}
        close = _opt_float(r.get("close"))
        amount = _opt_float(r.get("amount"))
        turnover_raw = _opt_float(r.get("turnover"))
        base = prev_close.get(sym)
        pct = (round((close / base - 1) * 100, 2)
               if (close is not None and base) else None)
        is_st = dim.get("is_st")
        if is_st is None:
            is_st = bool(ins.get("is_st") or False)
        is_halted = dim.get("is_halted")
        if is_halted is None:
            # universe 缺失时：截面里没有收盘价即视为当日无成交（停牌）
            is_halted = close is None
        rows.append({
            "symbol": sym,
            "name": dim.get("name") or ins.get("name"),
            "industry": dim.get("industry") or ins.get("industry"),
            "board": dim.get("board") or board_of(code),
            "close": close,
            "pct": pct,
            "amount": amount,
            "amount_yi": round(amount / 1e8, 2) if amount is not None else None,
            "total_cap_yi": None,
            "float_cap_yi": None,
            "turnover": (round(turnover_raw * 100, 2)
                         if turnover_raw is not None else None),
            "is_st": bool(is_st),
            "is_halted": bool(is_halted),
            "quote_status": "halted" if is_halted else "missing",
        })

    return {
        "rows": rows,
        "trade_date": d.isoformat(),
        "as_of": None,
        "basis": basis,
        "source": "local",
        "degraded": False,
        "quote_coverage": None,
        "basis_desc": f"本地日终截面 {d.isoformat()}",
        "basis_fields": _local_basis_fields(d.isoformat()),
    }


def _as_of_time(as_of: str | None) -> str | None:
    """快照时间归一为 ``HH:MM:SS``；源给的是日期时间则取时间部分。"""
    if not as_of:
        return None
    s = str(as_of).strip()
    if " " in s:
        s = s.split(" ", 1)[1]
    return s[:8] or None


def apply_quote_snapshot(payload: dict, quotes: list[dict], source: str,
                         as_of: str | None) -> dict:
    """把外部实时快照并入本地行；覆盖率不足则**整表**保留本地日终口径。

    降级口径（红线，勿改）：
        ``coverage = 命中数 / 请求数``；``coverage >= MIN_QUOTE_COVERAGE 且
        source != "degraded"`` → 用实时口径；否则整表切本地日终口径
        （pct = 当日 close / 前一有效截面 close - 1，amount 取截面 amount，
        两列市值全 None，``degraded=True``、``source="local"``、
        ``quote_coverage=None``）。

    ⚠️ 绝不「部分混用」：一半行实时、一半行日终会让同一列出现两种口径，
       排序结果不可解释，属数据造假。

    Args:
        payload: :func:`build_market_stock_rows` 的返回值（原地修改 rows）。
        quotes: 外部行情 quote 列表（含 ``symbol`` 键）。
        source: 外部源标识（``tencent``/``sina``/``degraded``）。
        as_of: 快照时间（``YYYY-MM-DD HH:MM:SS`` 或 ``HH:MM:SS``）。

    Returns:
        同一个 payload（已就地更新）。
    """
    rows: list[dict] = list(payload.get("rows") or [])
    requested = len(rows)
    qmap: dict[str, dict] = {}
    for quote in quotes or []:
        quote_sym = quote.get("symbol")
        if quote_sym:
            qmap[str(quote_sym)] = quote
    hit = sum(1 for r in rows if str(r.get("symbol")) in qmap)
    coverage = (hit / requested) if requested else 0.0
    trade_date = payload.get("trade_date")

    if source == "degraded" or coverage < MIN_QUOTE_COVERAGE:
        # 整表降级：任何外部字段都不落地，quote_status 如实标注
        for r in rows:
            r["quote_status"] = "halted" if r.get("is_halted") else "missing"
        payload.update({
            "source": "local",
            "degraded": True,
            "quote_coverage": None,
            "as_of": None,
            "basis_desc": (f"本地日终截面 {trade_date}：实时源不可用"
                           f"（命中 {hit}/{requested}，已降级；两列市值为空）"),
            "basis_fields": _local_basis_fields(trade_date),
        })
        return payload

    for r in rows:
        q = qmap.get(str(r.get("symbol")))
        if q is None:
            r["quote_status"] = "halted" if r.get("is_halted") else "missing"
            continue
        r["quote_status"] = "ok"
        price = _opt_float(q.get("price"))
        if price is not None:
            r["close"] = price
        pct = _opt_float(q.get("pct"))
        if pct is not None:
            r["pct"] = pct
        amount = _opt_float(q.get("amount"))
        if amount is not None:
            r["amount"] = amount
            r["amount_yi"] = round(amount / 1e8, 2)
        r["total_cap_yi"] = _opt_float(q.get("total_cap_yi"))
        r["float_cap_yi"] = _opt_float(q.get("float_cap_yi"))
        # 腾讯/东财的 turnover 已是百分比；新浪源为 None（不推算）
        turnover = _opt_float(q.get("turnover"))
        if turnover is not None:
            r["turnover"] = turnover

    stamp = _as_of_time(as_of)
    payload.update({
        "source": source,
        "degraded": False,
        "quote_coverage": {"hit": hit, "total": requested},
        "as_of": stamp,
        "basis_desc": (f"{_realtime_basis_fields(source)['close']}，"
                       f"截至 {stamp or '未知时间'}（{hit}/{requested} 只命中）"),
        "basis_fields": _realtime_basis_fields(source),
    })
    return payload


def filter_stock_rows(rows: list[dict], board: str = "all",
                      industry: str = "all", q: str = "",
                      exclude_st: int = 0) -> list[dict]:
    """板块 / 行业 / 关键词 / ST 过滤（全部在内存里做，缓存键不含这些维度）。

    Raises:
        AQPException: ``board`` 非法（业务码 40000）。行业无匹配**不报错**，
            返回空列表（前端下拉里可能残留已无标的的行业名）。
    """
    if board != "all" and board not in BOARDS:
        raise AQPException(ERR_PARAMS, f"board 仅支持 {sorted(set(BOARDS) | {'all'})}")

    out: list[dict] = []
    kw = (q or "").strip().lower()
    for r in rows:
        if board != "all" and r.get("board") != board:
            continue
        if industry and industry != "all" and r.get("industry") != industry:
            continue
        if exclude_st and r.get("is_st"):
            continue
        if kw:
            haystack = f"{r.get('symbol') or ''} {(r.get('name') or '')}".lower()
            if kw not in haystack:
                continue
        out.append(r)
    return out


def stock_options(rows: list[dict]) -> dict:
    """筛选下拉可选值：出现在本批次行里的板块 / 行业（去重升序）。"""
    boards = sorted({str(r["board"]) for r in rows if r.get("board")})
    industries = sorted({str(r["industry"]) for r in rows if r.get("industry")})
    return {"boards": boards, "industries": industries}


def sort_stock_rows(rows: list[dict], sort: str = "",
                    dir_: str = "desc") -> tuple[list[dict], str, str]:
    """服务端排序：null 恒排末尾 + ``symbol`` 次级 tie-break（跨页不抖动）。

    两条纪律：
    1. **null 恒末尾**（asc / desc 都一样）—— 缺失值按 0 兜底会让 -1 / 0 污染
       升序结果（ETF 列表此前正是这个坑）；
    2. 次级键 ``symbol asc`` —— 否则同值行在并发 / 分页间顺序不定，翻页会
       看到重复或漏行。

    Args:
        rows: 行 dict 列表。
        sort: 排序字段，白名单见 ``STOCK_SORT_FIELDS``；非法或空 → 默认。
        dir_: ``asc|desc``；非法 → ``desc``。

    Returns:
        ``(rows, sort_applied, dir_applied)``：后两者是**实际生效**的值，
        路由层原样回显（契约要求非法 sort 不报错、只回落并回显）。
        默认口径 = ``code 升序``，故 ``sort=""``（含非法值回落）时
        ``dir_applied`` 恒为 ``asc`` —— 与请求的 dir 无关，保证首屏顺序确定、
        翻页不抖动。
    """
    applied_sort = sort if sort in STOCK_SORT_FIELDS else ""
    applied_dir = dir_ if dir_ in ("asc", "desc") else "desc"
    if not applied_sort:
        applied_dir = "asc"
    if not rows:
        return [], applied_sort, applied_dir

    df = pl.DataFrame(rows)
    if "_code" not in df.columns:
        df = df.with_columns(
            pl.col("symbol").cast(pl.String).str.split(".").list.first().alias("_code"))
    order_key = applied_sort or "code"
    order_col = "_code" if order_key == "code" else order_key
    if order_col not in df.columns:  # 字段缺失（schema 漂移）-> 退回默认
        order_col, applied_sort, applied_dir = "_code", "", "asc"
    elif order_key in _STOCK_NUMERIC_SORT_FIELDS:
        df = df.with_columns(pl.col(order_col).cast(pl.Float64, strict=False))

    df = df.sort([order_col, "symbol"],
                 descending=[applied_dir == "desc", False],
                 nulls_last=[True, False])
    return df.drop("_code").to_dicts(), applied_sort, applied_dir
