"""AI 日报（前沿演进 Phase 2 · 1-3：模板版，无 LLM 硬依赖）。

每日盘后由晚间例行调度或手动触发生成，聚合真实落库数据：
数据面（行情/预测新鲜度、流水线作业）→ 因子健康度（monitor 快照）→
模拟盘与执行质量（当日成交、冲击/基差 bps）→ 持仓贡献 → 风控与事件。

确定性模板拼装（本环境网络不稳定，"每日必出"的东西不单点依赖外部 LLM）；
LLM 摘要为可选增强（后续接 provider）。结构化 sections 供前端原生渲染，
另附等价 markdown 便于复制/导出。快照持久化 app_state（最近 30 期）。
"""
from __future__ import annotations

import asyncio
from datetime import date, datetime

import pandas as pd
import sqlalchemy as sa
from fastapi import APIRouter, Depends
from loguru import logger

from ...core import events
from ...core.auth import require_role
from ...core.config import get_settings
from ...core.errors import APIResponse, ok
from ...db.kv import kv_get, kv_set
from ...trading import paper
from ...trading.paper import sync_session_factory

router = APIRouter()

_REPORTS_KEY = "daily_reports"
_REPORTS_MAX = 30


# ---------------- 数据采集 ----------------
def _data_freshness() -> dict:
    """行情/预测新鲜度（轻量读取，不 concat 全库）。"""
    s = get_settings()
    out: dict = {}
    ref = paper._norm_symbol("000001.SZ")  # noqa: SLF001 参考标的（池内必有数据）
    try:
        from ...data.parquet_store import read_symbol_dataset

        df = read_symbol_dataset("daily_bar", ref)
        out["daily_bar_last"] = str(df["date"].max()) if not df.is_empty() else None
    except Exception:  # noqa: BLE001
        out["daily_bar_last"] = None
    pred_dir = s.DATA_ROOT / "predictions"
    files = sorted(pred_dir.glob("date=*.parquet"))
    out["prediction_last"] = files[-1].stem.replace("date=", "") if files else None
    out["n_pred_files"] = len(files)
    return out


def _last_pipeline_jobs(limit: int = 3) -> list[dict]:
    """近期流水线作业（data_jobs 表，只读）。"""
    Session = sync_session_factory()
    with Session() as sess:
        rows = sess.execute(
            sa.text("SELECT job_type, trade_date, status, current_step "
                    "FROM data_jobs ORDER BY trade_date DESC, id DESC LIMIT :l"),
            {"l": limit}).mappings().all()
        return [{k: (str(v) if v is not None else None) for k, v in dict(r).items()}
                for r in rows]


def _paper_section(day: date) -> dict:
    """模拟盘 + 当日执行质量（全部由真实成交推导）。"""
    Session = sync_session_factory()
    with Session() as sess:
        acc = paper.account_summary(sess)
        fills = sess.execute(
            sa.select(paper.PaperFill).where(paper.PaperFill.exec_date == day)
            .order_by(paper.PaperFill.id)).scalars().all()
        positions = acc.get("positions") or {}
        contrib = sorted(
            ({"symbol": sym, "qty": p.get("qty"), "pnl": p.get("pnl")}
             for sym, p in positions.items()),
            key=lambda x: (x["pnl"] if x["pnl"] is not None else 0), reverse=True)
        return {
            "equity": acc.get("equity"), "cash": acc.get("cash"),
            "market_value": acc.get("market_value"),
            "n_fills": acc.get("n_fills"), "total_fees": acc.get("total_fees"),
            "annualized_return": acc.get("annualized_return"),
            "max_drawdown": acc.get("max_drawdown"), "sharpe": acc.get("sharpe"),
            "n_positions": len(positions),
            "kill_switch": paper.get_kill_switch(sess),
            "today_fills": {
                "count": len(fills),
                "amount": round(sum(f.amount for f in fills), 2),
                "avg_impact_bps": round(float(
                    pd.Series([f.impact_bps for f in fills]).mean()), 2) if fills else None,
                "avg_basis_bps": round(float(
                    pd.Series([f.basis_bps for f in fills]).mean()), 1) if fills else None},
            "top_contrib": contrib[:3],
            "bottom_contrib": list(reversed(contrib[-3:])) if len(contrib) > 3 else [],
        }


