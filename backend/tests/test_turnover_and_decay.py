"""审计（2026-09-21）§8.2 第 5 项「摩擦/换手口径」防回归。

覆盖两条**经实测确立**的缺陷，以及对报告 B5-08 的**实测更正**：

1. **P1-1 换手衰减被反复扣**（`engine.py` 每日无条件 `apply_decay_cost`，而
   `last_day_turnover` 只在有成交日更新）：
   实测周频 21 天，ORIG 把同一个周五的换手连扣 **5 次**（共 17 次），
   权益 −1.71% vs 修复后 −0.67%，Sharpe −22.86 vs −7.29。
   修复：`Broker.begin_day()` 逐日复位 + `match()` 内日期守卫。

2. **B5-08 报告结论被实测推翻**：报告称换手「买卖双边累加 ⇒ 2×，实测 0.94、
   真实≈0.47」。实测（`backend/.tmp_testrun/b508_turnover_verdict.py`）表明
   `broker.match` 仅 3 处调用、**从不传买卖混合列表**，且 `rebalance_to_weights`
   是「先卖一次 match、再买一次 match」而 `match` 每次**覆盖**
   `last_day_turnover` ⇒ 原值已经是**单边**（全仓换标的 0.9483 = 买腿，
   仅加仓 0.4741 = 买腿）。**0.9483 本就是全仓换标的的单边换手**，
   报告说的「真实≈0.47」是按两腿均值理解（见下方两口径对照）。

3. **但同一处确有另一类真实缺陷（口径随执行顺序漂移）**：原实现取「最后一次
   match 的那条腿」，只在两腿金额相等时巧合正确 —— 最典型的是**仅清仓日**
   （卖腿有成交、买腿全被拒）会拿到卖腿 0.95，而不对称调仓日又会取到较小的
   那条腿。修复：当日双腿累加，`last_day_turnover = (买 + 卖) / 2 / 权益`。

⚠️ **两种"单边换手"口径（2026-09-21 二次复核更正，先前版本曾错误声称等价）**：

    口径 A（买卖均值，**本实现**）：(B + S) / 2 / E
    口径 B（现金算持仓的 Σ|Δw|/2）：恒等于 **max(B, S) / E**

    （因 Δ现金 = S − B ⇒ (B + S + |S − B|)/2 = max(B, S)；手续费会略微打破恒等。）

    | 场景 | 买 B / 卖 S | 口径 A | 口径 B |
    |---|---|---|---|
    | 全仓换标的 | 0.95 / 0.95 | **0.95** | 0.95 |
    | 仅加仓（现金→股票） | 0.95 / 0 | **0.475** | **0.95** |
    | 仅清仓（股票→现金） | 0 / 0.95 | **0.475** | **0.95** |

    **现金平衡日两口径恒等**（这正是报告 §7 稳态换手 Top-10 134.7×/年、
    Top-50 81.0×/年不受影响的原因）；建仓/清仓日 A 是 B 的一半。
    代码选 A 的理由：`last_day_turnover` 主要驱动 `apply_decay_cost`（成本），
    而成本按**实际成交名义额**发生 —— 全清仓的名义额（1.0×E）只有全仓换标的
    （2.0×E）的一半，A 精确保持该比例，B 则把清仓与换标的记成同额。
    `test_two_turnover_conventions_relationship` 把该差异钉住，防止静默改口径。
"""
from __future__ import annotations

import sys
from datetime import date, timedelta
from pathlib import Path

BACKEND_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(BACKEND_ROOT))

import pandas as pd  # noqa: E402
import pytest  # noqa: E402

from app.backtest.broker import (  # noqa: E402
    Broker,
    BrokerConfig,
    Order,
    OrderSide,
)
from app.backtest.engine import rebalance_to_weights, run_backtest  # noqa: E402

D0 = date(2026, 8, 31)   # 建仓日
D1 = date(2026, 9, 1)    # 调仓日（必须与建仓日不同：当日账本是累加的）


def _uni(symbols: list[str], px: float = 10.0, *, limit_up_all: bool = False) -> pd.DataFrame:
    """构造 uni_d；limit_up_all=True 时把 open 顶到涨停（买单会被拒）。"""
    rows = {}
    for s in symbols:
        up = px * 1.1
        rows[s] = {
            "open": up if limit_up_all else px,
            "close": up if limit_up_all else px,
            "volume": 1e9, "amount": 1e9,
            "limit_up": up, "limit_down": px * 0.9,
        }
    return pd.DataFrame(rows).T


