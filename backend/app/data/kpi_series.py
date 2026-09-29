"""KPI 卡片的历史序列（30 日趋势）——单一事实源。

## 为什么需要本模块

前端选股中心 / ETF 中心顶部的 KPI 卡片此前用**假图形**：写死的装饰环形图、
只有两个点的"趋势线"（`values=[prev, today]`）、单根柱子。两点折线在视觉上
像趋势，实际不含任何趋势信息；`prev == null` 时更会传 `[x, x]` 画出一条
贴底平线，看起来"有历史且在横走"，实为无数据。

本模块为卡片提供**真实可回溯**的序列，并严格区分「有真实历史」与「没有」。

## 诚实性红线（本模块的核心约束）

1. **绝不用合成数据冒充真实指标**：不允许随机数、插值、两点连线、
   用当前值重复 N 次、或任何形式的兜底曲线。
2. **计数类指标的可比性必须如实判定**：`predictions` 各日分区覆盖度差异极大
   （实测 4.8%~97.5%，2026-09-07 仅 19 只），「股票数量 / 覆盖行业数 /
   强信号数」这类**计数**指标的日间起伏主要由**数据补全进度**驱动，不是市场
   变化。此类序列标记 ``comparable=False``，前端**不得**绘制为趋势。
3. **缺数据即缺数据**：某日分区缺失或样本不足时进 ``dropped`` 并写明原因，
   绝不静默补值。
4. 所有 ``basis`` 必须写清口径，不得为空串。

## 性能口径

逐标的复用 ``universe.read_prev_and_today`` 走 ``daily_bar/symbol=*`` 分区，
实测 **0.75 s/日**（30 日约 22s），不可用于请求路径。本模块改用
``cs/daily_bar`` **截面日线**（每日一文件，全市场 2492 行）做向量化 join，
实测 **0.006 s/日**（30 日合计 0.18s）。两条路径同日数值差 <0.001pp。

## 聚合公式的单一事实源

统计口径**必须**复用 :func:`app.data.screening.compute_stats`，禁止在
本模块另写一份聚合公式 —— 否则序列最新一日会与 ``/screener`` 实时榜单
对不上（口径漂移）。本模块只负责把截面数据拼成 ``compute_stats`` 需要的
``items`` 形状。
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date as date_cls
from datetime import datetime, timezone

import polars as pl
from loguru import logger

from ..cache.keys import k_etf_snap_history
from ..core.config import get_settings
from .etf_snapshot_sanitize import sanitize_etf_snapshots

# ---- 阈值（前端绘制决策的唯一依据）----
MIN_POINTS = 6            # 少于该点数不绘制趋势（如 ETF 侧存档初期只有 4 条）
MAX_WINDOW_DAYS = 60      # 窗口上限，防止一次回溯过多分区
COVERAGE_SPREAD = 0.10    # 计数类可比性阈值：coverage 极差 > 该值 ⇒ comparable=False

# ETF 快照存档：键名统一取自 cache/keys.py 的 k_etf_snap_history（与
# api/v1/etf.py 的 _SNAP_KEY 同一事实源，语义见该函数 docstring——它是持久
# 状态，不是缓存，禁止被按命名空间的批量清理删除）。
_ETF_SNAP_KEY = k_etf_snap_history()
_ETF_SNAP_MAX = 30


@dataclass(frozen=True)
class SeriesPoint:
    """序列中的一个真实数据点。

    ``n`` 为该日参与统计的样本数，``pool_size``/``coverage`` 用于计数类指标的
    可比性判定（coverage = pool_size / universe_rows）。
    """

    date: str
    value: float | None
    n: int | None = None
    pool_size: int | None = None
    coverage: float | None = None


@dataclass(frozen=True)
class MetricSeries:
    """一个指标的历史序列 + 口径 + 可绘制性判定。

    ``enough`` 与 ``comparable`` 是前端**唯一**的绘制开关：两者皆为 True 才画。
    """

    key: str
    kind: str          # 恒为 "platform"
    basis: str         # 口径说明（不得为空串）
    unit: str
    enough: bool
    comparable: bool
    count: int
    points: list[SeriesPoint] = field(default_factory=list)
    dropped: list[dict] = field(default_factory=list)
    note: str | None = None

    def to_dict(self) -> dict:
        return {
            "kind": self.kind,
            "basis": self.basis,
            "unit": self.unit,
            "enough": self.enough,
            "comparable": self.comparable,
            "count": self.count,
            "points": [
                {"date": p.date, "value": p.value, "n": p.n,
                 "pool_size": p.pool_size, "coverage": p.coverage}
                for p in self.points
            ],
            "dropped": self.dropped,
            "note": self.note,
        }


# ---------------- 内部工具 ----------------


def _as_date(v) -> date_cls | None:
    """宽容地把分区 stem / 字符串转成 date，失败返回 None。"""
    if isinstance(v, date_cls):
        return v
    if isinstance(v, datetime):
        return v.date()
    s = str(v)[:10].replace("-", "")
    try:
        return datetime.strptime(s, "%Y%m%d").date()
    except (ValueError, TypeError):
        return None


def _pred_dates_asc() -> list[date_cls]:
    """全部可用的预测日期（升序）。

    ``predictions`` 是逐日一文件的全局分区（**不是** symbol 分区），
    故这里直接 glob 文件名；不同于 ``universe`` 需按 symbol 读年份文件。
    """
    d = get_settings().DATA_ROOT / "predictions"
    if not d.exists():
        return []
    out: list[date_cls] = []
    for f in sorted(d.glob("date=*.parquet")):
        dt = _as_date(f.stem.replace("date=", ""))
        if dt is not None:
            out.append(dt)
    return out


def _cs_section(d: date_cls) -> pl.DataFrame | None:
    """读某日的全市场截面日线（cs/daily_bar），不存在返回 None。

    截面文件是**每日一个**（含全部标的），故无需逐 symbol 读年份分区 —— 这正是
    本模块能把 30 日耗时从 22s 压到 0.18s 的原因。
    """
    p = (get_settings().DATA_ROOT / "cs" / "daily_bar"
         / f"year={d.year}" / f"date={d.strftime('%Y%m%d')}.parquet")
    if not p.exists():
        return None
    try:
        return pl.read_parquet(p, columns=["symbol", "close"])
    except Exception as e:  # noqa: BLE001 单日损坏不应让整个序列不可用
        logger.warning(f"[kpi_series] 截面日线读取失败 {p.name}: {e!r}")
        return None


def _universe_rows_map(dates: list[date_cls]) -> dict[date_cls, int]:
    """批量取多日的 universe_daily 行数（coverage 判定的分母）。

    ⚠️ 必须**按年份分组一次读完**：初版实现对每个交易日各读一次整年文件
    （2026 年 43 万行），30 天即重复读 30 次 —— 实测把整体耗时从 0.2s 拖到 2.3s。
    这里每年只读一次，再按 date 聚合计数。
    """
    from .parquet_store import path_for_year

    by_year: dict[int, list[date_cls]] = {}
    for d in dates:
        by_year.setdefault(d.year, []).append(d)
    out: dict[date_cls, int] = {}
    for year, ds in by_year.items():
        up = path_for_year("universe_daily", "__all__", str(year))
        if not up.exists():
            continue
        try:
            u = pl.read_parquet(up, columns=["date"])
            if "date" not in u.columns:
                continue
            want = set(ds)
            counts = (u.filter(pl.col("date").is_in(list(want)))
                      .group_by("date").len())
            for row in counts.iter_rows(named=True):
                dt = row["date"]
                if isinstance(dt, datetime):
                    dt = dt.date()
                if isinstance(dt, date_cls):
                    out[dt] = int(row["len"])
        except Exception as e:  # noqa: BLE001 单年读取失败只影响 coverage 判定
            logger.warning(f"[kpi_series] universe 读取失败 {up.name}: {e!r}")
    return out


def _section_close_map(d: date_cls) -> dict[str, float]:
    """某日 ``symbol -> close``。"""
    df = _cs_section(d)
    if df is None or df.is_empty():
        return {}
    return dict(zip(df["symbol"].to_list(), df["close"].to_list()))


def _screener_day_stats(
    pred: pl.DataFrame,
    d: date_cls,
    top_k: int,
    board: str,
    close_map: dict[str, float],
    prev_close_map: dict[str, float],
) -> tuple[dict | None, dict | None, int]:
    """复算某日的榜单统计，返回 ``(stats, drop_reason, pool_size)``。

    ⚠️ 本函数**不自己算聚合**：它只把数据拼成 ``compute_stats`` 需要的
    ``items`` 形状，聚合公式由 :func:`app.data.screening.compute_stats` 提供
    （口径单一事实源）—— 这样序列的最新一日与 ``/screener`` 实时榜必然同口径。
    """
    from .screening import filter_universe, signal_strength_by_rank, SIGNAL_REFERENCE_DEPTH

    try:
        res = filter_universe(pred, d.isoformat(), board)
    except Exception as e:  # noqa: BLE001 单日算不出来不应拖垮整条序列
        return None, {"date": d.isoformat(), "reason": f"universe 过滤失败: {e!r}"}, 0

    pool_size = res.pool_size
    # 池子比请求的 top_k 还小 ⇒ 当日预测覆盖不全，榜单被池子规模截断。
    # 这类点不得硬塞进曲线（会把"数据没跑全"画成"市场变化"）。
    if pool_size < top_k:
        return None, {
            "date": d.isoformat(),
            "reason": f"pool_size({pool_size}) < top_k({top_k})：当日预测覆盖不全，"
                      f"榜单被池子规模截断",
        }, pool_size

    reference = res.df.head(SIGNAL_REFERENCE_DEPTH)
    labels = signal_strength_by_rank(reference.height)

    items: list[dict] = []
    for idx, r in enumerate(res.df.head(top_k).iter_rows(named=True)):
        sym = str(r["symbol"])
        close = close_map.get(sym)
        prev_close = prev_close_map.get(sym)
        pct: float | None = None
        if close is not None and prev_close:
            pct = round((close / prev_close - 1) * 100, 2)
        items.append({
            "symbol": sym,
            "industry": r.get("industry"),
            "close": close,
            "pct": pct,
            "score": round(float(r["pred_score"]), 6),
            "signal_strength": labels[idx] if idx < len(labels) else "weak",
        })
    # 有效样本不足（拿不到行情）⇒ 该日不可用于统计
    if not any(i["pct"] is not None for i in items):
        return None, {"date": d.isoformat(),
                      "reason": "当日榜单无可计算涨跌幅的样本（截面日线缺失）"}, pool_size

    from .screening import compute_stats

    return compute_stats(items, pool_size), None, pool_size


def _build_metric(
    key: str,
    basis: str,
    unit: str,
    raw: list[SeriesPoint],
    dropped: list[dict],
    *,
    countable: bool,
    note: str | None = None,
) -> MetricSeries:
    """按统一规则组装 ``MetricSeries``（enough / comparable 判定集中在此）。"""
    points = [p for p in raw if p.value is not None]
    enough = len(points) >= MIN_POINTS
    comparable = True
    final_note = note

    if countable:
        covs = [p.coverage for p in points if p.coverage is not None]
        if len(covs) >= 2 and (max(covs) - min(covs)) > COVERAGE_SPREAD:
            comparable = False
            spread_note = (
                f"窗口内预测覆盖度从 {min(covs) * 100:.1f}% 变到 {max(covs) * 100:.1f}%，"
                f"计数序列主要由数据补全进度驱动，不可解读为市场趋势"
            )
            final_note = f"{note}；{spread_note}" if note else spread_note

    return MetricSeries(
        key=key, kind="platform", basis=basis, unit=unit,
        enough=enough, comparable=comparable, count=len(points),
        points=points, dropped=dropped, note=final_note,
    )


# ---------------- 选股中心序列 ----------------


def screener_stats_series(
    *,
    days: int = 30,
    strategy: str = "alpha_basic_v1",
    top_k: int = 50,
    board: str = "all",
) -> dict[str, MetricSeries]:
    """选股中心 KPI 的历史序列（最近 ``days`` 个**有预测**的交易日）。

    实现要点：逐日读 ``predictions`` 分区 + ``cs/daily_bar`` 截面，用**前一日截面**
    的 close 作 prev_close 算涨跌幅，拼成 items 后交给 ``compute_stats``。

    Returns:
        ``key -> MetricSeries``，键集固定为
        ``pool_size / win_rate / avg_pct / avg_score / strong_signal / industry_count``。
    """
    days = max(MIN_POINTS, min(int(days or 30), MAX_WINDOW_DAYS))
    all_pred = _pred_dates_asc()
    if not all_pred:
        return _empty_screener_metrics("predictions 分区不存在，请先运行推理流水线")

    # 窗口内实际要用的日期（含 prev_close 需要的「多一天」余量）
    window = all_pred[-days:]
    if not window:
        return _empty_screener_metrics("窗口内无预测交易日")

    # 窗口内截面日线齐全的日期（prev_close 取「上一个可用截面」，故需往前多看若干天
    # 以覆盖「窗口首日的前一交易日」；这里向前扩 10 个预测日足矣 —— 预测分区只需
    # 存在**上一个截面**即可，不必追溯到窗口外更远）。
    probe = all_pred[max(0, len(all_pred) - days - 10):]
    section_dates = sorted({d for d in probe if _cs_section(d) is not None})
    pred_dates = [d for d in window if d in set(section_dates)]
    if not pred_dates:
        return _empty_screener_metrics("窗口内无同时具备预测与截面日线的交易日")

    # ⚠️ 只预热**窗口内 + 其前一交易日**的截面，不能对整个 probe 集全量预热：
    # 初版对 section_dates（含往前扩的日期）逐日调 _section_close_map，等于把
    # 用不到的日期也读了完整截面（每日 2492 行）。按需读是关键。
    need = set(pred_dates)
    for d in pred_dates:
        prev = next((x for x in reversed(section_dates) if x < d), None)
        if prev is not None:
            need.add(prev)
    sec_cache: dict[date_cls, dict[str, float]] = {d: _section_close_map(d) for d in need}

    uni_rows = _universe_rows_map(pred_dates)
    rows: list[dict] = []
    dropped: list[dict] = []
    for d in pred_dates:
        p = get_settings().DATA_ROOT / "predictions" / f"date={d.strftime('%Y%m%d')}.parquet"
        try:
            pred = pl.read_parquet(p)
        except Exception as e:  # noqa: BLE001
            dropped.append({"date": d.isoformat(), "reason": f"预测分区读取失败: {e!r}"})
            continue
        if pred.is_empty():
            dropped.append({"date": d.isoformat(), "reason": "预测分区为空"})
            continue
        # prev_close 取「上一个有截面的交易日」而非日历前一日（跨周末/节假日安全）
        prev_d = next((x for x in reversed(section_dates) if x < d), None)
        prev_map = sec_cache.get(prev_d, {}) if prev_d else {}
        stats, reason, pool_size = _screener_day_stats(
            pred, d, top_k, board, sec_cache.get(d, {}), prev_map)
        if reason:
            dropped.append(reason)
            continue
        assert stats is not None
        u_rows = uni_rows.get(d)
        cov = (pool_size / u_rows) if (u_rows and u_rows > 0) else None
        rows.append({"date": d.isoformat(), "stats": stats, "n": min(top_k, pool_size),
                     "pool_size": pool_size, "coverage": cov})

    if not rows:
        # ⚠️ 必须把已累积的 dropped **带进**空态：否则调用方只知道"没有数据"，
        # 不知道"为什么没有"（分区缺失 / pool_size < top_k / 截面缺失）。
        # 初版在这里直接 return 空态，丢掉了 dropped，等于把可诊断的失败
        # 降级成一句无信息的"不可用"。
        return _empty_screener_metrics(
            "窗口内无有效交易日可复算（详见各指标 dropped）", dropped=dropped)

    def _pts(field: str) -> list[SeriesPoint]:
        return [
            SeriesPoint(date=r["date"], value=r["stats"].get(field), n=r["n"],
                        pool_size=r["pool_size"], coverage=r["coverage"])
            for r in rows
        ]

    basis_suffix = (f"窗口 {rows[0]['date']} ~ {rows[-1]['date']}，逐日取预测分区按 "
                    f"pred_score 降序前 top_k={top_k}（board={board}）")
    out: dict[str, MetricSeries] = {}

    out["pool_size"] = _build_metric(
        "pool_size", unit="只", countable=True,
        basis=f"每日 universe 过滤（剔 ST/停牌）后、top_k 截断前的候选池规模；{basis_suffix}",
        raw=_pts("pool_size"), dropped=dropped,
    )
    out["win_rate"] = _build_metric(
        "win_rate", unit="%", countable=False,
        basis=f"每日前 top_k 名中当日上涨（pct>0）占比，等权；{basis_suffix}",
        raw=_pts("win_rate"), dropped=dropped,
    )
    out["avg_pct"] = _build_metric(
        "avg_pct", unit="%", countable=False,
        basis=f"每日前 top_k 名当日涨跌幅等权平均；涨跌幅由本地日终截面 cs/daily_bar "
              f"与上一交易日截面计算；{basis_suffix}",
        raw=_pts("avg_pct"), dropped=dropped,
    )
    out["avg_score"] = _build_metric(
        "avg_score", unit="小数", countable=False,
        basis=f"每日前 top_k 名模型 pred_score 等权平均；{basis_suffix}",
        raw=_pts("avg_score"), dropped=dropped,
    )
    out["strong_signal"] = _build_metric(
        "strong_signal", unit="只", countable=True,
        basis=f"每日 signal_strength=='strong' 的标的数（相对分位口径，参考总体为板块池前 "
              f"{200} 名）；{basis_suffix}",
        raw=_pts("strong_signal"), dropped=dropped,
    )
    out["industry_count"] = _build_metric(
        "industry_count", unit="个", countable=True,
        basis=f"每日前 top_k 名覆盖的行业数；{basis_suffix}",
        raw=_pts("industry_count"), dropped=dropped,
    )
    return out


def _empty_screener_metrics(reason: str,
                            dropped: list[dict] | None = None) -> dict[str, MetricSeries]:
    """全窗口不可用时的统一空态（enough=False，前端不画）。

    ``dropped`` 若传入则原样带给每个指标 —— 空态**不能**丢掉"为什么空"的证据。
    """
    spec = [
        ("pool_size", "只", "每日 universe 过滤后的候选池规模"),
        ("win_rate", "%", "每日前 top_k 名上涨占比"),
        ("avg_pct", "%", "每日前 top_k 名涨跌幅等权平均"),
        ("avg_score", "小数", "每日前 top_k 名 pred_score 等权平均"),
        ("strong_signal", "只", "每日强信号标的数"),
        ("industry_count", "个", "每日覆盖行业数"),
    ]
    dropped = dropped or []
    return {
        k: MetricSeries(key=k, kind="platform", basis=b, unit=u,
                        enough=False, comparable=False, count=0,
                        points=[], dropped=dropped, note=reason)
        for k, u, b in spec
    }


# ---------------- ETF 中心序列 ----------------


def _read_etf_snapshots() -> list[dict]:
    """读 Redis 里的 ETF 概览存档（``aqp:etf:snap:history``）。

    ⚠️ 存储形态是**单个 JSON 字符串**（不是 Redis list）：写入方
    ``api/v1/etf.py:append_etf_snapshot`` 用 ``orjson.dumps(history)`` +
    ``RedisClient.set``。此处**必须用同一套 ``RedisClient`` + ``orjson``**，
    否则会得到一个恒空的序列（`lrange` 对 string 型 key 报 WRONGTYPE）。

    同步上下文：``RedisClient`` 的异步 API 需要事件循环，故本函数走
    ``RedisClient.get_sync``（内部自建短连接，失败即返回 None）。Redis 不可用
    时返回 ``[]``，由上层判 ``enough=False`` —— 不抛异常，也不编造替代数据。

    ⚠️ 返回值**已过净化**（``sanitize_etf_snapshots``）：旧口径存档里
    ``net_inflow_yi=0.0`` 无法区分真实零净流入与取数失败，一律置 ``None`` 并补
    ``flow.status="unavailable"``；非零旧值保留但只标口径、不标 ok。净化幂等，
    对新口径行（已有 ``flow`` 子对象）零改动。
    """
    raw = _redis_get_sync(_ETF_SNAP_KEY)
    if not raw:
        return []
    try:
        import orjson

        data = orjson.loads(raw)
    except Exception as e:  # noqa: BLE001 存档损坏 ⇒ 视为无存档（不猜内容）
        logger.warning(f"[kpi_series] ETF 存档解析失败: {e!r}")
        return []
    if not isinstance(data, list):
        logger.warning("[kpi_series] ETF 存档不是列表，按无存档处理")
        return []
    rows = [d for d in data if isinstance(d, dict)]
    # 净化「旧口径假零」：历史存档里 net_inflow_yi=0.0 无法区分真实零净流入与
    # 取数失败（见 app/data/etf_snapshot_sanitize.py），任何读取路径都不能让
    # 它冒充真实指标。该函数幂等，对新口径行（已有 flow 子对象）零改动。
    return sanitize_etf_snapshots(rows)


def _redis_get_sync(key: str) -> str | None:
    """同步读 Redis 字符串（供非异步上下文使用）。

    刻意**不复用** ``cache.redis_client.RedisClient``：后者面向 async 场景，
    且带熔断状态，在 ``asyncio.to_thread`` 里调用会污染主循环的连接池。
    这里自建一次性连接，失败一律返回 None（调用方降级），3s 超时避免拖慢请求。
    """
    try:
        import redis  # type: ignore
    except Exception:  # noqa: BLE001
        return None
    s = get_settings()
    if not getattr(s, "REDIS_ENABLED", False):
        return None
    try:
        cli = redis.Redis(
            host=s.REDIS_HOST, port=s.REDIS_PORT, db=s.REDIS_DB,
            password=s.REDIS_PASSWORD, decode_responses=True,
            socket_timeout=3, socket_connect_timeout=3,
        )
        try:
            v = cli.get(key)
        finally:
            cli.close()
        return str(v) if v is not None else None
    except Exception as e:  # noqa: BLE001 Redis 不可用 ⇒ 上层降级
        logger.warning(f"[kpi_series] Redis 读取失败 {key}: {e!r}")
        return None


# 判定"同一观测被重复标注"时逐位比较的**指标字段**（不含 date/source/archived_*
# 等元字段）。必须比对全部指标：只比 ``avg_pct`` 之类的单字段会漏判（实测
# 2026-09-24=-0.4776 与 09-28=-1.5158 单看该字段并不相同），也会误删。
_ETF_SNAP_OBS_FIELDS = (
    "etf_count", "total_size_yi", "avg_pct", "net_inflow_yi", "amount_yi",
)


def _same_observation(a: dict, b: dict) -> bool:
    """两条 ETF 存档是否为**同一观测**（全部指标字段逐位相同）。

    为什么必须比**全部**指标而非单字段：真实相邻交易日恰好在某一字段上取值相同
    是常见的，只比单字段会误删真实观测；而"盘前占位/周末归档"是把**上一交易日
    的完整快照**原样重复标注到多个自然日上 ⇒ 全部指标逐位相同才是可靠判据。

    Args:
        a: 前一条存档（``date`` 更小）。
        b: 后一条存档（紧邻 ``a``）。

    Returns:
        全部 ``_ETF_SNAP_OBS_FIELDS`` 字段取值都相同则 True。
    """
    return all(a.get(f) == b.get(f) for f in _ETF_SNAP_OBS_FIELDS)


def etf_overview_series(*, days: int = 30) -> dict[str, MetricSeries]:
    """ETF 中心 KPI 的历史序列。

    ⚠️ **当前必然 ``enough=False``**：ETF 概览统计 100% 实时来自外部源、
    本地无落库；唯一存档是 Redis 每日快照，而它是**被动累积**的（历史遗留
    存档存在口径断裂：东财源不可达后切到新浪+腾讯，前后不可比）。
    故本函数按 ``source`` 过滤到与最新一条同口径的记录，返回真实条数，
    由 ``enough`` 判定是否可画 —— **绝不用任何方式补齐凑数**。

    另需剔除两类**非独立观测**：
    1. **非交易日快照**（周末/节假日/盘前归档，外部源当日返回的是上一交易日
       收盘 ⇒ 与前一日数值重复）；
    2. **相邻同值快照**（盘前/周末的被动归档把同一观测重复标注到多个自然日上，
       相邻两条的全部指标逐位相同 ⇒ 只能算一个观测）。
    归档双判据（``should_archive_etf_snapshot``）只管未来写入，历史脏数据只能在
    读取侧过滤 —— 否则趋势线会长出周末重复值拼成的假平线。被剔除的日期连同原因
    进 ``dropped``，**绝不静默丢弃**。

    30 天后（每日归档累积到 ≥ MIN_POINTS 条同口径记录）本函数会自动
    返回可绘制序列，前端无需改动。
    """
    snaps = _read_etf_snapshots()
    spec = [
        ("etf_count", "只", "全市场 ETF 数量"),
        ("total_size_yi", "亿元", "全市场 ETF 估算规模合计（份额 × 收盘）"),
        ("avg_pct", "%", "全市场 ETF 当日涨跌幅等权平均"),
        ("net_inflow_yi", "亿元", "ETF 主力净流入合计（东财口径）"),
        ("amount_yi", "亿元", "全市场 ETF 成交额合计"),
    ]
    if not snaps:
        return {
            k: MetricSeries(key=k, kind="platform", basis=b, unit=u,
                            enough=False, comparable=False, count=0, points=[],
                            dropped=[],
                            note="ETF 概览历史快照为空：该指标无本地落库，"
                                 "存档自每日归档任务上线后开始累积")
            for k, u, b in spec
        }

    # 按最新一条的 source 过滤 —— 口径断裂前后的数据不可比，不得混在同一条曲线
    latest_src = snaps[-1].get("source")
    mismatched: list[dict] = [s for s in snaps if s.get("source") != latest_src]
    # 同口径原始序列按日期升序 —— 相邻同值判定必须先排好序。
    consensus = sorted(
        (s for s in snaps if s.get("source") == latest_src),
        key=lambda s: str(s.get("date") or ""),
    )

    same: list[dict] = []
    non_trading: list[dict] = []
    duplicated: list[dict] = []
    raw_prev: dict | None = None
    for s in consensus:
        # ① 相邻同值去重（必须在交易日过滤**之前**）：盘前/周末的被动归档会把
        #    上一交易日的**同一观测**重复标注到多个自然日上（实测 09-26/27/28
        #    逐位相同）。这里逐条与**紧邻的前一条**比较**全字段值**，相同即判定
        #    "同一观测被重复标注"，只保留该链最早的一条作代表、其余剔除。
        #    ⚠️ 不能拿"与前一**保留**点比较"来替代：那样在交易日过滤**之后**再比，
        #    09-24(-0.4776) 与 09-28(-1.5158) 已不同值、抓不到污染点。
        is_dup = raw_prev is not None and _same_observation(raw_prev, s)
        raw_prev = s
        if is_dup:
            duplicated.append(s)
            continue
        ds = str(s.get("date") or "")
        try:
            d = date_cls.fromisoformat(ds)
        except ValueError:
            non_trading.append(s)
            continue
        # ② 非交易日快照必须剔除：双判据（``should_archive_etf_snapshot``）只挡未来
        #    写入，**挡不住判据上线前已落盘的脏数据**。实测线上归档 8 条里，
        #    2026-09-26（六）/09-27（日）两条与 09-25 周五数值完全相同，
        #    09-28 那条是 02:30 盘前写的、同样是周五数值 —— 三者都非独立观测。
        #    ① 的去重会先留 09-26 作代表、剔掉 09-27/09-28；09-26 又被本步判
        #    非交易日剔除 ⇒ 最终只剩 09-24。注释描述的风险必须被代码真正消掉。
        (same if _is_trading_day(d) else non_trading).append(s)
    older = same[:-days] if len(same) > days else []
    same = same[-days:]
    dropped = [
        {"date": str(s.get("date")), "reason": f"source={s.get('source')!r} "
                                               f"与最新口径 {latest_src!r} 不一致，不可比"}
        for s in mismatched
    ] + [
        {"date": str(s.get("date")), "reason":
         "与相邻存档全部指标同值 ⇒ 判定为同一观测被重复标注（多为盘前占位），"
         "不作为独立观测点计入趋势"}
        for s in duplicated
    ] + [
        {"date": str(s.get("date")), "reason":
         "非交易日快照（外部源当日返回的是上一交易日收盘，与前一日数值重复），"
         "不作为独立观测点计入趋势"}
        for s in non_trading
    ] + [
        {"date": str(s.get("date")), "reason": f"早于窗口起点，仅保留最近 {days} 天"}
        for s in older
    ]

    out: dict[str, MetricSeries] = {}
    for k, u, b in spec:
        pts = [
            SeriesPoint(date=str(s.get("date")), value=s.get(k),
                        n=s.get("etf_count"), pool_size=None, coverage=None)
            for s in same if s.get(k) is not None
        ]
        if not pts:
            out[k] = MetricSeries(
                key=k, kind="platform", basis=b, unit=u, enough=False,
                comparable=False, count=0, points=[], dropped=dropped,
                note="同口径存档不足；该指标无本地落库，靠每日归档累积")
            continue
        out[k] = _build_metric(
            k, basis=f"{b}；来源 = 每日盘后快照存档（source={latest_src}）；"
                     f"窗口 {pts[0].date} ~ {pts[-1].date}",
            unit=u, raw=pts, dropped=dropped, countable=False,
            note=f"每日盘后快照自 {pts[0].date} 起累积，当前 {len(pts)}/{days} 天"
                 f"（需 ≥{MIN_POINTS} 天才绘制趋势）",
        )
    return out


def _is_trading_day(d: date_cls) -> bool:
    """是否 A 股交易日（读 SQLite ``trade_calendar``）。表缺失时保守返回 False。

    为何必须判：ETF 快照归档按**自然日**运行会把周末/节假日/盘前的重复值灌进
    序列 —— 实测 2026-09-26（六）/09-27（日）/09-28（盘前）三天数值完全相同
    （外部源返回的都是 09-25 周五收盘）。不判交易日则 30 天里约 10 天是重复点，
    长出来的"趋势"依旧是假的。
    """
    import sqlite3
    path = get_settings().SQLITE_PATH
    if not path.exists():
        return False
    try:
        con = sqlite3.connect(path, timeout=30)
        try:
            row = con.execute(
                "SELECT 1 FROM trade_calendar WHERE trade_date = ? AND (is_sh = 1 OR is_sz = 1)",
                (d.isoformat(),),
            ).fetchone()
        finally:
            con.close()
        return row is not None
    except Exception as e:  # noqa: BLE001 表缺失/异常 ⇒ 保守判非交易日（宁可漏归档也不灌重复值）
        logger.warning(f"[kpi_series] trade_calendar 查询失败: {e!r}")
        return False


def should_archive_etf_snapshot(now: datetime | None = None) -> tuple[bool, str]:
    """是否应写入 ETF 快照存档，返回 ``(是否写, 原因)``。

    双判据（缺一不可）：
    1. **交易日** —— 非交易日外部源返回的是上一交易日收盘数据，写进去就是重复值；
    2. **盘后** —— 未收盘时写进去的是盘中快照，与其余存档日的"收盘值"口径不同。

    这是本模块对「按自然日归档会灌入重复值」的修复点。
    """
    now = now or datetime.now(timezone.utc).astimezone()
    d = now.date()
    if not _is_trading_day(d):
        return False, "非交易日：外部源返回上一交易日收盘数据，重复写入会造成假趋势"
    # A 股收盘 15:00（含收盘集合竞价到 15:30 数据落定），保守取 15:30 之后
    if (now.hour, now.minute) < (15, 30):
        return False, "未到盘后时点（需 15:30 之后）：盘中快照与收盘口径不一致"
    return True, "交易日盘后"
