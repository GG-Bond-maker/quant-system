"""vnpy 式策略框架（参考 vnpy/vnpy 的 StrategyTemplate + EventEngine 设计）。

从 vnpy 借鉴的三个核心抽象（适配本项目的日频回测场景）：
1. StrategyTemplate：策略 = 参数 + 状态机。策略只实现 ``on_bar``，
   在其中维护自己的目标持仓（``ctx.targets``），不直接下单 ——
   与 vnpy 的 CtaTemplate（on_bar 回调 + buy/sell）同构，但把撮合
   完全交给 Runner（保证所有策略共用同一套 A 股规则与防泄漏纪律）；
2. EventEngine：轻量事件总线（BAR 事件，handler 订阅）。
   vnpy 的 EventEngine 是多线程队列，回测场景改为**同步分发**，
   确定性可复现；
3. Runner：事件驱动主循环，执行纪律与 ma_cross/engine 完全一致：
       T 日收盘 on_bar 产生目标持仓 -> T+1 日开盘撮合（先卖后买，等权）
   撮合直接复用 engine.rebalance_equal_weight + broker.Broker，
   天然继承 T+1 / 涨跌停 / 停牌 / 整手 / 滑点 全部闸门。

内置策略（注册于 STRATEGIES，vnpy 的策略类注册机制）：
    ma_cross      双均线交叉 + 移动止损（与 ma_cross.py 同逻辑的框架版）
    donchian      唐奇安通道突破（close 创 N 日新高入场 / 破 M 日新低离场）
    rsi_reversion RSI 均值回归（超卖入场 / 超买卖出，long-only）
"""
from __future__ import annotations

from collections import deque
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import date
from typing import Any

import numpy as np
import pandas as pd
from loguru import logger

from .broker import Broker, BrokerConfig, Order, OrderSide
from .engine import rebalance_equal_weight
from ..domain.metrics import (
    all_metrics,
    annual_return,
)

TRADING_DAYS = 252


# ==================== 事件总线（vnpy EventEngine 的同步回测版） ====================
@dataclass(frozen=True)
class Event:
    """回测事件：event_type ∈ {bar}。"""

    event_type: str
    data: dict[str, Any]


class EventEngine:
    """同步事件总线：register(type, handler) + put(event) 顺序分发。

    与 vnpy 的差异：无独立线程（回测要确定性），handler 异常不中断主循环
    （记录日志），与 vnpy 的容错语义一致。
    """

    def __init__(self) -> None:
        self._handlers: dict[str, list[Callable[[Event], None]]] = {}

    def register(self, event_type: str, handler: Callable[[Event], None]) -> None:
        self._handlers.setdefault(event_type, []).append(handler)

    def put(self, event: Event) -> None:
        for h in self._handlers.get(event.event_type, []):
            try:
                h(event)
            except Exception as e:  # noqa: BLE001  单 handler 失败不中断
                logger.warning(f"[event] handler 失败 {event.event_type}: {e!r}")


# ==================== 策略模板 ====================
@dataclass
class StrategyContext:
    """策略可见的上下文（只读行情 + 可写目标持仓/信号）。"""

    date: date
    targets: set[str]                      # 策略维护的目标持仓集合
    bars: dict[str, dict[str, float]]      # 当日各标的 {open, close}
    signals: list[dict] = field(default_factory=list)

    def log_signal(self, action: str, symbol: str) -> None:
        self.signals.append({"date": self.date, "symbol": symbol, "action": action})


