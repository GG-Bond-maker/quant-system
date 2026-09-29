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
`match()` 的拒绝原因还包括：`no_bar`（该标的当日不在 `uni_d`，无行情）、
`no_cash`（买单未给出 `cash` 金额）。两者的 `price` 记 NaN（当日无行情/无报价，
编造价格会污染成交价聚合）。
"""
from __future__ import annotations

import math
from dataclasses import dataclass, field
from datetime import date
from enum import Enum

import pandas as pd

from ..domain.a_share_rules import effective_stamp_duty, effective_transfer_fee
from ..domain.trading_rules import COMMISSION_MIN, COMMISSION_RATE_DEFAULT

LOT_SIZE = 100


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
    # 默认佣金率来自唯一事实来源 domain.trading_rules（审计 R2 / §8.2 第 18 项）。
    commission_rate: float = COMMISSION_RATE_DEFAULT
    # 印花税率。None = 按**法定分段**（a_share_rules.stamp_duty_rate，2023-08-28
    # 前 1‰、之后 0.5‰）；显式给出数值则原样使用（尊重 DB/API 里配置的口径）。
    # ETF/LOF 无论哪种情况都免征（见 a_share_rules.effective_stamp_duty）。
    stamp_duty: float | None = None
    config: BrokerConfig | None = None
    cash: float = 0.0
    holdings: dict[str, int] = field(default_factory=dict)     # 可卖持仓（股数）
    _locked_today: dict[str, int] = field(default_factory=dict)  # T 日买入，当日不可卖
    total_equity: float = 0.0
    last_day_turnover: float = 0.0
    _prev_close: dict[str, float] = field(default_factory=dict)
    # 审计（2026-09-21）P1-1 / B5-08：当日双腿成交额累加。
    # 原实现让 `match()` **覆盖** `last_day_turnover`，于是该字段的取值取决于
    # 「最后一次 match 是哪条腿」——`rebalance_to_weights` 是先卖后买（留下买腿），
    # 而 `strategy_base` 单卖一次（留下卖腿），口径随调用顺序漂移；更糟的是没有
    # 任何成交的交易日它会**沿用上一次调仓的值**，engine 却每天照扣 decay。
    _day_buy_amount: float = 0.0
    _day_sell_amount: float = 0.0
    # 当日账本归属的交易日：`match()` 见到新日期即自动复位，使**任何**调用方
    # （engine 主循环 / 分组循环 / strategy_base 的自建循环）都得到正确的当日值，
    # 而不依赖调用方记得调用 `begin_day()`。
    _turnover_day: date | None = None

    friction_costs: dict[str, float] = field(
        default_factory=lambda: {"slippage": 0.0, "impact": 0.0, "decay": 0.0,
                                 "delist_loss": 0.0})

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

    # ---------------- 逐日换手口径（P1-1 / B5-08） ----------------
    def begin_day(self) -> None:
        """开启新交易日：清零当日双腿成交额与换手。

        ⚠️ 审计 P1-1：必须在**每个交易日开头**调用。原实现从不复位
        `last_day_turnover`，非调仓日 `match()` 不被调用 ⇒ engine 的
        「每日扣 decay」会反复扣**同一个**调仓日的换手成本
        （实测 21 天仅 1 次成交、成本被扣 16 次），周频策略尤甚
        （一周扣 5 次；报出的 `annual_turnover` 也虚高 5×）。
        复位后「当日无成交 ⇒ 换手 0 ⇒ 不扣费」，且 `turnover` 字段恢复
        「当日换手」的原义（`metrics.annual_turnover` 的日均口径才成立）。
        """
        self._day_buy_amount = 0.0
        self._day_sell_amount = 0.0
        self.last_day_turnover = 0.0
        self._turnover_day = None

    def day_traded_notional(self) -> float:
        """当日双腿成交额合计（成本核算/审计留痕用）。"""
        return self._day_buy_amount + self._day_sell_amount

    # ---------------- 费用 ----------------
    def _commission(self, amount: float) -> float:
        return max(COMMISSION_MIN, amount * self.commission_rate)

    def _sell_cost(self, amount: float, symbol: str | None = None,
                   d: date | None = None) -> float:
        """卖出费用 = 佣金 + 印花税 + 过户费。

        审计 B5-10 修复：此前是 ``self._commission(amount) + amount * self.stamp_duty``
        —— **不判品种**，对 ETF/LOF 卖出照收印花税，而项目另外三处
        （``ma_cross`` / ``paper`` / ``portfolio``）都豁免 ETF；且 ``stamp_duty``
        恒为 0.0005，2023-08-28 之前的回测把真实 1‰ 低估一半。现统一走
        :mod:`app.domain.a_share_rules` 的单一事实来源。
        """
        return (self._commission(amount)
                + amount * effective_stamp_duty(symbol or "", d, self.stamp_duty)
                + amount * effective_transfer_fee(symbol or ""))

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
        cost = self._sell_cost(amount, symbol, d) + impact
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
        # 含费（佣金+冲击+过户费）现金约束：不足则降一手重试
        # 审计 B5-10 / S1-T2：过户费**双边**收取（0.01‰，场内基金免），
        # 此前只算佣金与冲击成本，买入端的过户费完全没建模。
        transfer = 0.0
        while qty > 0:
            amount = price * qty
            transfer = amount * effective_transfer_fee(symbol)
            cost = (self._commission(amount)
                    + self._impact_cost_preview(amount, daily_amount)
                    + transfer)
            if amount + cost <= self.cash:
                break
            qty -= lot
        if qty <= 0:
            return Trade(d, symbol, OrderSide.BUY, open_, 0, 0.0, 0.0, reason="no_cash")
        amount = price * qty
        transfer = amount * effective_transfer_fee(symbol)
        impact = self._impact_cost(amount, daily_amount)  # 记账
        cost = self._commission(amount) + impact + transfer
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
        # 审计 P1-4 / S2（2026-09-21）：折价损失此前**完全不可见** ——
        # 只体现为"净值少涨了一点"，既不进 `friction_costs` 也不进任何披露字段，
        # 用户无法知道 haircut 究竟吃掉了多少钱（S2 明确要求计入 delist_loss）。
        # 损失 = 按最后有效收盘估值 − 折价回收额。
        loss = max(self._prev_close.get(symbol, 0.0) * qty - amount, 0.0)
        self.cash += amount
        self.friction_costs["delist_loss"] += loss
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
        """撮合一批订单：先卖后买（腾挪资金），返回全部成交与拒绝记录。

        ⚠️ 换手口径（审计 2026-09-21，P1-1 / B5-08 复核后修正）：
        `last_day_turnover` = **当日「买卖均值」单边换手**
        = (买腿成交额 + 卖腿成交额) / 2 / 权益。
        该值在当日多次 `match()` 调用间**累加**（`begin_day()` 复位），因此与
        「先卖一次 match、再买一次 match」或「买卖混在一批」的调用形态无关。

        ⚠️⚠️ **两种"单边换手"口径在现金不平衡日并不相等**（本节为 2026-09-21
        二次复核的更正；先前版本曾错误声称二者等价）：

            口径 A（买卖均值，**本实现**）：(B + S) / 2 / E
            口径 B（现金算持仓的 Σ|Δw|/2）：(B + S + |Δ现金|) / 2 / E

        | 场景 | 买 B / 卖 S | 口径 A | 口径 B |
        |---|---|---|---|
        | 全仓换标的（B≈S） | 0.95 / 0.95 | **0.95** | 0.95 |
        | 仅加仓（现金→股票） | 0.95 / 0 | **0.475** | **0.95** |
        | 仅清仓（股票→现金） | 0 / 0.95 | **0.475** | **0.95** |

        即：**现金平衡日（换标的）两口径恒等**，而**建仓/清仓日 A 比 B 小一半**
        （B 会把"现金腿"也计入 Δw）。选 A 的理由：decay 与冲击成本按**实际成交
        名义额**发生（每次成交都计费），A 与"平均成交名义额"成正比；且 B 会在
        "开盘建仓一次"这种单日行为上记满 1.0，与其后无交易的稳态不可比。
        差异面：每次回测**最多 1 个建仓日 + 1 个清仓日**，对报告 §7 的稳态换手
        （Top-10 134.7×/年、Top-50 81.0×/年）**无影响**（那些日子 B≈S）。

        原实现取「最后一次 match 的那条腿」，在两口径下都不自洽：仅清仓日拿到
        卖腿 0.95（口径 A 应为 0.475、口径 B 应为 0.95），不对称调仓日又会取到
        较小的那条腿。实测见 docs/audit-2026-09-18/FIXES-APPLIED.md。
        """
        trades: list[Trade] = []
        sell_amount = 0.0
        buy_amount = 0.0

        # 审计 B5-16（2026-09-21）：不在 uni_d 的订单此前被 `continue` **静默丢弃**
        # （既无 Trade 也无 reason），与本模块开头「被拒绝的订单以 qty=0 的 Trade
        # 记录返回，保证可观测性」的承诺直接矛盾 —— 调用方无从知道"为什么某些
        # 目标持仓当天没有建仓"。现按同一约定补 `reason="no_bar"` 的拒绝记录，
        # 使 `/backtest/run` 的 `rejected_trades` 汇总（backtest.py:164-167）
        # 能直接暴露"当日无行情/停牌未入面板"的订单数。
        # price 记 NaN：该标的当日**没有**行情，编造价格会污染成交价聚合。
        for o in orders:
            if o.side != OrderSide.SELL:
                continue
            if o.symbol not in uni_d.index:
                trades.append(Trade(d, o.symbol, OrderSide.SELL, float("nan"),
                                    0, 0.0, 0.0, reason="no_bar"))
                continue
            t = self.sell(d, o.symbol, int(o.qty or 0), uni_d.loc[o.symbol])
            trades.append(t)
            if t.reason == "filled":
                sell_amount += t.amount

        for o in orders:
            if o.side != OrderSide.BUY:
                continue
            if o.symbol not in uni_d.index:
                trades.append(Trade(d, o.symbol, OrderSide.BUY, float("nan"),
                                    0, 0.0, 0.0, reason="no_bar"))
                continue
            if o.cash is None:
                # 买单必须带 cash（buy() 按金额下单）；缺失时原实现静默跳过。
                trades.append(Trade(d, o.symbol, OrderSide.BUY, float("nan"),
                                    0, 0.0, 0.0, reason="no_cash"))
                continue
            t = self.buy(d, o.symbol, float(o.cash), uni_d.loc[o.symbol])
            trades.append(t)
            if t.reason == "filled":
                buy_amount += t.amount

        # 累加（而非覆盖）到当日账本；同一交易日的多次 match 共同构成当日换手。
        # 跨日则自动复位，避免调用方忘记 `begin_day()` 时累加器跨日增长。
        if self._turnover_day != d:
            self._turnover_day = d
            self._day_buy_amount = 0.0
            self._day_sell_amount = 0.0
        self._day_sell_amount += sell_amount
        self._day_buy_amount += buy_amount
        equity = max(self.total_equity, 1.0)
        # 口径 A「买卖均值」——非现金算持仓的 Σ|Δw|/2，二者仅在现金平衡日相等，
        # 详见本方法 docstring 的口径对照表。
        self.last_day_turnover = (
            (self._day_buy_amount + self._day_sell_amount) / 2.0 / equity)
        return trades
