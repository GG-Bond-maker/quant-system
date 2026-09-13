"""选股过滤与快照（L2-1，Sprint3）。

数据层单一事实源：universe join / ST·停牌过滤 / 板块过滤 / score 降序的口径
只在这里实现，API 实时路径（api/v1/screener.py）与盘后快照写入
（pipeline.step_screener_dump）共同消费——两路径数字必须一致。

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

import polars as pl
from loguru import logger

from ..core.errors import AQPException, ERR_DATA_EMPTY, ERR_PARAMS
from ..core.config import get_settings

BOARDS = ("all", "main", "chinext_star", "bse")
SNAPSHOT_TOP_K = 200
STRATEGY = "alpha_basic_v1"


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
        AQPException: board 非法。
    """
    if board != "all" and board not in BOARDS:
        raise AQPException(ERR_PARAMS, f"board 仅支持 {sorted(set(BOARDS) | {'all'})}")

    universe = pl.DataFrame()
    uni_path = (get_settings().DATA_ROOT / "universe_daily" / "symbol=__all__"
                / f"year={trade_date[:4]}.parquet")
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


def enrich_items(df: pl.DataFrame, top_k: int) -> list[dict]:
    """榜单行富化：名称/行业兜底 + daily_bar 最新收盘/涨跌/换手（原 _build_items 口径）。"""
    from .universe import read_prev_and_today

    ins_info = instrument_info()
    items: list[dict] = []
    for r in df.head(top_k).iter_rows(named=True):
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
            "risk": "low" if score >= 0.3 else "mid" if score >= 0.1 else "high",
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
        "high_risk": sum(1 for i in items if i["risk"] == "high"),
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
                  it["amount"], it["limit_pct"], it["risk"])
                 for i, (it, r) in enumerate(zip(items, df.head(top_k).iter_rows(named=True)),
                                             start=1)])
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
            "amount": r["amount"], "limit_pct": r["limit_pct"], "risk": r["risk"],
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
                    "industry": r["industry"], "risk": r["risk"],
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