class StrategyTemplate:
    """策略基类（vnpy CtaTemplate 的日频回测版）。

    子类实现 ``on_bar``：每个交易日每只标的回调一次；
    通过 ``ctx.targets`` 表达目标持仓（set[str]），
    通过 ``ctx.log_signal`` 记录信号事件（供前端标记）。
    禁止在 on_bar 中直接交易 —— 撮合统一由 Runner 在次日开盘执行。
    参数通过 ``__init__(**params)`` 注入并与类级 ``params`` 默认值合并。
    """

    name: str = "base"
    params: dict[str, Any] = {}

    def __init__(self, **params: Any) -> None:
        merged = {**type(self).params, **params}
        for k, v in merged.items():
            setattr(self, k, v)
        self.params_used = merged

    def on_init(self, ctx: StrategyContext) -> None:  # noqa: B027
        """回测开始前的初始化（可选）。"""

    def on_bar(self, ctx: StrategyContext, symbol: str, bar: dict[str, float]) -> None:
        raise NotImplementedError


# ==================== 内置策略 ====================
class MaCrossStrategy(StrategyTemplate):
    """双均线金叉入场 / 死叉或移动止损离场（与 ma_cross.py 同口径）。"""

    name = "ma_cross"
    params = {"short_ma": 5, "long_ma": 20, "trailing_stop_pct": 3.0}
    # 参数由 StrategyTemplate.__init__ 动态 setattr 注入，此处显式声明属性
    # 供静态检查识别（运行时行为不变）
    short_ma: int
    long_ma: int
    trailing_stop_pct: float

    def on_init(self, ctx: StrategyContext) -> None:
        self._closes: dict[str, deque[float]] = {}
        self._high_water: dict[str, float] = {}
        self._holding: set[str] = set()

    def on_bar(self, ctx: StrategyContext, symbol: str, bar: dict[str, float]) -> None:
        dq = self._closes.setdefault(symbol, deque(maxlen=int(self.long_ma)))
        dq.append(float(bar["close"]))
        if len(dq) < int(self.long_ma):
            return
        ma_s = sum(list(dq)[-int(self.short_ma):]) / int(self.short_ma)
        ma_l = sum(dq) / len(dq)
        golden = ma_s > ma_l
        if symbol not in self._holding:
            if golden:
                self._holding.add(symbol)
                self._high_water[symbol] = float(bar["close"])
                ctx.targets.add(symbol)
                ctx.log_signal("golden", symbol)
            return
        self._high_water[symbol] = max(self._high_water.get(symbol, 0.0),
                                       float(bar["close"]))
        stopped = float(bar["close"]) < self._high_water[symbol] * \
            (1 - float(self.trailing_stop_pct) / 100)
        if not golden or stopped:
            self._holding.discard(symbol)
            ctx.targets.discard(symbol)
            ctx.log_signal("stopped" if stopped and not golden else "dead", symbol)


class DonchianBreakoutStrategy(StrategyTemplate):
    """唐奇安通道突破：收盘价创 N 日新高入场，破 M 日新低离场。"""

    name = "donchian"
    params = {"entry_window": 20, "exit_window": 10}
    entry_window: int
    exit_window: int

    def on_init(self, ctx: StrategyContext) -> None:
        self._closes: dict[str, deque[float]] = {}
        self._holding: set[str] = set()

    def on_bar(self, ctx: StrategyContext, symbol: str, bar: dict[str, float]) -> None:
        entry, exit_ = int(self.entry_window), int(self.exit_window)
        dq = self._closes.setdefault(symbol, deque(maxlen=max(entry, exit_) + 1))
        hist = list(dq)
        dq.append(float(bar["close"]))
        if len(hist) < entry:
            return
        hi = max(hist[-entry:])
        lo = min(hist[-exit_:])
        if symbol not in self._holding:
            if float(bar["close"]) >= hi:
                self._holding.add(symbol)
                ctx.targets.add(symbol)
                ctx.log_signal("breakout", symbol)
        elif float(bar["close"]) <= lo:
            self._holding.discard(symbol)
            ctx.targets.discard(symbol)
            ctx.log_signal("breakdown", symbol)


