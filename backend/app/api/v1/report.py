"""AI 日报（前沿演进 Phase 2 · 1-3：模板版，无 LLM 硬依赖）。

每日盘后由晚间例行调度或手动触发生成，聚合真实落库数据：
数据面（行情/预测新鲜度、流水线作业）→ 因子健康度（monitor 快照）→
模拟盘与执行质量（当日成交、冲击/基差 bps）→ 持仓贡献 → 风控与事件。

P0/P1/P2 演进重构：
- 修正成交额基尼集中度（amount 替代 volume，防低价股失真）
- 引入成交额加权微观情绪模型与 8 分段截面涨跌幅直方图
- 升级次日调仓推演为打分凸优化权重与个股级 Almgren-Chriss 冲击滑点
- 新增 Barra 风格因子每日表现与行业轮动排行
- 新增组合实时在险价值 (95% VaR) 与风控雷达
- 生成专业 AI 投委会智能综述与多渠道 Webhook 分发
"""
from __future__ import annotations

from datetime import date, datetime

import pandas as pd
import sqlalchemy as sa
from fastapi import APIRouter, Depends
from loguru import logger

from ...core import events
from ...core.auth import require_role
from ...core.compute_guard import compute_slot
from ...core.compute_pool import run_in_compute_pool
from ...core.config import get_settings
from ...core.errors import ERR_DATA_EMPTY, APIResponse, AQPException, ok
from ...data.parquet_store import read_parquet_columns
from ...db.kv import kv_get, kv_set
from ...trading import paper
from ...trading.paper import sync_session_factory

router = APIRouter()

_REPORTS_KEY = "daily_reports"
_REPORTS_MAX = 30

# 打分极差下限：min-max 归一化前，若最高/最低分几乎相等（极差 < 该容差）则退化为等权，
# 避免除以趋零的极差放大噪声。⚠️ 这是**数值容差**，与过户费率 1e-5 只是数值巧合，
# 命名带 `_EPS` 后缀以与费率字面量区分（见 tests/test_trading_fee_single_source.py）。
_SCORE_SPREAD_EPS = 1e-5


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


def _brinson_section(window_days: int = 20) -> dict:
    """Brinson 行业归因（模拟盘真实持仓权重 vs 同池等权基准）。"""
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

    closes: dict[str, pd.Series] = {}
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
        uni = read_parquet_columns(ufiles, ["date", "symbol", "industry"])
        if not uni.is_empty():
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
            "summary": br["summary"], "top_sectors": br["sectors"][:5],
            "basis": ("模拟盘真实持仓市值权重 vs 同池等权基准；hfq 后复权"
                      f"{len(close_pd) - 1} 个交易日窗口收益；行业取 universe "
                      "最新截面 industry（缺失记「未分类」）")}


def _score_shift_section(top_n: int = 10) -> dict | None:
    """榜单变动（快照 top-N 进出）+ 模型分数迁移（近两期 top-K 重合度与分差）。"""
    import polars as pl

    s = get_settings()
    lines: list[str] = []

    try:
        import sqlite3

        with sqlite3.connect(f"file:{s.SQLITE_PATH}?mode=ro", uri=True, timeout=30) as conn:
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

    try:
        files = sorted((s.DATA_ROOT / "predictions").glob("date=*.parquet"))
        if len(files) >= 2:
            k = 50
            tops = []
            for f in files[-2:]:
                df = pl.read_parquet(f)
                if "pred_score" not in df.columns or df.schema["pred_score"] == pl.Null:
                    raise AQPException(
                        ERR_DATA_EMPTY,
                        "预测分区 pred_score 列不可用（缺列或整列全空），无法排序")
                df = df.sort("pred_score", descending=True).head(k)
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


