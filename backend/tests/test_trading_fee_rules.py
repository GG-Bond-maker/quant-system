"""审计（2026-09-21）§8.2 第 5 项 · B5-10 / S1-T2：费率与品种判定单一事实来源。

实测确认的三条缺陷（`backend/.tmp_testrun/b510_etf_predicate.py`）：

1. **ETF 判定有四套、互不一致**：
   `ma_cross`/`paper` 用 `("5","15","16",…)`、`watchlist` 用
   `("51","56","58","15")[:4] ∪ {"159"}`、**broker 根本不判品种**。
   实测 `160123`（深市 LOF）⇒ watchlist **False** 而 ma_cross/paper **True**；
   broker 对 510300/159915 等**全部**返回「非基金」并对卖出照收印花税。

2. **`watchlist.is_etf_code` 的 `and`/`or` 优先级缺陷**：原表达式
   `len==6 and isdigit() and … or startswith("159")` 使长度/数字校验被绕过，
   `"159abc"`、`"159915.SH"` 均被判为基金；且漏 16xxxx ⇒
   `normalize_symbol("160123")` 走股票分支，由 `code_to_symbol` 兜底拼成
   **并不存在的 `160123.SH`**。

3. **印花税不分段 + 无过户费**：`stamp_duty` 恒 0.0005，而 2023-08-28 之前
   法定为 1‰ ⇒ 覆盖 2016 年起的行情时把真实成本低估一半；过户费完全未建模。

修复后：以上全部委托 `app.domain.a_share_rules`（单一事实来源）。
"""
from __future__ import annotations

import sys
from datetime import date
from pathlib import Path

BACKEND_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(BACKEND_ROOT))

import pandas as pd  # noqa: E402
import pytest  # noqa: E402

from app.api.v1.watchlist import is_etf_code, normalize_symbol  # noqa: E402
from app.backtest.broker import Broker, Order, OrderSide  # noqa: E402
from app.backtest.ma_cross import _is_etf_symbol  # noqa: E402
from app.backtest.strategy_base import STRATEGIES, run_strategy  # noqa: E402
from app.domain.a_share_rules import (  # noqa: E402
    STAMP_DUTY_CUT_DATE,
    effective_stamp_duty,
    effective_transfer_fee,
    is_etf_symbol,
    stamp_duty_rate,
    transfer_fee_rate,
)
from app.trading.paper import _is_etf  # noqa: E402

# 510300 沪 ETF / 512880 沪 ETF / 588000 科创 ETF / 159915 深 ETF
# 160123 深 LOF / 180101 深封闭式 / 600519 沪股票 / 300750 深创业板 / 688981 科创股票
ETF_CODES = ["510300", "512880", "588000", "159915", "160123", "180101"]
STOCK_CODES = ["600519", "000001", "300750", "688981", "601318"]


# ---------------- 1) 单一事实来源 ----------------

@pytest.mark.parametrize("code", ETF_CODES)
def test_all_etf_predicates_agree_on_funds(code: str) -> None:
    """四处判定（含 broker 所用的同一函数）必须一致认作场内基金。"""
    assert is_etf_symbol(code) is True
    assert _is_etf_symbol(code) is True            # ma_cross
    assert _is_etf(code) is True                   # paper
    assert is_etf_code(code) is True               # watchlist


@pytest.mark.parametrize("code", STOCK_CODES)
def test_all_etf_predicates_agree_on_stocks(code: str) -> None:
    assert is_etf_symbol(code) is False
    assert _is_etf_symbol(code) is False
    assert _is_etf(code) is False
    assert is_etf_code(code) is False


def test_predicates_accept_exchange_suffix_and_whitespace() -> None:
    """带后缀 / 带空白是合法归一化形态，应仍判为基金。"""
    for form in ("510300.SH", "159915.SZ", " 510300 ", "160123.SZ"):
        assert is_etf_symbol(form) is True, form


@pytest.mark.parametrize("bad", ["159abc", "159915.SH.XX", "", "abc", "51030", "5103001"])
def test_malformed_codes_are_not_funds(bad: str) -> None:
    """格式损坏不得被判成基金（原 watchlist 因 `and/or` 优先级对 "159abc" 返回 True）。"""
    assert is_etf_symbol(bad) is False, f"{bad!r} 不应判为基金"
    assert is_etf_code(bad) is False, f"{bad!r} 不应判为基金（watchlist 优先级缺陷回归）"


def test_16x_fund_normalizes_without_fake_exchange_suffix() -> None:
    """160123（深 LOF）不得被拼成不存在的 `160123.SH`（原缺陷）。"""
    got = normalize_symbol("160123")
    assert got == "160123", f"深市 LOF 归一化错误：{got}（应为纯代码）"
    assert normalize_symbol("180101") == "180101"
    # 对照：股票仍应补后缀
    assert normalize_symbol("600519") == "600519.SH"


# ---------------- 2) 费率函数（单一来源） ----------------