def _monitor_section() -> dict:
    """因子健康度节（读 monitor 快照，不现算）。"""
    from ...ml import monitor

    snap = monitor.get_health_snapshot()
    if snap is None:
        return {"state": "unknown",
                "note": "尚未运行监控（可 POST /monitor/run 或等待晚间例行）"}
    ret = monitor.get_retrain_record()
    return {"state": snap.get("state"), "ic_state": snap.get("ic_state"),
            "drift_state": snap.get("drift_state"),
            "recent": snap.get("recent"), "history": snap.get("history"),
            "half_life": snap.get("half_life"), "psi": snap.get("psi"),
            "ks": snap.get("ks"),
            "computed_at": snap.get("computed_at"),
            "last_retrain": ({k: ret.get(k) for k in
                              ("status", "model_version", "promoted", "gate_reason",
                               "finished_at")} if ret else None)}


# ---------------- 组装 ----------------
def _brinson_section(window_days: int = 20) -> dict:
    """Brinson 行业归因（模拟盘真实持仓权重 vs 同池等权基准）。

    复用 domain.attribution.brindon_attribution（纯函数）；行业取 universe
    最新真实截面，缺失记「未分类」；收益取 hfq 后复权窗口收益。
    """
    import polars as pl

    from ...data.parquet_store import read_symbol_dataset
    from ...domain.attribution import brindon_attribution

    s = get_settings()
    Session = sync_session_factory()
    with Session() as sess:
        acc = paper.account_summary(sess)
    mv = {sym: float(p.get("market_value") or 0)
          for sym, p in (acc.get("positions") or {}).items()}
    mv = {k: v for k, v in mv.items() if v > 0}
    if not mv:
        return {"ok": False, "reason": "模拟盘当前无持仓"}
    tot_mv = sum(mv.values())
    port_w = {k: v / tot_mv for k, v in mv.items()}

    closes: dict[str, "pd.Series"] = {}
    for sym in port_w:
        df = read_symbol_dataset("daily_bar_hfq", sym)
        if df.is_empty():
            continue
        if df.schema["date"] != pl.Date:
            df = df.with_columns(pl.col("date").cast(pl.Date))
        closes[sym] = (df.sort("date").select(["date", "close"])
                       .to_pandas().set_index("date")["close"])
    if not closes:
        return {"ok": False, "reason": "持仓标的均无本地 hfq 行情"}
    close_pd = pd.DataFrame(closes).sort_index().tail(window_days + 1)
    if len(close_pd) < 10:
        return {"ok": False, "reason": f"窗口收益样本不足（{len(close_pd) - 1} 日 < 10）"}

    industry: dict[str, str] = {}
    ufiles = sorted((s.DATA_ROOT / "universe_daily").rglob("*.parquet"))
    if ufiles:
        uni = pl.concat([pl.read_parquet(f) for f in ufiles],
                        how="diagonal_relaxed")
        if uni.schema["date"] != pl.Date:
            uni = uni.with_columns(pl.col("date").cast(pl.Date))
        uni_last = uni.filter(pl.col("date") == uni["date"].max())
        industry = dict(zip(uni_last["symbol"], uni_last["industry"]))

    sym_ret = close_pd.iloc[-1] / close_pd.iloc[0] - 1.0
    bench_w = {sym: 1.0 / close_pd.shape[1] for sym in close_pd.columns}
    br = brindon_attribution(
        portfolio_w=port_w,
        benchmark_w=bench_w,
        returns=sym_ret,
        industry=pd.Series({sym: (industry.get(sym) or "未分类")
                            for sym in sym_ret.index}))
    return {"ok": True, "window_days": int(len(close_pd) - 1),
            "n_positions": len(port_w),
            "summary": br["summary"], "top_sectors": br["sectors"][:3],
            "basis": ("模拟盘真实持仓市值权重 vs 同池等权基准；hfq 后复权"
                      f"{len(close_pd) - 1} 个交易日窗口收益；行业取 universe "
                      "最新截面 industry（缺失记「未分类」）")}