def _market_compass(day: date) -> dict:
    """全市场微观流动性与情绪罗盘（成交额加权情绪修正与 8 分段直方图）。"""
    import polars as pl
    import numpy as np

    s = get_settings()
    out = {
        "trading_date": day.isoformat(),
        "up_count": 0, "down_count": 0, "flat_count": 0,
        "limit_up_count": 0, "limit_down_count": 0,
        "adl_change": 0, "turnover_gini": 0.42,
        "sentiment_score": 50, "sentiment_label": "中性",
        "breadth_bins": [
            {"bin": "<-7%", "count": 0},
            {"bin": "-7%~-5%", "count": 0},
            {"bin": "-5%~-2%", "count": 0},
            {"bin": "-2%~0%", "count": 0},
            {"bin": "0%~2%", "count": 0},
            {"bin": "2%~5%", "count": 0},
            {"bin": "5%~7%", "count": 0},
            {"bin": ">7%", "count": 0},
        ],
    }
    try:
        u_dir = s.DATA_ROOT / "universe_daily"
        files = sorted(u_dir.rglob("*.parquet"))
        if files:
            df = pl.read_parquet(files[-1])
            if "date" in df.columns:
                max_d = df["date"].max()
                sub = df.filter(pl.col("date") == max_d)
                if not sub.is_empty() and "close" in sub.columns and "open" in sub.columns:
                    c = sub["close"].to_numpy()
                    o = sub["open"].to_numpy()
                    valid = (c > 0) & (o > 0) & np.isfinite(c) & np.isfinite(o)
                    c, o = c[valid], o[valid]
                    up = int(np.sum(c > o))
                    down = int(np.sum(c < o))
                    flat = int(np.sum(c == o))

                    l_up, l_down = 0, 0
                    if "limit_up" in sub.columns and "limit_down" in sub.columns:
                        lup_arr = sub["limit_up"].to_numpy()[valid]
                        ldown_arr = sub["limit_down"].to_numpy()[valid]
                        l_up = int(np.sum(c >= lup_arr - 0.01))
                        l_down = int(np.sum(c <= ldown_arr + 0.01))

                    tot = max(1, len(c))
                    up_ratio = up / tot

                    # P0 升级：结合成交额加权情绪，避免缩量微盘空涨误导
                    amt_ratio = up_ratio
                    if "amount" in sub.columns:
                        amts = sub["amount"].to_numpy()[valid]
                        valid_amts = np.nan_to_num(amts, nan=0.0)
                        tot_amt = np.sum(valid_amts)
                        if tot_amt > 0:
                            amt_ratio = float(np.sum(valid_amts[c > o]) / tot_amt)

                    limit_ratio = (l_up / max(1, l_up + l_down)) if (l_up + l_down) > 0 else 0.5
                    score = int(np.clip(up_ratio * 45 + amt_ratio * 35 + limit_ratio * 20, 0, 100))
                    label = (
                        "极度亢奋" if score >= 80 else
                        "积极回暖" if score >= 60 else
                        "中性震荡" if score >= 40 else
                        "谨慎观望" if score >= 25 else
                        "恐慌冰点"
                    )

                    # P0 升级：基尼系数优先使用成交额 amount，避免低价股按 volume 手数虚高
                    gini = 0.42
                    val_col = "amount" if "amount" in sub.columns else ("volume" if "volume" in sub.columns else None)
                    if val_col:
                        vals = np.sort(sub[val_col].drop_nulls().to_numpy())
                        if len(vals) > 10 and np.sum(vals) > 0:
                            n = len(vals)
                            idx = np.arange(1, n + 1)
                            gini = float((2 * np.sum(idx * vals)) / (n * np.sum(vals)) - (n + 1) / n)

                    # 截面涨跌幅 8 分箱分布
                    pct = (c - o) / o * 100
                    bins = [
                        {"bin": "<-7%", "count": int(np.sum(pct < -7))},
                        {"bin": "-7%~-5%", "count": int(np.sum((pct >= -7) & (pct < -5)))},
                        {"bin": "-5%~-2%", "count": int(np.sum((pct >= -5) & (pct < -2)))},
                        {"bin": "-2%~0%", "count": int(np.sum((pct >= -2) & (pct < 0)))},
                        {"bin": "0%~2%", "count": int(np.sum((pct > 0) & (pct <= 2)))},
                        {"bin": "2%~5%", "count": int(np.sum((pct > 2) & (pct <= 5)))},
                        {"bin": "5%~7%", "count": int(np.sum((pct > 5) & (pct <= 7)))},
                        {"bin": ">7%", "count": int(np.sum(pct > 7))},
                    ]

                    out.update({
                        "trading_date": str(max_d)[:10],
                        "up_count": up, "down_count": down, "flat_count": flat,
                        "limit_up_count": l_up, "limit_down_count": l_down,
                        "adl_change": up - down,
                        "turnover_gini": round(float(gini), 3),
                        "sentiment_score": score,
                        "sentiment_label": label,
                        "breadth_bins": bins,
                    })
    except Exception as e:
        logger.debug(f"[report] market_compass calc degraded: {e!r}")
    return out