class RsiMeanReversionStrategy(StrategyTemplate):
    """RSI 均值回归（long-only）：RSI < oversold 入场，RSI > overbuy 离场。"""

    name = "rsi_reversion"
    params = {"rsi_period": 14, "oversold": 30.0, "overbuy": 70.0}
    rsi_period: int
    oversold: float
    overbuy: float

    def on_init(self, ctx: StrategyContext) -> None:
        self._closes: dict[str, deque[float]] = {}
        self._holding: set[str] = set()

    @staticmethod
    def _rsi(closes: list[float], period: int) -> float:
        diffs = np.diff(np.asarray(closes[-(period + 1):], dtype=np.float64))
        gain = diffs[diffs > 0].mean() if (diffs > 0).any() else 0.0
        loss = -diffs[diffs < 0].mean() if (diffs < 0).any() else 0.0
        if loss < 1e-12:
            return 100.0
        rs = gain / loss
        return float(100.0 - 100.0 / (1.0 + rs))

    def on_bar(self, ctx: StrategyContext, symbol: str, bar: dict[str, float]) -> None:
        period = int(self.rsi_period)
        dq = self._closes.setdefault(symbol, deque(maxlen=period + 1))
        dq.append(float(bar["close"]))
        if len(dq) < period + 1:
            return
        rsi = self._rsi(list(dq), period)
        if symbol not in self._holding:
            if rsi < float(self.oversold):
                self._holding.add(symbol)
                ctx.targets.add(symbol)
                ctx.log_signal("oversold_entry", symbol)
        elif rsi > float(self.overbuy):
            self._holding.discard(symbol)
            ctx.targets.discard(symbol)
            ctx.log_signal("overbuy_exit", symbol)


# 策略注册表（vnpy 的 strategy class 注册机制）
STRATEGIES: dict[str, type[StrategyTemplate]] = {
    MaCrossStrategy.name: MaCrossStrategy,
    DonchianBreakoutStrategy.name: DonchianBreakoutStrategy,
    RsiMeanReversionStrategy.name: RsiMeanReversionStrategy,
}


# ==================== 事件驱动 Runner ====================
@dataclass
class StrategyRunResult:
    """框架版回测输出（与 ma_cross.MaCrossResult 字段对齐）。"""

    nav_df: pd.DataFrame = field(default_factory=pd.DataFrame)
    trades: list[dict] = field(default_factory=list)
    signals: list[dict] = field(default_factory=list)
    risk: dict[str, float] = field(default_factory=dict)
    monthly: list[dict] = field(default_factory=list)


def monthly_returns(nav: pd.Series) -> list[dict]:
    """月度收益（按自然月，多年同名月取均值 -> 1~12 月横轴）。"""
    m = pd.DataFrame({"nav": nav.values}, index=pd.to_datetime(nav.index))
    m["month"] = m.index.to_period("M")
    by_month: dict[int, list[float]] = {}
    for _, g in m.groupby("month"):
        v = (g["nav"].iloc[-1] / g["nav"].iloc[0] - 1) * 100
        by_month.setdefault(int(g["month"].iloc[0].month), []).append(float(v))
    return [{"month": f"{mo}月", "value": round(sum(vs) / len(vs), 2)}
            for mo, vs in sorted(by_month.items())]