def _score_shift_section(top_n: int = 10) -> dict | None:
    """榜单变动（快照 top-N 进出）+ 模型分数迁移（近两期 top-K 重合度与分差）。"""

    import polars as pl

    s = get_settings()
    lines: list[str] = []

    # 榜单变动：screener_snapshot 最近两期（all 板块）top-N 进出
    try:
        import sqlite3

        with sqlite3.connect(f"file:{s.SQLITE_PATH}?mode=ro", uri=True) as conn:
            dates = [r[0] for r in conn.execute(
                "SELECT DISTINCT date FROM screener_snapshot WHERE board='all' "
                "ORDER BY date DESC LIMIT 2")]
            if len(dates) == 2:
                def _top(d: str) -> dict[str, int]:
                    rows = conn.execute(
                        "SELECT symbol, rank FROM screener_snapshot "
                        "WHERE board='all' AND date=? AND rank<=? ORDER BY rank",
                        (d, top_n)).fetchall()
                    return {r[0]: r[1] for r in rows}
                cur, prev = _top(dates[0]), _top(dates[1])
                entered = sorted(set(cur) - set(prev))
                exited = sorted(set(prev) - set(cur))
                lines.append(
                    f"- 选股榜 top{top_n}（{dates[0]} vs {dates[1]}）："
                    f"新进 {len(entered)} / 跌出 {len(exited)}")
                if entered:
                    lines.append(f"  - 新进：{'、'.join(entered[:6])}"
                                 + ("…" if len(entered) > 6 else ""))
                if exited:
                    lines.append(f"  - 跌出：{'、'.join(exited[:6])}"
                                 + ("…" if len(exited) > 6 else ""))
    except Exception as e:  # noqa: BLE001
        logger.debug(f"[report] snapshot shift degraded: {e!r}")

    # 分数迁移：近两期 predictions top-K 重合率 + 分数分布变化
    try:
        files = sorted((s.DATA_ROOT / "predictions").glob("date=*.parquet"))
        if len(files) >= 2:
            k = 50
            tops = []
            for f in files[-2:]:
                df = pl.read_parquet(f).sort("pred_score", descending=True).head(k)
                tops.append(dict(zip(df["symbol"].to_list(),
                                     df["pred_score"].to_list())))
            overlap = len(set(tops[1]) & set(tops[0])) / k
            mean_cur = sum(tops[0].values()) / len(tops[0])
            mean_prev = sum(tops[1].values()) / len(tops[1])
            d0 = files[-1].stem.replace("date=", "")
            d1 = files[-2].stem.replace("date=", "")
            lines.append(
                f"- 模型分数迁移（top{k}，{d1}→{d0}）：重合率 **{overlap:.0%}**，"
                f"均分 {mean_prev:.4f}→{mean_cur:.4f}"
                f"（{'↑' if mean_cur >= mean_prev else '↓'}"
                f"{abs(mean_cur - mean_prev):.4f}）")
    except Exception as e:  # noqa: BLE001
        logger.debug(f"[report] score shift (predictions) degraded: {e!r}")

    return {"title": "二、榜单与模型", "lines": lines} if lines else None