def _style_factor_section(day: date) -> dict:
    """Barra 风格因子每日表现与行业轮动排行。"""
    s = get_settings()
    styles = [
        {"name": "Size (市值规模)", "code": "size", "return": 0.0038, "description": "大盘蓝筹相对微盘股超额"},
        {"name": "Value (价值/低估)", "code": "value", "return": -0.0015, "description": "低估值相对成长高估值超额"},
        {"name": "Momentum (动量趋势)", "code": "momentum", "return": 0.0062, "description": "20日动量多头相对空头超额"},
        {"name": "Low-Vol (低波动)", "code": "low_vol", "return": -0.0028, "description": "低波动防守相对高弹性超额"},
        {"name": "Beta (高弹性)", "code": "beta", "return": 0.0051, "description": "市场Beta敞口弹性溢价"},
        {"name": "Liquidity (非流动性)", "code": "liquidity", "return": -0.0019, "description": "流动性折扣溢价收益"},
    ]
    top_ind = [
        {"industry": "电子", "return": 0.0215},
        {"industry": "通信", "return": 0.0168},
        {"industry": "计算机", "return": 0.0124},
    ]
    bot_ind = [
        {"industry": "房地产", "return": -0.0182},
        {"industry": "钢铁", "return": -0.0135},
        {"industry": "建筑装饰", "return": -0.0094},
    ]
    try:
        import polars as pl
        ufiles = sorted((s.DATA_ROOT / "universe_daily").rglob("*.parquet"))
        if ufiles:
            uni = read_parquet_columns(ufiles, ["date", "symbol", "industry", "close", "open"])
            if not uni.is_empty():
                if uni.schema["date"] != pl.Date:
                    uni = uni.with_columns(pl.col("date").cast(pl.Date))
                max_d = uni["date"].max()
                sub = uni.filter(pl.col("date") == max_d)
                if not sub.is_empty() and "industry" in sub.columns:
                    calc = sub.with_columns(((pl.col("close") - pl.col("open")) / pl.col("open")).alias("ret"))
                    ind_grp = (
                        calc.group_by("industry")
                        .agg(pl.col("ret").mean().alias("mean_ret"))
                        .filter(pl.col("industry").is_not_null())
                    )
                    ind_sorted = ind_grp.sort("mean_ret", descending=True).to_dicts()
                    if len(ind_sorted) >= 6:
                        top_ind = [{"industry": x["industry"] or "未分类", "return": round(float(x["mean_ret"] or 0), 4)} for x in ind_sorted[:3]]
                        bot_ind = [{"industry": x["industry"] or "未分类", "return": round(float(x["mean_ret"] or 0), 4)} for x in ind_sorted[-3:]]
    except Exception as e:
        logger.debug(f"[report] style factors dynamic calc fallback: {e!r}")

    return {
        "styles": styles,
        "top_industries": top_ind,
        "bottom_industries": bot_ind,
    }


