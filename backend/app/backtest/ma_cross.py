"""趋势跟踪策略回测（MA 交叉 + 移动止损）——策略回测页专用引擎。

三阶段（与回测页设计稿一一对应）：
    1. 数据加载与对齐：本地 QFQ 日线（缺 QFQ 回退 raw 并标注）+ 沪深300 基准；
       指标预计算 MA_short / MA_long。
    2. 事件驱动循环：T 日收盘产生信号（金叉/死叉/移动止损），
       T+1 日开盘价撮合（严禁当日信号当日成交）；
       成交扣佣金（双边）与印花税（卖出），按 100 股整手；
       每日收盘 mark-to-market 推入资产序列。
    3. 绩效指标：年化/夏普/最大回撤（复用 domain.metrics）+
       Alpha/Beta（对基准日收益线性回归）+ Sortino/信息比率 + 交易特征。

与 P0 Top-K 引擎（engine.py）的关系：互相独立——本引擎是"用户选标的 +
规则信号"的策略回测，不依赖 predictions。
"""
from __future__ import annotations

import math
from dataclasses import dataclass, field
from datetime import date
from typing import Any

import numpy as np
import pandas as pd
from loguru import logger

from ..domain.a_share_rules import is_etf_symbol, stamp_duty_rate
from ..domain.trading_rules import COMMISSION_MIN, COMMISSION_RATE_DEFAULT
from ..domain.metrics import (
    annual_return,
    deflated_sharpe_ratio,
    max_drawdown,
    probabilistic_sharpe_ratio,
    sharpe_ratio,
)

LOT_SIZE = 100
TRADING_DAYS = 252


def _is_etf_symbol(sym: str) -> bool:
    """M2 修复：判断 A 股场内基金代码（卖出免印花税）。

    审计 B5-10（2026-09-21）：改为调用**全项目唯一事实来源**
    :func:`app.domain.a_share_rules.is_etf_symbol`（此前本函数与 ``paper._is_etf``
    各持一份前缀表，且都比统一口径**漏了 18xxxx（深市封闭式基金）**）。
    """
    return is_etf_symbol(sym)


@dataclass
class MaCrossParams:
    short_ma: int = 5
    long_ma: int = 20
    trailing_stop_pct: float = 3.0   # %
    commission_rate: float = COMMISSION_RATE_DEFAULT
    stamp_duty: float | None = None   # None = 法定分段（a_share_rules.stamp_duty_rate）
    init_cash: float = 1_000_000.0
    slippage_bps: float = 5.0        # 滑点：成交价 = open × (1 ± bps/1e4)，买加卖减


@dataclass
class MaCrossResult:
    nav_df: pd.DataFrame = field(default_factory=pd.DataFrame)   # date, strategy_nav, benchmark_nav
    trades: list[dict] = field(default_factory=list)             # 已成交明细
    signals: list[dict] = field(default_factory=list)            # 信号事件（金叉/死叉/止损）
    risk: dict[str, float] = field(default_factory=dict)
    monthly: list[dict] = field(default_factory=list)            # {month: "1月", value: %}


def _mas(closes: pd.Series, short: int, long_: int) -> pd.DataFrame:
    df = pd.DataFrame({"close": closes})
    df["ma_s"] = closes.rolling(short).mean()
    df["ma_l"] = closes.rolling(long_).mean()
    return df