def test_stamp_duty_is_date_segmented() -> None:
    """2023-08-28 减半：之前 1‰、之后 0.5‰；不传日期用当前税率。"""
    before = STAMP_DUTY_CUT_DATE.replace(year=STAMP_DUTY_CUT_DATE.year - 1)
    assert stamp_duty_rate("stock", "sell", before) == 0.001
    assert stamp_duty_rate("stock", "sell", STAMP_DUTY_CUT_DATE) == 0.0005
    assert stamp_duty_rate("stock", "sell", date(2026, 9, 1)) == 0.0005
    # 不传日期 = 当前税率（保持历史调用方行为）
    assert stamp_duty_rate("stock", "sell") == 0.0005
    # ETF 与买入恒为 0
    assert stamp_duty_rate("etf", "sell", before) == 0.0
    assert stamp_duty_rate("stock", "buy", before) == 0.0


def test_effective_stamp_duty_priority() -> None:
    """优先级：ETF 免征 > 显式 override > 法定分段。"""
    old = date(2020, 1, 2)
    assert effective_stamp_duty("510300", old, None) == 0.0
    # ETF 的法定豁免**不可**被 override 覆盖
    assert effective_stamp_duty("510300", old, 0.001) == 0.0
    assert effective_stamp_duty("600519", old, None) == 0.001
    assert effective_stamp_duty("600519", old, 0.0005) == 0.0005
    assert effective_stamp_duty("600519", date(2026, 9, 1), None) == 0.0005


def test_transfer_fee_stocks_only() -> None:
    assert transfer_fee_rate("stock") == 0.00001
    assert transfer_fee_rate("etf") == 0.0
    assert effective_transfer_fee("600519.SH") == 0.00001
    assert effective_transfer_fee("510300.SH") == 0.0


# ---------------- 3) Broker 集成 ----------------

def _row(px: float = 10.0) -> pd.Series:
    return pd.Series({"open": px, "close": px, "volume": 1e9, "amount": 1e9,
                      "limit_up": px * 1.1, "limit_down": px * 0.9, "factor": 1.0})


def _sell_cost(symbol: str, d: date, amount: float = 10_000.0,
               stamp_duty: float | None = None) -> float:
    """建仓后卖出 amount 规模，返回 cost。"""
    b = Broker(init_cash=1_000_000.0, stamp_duty=stamp_duty)
    b.holdings[symbol] = int(amount / 10.0)
    b._prev_close[symbol] = 10.0
    t = b.sell(d, symbol, qty=int(amount / 10.0), row=_row())
    assert t.reason == "filled", t.reason
    return t.cost


def test_broker_exempts_etf_stamp_duty() -> None:
    """broker 卖出 ETF 不得收印花税（B5-10 的「1 处收」）。"""
    d = date(2026, 9, 1)
    stock = _sell_cost("600519.SH", d)
    etf = _sell_cost("510300.SH", d)
    amount = 10_000.0
    # 股票：佣金 5 + 印花税 5 + 过户费 0.1 = 10.1；ETF：佣金 5 + 0 + 0 = 5.0
    assert stock == pytest.approx(5.0 + amount * 0.0005 + amount * 0.00001)
    assert etf == pytest.approx(5.0), f"ETF 卖出被收印花税/过户费：{etf}"
    assert etf < stock, "ETF 卖出成本应低于同额股票"


def test_broker_charges_legacy_stamp_duty_before_2023_08_28() -> None:
    """P0 数据覆盖 2016 年起：2023-08-28 之前的卖出必须按 1‰ 计（原实现低估一半）。"""
    amount = 10_000.0
    old = _sell_cost("600519.SH", date(2020, 6, 1))
    new = _sell_cost("600519.SH", date(2026, 9, 1))
    assert old - new == pytest.approx(amount * 0.0005), (
        f"新旧税率差额应为 {amount * 0.0005}（1‰ vs 0.5‰），实际 {old - new}")
    assert old == pytest.approx(5.0 + amount * 0.001 + amount * 0.00001)


def test_broker_honours_explicit_stamp_duty_override() -> None:
    """显式注入的税率原样使用（DB/API 配置的口径不被法定分段覆盖）。"""
    amount = 10_000.0
    got = _sell_cost("600519.SH", date(2020, 6, 1), stamp_duty=0.0003)
    assert got == pytest.approx(5.0 + amount * 0.0003 + amount * 0.00001)


def test_broker_buy_pays_transfer_fee_but_not_stamp_duty() -> None:
    """买入：无印花税，但过户费双边收取（S1-T2 补建模）。"""
    b = Broker(init_cash=1_000_000.0)
    t = b.buy(date(2026, 9, 1), "600519.SH", cash_amount=100_000.0, row=_row())
    assert t.reason == "filled"
    assert t.cost == pytest.approx(100_000.0 * 0.0003 + 100_000.0 * 0.00001)


def test_broker_buy_etf_transfer_fee_exempt() -> None:
    b = Broker(init_cash=1_000_000.0)
    t = b.buy(date(2026, 9, 1), "510300.SH", cash_amount=100_000.0, row=_row())
    assert t.reason == "filled"
    assert t.cost == pytest.approx(100_000.0 * 0.0003), "ETF 买入不应收过户费"