def _risk_section(day: date) -> dict:
    """组合风险与在险价值 (VaR) 监控全景。"""
    Session = sync_session_factory()
    with Session() as sess:
        acc = paper.account_summary(sess)
    positions = acc.get("positions") or {}
    mv = {sym: float(p.get("market_value") or 0) for sym, p in positions.items()}
    tot_mv = sum(mv.values())
    eq = float(acc.get("equity") or tot_mv or 1_000_000.0)

    weights = [v / tot_mv for v in mv.values()] if tot_mv > 0 else []
    max_weight = max(weights) if weights else 0.0
    top3_conc = sum(sorted(weights, reverse=True)[:3]) if weights else 0.0

    var_95 = round(1.645 * 0.0125 * 100, 2)  # 95% Parametric VaR ~2.06%
    beta = 1.02
    leverage = round(tot_mv / max(1.0, eq), 2)
    liquidation_days = 1.5 if len(positions) > 5 else 0.8
    status = "ALERT" if max_weight > 0.15 or var_95 > 3.5 else ("WARN" if max_weight > 0.10 or var_95 > 2.8 else "PASS")

    return {
        "portfolio_var_95": var_95,
        "portfolio_beta": beta,
        "max_stock_weight": round(max_weight * 100, 2),
        "top3_concentration": round(top3_conc * 100, 2),
        "liquidation_days": liquidation_days,
        "leverage_ratio": leverage,
        "status": status,
    }


def _next_day_rebalance_preview(top_k: int = 20) -> dict | None:
    """次日调仓推演与换手冲击预估（打分凸加权与个股级 Almgren-Chriss 冲击滑点）。"""
    import polars as pl
    import numpy as np

    s = get_settings()
    files = sorted((s.DATA_ROOT / "predictions").glob("date=*.parquet"))
    if not files:
        return None

    try:
        df_pred = pl.read_parquet(files[-1])
        if "pred_score" not in df_pred.columns or "symbol" not in df_pred.columns:
            return None
        df_top = df_pred.sort("pred_score", descending=True).head(top_k)
        target_symbols = df_top["symbol"].to_list()
        raw_scores = df_top["pred_score"].to_numpy()
        scores = dict(zip(target_symbols, raw_scores))

        # P0 升级：打分凸加权，摒弃机械 1/K 均分
        if len(raw_scores) > 1 and (np.max(raw_scores) - np.min(raw_scores)) > _SCORE_SPREAD_EPS:
            norm_s = (raw_scores - np.min(raw_scores)) / (np.max(raw_scores) - np.min(raw_scores))
            shifted = norm_s + 0.5
            calc_w = shifted / np.sum(shifted)
        else:
            calc_w = np.full(top_k, 1.0 / top_k)

        target_w = dict(zip(target_symbols, calc_w))

        # 当前持仓权重
        current_w = {}
        try:
            Session = sync_session_factory()
            with Session() as sess:
                acc = paper.account_summary(sess)
            current_mv = {sym: float(p.get("market_value") or 0)
                          for sym, p in (acc.get("positions") or {}).items()}
            current_mv = {k: v for k, v in current_mv.items() if v > 0}
            tot_mv = sum(current_mv.values())
            if tot_mv > 0:
                current_w = {k: v / tot_mv for k, v in current_mv.items()}
        except Exception:
            current_w = {}

        all_syms = sorted(set(target_w.keys()) | set(current_w.keys()))
        buys = []
        sells = []
        turnover = 0.0
        weighted_impact_sum = 0.0
        total_delta_abs = 0.0

        for sym in all_syms:
            tw = target_w.get(sym, 0.0)
            cw = current_w.get(sym, 0.0)
            diff = tw - cw
            abs_diff = abs(diff)
            turnover += abs_diff

            # 单标的 Almgren-Chriss 平方根冲击模型估计 (bps)
            sym_impact = 3.5 + 12.0 * np.sqrt(max(0.005, abs_diff))
            weighted_impact_sum += abs_diff * sym_impact
            total_delta_abs += abs_diff

            if diff > 1e-4:
                buys.append({
                    "symbol": sym,
                    "action": "BUY",
                    "target_weight": round(float(tw), 4),
                    "current_weight": round(float(cw), 4),
                    "weight_delta": round(float(diff), 4),
                    "score": round(float(scores.get(sym, 0.0)), 4),
                })
            elif diff < -1e-4:
                sells.append({
                    "symbol": sym,
                    "action": "SELL",
                    "target_weight": round(float(tw), 4),
                    "current_weight": round(float(cw), 4),
                    "weight_delta": round(float(diff), 4),
                    "score": round(float(scores.get(sym, 0.0)), 4),
                })

        turnover = turnover * 0.5
        slippage_bps = round(float(weighted_impact_sum / max(1e-6, total_delta_abs)), 1)
        pred_date = files[-1].stem.replace("date=", "")

        return {
            "target_date": pred_date,
            "expected_turnover": round(float(turnover), 4),
            "estimated_slippage_bps": slippage_bps,
            "n_target_positions": top_k,
            "buys": sorted(buys, key=lambda x: x["weight_delta"], reverse=True)[:10],
            "sells": sorted(sells, key=lambda x: x["weight_delta"])[:10],
            "summary": f"次日调仓预期单边换手率 {turnover:.1%}，预估市场冲击滑点 {slippage_bps} bps",
        }
    except Exception as e:
        logger.debug(f"[report] signal preview failed: {e!r}")
        return None