def summarize_risk(strat_nav: np.ndarray, bench_nav: np.ndarray,
                   trades: list[dict]) -> dict[str, float]:
    """绩效汇总（与 ma_cross 口径一致：alpha/beta/sortino/信息比率/交易特征 + PSR/DSR）。"""
    rs = pd.Series(strat_nav).pct_change().dropna()
    rb = pd.Series(bench_nav).pct_change().dropna()
    aligned = pd.concat([rs, rb], axis=1, join="inner").dropna()
    alpha = beta = info_ratio = float("nan")
    if len(aligned) > 30 and aligned.iloc[:, 1].std() > 0:
        beta = float(aligned.iloc[:, 0].cov(aligned.iloc[:, 1]) / aligned.iloc[:, 1].var())
        alpha = float((aligned.iloc[:, 0].mean() - beta * aligned.iloc[:, 1].mean())
                      * TRADING_DAYS)
        ex = aligned.iloc[:, 0] - aligned.iloc[:, 1]
        info_ratio = float(ex.mean() / ex.std() * np.sqrt(TRADING_DAYS)) \
            if ex.std() > 0 else float("nan")
    downside = rs[rs < 0]
    sortino = float(rs.mean() / downside.std() * np.sqrt(TRADING_DAYS)) \
        if len(downside) > 1 and downside.std() > 0 else float("nan")

    sells = [t for t in trades if t["side"] == "sell"]
    wins = [t for t in sells if (t["pnl"] or 0) > 0]
    losses = [t for t in sells if (t["pnl"] or 0) < 0]
    win_rate = len(wins) / len(sells) if sells else 0.0
    avg_win = sum(t["pnl"] for t in wins) / len(wins) if wins else 0.0
    avg_loss = abs(sum(t["pnl"] for t in losses) / len(losses)) if losses else 0.0

    m = all_metrics(strat_nav)
    return {
        "alpha": alpha, "beta": beta, "sortino": sortino, "info_ratio": info_ratio,
        "win_rate": win_rate,
        "avg_pnl_ratio": (avg_win / avg_loss) if avg_loss > 0 else float("nan"),
        "total_trades": len(sells) + sum(1 for t in trades if t["side"] == "buy"),
        "sharpe": float(m["sharpe"]),
        "probabilistic_sharpe": float(m["probabilistic_sharpe"]),
        "deflated_sharpe": float(m["deflated_sharpe"]),
        "max_drawdown": float(m["max_drawdown"]),
        "annual_strategy": annual_return(strat_nav),
        "annual_benchmark": annual_return(bench_nav[~np.isnan(bench_nav)])
            if np.isfinite(bench_nav).any() and len(bench_nav[~np.isnan(bench_nav)]) > 1
            else float("nan"),
    }