def test_match_path_also_uses_single_source() -> None:
    """经 `match()` 批量撮合的卖出同样享受 ETF 豁免（勿只改 `sell()`）。"""
    uni = pd.DataFrame({"open": [10.0], "close": [10.0], "volume": [1e9],
                        "amount": [1e9], "limit_up": [11.0], "limit_down": [9.0]},
                       index=["510300.SH"])
    b = Broker(init_cash=1_000_000.0)
    b.holdings["510300.SH"] = 1_000
    b._prev_close["510300.SH"] = 10.0
    trades = b.match(date(2026, 9, 1),
                     [Order(symbol="510300.SH", side=OrderSide.SELL, qty=1_000)], uni)
    t = next(x for x in trades if x.reason == "filled")
    assert t.cost == pytest.approx(5.0), f"match 路径 ETF 卖出成本异常：{t.cost}"


# ---------------- 4) 回归：面板日期是 pd.Timestamp（线上确定性崩溃） ----------------
# 线上缺陷：回测面板索引（strategy_base.run_strategy 的 d）是 pd.Timestamp，
# stamp_duty_rate 此前直接把它与 datetime.date 常量比较 ⇒
#   TypeError: Cannot compare Timestamp with datetime.date
# ⇒ 每次卖出都崩、策略回测恒返回 code=50000。以下断言**显式构造
# pd.Timestamp**（只测 date 测不出该缺陷）。

def test_stamp_duty_rate_accepts_pandas_timestamp() -> None:
    """Timestamp 入参：2023-08-28 切点之后 0.5‰、之前 1‰。"""
    assert stamp_duty_rate("stock", "sell", pd.Timestamp("2024-01-02")) == 0.0005
    assert stamp_duty_rate("stock", "sell", pd.Timestamp("2023-01-02")) == 0.001


def test_stamp_duty_rate_timestamp_non_stock_sell_or_buy_is_zero() -> None:
    """买入 / 场内基金卖出恒为 0（Timestamp 入参同样适用）。"""
    assert stamp_duty_rate("stock", "buy", pd.Timestamp("2024-01-02")) == 0.0
    assert stamp_duty_rate("etf", "sell", pd.Timestamp("2024-01-02")) == 0.0


@pytest.mark.parametrize("raw", ["2023-01-02", "2024-01-02", "2023-08-28"])
def test_stamp_duty_rate_date_and_timestamp_agree(raw: str) -> None:
    """同一日期用 datetime.date 与 pd.Timestamp 两种入参必须得到相同答案（本 bug 直接回归）。"""
    as_date = date.fromisoformat(raw)
    assert stamp_duty_rate("stock", "sell", as_date) \
        == stamp_duty_rate("stock", "sell", pd.Timestamp(raw))


def test_effective_stamp_duty_accepts_timestamp_and_keeps_priority() -> None:
    """计费入口 effective_stamp_duty 同样接受 Timestamp；优先级语义不变。"""
    assert effective_stamp_duty("600519.SH", pd.Timestamp("2023-01-02")) == 0.001
    assert effective_stamp_duty("600519.SH", pd.Timestamp("2024-01-02")) == 0.0005
    # ETF 法定豁免 > override（override 不能覆盖豁免）
    assert effective_stamp_duty("510300.SH", pd.Timestamp("2023-01-02"), 0.001) == 0.0
    # 股票 override 显式给出则原样使用
    assert effective_stamp_duty("600519.SH", pd.Timestamp("2023-01-02"), 0.0003) == 0.0003


def _timestamp_indexed_frame() -> pd.DataFrame:
    """date 列为 pd.Timestamp（回测面板真实形态）——含金叉买入 + 死叉卖出。"""
    close = [10.0, 10.1, 10.2, 10.3, 10.31, 10.305, 10.309, 10.310, 10.29, 10.28]
    return pd.DataFrame({
        "date": pd.bdate_range("2026-01-05", periods=len(close)),  # pd.Timestamp
        "symbol": "600000.SH",
        "open": close, "high": [c * 1.01 for c in close],
        "low": [c * 0.99 for c in close], "close": close,
        "volume": 1e7, "amount": 1e8,
        "limit_up": [c * 1.1 for c in close], "limit_down": [c * 0.9 for c in close],
    })


def test_run_strategy_timestamp_panel_with_sell_does_not_crash() -> None:
    """端到端：面板索引为 Timestamp 且含卖出时，run_strategy 应跑通而非抛 TypeError。

    这是本缺陷的**决定性**判据：修复前，面板日期为 Timestamp 时任一卖出都会崩。
    """
    tmpl = STRATEGIES["ma_cross"](short_ma=2, long_ma=3, trailing_stop_pct=99.0)
    res = run_strategy({"600000.SH": _timestamp_indexed_frame()}, tmpl,
                       init_cash=100_000.0, commission_rate=0.0003, slippage_bps=0.0)
    sides = [t["side"] for t in res.trades]
    assert "sell" in sides, f"该行情路径应含卖出以复现缺陷；实际成交 {sides}"