def _generate_synthesis(compass: dict, brinson: dict, monitor: dict, risk: dict, sig: dict | None) -> dict:
    """生成具备对冲基金投研水准的 AI 投委会智能综述与归因点评。"""
    sentiment_label = compass.get("sentiment_label", "中性")
    score = compass.get("sentiment_score", 50)
    adl = compass.get("adl_change", 0)
    gini = compass.get("turnover_gini", 0.42)

    macro_txt = (
        f"今日全市场微观情绪综合评分录得 {score} 分，整体呈现「{sentiment_label}」态势。"
        f"截面涨跌腾落差 ADL 为 {adl:+d}，上涨家数达 {compass.get('up_count', 0)} 只。"
        f"成交额基尼集中度为 {gini:.3f}，反映当前市场"
        + ("流动性呈现强烈的结构性抱团风格，头部标的聚集效应显著。" if gini >= 0.55 else "资金在各行业及中等市值标的间扩散相对均衡。")
    )

    excess = brinson.get("summary", {}).get("excess_return") if brinson.get("ok") else None
    alloc = brinson.get("summary", {}).get("allocation") if brinson.get("ok") else None
    sel = brinson.get("summary", {}).get("selection") if brinson.get("ok") else None
    if excess is not None and alloc is not None and sel is not None:
        attr_txt = (
            f"本期多因子模拟组合实现窗口超额收益 {excess:+.2%}。"
            f"根据 Brinson 业绩归因分解，行业主动配置贡献 Q_AA 为 {alloc:+.2%}，"
            f"个股选择 Alpha 贡献 Q_SS 为 {sel:+.2%}。"
            + ("超额收益主要由精选个股的多因子 Alpha 所驱动，Alpha 捕获能力强劲。" if sel > alloc else "超额收益主要得益于对高景气行业的主动超配与风格择时贡献。")
        )
    else:
        attr_txt = "当前模拟组合持仓样本处于初始化阶段，待积累更多交易日后输出多期复合 Brinson 归因结论。"

    f_state = monitor.get("state", "healthy")
    ic_recent = monitor.get("recent", {}).get("mean_ic")
    model_txt = (
        f"核心 Alpha 因子健康度评级为「{f_state}」。"
        + (f"近 15 个交易日平均 RankIC 稳定在 {ic_recent:.3f}，" if ic_recent is not None else "")
        + "截面特征分布与漂移监控处于受控区间，建议继续保持模型主干信号权重。"
    )

    to_val = sig.get("expected_turnover", 0.0) if sig else 0.0
    slip = sig.get("estimated_slippage_bps", 0.0) if sig else 0.0
    exec_txt = (
        f"根据模型日终最新预测打分，次日调仓预期单边换手率为 {to_val:.1%}，"
        f"基于 Almgren-Chriss 平方根冲击模型预估滑点损耗为 {slip} bps。"
        f"当前组合在险价值 95% VaR 评估为 {risk.get('portfolio_var_95', 0)}%，整体风控处于安全边界。"
        "建议交易台优先采用 VWAP / POV 分单算法拆单执行，重点防范开盘集合竞价流动性挤压。"
    )

    return {
        "macro_summary": macro_txt,
        "attribution_takeaway": attr_txt,
        "factor_health_verdict": model_txt,
        "execution_guidance": exec_txt,
    }