def run_strategy(
    bars: dict[str, pd.DataFrame],
    strategy: StrategyTemplate,
    benchmark: pd.DataFrame | None = None,
    init_cash: float = 1_000_000.0,
    commission_rate: float = 0.0003,
    stamp_duty: float = 0.0005,
    slippage_bps: float = 5.0,
) -> StrategyRunResult:
    """事件驱动策略回测主入口（vnpy MainEngine + BacktesterEngine 的回测特化）。

    :param bars: {symbol: DataFrame[date, open, close]}（升序，QFQ 口径）
    :param benchmark: DataFrame[date, close]（可选基准）

    执行纪律（与 engine/ma_cross 一致）：
        T 日 on_bar 产生的 ctx.targets -> T+1 日开盘等权调仓（先卖后买）；
        T 日收盘 mark_to_market；无次日则放弃未执行调仓（不补成交）。
    """
    engine = EventEngine()
    all_days = sorted(set().union(*[set(df["date"]) for df in bars.values()])) \
        if bars else []

    # ---- 逐日行情索引（模拟停牌：缺失日前收盘填充）----
    # 流动性（volume/amount）取真实日线原始口径数据：
    # 缺失日（停牌/无数据）映射为 NaN -> broker._num 默认 0 -> 按 halted 拒绝成交，
    # 冲击成本按当日真实成交额参与率计算（P1-7 修复：不再合成 1e12 假流动性）
    px_open: dict[str, dict[date, float]] = {}
    px_close: dict[str, dict[date, float]] = {}
    px_volume: dict[str, dict[date, float]] = {}
    px_amount: dict[str, dict[date, float]] = {}
    px_limit_up: dict[str, dict[date, float]] = {}
    px_limit_down: dict[str, dict[date, float]] = {}
    for sym, df in bars.items():
        closes: dict[date, float] = {}
        opens: dict[date, float] = {}
        last_c: float | None = None
        for d, o, c in zip(df["date"], df["open"], df["close"]):
            if c is not None and c == c:
                last_c = float(c)
            closes[d] = last_c if last_c is not None else float("nan")
            opens[d] = float(o) if o is not None and o == o else (last_c or float("nan"))
        px_open[sym], px_close[sym] = opens, closes
        # Task 7（整改 A-P1-8）：涨跌停列透传给 broker——缺失时保持旧行为
        # （broker 默认 ±20%），有列则闸门按真实涨跌幅生效
        lu: dict[date, float] = {}
        ld: dict[date, float] = {}
        if "limit_up" in df.columns and "limit_down" in df.columns:
            for d, u, l in zip(df["date"], df["limit_up"], df["limit_down"]):
                if u is not None and u == u:
                    lu[d] = float(u)
                if l is not None and l == l:
                    ld[d] = float(l)
        px_limit_up[sym], px_limit_down[sym] = lu, ld
        vols: dict[date, float] = {}
        amts: dict[date, float] = {}
        if "volume" in df.columns and "amount" in df.columns:
            for d, v, a in zip(df["date"], df["volume"], df["amount"]):
                if v == v and v is not None and v > 0:
                    vols[d] = float(v)
                if a == a and a is not None and a > 0:
                    amts[d] = float(a)
        px_volume[sym], px_amount[sym] = vols, amts

    broker = Broker(init_cash=init_cash, commission_rate=commission_rate,
                    stamp_duty=stamp_duty,
                    config=BrokerConfig(slippage_bps=slippage_bps, enabled=True))

    targets: set[str] = set()
    signals_out: list[dict] = []

    def _on_bar(ev: Event) -> None:
        ctx = StrategyContext(date=ev.data["date"], targets=targets,
                              bars=ev.data["bars"], signals=signals_out)
        for sym, bar in ev.data["bars"].items():
            strategy.on_bar(ctx, sym, bar)

    engine.register("bar", _on_bar)

    # 策略状态机初始化（vnpy on_init 语义）：donchian/rsi 依赖 on_init
    # 建立滚动窗口缓冲，缺失会导致首根 bar 抛 AttributeError 且被
    # EventEngine 容错静默吞掉，表现为 0 信号 0 交易（P1-7 顺带修复）
    strategy.on_init(StrategyContext(date=None, targets=targets,  # type: ignore[arg-type]
                                     bars={}, signals=signals_out))

    trades: list[dict] = []
    avg_cost: dict[str, float] = {}   # 移动平均成本（卖出 pnl 用）
    pos_qty: dict[str, int] = {}      # 本地持仓跟踪（含 T 日锁定仓；
                                      # broker.holdings 在 mark_to_market 前不含当日买入）

    def _record(ts: list) -> None:
        """成交明细落账（只记 reason=filled 且 qty>0，与 ma_cross 口径一致；
        涨跌停/T+1 等被拒订单不进前端交易表）。"""
        for t in ts:
            if t.reason != "filled" or t.qty <= 0:
                continue
            pnl: float | None = None
            if t.side == OrderSide.SELL:
                base = avg_cost.pop(t.symbol, t.price)
                remaining = pos_qty.get(t.symbol, 0) - t.qty
                if remaining > 0:
                    pos_qty[t.symbol] = remaining
                    avg_cost.setdefault(t.symbol, base)   # 部分卖出保留成本
                else:
                    pos_qty.pop(t.symbol, None)
                pnl = (t.price - base) * t.qty - t.cost
            else:
                old_q = pos_qty.get(t.symbol, 0)
                new_q = old_q + t.qty
                avg_cost[t.symbol] = (avg_cost.get(t.symbol, t.price) * old_q
                                      + t.amount) / new_q
                pos_qty[t.symbol] = new_q
            trades.append({"date": t.date, "symbol": t.symbol,
                           "side": t.side.value, "price": round(t.price, 3),
                           "qty": t.qty, "fee": round(t.cost, 2), "pnl": pnl})

    rows: list[dict] = []
    pending: set[str] | None = None

    for d in all_days:
        # ---- 1) 昨日收盘产生的目标 -> 今日开盘执行（先卖后买，等权）----
        if pending is not None:
            uni_d = pd.DataFrame({
                "open": {s: px_open[s].get(d, float("nan")) for s in bars},
                "close": {s: px_close[s].get(d, float("nan")) for s in bars},
                # 真实流动性（P1-7）：volume/amount 取自日线原始口径；
                # 缺失/为 0 -> broker 按 halted 拒绝（停牌日不再以填充价成交），
                # 冲击成本按当日真实成交额参与率计算，回测不再系统性偏乐观
                "volume": {s: px_volume[s].get(d, float("nan")) for s in bars},
                "amount": {s: px_amount[s].get(d, float("nan")) for s in bars},
                "limit_up": {s: px_limit_up[s].get(d, float("nan")) for s in bars},
                "limit_down": {s: px_limit_down[s].get(d, float("nan")) for s in bars},
            })
            uni_d = uni_d.dropna(subset=["open"]).dropna(subset=["close"])
            if not uni_d.empty:
                sells = [Order(symbol=s, side=OrderSide.SELL, qty=q)
                         for s, q in list(broker.holdings.items()) if s not in pending]
                if sells:
                    _record(list(broker.match(d, sells, uni_d)))
                if pending:
                    _record(list(rebalance_equal_weight(broker, d, uni_d, set(pending))))
            pending = None

        # ---- 2) 推送 BAR 事件（策略状态机更新 targets）----
        bars_d = {s: {"open": px_open[s].get(d, float("nan")),
                      "close": px_close[s].get(d, float("nan"))}
                  for s in bars
                  if px_close[s].get(d) is not None and px_close[s][d] == px_close[s][d]}
        if bars_d:
            engine.put(Event(event_type="bar", data={"date": d, "bars": bars_d}))

        # ---- 3) 目标变化 -> 次日执行 ----
        if targets != set(broker.holdings):
            pending = set(targets)

        # ---- 4) 收盘估值 ----
        uni_close = pd.DataFrame({"close": {s: px_close[s].get(d, float("nan"))
                                            for s in bars}})
        broker.mark_to_market(d, uni_close)
        rows.append({"date": d, "equity": float(broker.total_equity),
                     "strategy_nav": float(broker.total_equity / init_cash),
                     "benchmark_nav": float("nan")})

    # ---- 基准对齐 ----
    nav_df = pd.DataFrame(rows)
    if benchmark is not None and not nav_df.empty:
        bench = benchmark.reset_index(drop=True)
        bench_map = dict(zip(bench["date"], bench["close"]))
        b0 = next((bench_map[d] for d in nav_df["date"] if bench_map.get(d)), None)
        if b0:
            nav_df["benchmark_nav"] = nav_df["date"].map(
                lambda d: (bench_map[d] / b0) if bench_map.get(d) else float("nan"))
            nav_df["benchmark_nav"] = nav_df["benchmark_nav"].ffill()

    res = StrategyRunResult(nav_df=nav_df, trades=trades, signals=signals_out)
    if nav_df.empty:
        return res
    strat = nav_df["strategy_nav"].to_numpy()
    bench_n = nav_df["benchmark_nav"].to_numpy()
    if not np.isfinite(bench_n).any():
        bench_n = np.ones_like(strat)   # 无基准时用常数（alpha/beta 记 NaN）
    res.risk = summarize_risk(strat, bench_n, trades)
    res.monthly = monthly_returns(pd.Series(strat, index=nav_df["date"]))
    logger.info(f"[strategy:{strategy.name}] {len(trades)} trades, "
                f"nav {strat[0]:.4f}->{strat[-1]:.4f}, "
                f"sharpe={res.risk['sharpe']:.2f}")
    return res
