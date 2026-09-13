"""
A 股撮合器 Broker（P0-Critical #1）。

职责：接收订单，按 A 股规则逐单撮合，产出成交与【拒绝原因】。
引擎（engine.py）只负责时间推进与信号选择，撮合规则全部收敛在这里。

实现的 A 股规则（每条都有对应 TC 测试）：
- T+1       ：T 日买入进入 _locked_today，当日不可卖（reason="t1"），
              mark_to_market 时解冻到 holdings，T+1 可卖；
- 涨停      ：open >= limit_up * 0.9999 -> BUY 拒绝（reason="limit_up"）；
- 跌停      ：open <= limit_down * 1.0001 -> SELL 拒绝（reason="limit_down"）；
- 停牌      ：is_halted 或 volume <= 0 -> BUY/SELL 拒绝（reason="halted"）；
- 整手      ：买入数量向下取整到 100 股（floor(qty/100)*100），不足一手拒绝（reason="lot"）；
- 佣金      ：max(5.0, amount * commission_rate)，双边收取；
- 印花税    ：仅卖出 amount * stamp_duty（默认 0.0005）。

被拒绝的订单以 qty=0 的 Trade 记录返回（reason 标注原因），保证可观测性。
"""
from __future__ import annotations

import math
from dataclasses import dataclass, field
from datetime import date
from enum import Enum

import pandas as pd

LOT_SIZE = 100
COMMISSION_MIN = 5.0


@dataclass
class BrokerConfig:
    """摩擦成本配置（P2-4 + 机构级升级）：滑点 / 换手衰减 / 冲击成本。

    enabled=False 时全部成本为 0（P0 行为完全兼容）。

    冲击成本两种模型（Almgren-Chriss / Barra 口径）：
    - linear（默认，向后兼容）：order_amount 超过 daily_amount × impact_pct
      的部分按超额倍数 × impact_linear_bps 计费；
    - sqrt（机构级，推荐）：成本 = amount × coef_bps × sqrt(participation)，
      participation = amount / daily_amount，即平方根冲击成本模型
      —— 小单几乎免费，大单按参与率平方根增长，回测更保守更真实。

    参与率上限（流动性闸门）：max_participation > 0 时，
    单笔订单金额被截断到当日成交额的 max_participation 倍
    （机构常用 1%~5%），超出部分当日不成交（买单拒绝 reason=liquidity_cap，
    卖单部分成交、余量留仓次日再卖）。
    """

    slippage_bps: float = 5.0        # 滑点：成交价 = open × (1 ± bps/1e4)
    decay_bps: float = 10.0          # 换手衰减：|Δweight| × bps（engine 层按单边换手计）
    impact_pct: float = 0.02         # 线性冲击阈值：order_amount > daily_amount × pct 触发
    impact_linear_bps: float = 30.0  # 线性冲击：超额倍数 × bps
    impact_model: str = "linear"     # 冲击模型："linear" | "sqrt"
    impact_sqrt_coef_bps: float = 10.0   # sqrt 模型系数：bps = coef × sqrt(participation)
    max_participation: float = 0.0   # 单笔参与率上限（0 = 不限制）；如 0.05 = 5% 日成交额
    enabled: bool = False

    def __post_init__(self) -> None:
        if self.impact_model not in ("linear", "sqrt"):
            raise ValueError(
                f"impact_model 仅支持 linear/sqrt，收到 {self.impact_model!r}")


class OrderSide(str, Enum):
    BUY = "buy"
    SELL = "sell"


@dataclass(frozen=True)
class Order:
    """订单：BUY 用 cash 金额表达（按开盘价折算数量），SELL 用 qty 股数表达。"""

    symbol: str
    side: OrderSide
    qty: int | None = None
    cash: float | None = None


@dataclass(frozen=True)
class Trade:
    """成交或被拒绝的订单记录（qty=0 且 reason 非 "filled" 表示拒绝）。"""

    date: date
    symbol: str
    side: OrderSide
    price: float
    qty: int
    amount: float
    cost: float
    reason: str = "filled"