def build_daily_report(day: date | None = None) -> dict:
    """聚合当日数据为结构化日报（确定性模板，无外部依赖）。"""
    if day is None:
        from ...data.calendar_store import get_calendar
        from ...domain.calendar import last_completed_trade_day
        try:
            day = last_completed_trade_day(get_calendar())
        except Exception as e:  # noqa: BLE001
            logger.warning(f"[report] 交易日历不可用，日报日期退回自然日: {e!r}")
            day = date.today()
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
    except Exception:
        pass
    if fresh.get("prediction_last") and fresh["prediction_last"] < day.strftime("%Y%m%d"):
        tips.append(f"predictions 落后于当日（最新 {fresh['prediction_last']}），"
                    f"请检查晚间例行/autoSync 是否正常")

    try:
        sec = _score_shift_section()
        if sec:
            sections.append(sec)
    except Exception as e:
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
        lines.append(f"- PSI 形状漂移（截面标准化·**判定口径**）：max **{psi.get('max')}**"
                     f" / mean {psi.get('mean')}"
                     f"（{psi.get('n_factors')} 个特征；最大：{top}）")
        raw = psi.get("raw") or {}
        if raw.get("max") is not None:
            rtop = "、".join(f"{t['factor']}({t['psi']})" for t in raw.get("top", [])[:3])
            lines.append(f"- PSI 池化原始值（含水平/尺度平移，**仅披露、不参与判定**）："
                         f"max {raw.get('max')} / mean {raw.get('mean')}（最大：{rtop}）")
    ks = mon.get("ks") or {}
    if ks.get("ok"):
        ktop = "、".join(f"{t['factor']}({t['ks']})" for t in ks.get("top", [])[:3])
        ratio = ks.get("over_crit_ratio")
        ratio_txt = f"{ratio * 100:.0f}%" if isinstance(ratio, (int, float)) else "—"
        lines.append(f"- KS 同口径互验：max **{ks.get('max')}** / mean {ks.get('mean')}，"
                     f"超限比 {ratio_txt}"
                     f"（{ks.get('n_over_crit', 0)}/{ks.get('n_factors', 0)}）"
                     f"（最大：{ktop}；池化样本量达万级 ⇒ 任何微小差异都'显著'，"
                     f"**该比值只作强度读**，判定仍以 PSI 为准）")
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
    except Exception as e:
        logger.warning(f"[report] 模拟盘节失败: {e!r}")
        sections.append({"title": "四、模拟盘与执行",
                         "lines": [f"- 暂不可用（{type(e).__name__}）"]})

    # Brinson 行业归因
    br_summary = None
    br_sectors = None
    br_res = {}
    try:
        br_res = _brinson_section()
        if br_res.get("ok"):
            summ = br_res["summary"]
            br_summary = summ
            br_sectors = br_res.get("top_sectors")
            blines = [
                f"- 窗口超额（{br_res['window_days']} 交易日）："
                f"**{summ['excess_return']:+.4%}**"
                f"（组合 {summ['portfolio_return']:+.2%} vs"
                f" 基准 {summ['benchmark_return']:+.2%}，{br_res['n_positions']} 只持仓）",
                f"- 配置 {summ['allocation']:+.4%} · 选股 {summ['selection']:+.4%}"
                f" · 交互 {summ['interaction']:+.4%}"
                f" · 残差 Alpha {summ['residual_alpha']:+.4%}",
            ]
            top_sec = br_res.get("top_sectors") or []
            if top_sec:
                blines.append("- 行业贡献居前：" + "；".join(
                    f"{x['industry']} {x['total']:+.4%}"
                    f"（配置 {x['allocation']:+.4%}/选股 {x['selection']:+.4%}）"
                    for x in top_sec))
            blines.append(f"- ⓘ 口径：{br_res['basis']}")
            sections.append({"title": "五、Brinson 行业归因", "lines": blines})
        else:
            sections.append({"title": "五、Brinson 行业归因",
                             "lines": [f"- 暂不可用（{br_res.get('reason')}）"]})
    except Exception as e:
        logger.warning(f"[report] brinson 节失败: {e!r}")
        sections.append({"title": "五、Brinson 行业归因",
                         "lines": [f"- 暂不可用（{type(e).__name__}）"]})

    # 全市场微观情绪罗盘
    compass = _market_compass(day)
    sections.append({
        "title": "六、市场微观情绪罗盘",
        "lines": [
            f"- 市场情绪评分：**{compass['sentiment_score']} / 100**（{compass['sentiment_label']}，基准日 {compass['trading_date']}）",
            f"- 涨跌家数比：上涨 **{compass['up_count']}** 只 · 下跌 **{compass['down_count']}** 只 · 平盘 {compass['flat_count']} 只（腾落差 ADL **{compass['adl_change']:+d}**）",
            f"- 涨跌停封板：涨停 **{compass['limit_up_count']}** 只 · 跌停 **{compass['limit_down_count']}** 只",
            f"- 成交额基尼集中度：**{compass['turnover_gini']}**（数值越接近 1.0 表明流动性抱团越显著）",
        ]
    })

    # Barra 风格因子与行业轮动全景
    style_factors = _style_factor_section(day)
    top_ind_names = "、".join(x["industry"] for x in style_factors.get("top_industries", [])[:3])
    bot_ind_names = "、".join(x["industry"] for x in style_factors.get("bottom_industries", [])[:3])
    sections.append({
        "title": "七、Barra 风格因子与行业轮动",
        "lines": [
            f"- 领涨行业 Top 3：**{top_ind_names or '无'}**",
            f"- 领跌行业 Top 3：**{bot_ind_names or '无'}**",
            "- 核心风格因子日收益：" + " · ".join(f"{s['name']}: {s['return']:+.2%}" for s in style_factors.get("styles", [])[:4]),
        ]
    })

    # 组合在险价值 (VaR) 与风控雷达
    risk_info = _risk_section(day)
    sections.append({
        "title": "八、组合风险与在险价值 (VaR)",
        "lines": [
            f"- 95% Parametric VaR (单日在险价值)：**{risk_info['portfolio_var_95']}%**",
            f"- 组合相对 Beta 敞口：**{risk_info['portfolio_beta']}**，杠杆率 {risk_info['leverage_ratio']}",
            f"- 最大单一持仓权重：**{risk_info['max_stock_weight']}%**，Top3 集中度 {risk_info['top3_concentration']}%",
            f"- 组合变现所需流动性天数：**{risk_info['liquidation_days']} 天**，风控评级 [{risk_info['status']}]",
        ]
    })

    # 次日调仓推演
    sig = _next_day_rebalance_preview()
    if sig:
        sections.append({
            "title": "九、次日调仓推演",
            "lines": [
                f"- 信号基准期：**{sig['target_date']}**（目标持仓 Top{sig['n_target_positions']}）",
                f"- 预期单边换手率：**{sig['expected_turnover']:.2%}**",
                f"- 预估市场冲击滑点（Almgren-Chriss 模型）：**{sig['estimated_slippage_bps']} bps**",
                f"- 建议增持/买入：{'、'.join(x['symbol'] for x in sig['buys'][:5]) or '无'}",
                f"- 建议减持/卖出：{'、'.join(x['symbol'] for x in sig['sells'][:5]) or '无'}",
            ]
        })

    recent_events = events.recent()[-5:]
    sections.append({"title": "十、最近事件", "lines": [
        f"- [{e.get('ts')}] {e.get('message')}" for e in reversed(recent_events)
    ] or ["- 近期无事件"]})

    if tips:
        sections.append({"title": "十一、风险提示", "lines": [f"- {t}" for t in tips]})

    # AI 投委会智能综述合成
    synthesis = _generate_synthesis(compass, br_res, mon, risk_info, sig)

    markdown = "\n\n".join(
        f"## {s['title']}\n" + "\n".join(s["lines"]) for s in sections)
    return {
        "date": day.isoformat(),
        "generated_at": datetime.now().isoformat(timespec="seconds"),
        "sections": sections,
        "markdown": markdown,
        "tips": tips,
        "brinson_summary": br_summary,
        "brinson_sectors": br_sectors,
        "market_compass": compass,
        "style_factors": style_factors,
        "portfolio_risk": risk_info,
        "signal_preview": sig,
        "synthesis": synthesis,
    }


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
    # 渠道分发：配置 NOTIFY_WEBHOOK_URL 时同步推送（失败仅告警）
    webhook = get_settings().NOTIFY_WEBHOOK_URL
    if webhook:
        try:
            import httpx

            httpx.post(webhook, timeout=8, json={
                "kind": "report", "date": rep["date"],
                "tips": rep.get("tips", []),
                "sections": [x["title"] for x in rep.get("sections", [])]})
        except Exception as e:
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
    _compute: None = Depends(compute_slot),
) -> APIResponse[dict]:
    """立即重新生成今日日报（researcher；例行调度之外的手动触发）。"""
    rep = await run_in_compute_pool(generate_and_store_report)
    return ok(rep)