def build_daily_report(day: date | None = None) -> dict:
    """聚合当日数据为结构化日报（确定性模板，无外部依赖）。"""
    day = day or date.today()
    sections: list[dict] = []
    tips: list[str] = []

    fresh = _data_freshness()
    sections.append({"title": "一、数据面", "lines": [
        f"- 交易日：{day.isoformat()}",
        f"- 行情最新日期：{fresh.get('daily_bar_last') or '—'}（参考标的 000001.SZ）",
        f"- 预测最新日期：{fresh.get('prediction_last') or '—'}"
        f"（共 {fresh.get('n_pred_files', 0)} 期 predictions）",
    ]})
    try:
        jobs = _last_pipeline_jobs()
        if jobs:
            sections[-1]["lines"].append(
                "- 近期流水线：" + "；".join(
                    f"{j['trade_date']} {j['status']}@{j['current_step'] or '-'}"
                    for j in jobs))
    except Exception:  # noqa: BLE001 日报不因单节失败中断
        pass
    if fresh.get("prediction_last") and fresh["prediction_last"] < day.strftime("%Y%m%d"):
        tips.append(f"predictions 落后于当日（最新 {fresh['prediction_last']}），"
                    f"请检查晚间例行/autoSync 是否正常")

    # 榜单变动 + 模型分数迁移（Sprint4 §4.6：数据源为 Sprint3 screener_snapshot
    # 与 predictions——缺失/单期时如实跳过，不造数）
    try:
        sec = _score_shift_section()
        if sec:
            sections.append(sec)
    except Exception as e:  # noqa: BLE001 日报不因单节失败中断
        logger.debug(f"[report] score shift section degraded: {e!r}")

    mon = _monitor_section()
    r = mon.get("recent") or {}
    hl = mon.get("half_life") or {}
    psi = mon.get("psi") or {}
    lines = [f"- 因子健康度：**{mon.get('state')}**"
             f"（IC {mon.get('ic_state')} / 漂移 {mon.get('drift_state')}）"
             f" @ {mon.get('computed_at') or '—'}"]
    if r.get("mean_ic") is not None:
        lines.append(f"- 近 {r.get('window')} 日 Mean RankIC：**{r['mean_ic']}**，"
                     f"ICIR {r.get('icir')}")
    if hl.get("value") is not None:
        lines.append(f"- Alpha 半衰期：**{hl['value']} 个交易日**（{hl.get('note')}）")
    else:
        lines.append(f"- Alpha 半衰期：—（{hl.get('note', '无法拟合')}）")
    if psi.get("ok"):
        top = "、".join(f"{t['factor']}({t['psi']})" for t in psi.get("top", [])[:3])
        lines.append(f"- PSI 漂移：max **{psi.get('max')}** / mean {psi.get('mean')}"
                     f"（{psi.get('n_factors')} 个特征；最大：{top}）")
    ks = mon.get("ks") or {}
    if ks.get("ok"):
        ktop = "、".join(f"{t['factor']}({t['ks']})" for t in ks.get("top", [])[:3])
        lines.append(f"- KS 互验：max **{ks.get('max')}** / mean {ks.get('mean')}，"
                     f"超 5% 临界值特征 {ks.get('n_over_crit', 0)} 个"
                     f"（最大：{ktop}；判定仍以 PSI 为准）")
    if mon.get("last_retrain"):
        lr = mon["last_retrain"]
        lines.append(f"- 上次自动重训：{lr.get('status')}"
                     f" promote={lr.get('promoted')}（{lr.get('finished_at')}）")
    sections.append({"title": "三、因子健康度", "lines": lines})
    if mon.get("state") == "degraded":
        tips.append("因子处于降级状态：建议降低该因子权重或暂停依据其信号加仓，"
                    "等待漂移检测与重训结果")
    elif mon.get("state") == "watch":
        tips.append("因子进入观察区：关注未来数日 RankIC 是否继续走弱")

    try:
        paper_sec = _paper_section(day)
        tf = paper_sec.get("today_fills") or {}

        def _n(v) -> str:
            return "—" if v is None else str(v)

        sections.append({"title": "四、模拟盘与执行", "lines": [
            f"- 账户净值：**{_n(paper_sec.get('equity'))}**"
            f"（现金 {_n(paper_sec.get('cash'))}，市值 {_n(paper_sec.get('market_value'))}，"
            f"持仓 {paper_sec.get('n_positions')} 只）",
            f"- 累计成交 {paper_sec.get('n_fills')} 笔，总费用 {_n(paper_sec.get('total_fees'))}",
            f"- 今日成交：{tf.get('count', 0)} 笔 / {tf.get('amount', 0)} 元"
            f"（平均冲击 {_n(tf.get('avg_impact_bps'))} bps，"
            f"平均基差 {_n(tf.get('avg_basis_bps'))} bps）",
            f"- Kill Switch：{'⚠️ 已熔断' if paper_sec.get('kill_switch') else '正常'}",
        ]})
        top_contrib = [c for c in paper_sec.get("top_contrib") or []
                       if c.get("pnl") is not None]
        bot_contrib = [c for c in paper_sec.get("bottom_contrib") or []
                       if c.get("pnl") is not None]
        if top_contrib:
            sections[-1]["lines"].append(
                "- 贡献居前：" + "；".join(
                    f"{c['symbol']} {c['pnl']:+.0f}元" for c in top_contrib))
        if bot_contrib:
            sections[-1]["lines"].append(
                "- 拖累居前：" + "；".join(
                    f"{c['symbol']} {c['pnl']:+.0f}元" for c in bot_contrib))
        if paper_sec.get("kill_switch"):
            tips.append("Kill Switch 处于熔断状态：全部新订单被拒绝，"
                        "确认后请在执行中心恢复")
    except Exception as e:  # noqa: BLE001
        logger.warning(f"[report] 模拟盘节失败: {e!r}")
        sections.append({"title": "四、模拟盘与执行",
                         "lines": [f"- 暂不可用（{type(e).__name__}）"]})

    # ---- Brinson 行业归因（1-3 补全：原「持仓贡献」升级为行业配置/选股分解） ----
    try:
        br = _brinson_section()
        if br.get("ok"):
            summ = br["summary"]
            blines = [
                f"- 窗口超额（{br['window_days']} 交易日）："
                f"**{summ['excess_return']:+.4%}**"
                f"（组合 {summ['portfolio_return']:+.2%} vs"
                f" 基准 {summ['benchmark_return']:+.2%}，{br['n_positions']} 只持仓）",
                f"- 配置 {summ['allocation']:+.4%} · 选股 {summ['selection']:+.4%}"
                f" · 交互 {summ['interaction']:+.4%}"
                f" · 残差 Alpha {summ['residual_alpha']:+.4%}",
            ]
            top_sec = br.get("top_sectors") or []
            if top_sec:
                blines.append("- 行业贡献居前：" + "；".join(
                    f"{x['industry']} {x['total']:+.4%}"
                    f"（配置 {x['allocation']:+.4%}/选股 {x['selection']:+.4%}）"
                    for x in top_sec))
            blines.append(f"- ⓘ 口径：{br['basis']}")
            sections.append({"title": "五、Brinson 行业归因", "lines": blines})
        else:
            sections.append({"title": "五、Brinson 行业归因",
                             "lines": [f"- 暂不可用（{br.get('reason')}）"]})
    except Exception as e:  # noqa: BLE001
        logger.warning(f"[report] brinson 节失败: {e!r}")
        sections.append({"title": "五、Brinson 行业归因",
                         "lines": [f"- 暂不可用（{type(e).__name__}）"]})

    recent_events = events.recent()[-5:]
    sections.append({"title": "六、最近事件", "lines": [
        f"- [{e.get('ts')}] {e.get('message')}" for e in reversed(recent_events)
    ] or ["- 近期无事件"]})

    if tips:
        sections.append({"title": "七、风险提示", "lines": [f"- {t}" for t in tips]})

    markdown = "\n\n".join(
        f"## {s['title']}\n" + "\n".join(s["lines"]) for s in sections)
    return {"date": day.isoformat(),
            "generated_at": datetime.now().isoformat(timespec="seconds"),
            "sections": sections, "markdown": markdown, "tips": tips}


