"""
回测引擎（P0-Critical #1 + 机构级权重优化）：按交易日推进的 Top-K 日频策略回测。

执行顺序（严禁未来信号 / 未来价格）：
    交易日 T
        ↓
    读取 T-1 日 signal（signal_lag=1，硬编码滞后，杜绝当日信号当日成交）
        ↓
    筛选 Top-K（按 pred_score 降序）
        ↓
    计算目标权重（weighting 方案，风险类方案只用 T-1 及之前的收盘价）
        ↓
    生成订单：卖出不在目标内的持仓 + 按目标权重买入
        ↓
    Broker.match()（T 日 open 撮合，A 股规则闸门 + 摩擦模型见 broker.py）
        ↓
    T 日收盘 mark_to_market
        ↓
    记录 cash / equity / nav / turnover / holdings -> 进入 T+1

权重方案（weighting）：
    equal          Top-K 等权（默认，向后兼容）
    score_weighted 按 pred_score 截面秩百分位加权（高分重仓）
    risk_parity    风险平摊（Ledoit-Wolf 收缩 + RMT 去噪协方差，CCD 求解）
    max_div        最大分散度组合
    inverse_vol    波动率倒数加权

风险类方案的无前视纪律：协方差只用**执行日之前**的收盘价（closes_hist
在每日循环末尾追加，处理 T 日时可见的最大日期为 T-1）。
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date
from typing import Any

import numpy as np
import pandas as pd
from loguru import logger

from ..domain.metrics import all_metrics
from ..domain.optimizer import apply_weight_cap, compute_weights_from_returns
from .broker import LOT_SIZE, Broker, BrokerConfig, Order, OrderSide, Trade

# 支持的权重方案
WEIGHTING_CHOICES = ("equal", "score_weighted", "risk_parity", "max_div", "inverse_vol")


@dataclass
class BacktestResult:
    """回测输出：净值序列 / 成交明细（含拒绝）/ 持仓历史 / 绩效指标。"""

    nav_df: pd.DataFrame                       # date, cash, equity, nav, turnover
    trades: list[dict] = field(default_factory=list)
    holdings_history: list[dict] = field(default_factory=list)
    metrics: dict[str, float | int] = field(default_factory=dict)
    friction_costs: dict[str, float] = field(default_factory=dict)


def _target_symbols(
    sig_d: pd.DataFrame, top_k: int, current_holdings: dict[str, int],
    dropout_n: int = 0,
) -> set[str]:
    """TopkDropout 选股（qlib TopkDropoutStrategy 口径）。

    - dropout_n=0（默认）：经典 Top-K；
    - dropout_n>0：持仓只要仍处于前 (top_k + dropout_n) 名就**继续持有**，
      仅空出的名额按信号降序买入最优新标的。
      效果：边缘持仓不因排名微小波动被反复买卖 => 大幅降低换手与摩擦成本
      （qlib 实证：Topk-Dropout 在近似收益下换手显著低于朴素 Top-K）。
    - 信号不足时保留现有持仓，避免被动清仓。
    """
    if sig_d.empty:
        return set(current_holdings)
    ranked = sig_d.sort_values("pred_score", ascending=False)
    topk_syms = ranked.head(top_k)["symbol"].tolist()
    if dropout_n <= 0 or not current_holdings:
        return set(topk_syms)
    relaxed = set(ranked.head(top_k + dropout_n)["symbol"].tolist())
    keep = {s for s in current_holdings if s in relaxed}
    need = top_k - len(keep)
    if need <= 0:
        return keep
    new_syms = [s for s in topk_syms if s not in keep][:need]
    return keep | set(new_syms)


# 等权再平衡的容忍带：持仓权重偏离目标超过该比例才调仓，
# 避免每日因价格波动产生无意义的往返交易（省摩擦成本）。
WEIGHT_TOLERANCE = 0.10


def _open_prices(uni_d: pd.DataFrame) -> dict[str, float]:
    """取当日撮合价（开盘价）；缺 open 列时回退到 close。

    universe_daily 若不携带 OHLC（旧分区），直接返回空会让回测
    **一笔成交都不发生**却毫无提示。这里做兜底并显式告警。
    """
    if uni_d is None or uni_d.empty:
        return {}
    if "open" in uni_d.columns:
        col = "open"
    elif "close" in uni_d.columns:
        logger.warning("universe 缺少 open 列，回退用 close 定价（撮合价会失真）")
        col = "close"
    else:
        logger.warning("universe 缺少 open/close 列，无法定价 -> 当日不交易")
        return {}
    s = uni_d[col]
    return {sym: float(v) for sym, v in s.items() if v is not None and v == v}  # noqa: PLR0124  # NaN 判定的标准写法


def _portfolio_value(broker: Broker, px: dict[str, float]) -> tuple[float, float]:
    """返回 (持仓市值, 总资产)。价格缺失时用 0 估值并记为不可交易。"""
    mv = 0.0
    for sym, qty in broker.holdings.items():
        p = px.get(sym)
        if p is not None and p == p:  # noqa: PLR0124  # NaN 判定的标准写法
            mv += qty * float(p)
    return mv, float(broker.cash) + mv


def rebalance_to_weights(
    broker: Broker,
    d: date,
    uni_d: pd.DataFrame,
    target_weights: dict[str, float],
    cash_buffer: float = 0.95,
    tolerance: float = WEIGHT_TOLERANCE,
) -> list[Trade]:
    """按目标权重再平衡：**先卖出 -> 更新现金 -> 再按权重分配买入**。

    target_weights: {symbol: weight}，权重和 ≈ 1（内部不强制归一）。

    CRIT-001 修复说明（沿等权版口径）：
        1. 用开盘价估算总资产 -> 计算各目标的目标市值 equity × cash_buffer × w；
        2. 生成卖出单：清掉非目标持仓 + 削减超权重的目标持仓；
        3. **执行卖出**（现金真实增加）；
        4. 用卖出后的现金 + 持仓市值重新计算总资产；
        5. 按目标权重计算每个目标的资金缺口 -> 生成买入单；
        6. 执行买入。
    """
    px = _open_prices(uni_d)
    targets = {s for s, w in target_weights.items() if w > 0}
    if not targets or not px:
        return []

    _, equity_pre = _portfolio_value(broker, px)

    # ---- 阶段 1：卖出（非目标全清 + 超权重削减）----
    sell_orders: list[Order] = []
    for sym in list(broker.holdings):
        qty = broker.holdings[sym]
        p = px.get(sym)
        if p is None or p != p or p <= 0:  # noqa: PLR0124  # NaN 判定的标准写法
            continue
        w = target_weights.get(sym, 0.0)
        target_value = equity_pre * cash_buffer * w
        if w <= 0:
            sell_orders.append(Order(symbol=sym, side=OrderSide.SELL, qty=qty))
            continue
        cur_value = qty * p
        if cur_value > target_value * (1.0 + tolerance):
            excess_shares = int((cur_value - target_value) / p)
            excess_lots = (excess_shares // LOT_SIZE) * LOT_SIZE
            if excess_lots >= LOT_SIZE:
                sell_orders.append(
                    Order(symbol=sym, side=OrderSide.SELL, qty=excess_lots))
    trades = list(broker.match(d, sell_orders, uni_d)) if sell_orders else []

    # ---- 阶段 2：用卖出后的真实现金分配买入 ----
    _, equity_post = _portfolio_value(broker, px)

    buy_orders: list[Order] = []
    for sym in sorted(target_weights):
        w = target_weights[sym]
        if w <= 0:
            continue
        p = px.get(sym)
        if p is None or p != p or p <= 0:  # noqa: PLR0124  # NaN 判定的标准写法
            continue  # 停牌/无行情，不参与当日建仓
        target_value_post = equity_post * cash_buffer * w
        cur_value = broker.holdings.get(sym, 0) * p
        deficit = target_value_post - cur_value
        if deficit > 0:
            buy_orders.append(Order(symbol=sym, side=OrderSide.BUY, cash=float(deficit)))
    if buy_orders:
        trades += list(broker.match(d, buy_orders, uni_d))
    return trades


def rebalance_equal_weight(
    broker: Broker,
    d: date,
    uni_d: pd.DataFrame,
    targets: set[str],
    cash_buffer: float = 0.95,
    tolerance: float = WEIGHT_TOLERANCE,
) -> list[Trade]:
    """等权再平衡（rebalance_to_weights 的 1/N 特例，向后兼容）。"""
    if not targets:
        return []
    return rebalance_to_weights(
        broker, d, uni_d, {s: 1.0 / len(targets) for s in targets},
        cash_buffer=cash_buffer, tolerance=tolerance)


# ==================== 目标权重计算（无前视） ====================
def _trailing_returns(
    closes_hist: list[dict[str, float]],
    symbols: list[str],
    window: int,
    min_obs: int = 21,
) -> np.ndarray | None:
    """从逐日收盘快照构建对齐收益率矩阵（仅用执行日之前的数据）。

    closes_hist 每日循环末尾追加 {symbol: close}；处理 T 日时其内容
    最大日期为 T-1，天然无前视。目标资产在窗口内任一日缺行情则该日剔除；
    有效观测 < min_obs 返回 None（调用方回退等权）。
    """
    rows: list[list[float]] = []
    for day_map in closes_hist[-(window + 1):]:
        vals: list[float] = []
        ok = True
        for s in symbols:
            v = day_map.get(s, float("nan"))
            if v is None or v != v:  # noqa: PLR0124
                ok = False
                break
            vals.append(float(v))
        if ok:
            rows.append(vals)
    if len(rows) < max(min_obs, 3):
        return None
    M = np.asarray(rows, dtype=np.float64)
    with np.errstate(invalid="ignore", divide="ignore"):
        R = M[1:] / M[:-1] - 1.0
    R = R[np.isfinite(R).all(axis=1)]
    return R if R.shape[0] >= max(min_obs - 1, 2) else None


def _compute_target_weights(
    weighting: str,
    sig_d: pd.DataFrame,
    targets: set[str],
    closes_hist: list[dict[str, float]],
    cov_window: int,
    weight_cap: float,
) -> dict[str, float]:
    """按 weighting 方案计算目标权重；数据不足时回退等权（记 warning）。"""
    n = len(targets)
    if n == 0:
        return {}
    if weighting == "equal":
        return {s: 1.0 / n for s in targets}

    if weighting == "score_weighted":
        if sig_d.empty:
            return {s: 1.0 / n for s in targets}
        sub = sig_d[sig_d["symbol"].isin(targets)]
        if sub.empty:
            return {s: 1.0 / n for s in targets}
        pct = sub.set_index("symbol")["pred_score"].rank(pct=True).clip(lower=0.05)
        arr = pct.reindex(sorted(targets)).fillna(0.05).to_numpy(dtype=np.float64)
        w = arr / arr.sum()
        if weight_cap > 0:
            w = apply_weight_cap(w, weight_cap)
        return dict(zip(sorted(targets), w))

    # ---- 风险类方案：协方差只用执行日之前收盘价 ----
    syms = sorted(targets)
    R = _trailing_returns(closes_hist, syms, cov_window)
    if R is None:
        logger.warning(f"weighting={weighting} 历史观测不足，回退等权")
        return {s: 1.0 / n for s in targets}
    w = compute_weights_from_returns(R, method=weighting, weight_cap=weight_cap)
    return dict(zip(syms, w))


def run_backtest(
    universe: pd.DataFrame,
    signal: pd.DataFrame,
    init_cash: float = 1_000_000,
    top_k: int = 10,
    rebalance_freq: str = "daily",
    commission_rate: float = 0.0003,
    stamp_duty: float = 0.0005,
    signal_lag: int = 1,
    friction: BrokerConfig | None = None,
    weighting: str = "equal",
    cov_window: int = 60,
    weight_cap: float = 0.0,
    n_trials: int = 1,
    dropout_n: int = 0,
    delist_haircut: float = 0.5,
    delist_grace_days: int = 60,
) -> BacktestResult:
    """运行 Top-K 回测（默认等权，可选机构级权重方案）。

    :param universe: DataFrame[date, symbol, open, high, low, close, volume,
                            limit_up, limit_down, is_halted?]
    :param signal:   DataFrame[date, symbol, pred_score]（信号日期 S 的信号
                     最早只能在 S+signal_lag 个交易日执行）
    :param rebalance_freq: "daily" 或 "weekly"（周五调仓）
    :param weighting: 权重方案，见 WEIGHTING_CHOICES（equal/score_weighted/
                      risk_parity/max_div/inverse_vol）
    :param cov_window: 风险类方案使用的协方差回看窗口（交易日）
    :param weight_cap: 单资产权重上限（0 = 不限制；如 0.3 = 30%）
    :param n_trials:   Deflated Sharpe Ratio 的多重试验次数口径
    :param dropout_n:  TopkDropout 容忍名次（qlib TopkDropoutStrategy；
                       0 = 朴素 Top-K，>0 降低换手）
    :param delist_haircut: 退市强平折价（持仓连续 delist_grace_days 个交易日
                       退出宇宙后，按最后收盘 × haircut 折价清仓记账；
                       0 = 关闭强平，回到旧的"永久冻结估值"行为）
    :param delist_grace_days: 强平宽限交易日数
    """
    if rebalance_freq not in ("daily", "weekly"):
        raise ValueError(f"rebalance_freq 仅支持 daily/weekly，收到 {rebalance_freq!r}")
    if signal_lag < 1:
        raise ValueError("signal_lag 必须 >= 1（当日信号严禁当日成交，防未来信号泄漏）")
    if weighting not in WEIGHTING_CHOICES:
        raise ValueError(f"weighting 仅支持 {WEIGHTING_CHOICES}，收到 {weighting!r}")
    if dropout_n < 0:
        raise ValueError("dropout_n 必须 >= 0")

    uni = universe.sort_values(["date", "symbol"]).copy()
    sig = signal.sort_values(["date", "symbol"]).copy()
    # CRIT-6 优化：groupby 预分组替代逐日全表布尔过滤（O(N×D) -> O(N)）。
    # uni 已按 date, symbol 排序，每组内部顺序与原 uni[uni["date"]==d] 一致。
    uni_by_date: dict = {d: g for d, g in uni.groupby("date", sort=True)}
    dates = list(uni_by_date.keys())
    sig_by_date = {d: g for d, g in sig.groupby("date")}
    logger.info(f"backtest: {len(dates)} days, top_k={top_k}, freq={rebalance_freq}, "
                f"signal_lag={signal_lag}, weighting={weighting}")

    broker = Broker(init_cash=init_cash, commission_rate=commission_rate,
                    stamp_duty=stamp_duty, config=friction)
    rows: list[dict] = []
    trades_out: list[dict] = []
    holdings_out: list[dict] = []
    # 逐日收盘快照（处理 T 日时只有 T-1 及之前 -> 协方差无前视）
    closes_hist: list[dict[str, float]] = []
    # Task 6：持仓退出宇宙的连续缺席计数（退市强平用）
    absent_streak: dict[str, int] = {}

    for i, d in enumerate(dates):
        uni_d = uni_by_date[d].set_index("symbol")

        # ---- 1) 读取滞后信号（严禁 T 日及之后） ----
        sig_date = dates[i - signal_lag] if i >= signal_lag else None
        sig_d = sig_by_date.get(sig_date, pd.DataFrame()) if sig_date else pd.DataFrame()

        need_reb = True
        if rebalance_freq == "weekly" and i > 0:
            need_reb = pd.Timestamp(d).weekday() == 4  # 周五

        # ---- 2) 目标持仓 + 权重方案 + 再平衡（先卖后买）----
        if need_reb:
            targets = _target_symbols(sig_d, top_k, broker.holdings, dropout_n)
            tradable = {s for s in targets if s in uni_d.index}
            tw = _compute_target_weights(weighting, sig_d, tradable,
                                         closes_hist, cov_window, weight_cap)
            trades = rebalance_to_weights(broker, d, uni_d, tw)
        else:
            trades = []
        trades_out.extend(t.__dict__ for t in trades)

        # ---- 3.5) 换手衰减成本（P2-4）：单边换手 × decay_bps，按上日权益计 ----
        if friction is not None and friction.enabled:
            prev_equity = broker.total_equity
            broker.apply_decay_cost(broker.last_day_turnover, prev_equity)

        # ---- 4) T 日收盘估值（同时解冻 T 日买入 -> T+1 可卖）----
        broker.mark_to_market(d, uni_d)

        # ---- 4.5) 退市强平减记（Task 6）：持仓连续缺席超过宽限期 -> 折价清仓 ----
        if delist_haircut > 0:
            for sym in list(broker.holdings):
                if sym in uni_d.index:
                    absent_streak.pop(sym, None)
                else:
                    absent_streak[sym] = absent_streak.get(sym, 0) + 1
            for sym in [s for s, c in absent_streak.items()
                        if c > delist_grace_days]:
                t_liq = broker.liquidate(d, sym, delist_haircut)
                if t_liq is not None:
                    trades_out.append(t_liq.__dict__)
                absent_streak.pop(sym, None)

        # ---- 5) 收盘快照追加（供 T+1 及之后的风险权重计算）----
        day_close: dict[str, float] = {}
        if "close" in uni_d.columns:
            for sym, v in uni_d["close"].items():
                if v is not None and v == v and float(v) > 0:  # noqa: PLR0124
                    day_close[sym] = float(v)
        closes_hist.append(day_close)

        rows.append({
            "date": d,
            "cash": float(broker.cash),
            "equity": float(broker.total_equity),
            "nav": float(broker.total_equity / init_cash),
            "turnover": float(broker.last_day_turnover),
        })
        holdings_out.append({"date": d, "holdings": dict(broker.holdings)})

    nav_df = pd.DataFrame(rows)
    if nav_df.empty:
        return BacktestResult(nav_df=nav_df)
    metrics = all_metrics(nav_df["nav"].to_numpy(),
                          turnovers_per_day=nav_df["turnover"].to_numpy(),
                          n_trials=n_trials)
    logger.info(f"backtest done: nav=[{nav_df['nav'].iloc[0]:.4f}, "
                f"{nav_df['nav'].iloc[-1]:.4f}] sharpe={metrics['sharpe']:.3f} "
                f"mdd={metrics['max_drawdown']:.3f} "
                f"dsr={metrics['deflated_sharpe']:.3f}")
    return BacktestResult(nav_df=nav_df, trades=trades_out,
                          holdings_history=holdings_out, metrics=metrics,
                          friction_costs=dict(broker.friction_costs))


def run_backtest_comparison(
    universe: pd.DataFrame,
    signal: pd.DataFrame,
    friction: BrokerConfig,
    **kwargs: object,
) -> tuple[BacktestResult, BacktestResult]:
    """P2-4 验收：同一策略跑 raw（无摩擦）与 friction 两个版本，返回 (raw, friction)。"""
    raw = run_backtest(universe, signal, friction=None, **kwargs)  # type: ignore[arg-type]
    fri = run_backtest(universe, signal, friction=friction, **kwargs)  # type: ignore[arg-type]
    return raw, fri


# ================= P2-3：分组回测 =================
def run_group_backtest(
    universe: pd.DataFrame,
    signal: pd.DataFrame,
    groups: int = 5,
    init_cash: float = 1_000_000,
    commission_rate: float = 0.0003,
    stamp_duty: float = 0.0005,
    signal_lag: int = 1,
    friction: BrokerConfig | None = None,
) -> dict[str, object]:
    """按当日信号 rank 分位分组（Q1 最低 .. Qg 最高），组内等权、独立 Broker。

    防泄漏：T 日执行的是 T-1 日（signal_lag 前）的分组，与 run_backtest 同纪律。
    返回 group_nav（date × Q1..Qg + long_short）/ group_metrics /
    group_turnover / monthly_monotonic_ratio。
    """
    if groups < 2:
        raise ValueError("groups 必须 >= 2")
    uni = universe.sort_values(["date", "symbol"]).copy()
    sig = signal.sort_values(["date", "symbol"]).copy()
    # CRIT-6 优化：groupby 预分组替代逐日全表布尔过滤（同 run_backtest）
    uni_by_date: dict = {d: g for d, g in uni.groupby("date", sort=True)}
    dates = list(uni_by_date.keys())
    sig_by_date = {d: g for d, g in sig.groupby("date")}

    brokers = {q: Broker(init_cash=init_cash, commission_rate=commission_rate,
                         stamp_duty=stamp_duty, config=friction)
               for q in range(1, groups + 1)}
    nav_rows: list[dict] = []
    # MEDIUM 修复：原实现用 b.last_day_turnover（**最后一天**的换手）冒充分组换手率，
    # 字段名暗示是期间指标，实际只取了一天，严重低估/随机化。
    # 现在按交易日累加单边换手，最后取均值。
    turnover_sum: dict[int, float] = {q: 0.0 for q in range(1, groups + 1)}
    turnover_days: dict[int, int] = {q: 0 for q in range(1, groups + 1)}

    def group_of(score_rank_pct: float) -> int:
        return min(groups, int(score_rank_pct * groups) + 1)

    for i, d in enumerate(dates):
        uni_d = uni_by_date[d].set_index("symbol")
        sig_date = dates[i - signal_lag] if i >= signal_lag else None
        sig_d = sig_by_date.get(sig_date, pd.DataFrame()) if sig_date else pd.DataFrame()

        # 当日（滞后信号日）截面 rank_pct -> 分组目标
        targets: dict[int, set[str]] = {q: set() for q in range(1, groups + 1)}
        if not sig_d.empty:
            ranked = sig_d["pred_score"].rank(pct=True)
            for sym, pct in zip(sig_d["symbol"], ranked):
                targets[group_of(float(pct))].add(sym)

        for q in range(1, groups + 1):
            b = brokers[q]
            members = {s for s in targets[q] if s in uni_d.index}
            # 复用同一套等权再平衡（CRIT-001 同样影响分组回测）
            rebalance_equal_weight(b, d, uni_d, members)
            if friction is not None and friction.enabled:
                b.apply_decay_cost(b.last_day_turnover, b.total_equity)
            b.mark_to_market(d, uni_d)
            turnover_sum[q] += float(b.last_day_turnover)
            turnover_days[q] += 1

        row: dict[str, Any] = {"date": d}
        for q in range(1, groups + 1):
            row[f"Q{q}"] = brokers[q].total_equity / init_cash
        row["long_short"] = float(row[f"Q{groups}"]) - float(row["Q1"])
        nav_rows.append(row)

    nav_df = pd.DataFrame(nav_rows)
    group_metrics = {f"Q{q}": all_metrics(nav_df[f"Q{q}"].to_numpy())
                     for q in range(1, groups + 1)}

    # 按月单调性：各自然月内 Qg 月收益 > ... > Q1 的月份比例
    monthly = nav_df.copy()
    monthly["month"] = pd.to_datetime(monthly["date"]).dt.to_period("M")
    mono_months = 0
    total_months = 0
    for _, g in monthly.groupby("month"):
        rets = [g[f"Q{q}"].iloc[-1] / (g[f"Q{q}"].iloc[0] if len(g) > 1 else 1.0) - 0.0
                for q in range(1, groups + 1)]
        rets = [g[f"Q{q}"].iloc[-1] - g[f"Q{q}"].iloc[0] for q in range(1, groups + 1)]
        total_months += 1
        if all(rets[i] > rets[i + 1] for i in range(len(rets) - 1)):
            mono_months += 1
    return {
        "groups": groups,
        "group_nav": nav_df,
        "group_metrics": group_metrics,
        # 日均单边换手率（期间均值，非最后一日快照）
        "group_turnover": {f"Q{q}": (turnover_sum[q] / turnover_days[q]
                                     if turnover_days[q] else 0.0)
                           for q in range(1, groups + 1)},
        "monthly_monotonic_ratio": (mono_months / total_months) if total_months else 0.0,
        "friction_costs": dict(brokers[groups].friction_costs),
    }