@dataclass
class Broker:
    """账户状态 + 撮合规则。"""

    init_cash: float
    commission_rate: float = 0.0003
    stamp_duty: float = 0.0005
    config: BrokerConfig | None = None
    cash: float = 0.0
    holdings: dict[str, int] = field(default_factory=dict)     # 可卖持仓（股数）
    _locked_today: dict[str, int] = field(default_factory=dict)  # T 日买入，当日不可卖
    total_equity: float = 0.0
    last_day_turnover: float = 0.0
    _prev_close: dict[str, float] = field(default_factory=dict)

    friction_costs: dict[str, float] = field(
        default_factory=lambda: {"slippage": 0.0, "impact": 0.0, "decay": 0.0})

    def __post_init__(self) -> None:
        self.cash = float(self.init_cash)
        self.total_equity = float(self.init_cash)

    # ---------------- 摩擦成本（P2-4） ----------------
    def _friction_price(self, open_: float, side: OrderSide) -> float:
        """滑点执行价（纯函数）：买 (1+slip)、卖 (1-slip)；未启用返回原价。"""
        if self.config is None or not self.config.enabled:
            return open_
        slip = self.config.slippage_bps / 10_000.0
        return open_ * (1 + slip) if side == OrderSide.BUY else open_ * (1 - slip)

    def _record_slippage(self, open_: float, px: float, qty: int) -> None:
        if self.config is not None and self.config.enabled and qty > 0:
            self.friction_costs["slippage"] += abs(px - open_) * qty

    def _impact_cost(self, amount: float, daily_amount: float, record: bool = True) -> float:
        """冲击成本（启用摩擦时生效）。

        - linear（默认）：超出 daily_amount×impact_pct 的部分按超额倍数计 bps；
        - sqrt（平方根冲击模型）：cost = amount × coef/1e4 × sqrt(participation)，
          participation = amount / daily_amount（截断到 1）。
          daily_amount 缺失（0）时不计冲击（数据缺失宁可低估也不虚构成本）。
        """
        if self.config is None or not self.config.enabled:
            return 0.0
        if self.config.impact_model == "sqrt":
            if daily_amount <= 0:
                return 0.0
            participation = min(max(amount / daily_amount, 0.0), 1.0)
            cost = amount * (self.config.impact_sqrt_coef_bps / 10_000.0) \
                * math.sqrt(participation)
            if record:
                self.friction_costs["impact"] += cost
            return cost
        # ---- linear（向后兼容原口径） ----
        limit = daily_amount * self.config.impact_pct
        if amount <= limit or limit <= 0:
            return 0.0
        excess_ratio = amount / limit - 1.0
        cost = amount * (self.config.impact_linear_bps / 10_000.0) * excess_ratio
        if record:
            self.friction_costs["impact"] += cost
        return cost

    def _impact_cost_preview(self, amount: float, daily_amount: float) -> float:
        return self._impact_cost(amount, daily_amount, record=False)

    def apply_decay_cost(self, side_turnover: float, equity: float) -> float:
        """换手衰减：单边换手 × decay_bps，从现金扣除（engine 在 match 后调用）。"""
        if self.config is None or not self.config.enabled or side_turnover <= 0:
            return 0.0
        cost = side_turnover * (self.config.decay_bps / 10_000.0) * equity
        self.cash -= cost
        self.friction_costs["decay"] += cost
        return cost

    # ---------------- 费用 ----------------
    def _commission(self, amount: float) -> float:
        return max(COMMISSION_MIN, amount * self.commission_rate)

    def _sell_cost(self, amount: float) -> float:
        return self._commission(amount) + amount * self.stamp_duty

    # ---------------- 状态推进 ----------------
    def mark_to_market(self, d: date, uni_d: pd.DataFrame) -> None:
        """T 日收盘：T+1 解冻（locked -> 可卖），按收盘价重估总资产。

        CRIT-8 修复：收盘价缺失/NaN 时回退上一有效收盘价，避免 NaN 污染
        净值序列（旧实现 NaN 会直接传染 total_equity 并扩散到全部指标）。
        """
        for sym, qty in self._locked_today.items():
            self.holdings[sym] = self.holdings.get(sym, 0) + qty
        self._locked_today.clear()

        market_value = 0.0
        for sym, qty in self.holdings.items():
            px = self._prev_close.get(sym, 0.0)
            if sym in uni_d.index:
                px_today = self._num(uni_d.at[sym, "close"], default=px)
                if px_today == px_today and px_today > 0:   # 非 NaN 才更新
                    px = px_today
                    self._prev_close[sym] = px_today
            market_value += qty * px
        self.total_equity = self.cash + market_value

    # ---------------- 行情字段安全读取 ----------------
    @staticmethod
    def _num(v: object, default: float = 0.0) -> float:
        """CRIT-8 修复：NaN 安全的 float 转换。

        旧实现 `float(row.get("close", 0) or 0)` 中 NaN 为真值，
        `NaN or 0` 恒返回 NaN —— 导致停牌/缺口行的闸门（volume/halted/涨跌停
        判断）全部失效，订单以 NaN 价格"成交"并污染现金账户。
        """
        try:
            f = float(v)  # type: ignore[arg-type]
        except (TypeError, ValueError):
            return default
        return default if f != f else f   # f != f 即 NaN

    @classmethod
    def _row(cls, row: pd.Series) -> tuple[bool, float, float, float, float, float]:
        halted = bool(row.get("is_halted", False))
        close = cls._num(row.get("close"), default=0.0)
        volume = cls._num(row.get("volume"), default=0.0)
        open_ = cls._num(row.get("open"), default=close)
        limit_up = cls._num(row.get("limit_up"), default=close * 1.2)
        limit_down = cls._num(row.get("limit_down"), default=close * 0.8)
        # 复权因子 f_t = hfq/raw（Task 1）：hfq 口径结算时用于把整手/成交额
        # 换算回"物理股数/真实金额"域；无 factor 列（raw 域或策略框架路径）
        # 时为 1.0，行为与旧口径完全一致。
        factor = cls._num(row.get("factor"), default=1.0)
        if factor <= 0:
            factor = 1.0
        return halted, volume, open_, limit_up, limit_down, factor

    @staticmethod
    def _lot_size(factor: float) -> int:
        """hfq 域等效一手：lot_h = round(100 / f_t)，使 hfq 股数 × f_t ≈ 100 物理股。

        f_t=1 时恰为 100（与 raw 域一致）；f_t=2（10送10 后）为 50——
        50 股 hfq = 100 物理股，整手纪律在物理域保持。
        """
        if factor <= 0:
            return LOT_SIZE
        return max(1, int(round(LOT_SIZE / factor)))

    # ---------------- 参与率上限（流动性闸门） ----------------
    def _participation_cap_amount(self, row: pd.Series) -> float | None:
        """单笔最大成交金额（hfq 域；None = 不限制或无成交额数据）。"""
        cfg = self.config
        if cfg is None or not cfg.enabled or cfg.max_participation <= 0:
            return None
        daily_amount = self._daily_amount_hfq(row)
        if daily_amount <= 0:
            return None
        return daily_amount * cfg.max_participation

    def _daily_amount_hfq(self, row: pd.Series) -> float:
        """日成交额换算到 hfq 域（raw amount × f_t）；无 factor 列时为原值。

        订单金额（hfq 域）与日成交额必须同域，参与率才是真实口径。
        """
        _, _, _, _, _, factor = self._row(row)
        return self._num(row.get("amount"), default=0.0) * factor

    # ---------------- 卖出 ----------------
    def sell(self, d: date, symbol: str, qty: int, row: pd.Series) -> Trade:
        """卖出：T+1 / 跌停 / 停牌 / 参与率上限 检查 -> 按 open 价成交。

        参与率上限下允许**部分成交**：截断到当日成交额 × max_participation，
        余量留在持仓中（次日可再卖），与现实中的大单分日执行一致。
        """
        halted, volume, open_, _, limit_down, factor = self._row(row)
        if halted or volume <= 0 or open_ <= 0:
            return Trade(d, symbol, OrderSide.SELL, open_, 0, 0.0, 0.0, reason="halted")
        if open_ <= limit_down * 1.0001:
            return Trade(d, symbol, OrderSide.SELL, open_, 0, 0.0, 0.0, reason="limit_down")
        available = self.holdings.get(symbol, 0)
        locked = self._locked_today.get(symbol, 0)
        if available < int(qty) and locked > 0:
            # T 日买入部分仍在锁定区：触发 T+1 拒绝
            return Trade(d, symbol, OrderSide.SELL, open_, 0, 0.0, 0.0, reason="t1")
        qty = min(int(qty), available)
        if qty <= 0:
            return Trade(d, symbol, OrderSide.SELL, open_, 0, 0.0, 0.0, reason="no_position")
        cap_amount = self._participation_cap_amount(row)
        if cap_amount is not None:
            lot = self._lot_size(factor)
            max_qty = int(math.floor(cap_amount / max(open_, 1e-9) / lot) * lot)
            qty = min(qty, max_qty)
            if qty <= 0:
                return Trade(d, symbol, OrderSide.SELL, open_, 0, 0.0, 0.0,
                             reason="liquidity_cap")
        price = self._friction_price(open_, OrderSide.SELL)
        amount = price * qty
        impact = self._impact_cost(amount, self._daily_amount_hfq(row))
        self.cash -= impact
        cost = self._sell_cost(amount) + impact
        self._record_slippage(open_, price, qty)
        self.cash += amount - cost
        remaining = available - qty
        if remaining > 0:
            self.holdings[symbol] = remaining
        else:
            self.holdings.pop(symbol, None)
        return Trade(d, symbol, OrderSide.SELL, price, qty, amount, cost, reason="filled")

    # ---------------- 买入 ----------------
    def buy(self, d: date, symbol: str, cash_amount: float, row: pd.Series) -> Trade:
        """买入：涨停 / 停牌 / 参与率上限 / 整手 / 现金充足 检查 -> 按 open 价成交，进入 T+1 锁定。

        参与率上限：买入预算截断到当日成交额 × max_participation；
        截断后不足一手 -> 拒绝 reason="liquidity_cap"（当日流动性已被吃满）。
        """
        halted, volume, open_, limit_up, _, factor = self._row(row)
        if halted or volume <= 0 or open_ <= 0:
            return Trade(d, symbol, OrderSide.BUY, open_, 0, 0.0, 0.0, reason="halted")
        if open_ >= limit_up * 0.9999:
            return Trade(d, symbol, OrderSide.BUY, open_, 0, 0.0, 0.0, reason="limit_up")
        use_cash = min(float(cash_amount), self.cash)
        if use_cash <= 0:
            return Trade(d, symbol, OrderSide.BUY, open_, 0, 0.0, 0.0, reason="no_cash")
        cap_amount = self._participation_cap_amount(row)
        liquidity_capped = False
        if cap_amount is not None and cap_amount < use_cash:
            use_cash = cap_amount
            liquidity_capped = True
            if use_cash <= 0:
                return Trade(d, symbol, OrderSide.BUY, open_, 0, 0.0, 0.0,
                             reason="liquidity_cap")
        price = self._friction_price(open_, OrderSide.BUY)  # 滑点价参与数量计算
        daily_amount = self._daily_amount_hfq(row)
        lot = self._lot_size(factor)  # A 股整手（hfq 域等效手数，物理≈100 股）
        raw_qty = use_cash / price
        qty = int(math.floor(raw_qty / lot) * lot)
        if qty <= 0:
            reason = "liquidity_cap" if liquidity_capped else "lot"
            return Trade(d, symbol, OrderSide.BUY, open_, 0, 0.0, 0.0, reason=reason)
        # 含费（佣金+冲击）现金约束：不足则降一手重试
        while qty > 0:
            amount = price * qty
            cost = self._commission(amount) + self._impact_cost_preview(amount, daily_amount)
            if amount + cost <= self.cash:
                break
            qty -= lot
        if qty <= 0:
            return Trade(d, symbol, OrderSide.BUY, open_, 0, 0.0, 0.0, reason="no_cash")
        amount = price * qty
        impact = self._impact_cost(amount, daily_amount)  # 记账
        cost = self._commission(amount) + impact
        self._record_slippage(open_, price, qty)
        self.cash -= amount + cost
        self._locked_today[symbol] = self._locked_today.get(symbol, 0) + qty
        return Trade(d, symbol, OrderSide.BUY, price, qty, amount, cost, reason="filled")

    # ---------------- 退市强平 ----------------
    def liquidate(self, d: date, symbol: str, haircut: float = 0.5) -> Trade | None:
        """退市/长期退出宇宙的持仓强平：按最后有效收盘 × haircut 折价清仓。

        旧口径下此类持仓被 _prev_close 永久估值且卖单被 halted 闸门拒绝，
        净值系统性高估。折价清算近似真实退市回收（含整理期流动性缺失）。
        :return: 强平 Trade（reason="delisted_liquidation"）；无持仓返回 None。
        """
        qty = self.holdings.get(symbol, 0) + self._locked_today.get(symbol, 0)
        if qty <= 0:
            return None
        px = self._prev_close.get(symbol, 0.0) * haircut
        amount = px * qty
        self.cash += amount
        self.holdings.pop(symbol, None)
        self._locked_today.pop(symbol, None)
        self._prev_close.pop(symbol, None)
        # 立即重估总资产（剩余持仓按最后有效收盘价），否则净值滞后一天
        mv = sum(q * self._prev_close.get(s, 0.0)
                 for s, q in self.holdings.items())
        self.total_equity = self.cash + mv
        return Trade(d, symbol, OrderSide.SELL, px, qty, amount, 0.0,
                     reason="delisted_liquidation")

    # ---------------- 批量撮合 ----------------
    def match(self, d: date, orders: list[Order], uni_d: pd.DataFrame) -> list[Trade]:
        """撮合一批订单：先卖后买（腾挪资金），返回全部成交与拒绝记录。"""
        trades: list[Trade] = []
        turnover = 0.0

        for o in orders:
            if o.side != OrderSide.SELL or o.symbol not in uni_d.index:
                continue
            t = self.sell(d, o.symbol, int(o.qty or 0), uni_d.loc[o.symbol])
            trades.append(t)
            if t.reason == "filled":
                turnover += t.amount

        for o in orders:
            if o.side != OrderSide.BUY or o.cash is None or o.symbol not in uni_d.index:
                continue
            t = self.buy(d, o.symbol, float(o.cash), uni_d.loc[o.symbol])
            trades.append(t)
            if t.reason == "filled":
                turnover += t.amount

        equity = max(self.total_equity, 1.0)
        self.last_day_turnover = turnover / equity
        return trades