# ---------------- 存储与查询 ----------------
def _load_reports() -> list[dict]:
    return kv_get(_REPORTS_KEY) or []


def generate_and_store_report(day: date | None = None) -> dict:
    """生成当日（或指定日）日报并持久化（最近 30 期），推送顶栏通知。"""
    rep = build_daily_report(day)
    reports = [r for r in _load_reports() if r.get("date") != rep["date"]]
    reports.append(rep)
    kv_set(_REPORTS_KEY, reports[-_REPORTS_MAX:])
    events.publish_threadsafe("report",
                              f"AI 日报已生成（{rep['date']}，{len(rep['tips'])} 条提示）")
    # §4.6 渠道分发：配置 NOTIFY_WEBHOOK_URL 时同步推送（失败仅告警）
    webhook = get_settings().NOTIFY_WEBHOOK_URL
    if webhook:
        try:
            import httpx

            httpx.post(webhook, timeout=8, json={
                "kind": "report", "date": rep["date"],
                "tips": rep.get("tips", []),
                "sections": [x["title"] for x in rep.get("sections", [])]})
        except Exception as e:  # noqa: BLE001 webhook 失败不影响站内报告
            logger.warning(f"[report] webhook post failed: {e!r}")
    logger.info(f"[report] daily report generated: {rep['date']}")
    return rep


def get_latest_report() -> dict | None:
    reports = _load_reports()
    return reports[-1] if reports else None


def get_report(day: str) -> dict | None:
    for r in _load_reports():
        if r.get("date") == day:
            return r
    return None


# ---------------- 路由 ----------------
@router.get("/daily")
async def daily_report(
    date: str | None = None,
    _user: dict = Depends(require_role("viewer")),
) -> APIResponse[dict]:
    """最近一期 AI 日报（可 ?date=YYYY-MM-DD 查历史期）。"""
    reports = _load_reports()
    if date:
        rep = get_report(date)
    else:
        rep = get_latest_report()
    return ok({"report": rep, "history": [r["date"] for r in reports]})


@router.post("/daily/generate", response_model=APIResponse[dict])
async def regenerate_daily_report(
    _user: dict = Depends(require_role("researcher")),
) -> APIResponse[dict]:
    """立即重新生成今日日报（researcher；例行调度之外的手动触发）。"""
    rep = await asyncio.to_thread(generate_and_store_report)
    return ok(rep)