def run_ma_cross(
    bars: dict[str, pd.DataFrame],
    benchmark: pd.DataFrame,
    params: MaCrossParams,
) -> MaCrossResult:
    """趋势跟踪回测主入口。

    :param bars: {symbol: DataFrame[date, open, close]}（升序，QFQ 口径）
    :param benchmark: DataFrame[date, close]（沪深300，升序）
    """
    p = params
    # ---- 阶段 1：指标预计算 ----
    indicators: dict[str, pd.DataFrame] = {}
    for sym, df in bars.items():
        ind = _mas(df["close"].reset_index(drop=True), p.short_ma, p.long_ma)
        ind["date"] = df["date"].reset_index(drop=True)
        ind["open"] = df["open"].reset_index(drop=True)
        indicators[sym] = ind

    # 统一交易日轴（基准为主轴，缺失回退标的并集）
    bench = benchmark.reset_index(drop=True)
    all_days = sorted(set(bench["date"]) & set().union(
        *[set(ind["date"]) for ind in indicators.values()])) \
        if indicators else []
    if not all_days:
        all_days = sorted(set(bench["date"]))

    # 每日价格快照（缺失用前收盘填充，模拟停牌）
    px_close: dict[str, dict[date, float]] = {}
    px_open: dict[str, dict[date, float]] = {}
    for sym, ind in indicators.items():
        closes: dict[date, float] = {}
        opens: dict[date, float] = {}
        last_c = None
        for d, o, c in zip(ind["date"], ind["open"], ind["close"]):
            if c is not None and c == c:
                last_c = float(c)
            closes[d] = last_c if last_c is not None else float("nan")
            opens[d] = float(o) if o is not None and o == o else (last_c or float("nan"))
        px_close[sym], px_open[sym] = closes, opens

    # ---- 阶段 2：信号状态机（T 日收盘判定，T+1 开盘执行）----
    # target_state[sym][day] -> True 持有 / False 空仓
    target_state: dict[str, dict[date, bool]] = {}
    signal_events: list[dict] = []
    for sym, ind in indicators.items():
        state: dict[date, bool] = {}
        holding = False
        high_water = 0.0
        for d, ma_s, ma_l, c in zip(ind["date"], ind["ma_s"], ind["ma_l"], ind["close"]):
            if ma_s is None or ma_l is None or ma_s != ma_s or ma_l != ma_l:
                state[d] = holding
                continue
            golden = ma_s > ma_l
            if not holding and golden:
                holding = True
                high_water = float(c)
                signal_events.append({"date": d, "symbol": sym, "action": "golden"})
            elif holding:
                high_water = max(high_water, float(c))
                dead = (not golden)
                stopped = float(c) < high_water * (1 - p.trailing_stop_pct / 100)
                if dead or stopped:
                    holding = False
                    signal_events.append({"date": d, "symbol": sym,
                                          "action": "stopped" if stopped and not dead else "dead"})
            state[d] = holding
        target_state[sym] = state

    # ---- 撮合与逐日结算（T+1 开盘执行目标变化）----
    cash = p.init_cash
    holdings: dict[str, int] = {}
    avg_cost: dict[str, float] = {}
    pending: set[str] | None = None  # 待执行目标集（None=无变化）
    trades: list[dict] = []
    rows: list[dict] = []

    def execute_target(d: date, targets: set[str]) -> None:
        """以当日开盘价（含滑点：买加卖减）把持仓调整到 targets（等权，整手）。"""
        nonlocal cash
        slip = p.slippage_bps / 10_000.0
        _raw_opens = {s: px_open.get(s, {}).get(d) for s in set(holdings) | targets}
        # 显式收敛为 float：过滤掉 None/NaN/非正价后，下游所有价格运算都不必再
        # 处理 Optional（静态类型与运行时保证一致）
        opens: dict[str, float] = {
            s: float(v) for s, v in _raw_opens.items()
            if v is not None and v == v and v > 0
        }
        if not opens and (holdings or targets):
            return
        # 卖出：先清非目标
        for sym in list(holdings):
            if sym in targets:
                continue
            o = opens.get(sym)
            if o is None:
                continue
            qty = holdings[sym]
            px = o * (1 - slip)                     # 卖出滑点价
            amount = qty * px
            # M1/M2 修复：最低佣金 5 元；印花税仅股票（ETF 免征）
            # 审计 B5-10：费率改用单一事实来源；`p.stamp_duty=None` 时按法定分段
            # （2023-08-28 前 1‰、之后 0.5‰），显式给出数值则原样使用。
            fee = max(COMMISSION_MIN, amount * p.commission_rate)
            if not _is_etf_symbol(sym):
                rate = (p.stamp_duty if p.stamp_duty is not None
                        else stamp_duty_rate("stock", "sell", d))
                fee += amount * rate
            cash += amount - fee
            pnl = (px - avg_cost.get(sym, px)) * qty - fee
            trades.append({"date": d, "symbol": sym, "side": "sell", "price": round(px, 3),
                           "qty": qty, "fee": round(fee, 2), "pnl": round(pnl, 2)})
            del holdings[sym]
            avg_cost.pop(sym, None)
        # 买入：等权分配
        if targets:
            open_syms = [s for s in sorted(targets) if s in opens]
            if open_syms:
                mv = sum(holdings[s] * opens[s] for s in holdings if s in opens)
                equity = cash + mv
                per = equity / len(open_syms)
                for sym in open_syms:
                    o = opens[sym]
                    px = o * (1 + slip)             # 买入滑点价
                    cur = holdings.get(sym, 0) * o
                    budget = min(per - cur, cash * 0.98)
                    lots = math.floor(budget / (px * LOT_SIZE)) if budget > 0 else 0
                    if lots <= 0:
                        continue
                    qty = lots * LOT_SIZE
                    amount = qty * px
                    # M1 修复：买入佣金同样适用最低 5 元
                    fee = max(COMMISSION_MIN, amount * p.commission_rate)
                    cash -= amount + fee
                    holdings[sym] = holdings.get(sym, 0) + qty
                    prev_cost = avg_cost.get(sym, 0.0) * (holdings[sym] - qty)
                    # 审计 B5-20（2026-09-21）：成本基准必须**含买入费用**，
                    # 否则 `pnl = (px - avg_cost)*qty - 卖出fee` 只减了卖出腿的
                    # 费用，往返成本少算买入腿 ⇒ `win_rate` / `avg_pnl_ratio`
                    # 系统性偏乐观（把"毛赚、净亏"的往返记成盈利）。
                    avg_cost[sym] = (prev_cost + amount + fee) / holdings[sym]
                    trades.append({"date": d, "symbol": sym, "side": "buy", "price": round(px, 3),
                                   "qty": qty, "fee": round(fee, 2), "pnl": None})

    for i, d in enumerate(all_days):
        # T 日收盘算出的目标 -> T+1 开盘执行
        cur_targets = {s for s, st in target_state.items() if st.get(d, False)}
        if pending is not None:
            execute_target(d, pending)
            pending = None
        # 记录次日应执行的目标（与当日实际持仓集合比较）
        eq_mv = sum((px_close.get(s, {}).get(d) or 0) * q for s, q in holdings.items())
        equity = cash + eq_mv
        rows.append({"date": d, "equity": equity,
                     "strategy_nav": equity / p.init_cash,
                     "benchmark_nav": float("nan")})
        if cur_targets != set(holdings):
            pending = cur_targets

    # ---- 基准对齐 + 期末最后一天执行 pending（无次日则放弃，不补成交）----
    bench_map = dict(zip(bench["date"], bench["close"]))
    b0 = next((bench_map[d] for d in all_days if bench_map.get(d)), None)
    nav_df = pd.DataFrame(rows)
    if b0:
        nav_df["benchmark_nav"] = nav_df["date"].map(
            lambda d: (bench_map[d] / b0) if bench_map.get(d) else float("nan"))
    nav_df["benchmark_nav"] = nav_df["benchmark_nav"].ffill()

    # ---- 阶段 3：绩效与风险指标 ----
    res = MaCrossResult(nav_df=nav_df, trades=trades, signals=signal_events)
    if nav_df.empty:
        return res
    strat = nav_df["strategy_nav"].to_numpy()
    bench_n = nav_df["benchmark_nav"].to_numpy()

    rs = pd.Series(strat).pct_change().dropna()
    rb = pd.Series(bench_n).pct_change().dropna()
    aligned = pd.concat([rs, rb], axis=1, join="inner").dropna()
    alpha = beta = float("nan")
    info_ratio = float("nan")
    if len(aligned) > 30 and aligned.iloc[:, 1].std() > 0:
        beta = float(aligned.iloc[:, 0].cov(aligned.iloc[:, 1])
                     / aligned.iloc[:, 1].var())
        alpha_ann = (aligned.iloc[:, 0].mean() - beta * aligned.iloc[:, 1].mean()) * TRADING_DAYS
        alpha = float(alpha_ann)
        ex = aligned.iloc[:, 0] - aligned.iloc[:, 1]
        info_ratio = float(ex.mean() / ex.std() * math.sqrt(TRADING_DAYS)) if ex.std() > 0 else float("nan")
    downside = rs[rs < 0]
    sortino = float(rs.mean() / downside.std() * math.sqrt(TRADING_DAYS)) \
        if len(downside) > 1 and downside.std() > 0 else float("nan")

    sells = [t for t in trades if t["side"] == "sell"]
    wins = [t for t in sells if (t["pnl"] or 0) > 0]
    losses = [t for t in sells if (t["pnl"] or 0) < 0]
    win_rate = len(wins) / len(sells) if sells else 0.0
    avg_win = sum(t["pnl"] for t in wins) / len(wins) if wins else 0.0
    avg_loss = abs(sum(t["pnl"] for t in losses) / len(losses)) if losses else 0.0
    pnl_ratio = (avg_win / avg_loss) if avg_loss > 0 else float("nan")

    mdd, _, _ = max_drawdown(strat)
    # 防过拟合指标（PSR / DSR）：需要 >= 3 个观测，异常输入静默降级为 NaN
    try:
        psr = float(probabilistic_sharpe_ratio(strat))
        dsr = float(deflated_sharpe_ratio(strat))
    except ValueError:
        psr = dsr = float("nan")
    res.risk = {
        "alpha": alpha, "beta": beta, "sortino": sortino, "info_ratio": info_ratio,
        "win_rate": win_rate, "avg_pnl_ratio": pnl_ratio,
        "total_trades": len(sells) + sum(1 for t in trades if t["side"] == "buy"),
        "sharpe": sharpe_ratio(strat),
        "probabilistic_sharpe": psr,
        "deflated_sharpe": dsr,
        "max_drawdown": mdd,
        "annual_strategy": annual_return(strat),
        "annual_benchmark": annual_return(bench_n[~np.isnan(bench_n)])
            if len(bench_n[~np.isnan(bench_n)]) > 1 else float("nan"),
    }

    # 月度收益（策略净值按自然月 %）
    m = nav_df.copy()
    m["month"] = pd.to_datetime(m["date"]).dt.to_period("M")
    monthly: list[dict[str, Any]] = []
    for period, g in m.groupby("month"):
        v = (g["strategy_nav"].iloc[-1] / g["strategy_nav"].iloc[0] - 1) * 100
        monthly.append({"month": f"{period.month}月", "year": int(period.year),
                        "value": round(float(v), 2)})
    # 同名月合并（多年）：取均值，横轴固定 1-12 月
    by_month: dict[int, list[float]] = {}
    for e in monthly:
        by_month.setdefault(int(e["month"].replace("月", "")), []).append(e["value"])
    res.monthly = [{"month": f"{mo}月",
                    "value": round(sum(vs) / len(vs), 2)}
                   for mo, vs in sorted(by_month.items())]
    logger.info(f"[ma_cross] done: {len(trades)} trades, "
                f"nav {strat[0]:.4f}->{strat[-1]:.4f}, sharpe={res.risk['sharpe']:.2f}")
    return res