def _broker() -> Broker:
    return Broker(init_cash=1_000_000.0,
                  config=BrokerConfig(enabled=True, slippage_bps=0.0,
                                      decay_bps=10.0, impact_linear_bps=0.0))


def _leg_amounts(trades) -> tuple[float, float]:
    sell = sum(t.amount for t in trades
               if t.side == OrderSide.SELL and t.reason == "filled")
    buy = sum(t.amount for t in trades
              if t.side == OrderSide.BUY and t.reason == "filled")
    return sell, buy


def _build(b: Broker, uni: pd.DataFrame, symbols: list[str]) -> None:
    """在 D0 等权建仓并解锁（不 begin_day：模拟引擎的逐日推进由调用方控制）。"""
    rebalance_to_weights(b, D0, uni, {s: 1.0 / len(symbols) for s in symbols})
    b.mark_to_market(D0, uni)
    b.begin_day()          # 进入新交易日


# ---------------- 1) 单边换手定义 ----------------

def test_full_switch_turnover_is_one_way_not_two_sided() -> None:
    """全仓换标的：单边换手 ≈ 一腿金额/权益，**不是**两腿之和（更正 B5-08）。"""
    hold, tgt = ["600000.SH", "600001.SH"], ["600002.SH", "600003.SH"]
    b = _broker()
    uni = _uni(sorted(set(hold) | set(tgt)))
    _build(b, uni, hold)
    eq = b.total_equity

    trades = rebalance_to_weights(b, D1, uni, {s: 0.5 for s in tgt})
    sell, buy = _leg_amounts(trades)
    one_way, two_sided = (sell + buy) / 2 / eq, (sell + buy) / eq

    assert abs(b.last_day_turnover - one_way) < 0.01, (
        f"应为标准单边换手 {one_way:.4f}，实际 {b.last_day_turnover:.4f}")
    assert abs(b.last_day_turnover - two_sided) > 0.5, (
        f"换手被算成双边累加 {two_sided:.4f} ⇒ B5-08 所述缺陷复活")
    # 全仓换标的的标准单边换手应接近满仓比例（≈0.95），不应是它的一半
    assert b.last_day_turnover > 0.8, f"全仓换标的单边换手仅 {b.last_day_turnover:.4f}"


def test_sell_only_day_charges_half_not_full() -> None:
    """仅卖出腿成交（买腿全被涨停拒绝）：单边 = 卖/2/权益，原实现会多扣 2×。

    这是 P1-1/B5-08 区域里**真正**的 2× 场景（报告指错了场景，但方向确实存在）。
    """
    hold = ["600000.SH", "600001.SH"]
    b = _broker()
    uni = _uni(sorted(set(hold) | {"600002.SH"}))
    _build(b, uni, hold)
    eq = b.total_equity

    # 目标票开盘即涨停 -> broker.buy 返回 reason="limit_up"，只有卖腿成交
    uni_up = _uni(sorted(set(hold) | {"600002.SH"}), limit_up_all=True)
    trades = rebalance_to_weights(b, D1, uni_up, {"600002.SH": 1.0})
    sell, buy = _leg_amounts(trades)
    assert buy == 0.0, "买腿不应成交（涨停）"
    assert sell > 0.0, "卖腿应有成交"

    expected = sell / 2 / eq
    assert abs(b.last_day_turnover - expected) < 0.01, (
        f"仅卖出腿时应为 {expected:.4f}（标准 Σ|Δw|/2），"
        f"实际 {b.last_day_turnover:.4f}；若为 {sell / eq:.4f} 则是原实现的 2× 多扣")
    assert b.last_day_turnover < sell / eq * 0.75, "卖腿单边换手被当成整边（2× 多扣）"


# ---------------- 2) 逐日账本 ----------------

def test_turnover_accumulates_within_same_day() -> None:
    """同一交易日的多次 match（先卖后买）必须累加，而不是后者覆盖前者。"""
    hold, tgt = ["600000.SH"], ["600001.SH"]
    b = _broker()
    uni = _uni(sorted(set(hold) | set(tgt)))
    _build(b, uni, hold)

    sells = [Order(symbol="600000.SH", side=OrderSide.SELL, qty=b.holdings["600000.SH"])]
    t_sell = list(b.match(D1, sells, uni))
    after_sell = b.last_day_turnover
    assert after_sell > 0

    buys = [Order(symbol="600001.SH", side=OrderSide.BUY, cash=after_sell * 0 + 400_000.0)]
    t_buy = list(b.match(D1, buys, uni))
    after_buy = b.last_day_turnover

    sell_amt = sum(t.amount for t in t_sell if t.reason == "filled")
    buy_amt = sum(t.amount for t in t_buy if t.reason == "filled")
    assert buy_amt > 0 and sell_amt > 0
    expected = (sell_amt + buy_amt) / 2 / max(b.total_equity, 1.0)
    assert after_buy > after_sell, "两次 match 未累加（买腿覆盖了卖腿）⇒ 退回原实现"
    assert abs(after_buy - expected) < 0.01