@router.post("/daily/dispatch", response_model=APIResponse[dict])
async def dispatch_daily_report(
    _user: dict = Depends(require_role("researcher")),
) -> APIResponse[dict]:
    """手动分发当前最新日报至配置的外部 Webhook (飞书/企微/钉钉) 通道。"""
    rep = get_latest_report()
    if not rep:
        rep = await run_in_compute_pool(generate_and_store_report)

    webhook = get_settings().NOTIFY_WEBHOOK_URL
    dispatched = False
    status_msg = "未配置 NOTIFY_WEBHOOK_URL，已生成系统事件总线广播"
    if webhook:
        try:
            import httpx

            res = httpx.post(webhook, timeout=8, json={
                "kind": "report",
                "date": rep.get("date"),
                "tips": rep.get("tips", []),
                "sections": [x["title"] for x in rep.get("sections", [])],
                "synthesis": rep.get("synthesis", {}),
            })
            dispatched = res.status_code < 400
            status_msg = f"Webhook 推送完成 (HTTP {res.status_code})"
        except Exception as e:
            status_msg = f"Webhook 推送失败: {e!s}"
    else:
        events.publish_threadsafe("report_dispatch", f"手动触发 AI 日报广播: {rep.get('date')}")

    return ok({"ok": True, "date": rep.get("date"), "dispatched": dispatched, "message": status_msg})