def test_new_date_resets_ledger_without_begin_day() -> None:
    """`match()` 自带日期守卫：调用方忘记 begin_day 也不得跨日累加。"""
    hold = ["600000.SH"]
    b = _broker()
    uni = _uni(sorted(set(hold) | {"600001.SH"}))
    _build(b, uni, hold)
    qty0 = b.holdings["600000.SH"]

    # 第 1 日：卖 3/5
    t1 = list(b.match(D1, [Order(symbol="600000.SH", side=OrderSide.SELL,
                                 qty=qty0 * 3 // 5)], uni))
    sell1 = sum(t.amount for t in t1 if t.reason == "filled")
    lap1 = b.last_day_turnover
    assert sell1 > 0 and lap1 > 0

    # 第 2 日：**不调用 begin_day**，只卖剩余的一半（金额明显小于第 1 日）
    d_next = D1 + timedelta(days=1)
    b.mark_to_market(D1, uni)
    qty1 = b.holdings["600000.SH"]
    t2 = list(b.match(d_next, [Order(symbol="600000.SH", side=OrderSide.SELL,
                                     qty=qty1 // 2)], uni))
    sell2 = sum(t.amount for t in t2 if t.reason == "filled")
    lap2 = b.last_day_turnover

    assert 0 < sell2 < sell1, "构造前提：第 2 日成交额应小于第 1 日"
    expected2 = sell2 / 2 / max(b.total_equity, 1.0)
    assert abs(lap2 - expected2) < 0.01, (
        f"第 2 日换手应为当日单边 {expected2:.4f}，实际 {lap2:.4f}；"
        f"若接近 {(sell1 + sell2) / 2 / max(b.total_equity, 1.0):.4f} 则是跨日累加未复位")
    assert lap2 < lap1, f"跨日未复位：新日换手 {lap2:.4f} ≥ 前一日 {lap1:.4f}"


def test_begin_day_zeroes_turnover_for_no_trade_day() -> None:
    """无成交日：begin_day 后换手必须归 0（否则 engine 每天照扣 decay = P1-1）。"""
    b = _broker()
    uni = _uni(["600000.SH"])
    _build(b, uni, ["600000.SH"])
    assert b.last_day_turnover == 0.0
    b.begin_day()
    assert b.last_day_turnover == 0.0
    assert b.day_traded_notional() == 0.0


# ---------------- 3) 引擎层：decay 只在成交日扣 ----------------

class _DecaySpy(Broker):
    """记录每次**真正扣费**的 (日期, 换手)。"""

    events: list[tuple[date, float]] = []
    current_day: date | None = None

    def begin_day(self) -> None:
        super().begin_day()
        _DecaySpy.current_day = None

    def match(self, d, orders, uni_d):      # noqa: ANN001
        _DecaySpy.current_day = d
        return super().match(d, orders, uni_d)

    def apply_decay_cost(self, side_turnover: float, equity: float) -> float:
        cost = super().apply_decay_cost(side_turnover, equity)
        if cost > 0:
            assert _DecaySpy.current_day is not None
            _DecaySpy.events.append((_DecaySpy.current_day, side_turnover))
        return cost


def _weekly_frames(n_days: int = 21):
    syms = ["600000.SH", "600001.SH", "600002.SH", "600003.SH"]
    days: list[date] = []
    cur = date(2026, 3, 2)
    while len(days) < n_days:
        if cur.weekday() < 5:
            days.append(cur)
        cur += timedelta(days=1)
    uni_rows, sig_rows = [], []
    for i, d in enumerate(days):
        for j, s in enumerate(syms):
            px = 10.0 + 0.1 * (j + 1)
            uni_rows.append({"date": d, "symbol": s, "open": px, "high": px,
                             "low": px, "close": px, "volume": 1e9, "amount": 1e9,
                             "limit_up": px * 1.1, "limit_down": px * 0.9})
            score = float(j) if (i // 5) % 2 == 0 else float(len(syms) - j)
            sig_rows.append({"date": d, "symbol": s, "pred_score": score})
    return pd.DataFrame(uni_rows), pd.DataFrame(sig_rows), days


def test_decay_charged_once_per_rebalance_not_every_day(monkeypatch) -> None:
    """周频 21 天：每个调仓日的换手只应扣**一次**（原实现扣 5 次）。"""
    import app.backtest.engine as eng

    _DecaySpy.events = []
    monkeypatch.setattr(eng, "Broker", _DecaySpy)

    uni, sig, days = _weekly_frames(21)
    run_backtest(uni, sig, init_cash=1_000_000, top_k=2,
                 rebalance_freq="weekly",
                 friction=BrokerConfig(enabled=True, slippage_bps=0.0,
                                       decay_bps=10.0, impact_linear_bps=0.0))

    events = _DecaySpy.events
    assert events, "本用例应至少产生一次 decay 扣费"
    charged_days = [d for d, _ in events]
    assert len(charged_days) == len(set(charged_days)), (
        f"同一交易日被重复扣费：{charged_days} ⇒ P1-1 回归")
    # 周频：21 个交易日 ≈ 4 个周五调仓日 ⇒ 扣费天数远小于 21
    assert len(charged_days) <= 5, (
        f"扣费天数 {len(charged_days)} 过多（应≈调仓日数 4）⇒ 非调仓日仍在扣费")
    # 且必须是周五（周频调仓落在周五）
    assert all(pd.Timestamp(d).weekday() == 4 for d in charged_days), (
        f"扣费日不全是调仓日：{charged_days}")


def test_reported_daily_turnover_is_zero_on_non_rebalance_days(monkeypatch) -> None:
    """`nav_df["turnover"]` 是「当日换手」：非调仓日必须为 0（年化口径才成立）。

    原实现该列在非调仓日填的是**上一次调仓的换手**，
    于是 `annual_turnover = 日均 × 252` 对周频策略虚高约 5×。
    """
    uni, sig, days = _weekly_frames(21)
    res = run_backtest(uni, sig, init_cash=1_000_000, top_k=2,
                       rebalance_freq="weekly",
                       friction=BrokerConfig(enabled=True, slippage_bps=0.0,
                                             decay_bps=10.0, impact_linear_bps=0.0))
    tv = res.nav_df["turnover"].to_numpy()
    nonzero = [float(x) for x in tv if x > 0]
    assert len(nonzero) == len(tv) - sum(1 for x in tv if x == 0), "计数自洽性"
    assert len(nonzero) <= 5, (
        f"非调仓日也报了非零换手（{len(nonzero)}/{len(tv)} 天）⇒ P1-1 回归：{tv}")
    # 调仓日的换手应接近满仓量级（等权 2 只、每周翻转）
    assert max(nonzero) > 0.3, f"调仓日换手异常小：{nonzero}"


@pytest.mark.parametrize("freq", ["daily", "weekly"])
def test_turnover_never_exceeds_one_way_cap(freq: str) -> None:
    """换手不应超过「满仓全部换掉」的量级：单边 ≤ 1.0（含少量滑点余量）。

    双边累加会把满仓换标的推到 ≈1.9，该上界可捕获 B5-08 那类回归。
    """
    uni, sig, _ = _weekly_frames(15)
    res = run_backtest(uni, sig, init_cash=1_000_000, top_k=2,
                       rebalance_freq=freq,
                       friction=BrokerConfig(enabled=True, slippage_bps=0.0,
                                             decay_bps=10.0, impact_linear_bps=0.0))
    tv = res.nav_df["turnover"].to_numpy()
    assert float(tv.max()) <= 1.01, (
        f"freq={freq} 单日换手 {float(tv.max()):.4f} > 1.0 ⇒ 疑似双边累加")


# ---------------- 两口径关系（防止静默改口径） ----------------

def test_two_turnover_conventions_relationship() -> None:
    """钉住「口径 A（本实现）」与「口径 B（含现金 Σ|Δw|/2 = max(B,S)/E）」的关系。

    现金平衡日（全仓换标的）两者**恒等**；单腿日（建仓/清仓）A 是 B 的**一半**。
    该差异是有意为之（A 与"实际成交名义额"成正比，见模块 docstring），
    此处的目的只是让任何口径漂移**显式失败**而不是静默改变成本。
    """
    def _turnover_after(build_syms: list[str], then: list[str]) -> tuple[float, float, float]:
        """在 D0 建仓 build_syms，D1 换成 then，返回 (A, B, 两腿名义额/权益)。"""
        b = Broker(init_cash=1_000_000.0,
                   config=BrokerConfig(enabled=False, slippage_bps=0.0))
        syms = sorted(set(build_syms) | set(then))
        uni = _uni(syms)
        b.match(D0, [Order(symbol=s, side=OrderSide.BUY, cash=499_000.0)
                     for s in build_syms], uni)
        b.mark_to_market(D0, pd.DataFrame({"close": {s: 10.0 for s in syms}}))
        b.begin_day()                       # D1
        sells = [Order(symbol=s, side=OrderSide.SELL, qty=b.holdings.get(s, 0))
                 for s in b.holdings if s not in then]
        buys = [Order(symbol=s, side=OrderSide.BUY,
                      cash=min(499_000.0, b.cash / max(len(then), 1)))
                for s in then]
        b.match(D1, sells + buys, uni)
        equity = max(b.total_equity, 1.0)
        a = b.last_day_turnover
        bb = max(b._day_buy_amount, b._day_sell_amount) / equity      # 口径 B
        return a, bb, (b._day_buy_amount + b._day_sell_amount) / equity

    # ① 全仓换标的：买腿 ≈ 卖腿 ⇒ 两口径恒等
    a, bb, both = _turnover_after(["600000.SH"], ["600001.SH"])
    assert a == pytest.approx(bb, rel=1e-6), (
        f"现金平衡日两口径应相等：A={a:.4f} B={bb:.4f}")
    assert a == pytest.approx(both / 2, rel=1e-6), "A 应恒等于(买+卖)/2/权益"

    # ② 仅加仓（现金 → 股票）：B 是 A 的两倍
    a2, b2, _ = _turnover_after(["600000.SH"], ["600000.SH", "600001.SH"])
    assert b2 == pytest.approx(a2 * 2, rel=1e-3), (
        f"仅加仓日：口径 B({b2:.4f}) 应为口径 A({a2:.4f}) 的 2 倍")

    # ③ 仅清仓（股票 → 现金）：B 是 A 的两倍（本次修复把 0.95 改成 0.475）
    a3, b3, _ = _turnover_after(["600000.SH"], [])
    assert a3 > 0, "清仓日仍应有换手（卖腿）"
    assert b3 == pytest.approx(a3 * 2, rel=1e-3), (
        f"仅清仓日：口径 B({b3:.4f}) 应为口径 A({a3:.4f}) 的 2 倍")


# ---------------- B5-17：策略回测路径的 decay ----------------

class _StrategyDecaySpy(Broker):
    """记录 `apply_decay_cost` 的逐日调用（turnover, equity, cost）。"""

    calls: list[tuple[float, float, float]] = []

    def apply_decay_cost(self, side_turnover: float, equity: float) -> float:
        cost = super().apply_decay_cost(side_turnover, equity)
        type(self).calls.append((side_turnover, equity, cost))
        return cost


def _strategy_bars(n_sym: int = 3, n_day: int = 40) -> dict[str, pd.DataFrame]:
    """构造有均线穿越的日线，使 ma_cross 产生真实换手。"""
    days: list[date] = []
    cur = date(2026, 1, 5)
    while len(days) < n_day:
        if cur.weekday() < 5:
            days.append(cur)
        cur += timedelta(days=1)
    out: dict[str, pd.DataFrame] = {}
    for j in range(n_sym):
        sym = f"60000{j}.SH"
        rows = []
        for i, d in enumerate(days):
            px = (10.0 + j) * (1.0 + 0.03 * ((i + j) % 9 - 4))
            rows.append({"date": d, "open": px, "high": px * 1.01, "low": px * 0.99,
                         "close": px, "volume": 1e7, "amount": px * 1e7,
                         "limit_up": px * 1.1, "limit_down": px * 0.9})
        out[sym] = pd.DataFrame(rows)
    return out


def test_strategy_path_charges_decay(monkeypatch) -> None:
    """B5-17：策略回测路径必须真的扣 `decay_bps`（原实现完全不扣 = 0 元）。

    `run_strategy` 构造 `BrokerConfig(..., enabled=True)`（decay 默认 10bp）
    却从不调用 `apply_decay_cost` ⇒ 声明的成本从未入账。
    """
    import app.backtest.strategy_base as sb
    from app.backtest.strategy_base import MaCrossStrategy

    _StrategyDecaySpy.calls = []
    monkeypatch.setattr(sb, "Broker", _StrategyDecaySpy)
    sb.run_strategy(bars=_strategy_bars(), strategy=MaCrossStrategy(),
                    init_cash=1_000_000.0, slippage_bps=5.0)

    charged = [c for _, _, c in _StrategyDecaySpy.calls if c > 0]
    assert charged, "策略路径没有任何 decay 入账 ⇒ B5-17 回归（声明了却从不扣）"
    assert sum(charged) > 0
    # 扣费额必须等于声明费率 × 当日单边换手 × 上日权益
    for turnover, equity, cost in _StrategyDecaySpy.calls:
        if turnover <= 0:
            assert cost == 0.0, "零换手日不得扣费"
            continue
        assert cost == pytest.approx(turnover * 10.0 / 10_000.0 * equity, rel=1e-9)


def test_strategy_decay_only_on_days_with_turnover(monkeypatch) -> None:
    """非换手日不得扣费（P1-1 在策略路径上的等价回归）。

    若忘了逐日 `begin_day()`，`last_day_turnover` 会沿用上次调仓值 ⇒
    **每个交易日**都扣一次，扣费天数会等于总交易日数。
    """
    import app.backtest.strategy_base as sb
    from app.backtest.strategy_base import MaCrossStrategy

    _StrategyDecaySpy.calls = []
    monkeypatch.setattr(sb, "Broker", _StrategyDecaySpy)
    bars = _strategy_bars(n_sym=3, n_day=40)
    sb.run_strategy(bars=bars, strategy=MaCrossStrategy(),
                    init_cash=1_000_000.0, slippage_bps=5.0)

    n_days = len(bars["600000.SH"])
    charged_days = sum(1 for _, _, c in _StrategyDecaySpy.calls if c > 0)
    assert charged_days > 0, "应至少有一个换手日"
    assert charged_days < n_days, (
        f"扣费天数({charged_days}) == 交易日数({n_days}) ⇒ 非换手日也在扣费（P1-1 回归）")
    # 存在"被调用但零换手"的日子 ⇒ 说明逐日复位确实生效
    zero_days = sum(1 for t, _, _ in _StrategyDecaySpy.calls if t <= 0)
    assert zero_days > 0, "应存在零换手的交易日（否则无法区分是否逐日复位）"


def test_strategy_decay_uses_configured_bps(monkeypatch) -> None:
    """扣费额随 `BrokerConfig.decay_bps` 变化（4× 费率 ⇒ 4× 成本）。"""
    import app.backtest.strategy_base as sb
    from app.backtest.broker import BrokerConfig as BC
    from app.backtest.strategy_base import MaCrossStrategy

    def _total(decay_bps: float) -> float:
        _StrategyDecaySpy.calls = []
        monkeypatch.setattr(sb, "Broker", _StrategyDecaySpy)
        monkeypatch.setattr(sb, "BrokerConfig",
                            lambda **kw: BC(**{**kw, "decay_bps": decay_bps}))
        sb.run_strategy(bars=_strategy_bars(), strategy=MaCrossStrategy(),
                        init_cash=1_000_000.0, slippage_bps=5.0)
        return sum(c for _, _, c in _StrategyDecaySpy.calls)

    base = _total(10.0)
    quadruple = _total(40.0)
    assert base > 0 and quadruple == pytest.approx(base * 4, rel=0.02), (
        f"decay 未随 decay_bps 变化：10bp={base:.2f}, 40bp={quadruple:.2f}")


def test_strategy_decay_zero_when_friction_disabled(monkeypatch) -> None:
    """`enabled=False` 时不得扣任何摩擦成本（含 decay）。"""
    import app.backtest.strategy_base as sb
    from app.backtest.broker import BrokerConfig as BC
    from app.backtest.strategy_base import MaCrossStrategy

    _StrategyDecaySpy.calls = []
    monkeypatch.setattr(sb, "Broker", _StrategyDecaySpy)
    monkeypatch.setattr(sb, "BrokerConfig", lambda **kw: BC(enabled=False))
    sb.run_strategy(bars=_strategy_bars(), strategy=MaCrossStrategy(),
                    init_cash=1_000_000.0, slippage_bps=5.0)
    assert all(c == 0.0 for _, _, c in _StrategyDecaySpy.calls), (
        "enabled=False 仍在扣 decay")