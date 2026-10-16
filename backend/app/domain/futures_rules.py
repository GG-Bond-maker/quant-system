"""期货（衍生品）交易规则、合约规格与核心数学模型（纯函数，无 IO）。

口径与规则（对标中金所 CFFEX、上期所 SHFE、大商所 DCE、郑商所 CZCE、能源中心 INE）：
1. 合约要素：
   - 合约乘数（Contract Multiplier）：如 IF 300元/点、IC 200元/点、AU 1000克/手、RB 10吨/手；
   - 保证金率（Margin Rate）：初始保证金与维持保证金；
   - 最小变动价位（Tick Size）：如 IF 0.2点、AU 0.02元/克；
2. 交易制度：
   - T+0 交易与双向交易（做多 Long / 做空 Short）；
   - 每日无负债盯市制度（Mark-to-Market）；
   - 杠杆倍数 = 1 / 保证金率；
3. 基差与期限结构：
   - 基差 = 现货指数价格 - 期货价格；
   - 基差率 = (现货价格 - 期货价格) / 现货价格 * 100%；
   - 年化基差率 = 基差率 * 365 / 到期天数；
   - Contango (升水): 远月 > 近月; Backwardation (贴水): 远月 < 近月;
4. 机构级回撤分析模型：
   - 从区间高点计算的回测表（Drawdown from Peak / High-Water Mark Breakdown Table）：
     逐段统计历史前 N 大回撤周期（峰值日期、谷底日期、修复日期、回撤深度、恢复天数）。
5. 费率与单源真理：
   - 默认费率继承 trading_rules 单一事实来源，严禁散落魔法字面量。
"""
from __future__ import annotations

import math
from dataclasses import dataclass
from datetime import date, datetime
from typing import Any

from .metrics import (
    RISK_FREE_ANNUAL,
    TRADING_DAYS_PER_YEAR,
    deflated_sharpe_ratio,
    probabilistic_sharpe_ratio,
)
from .trading_rules import COMMISSION_RATE_DEFAULT

# 默认期货交易手续费率（继承单一事实源）
FUTURES_COMMISSION_RATE_DEFAULT = COMMISSION_RATE_DEFAULT


@dataclass(frozen=True)
class FuturesContractSpec:
    """期货合约规格元数据。"""
    symbol_prefix: str       # 品种代码（如 'IF', 'IC', 'AU', 'RB'）
    name: str                # 品种中文全称
    exchange: str            # 交易所代码 ('CFFEX', 'SHFE', 'DCE', 'CZCE', 'INE')
    category: str            # 品种大类 ('index', 'bond', 'precious', 'metal', 'ferrous', 'energy', 'agri')
    multiplier: float        # 合约乘数（元/点 或 元/吨）
    tick_size: float         # 最小变动价位
    margin_rate: float       # 基准保证金比例（如 0.12 表示 12%）
    fee_type: str            # 'rate' (比例) 或 'fixed' (每手固定金额)
    fee_unit: float          # 费率数值或每手固定金额
    underlying_code: str | None = None  # 对应标的代码（如 '000300.SH'）


# 核心国内主力期货品种规格表（单源真理定义）
FUTURES_SPECS: dict[str, FuturesContractSpec] = {
    # ---------- 股指期货 (CFFEX) ----------
    "IF": FuturesContractSpec(
        symbol_prefix="IF",
        name="沪深300股指期货",
        exchange="CFFEX",
        category="index",
        multiplier=300.0,
        tick_size=0.2,
        margin_rate=0.12,
        fee_type="rate",
        fee_unit=FUTURES_COMMISSION_RATE_DEFAULT,
        underlying_code="000300.SH",
    ),
    "IC": FuturesContractSpec(
        symbol_prefix="IC",
        name="中证500股指期货",
        exchange="CFFEX",
        category="index",
        multiplier=200.0,
        tick_size=0.2,
        margin_rate=0.12,
        fee_type="rate",
        fee_unit=FUTURES_COMMISSION_RATE_DEFAULT,
        underlying_code="000905.SH",
    ),
    "IM": FuturesContractSpec(
        symbol_prefix="IM",
        name="中证1000股指期货",
        exchange="CFFEX",
        category="index",
        multiplier=200.0,
        tick_size=0.2,
        margin_rate=0.15,
        fee_type="rate",
        fee_unit=FUTURES_COMMISSION_RATE_DEFAULT,
        underlying_code="000852.SH",
    ),
    "IH": FuturesContractSpec(
        symbol_prefix="IH",
        name="上证50股指期货",
        exchange="CFFEX",
        category="index",
        multiplier=300.0,
        tick_size=0.2,
        margin_rate=0.12,
        fee_type="rate",
        fee_unit=FUTURES_COMMISSION_RATE_DEFAULT,
        underlying_code="000016.SH",
    ),
    # ---------- 国债期货 (CFFEX) ----------
    "T": FuturesContractSpec(
        symbol_prefix="T",
        name="10年期国债期货",
        exchange="CFFEX",
        category="bond",
        multiplier=10000.0,
        tick_size=0.005,
        margin_rate=0.02,
        fee_type="fixed",
        fee_unit=3.0,
        underlying_code=None,
    ),
    "TF": FuturesContractSpec(
        symbol_prefix="TF",
        name="5年期国债期货",
        exchange="CFFEX",
        category="bond",
        multiplier=10000.0,
        tick_size=0.005,
        margin_rate=0.015,
        fee_type="fixed",
        fee_unit=3.0,
        underlying_code=None,
    ),
    # ---------- 贵金属 (SHFE) ----------
    "AU": FuturesContractSpec(
        symbol_prefix="AU",
        name="沪金主力",
        exchange="SHFE",
        category="precious",
        multiplier=1000.0,
        tick_size=0.02,
        margin_rate=0.10,
        fee_type="fixed",
        fee_unit=10.0,
        underlying_code=None,
    ),
    "AG": FuturesContractSpec(
        symbol_prefix="AG",
        name="沪银主力",
        exchange="SHFE",
        category="precious",
        multiplier=15.0,
        tick_size=1.0,
        margin_rate=0.12,
        fee_type="rate",
        fee_unit=0.0001,
        underlying_code=None,
    ),
    # ---------- 黑色建材 (SHFE / DCE) ----------
    "RB": FuturesContractSpec(
        symbol_prefix="RB",
        name="螺纹钢主力",
        exchange="SHFE",
        category="ferrous",
        multiplier=10.0,
        tick_size=1.0,
        margin_rate=0.10,
        fee_type="rate",
        fee_unit=0.0001,
        underlying_code=None,
    ),
    "I": FuturesContractSpec(
        symbol_prefix="I",
        name="铁矿石主力",
        exchange="DCE",
        category="ferrous",
        multiplier=100.0,
        tick_size=0.5,
        margin_rate=0.13,
        fee_type="rate",
        fee_unit=0.0001,
        underlying_code=None,
    ),
    # ---------- 有色金属 (SHFE) ----------
    "CU": FuturesContractSpec(
        symbol_prefix="CU",
        name="沪铜主力",
        exchange="SHFE",
        category="metal",
        multiplier=5.0,
        tick_size=10.0,
        margin_rate=0.10,
        fee_type="rate",
        fee_unit=0.0001,
        underlying_code=None,
    ),
    # ---------- 能源化工 (INE / CZCE) ----------
    "SC": FuturesContractSpec(
        symbol_prefix="SC",
        name="原油期货",
        exchange="INE",
        category="energy",
        multiplier=1000.0,
        tick_size=0.1,
        margin_rate=0.10,
        fee_type="fixed",
        fee_unit=20.0,
        underlying_code=None,
    ),
    "TA": FuturesContractSpec(
        symbol_prefix="TA",
        name="PTA主力",
        exchange="CZCE",
        category="energy",
        multiplier=5.0,
        tick_size=2.0,
        margin_rate=0.08,
        fee_type="fixed",
        fee_unit=3.0,
        underlying_code=None,
    ),
    "MA": FuturesContractSpec(
        symbol_prefix="MA",
        name="甲醇主力",
        exchange="CZCE",
        category="energy",
        multiplier=10.0,
        tick_size=1.0,
        margin_rate=0.09,
        fee_type="rate",
        fee_unit=0.0001,
        underlying_code=None,
    ),
}


def get_futures_spec(symbol: str) -> FuturesContractSpec | None:
    """根据合约代码（如 'IF2412'、'IF'、'AU2506'）匹配合约规格。"""
    if not symbol:
        return None
    s = symbol.strip().upper()
    # 提取开头的英文字母前缀
    prefix = ""
    for ch in s:
        if ch.isalpha():
            prefix += ch
        else:
            break
    return FUTURES_SPECS.get(prefix)


def calc_contract_value(price: float, qty: int, multiplier: float) -> float:
    """计算期货合约的名义本金市值（Notional Value）。"""
    if price <= 0 or qty <= 0 or multiplier <= 0:
        return 0.0
    return float(price * qty * multiplier)


def calc_margin(price: float, qty: int, multiplier: float, margin_rate: float) -> float:
    """计算开仓保证金占用。"""
    val = calc_contract_value(price, qty, multiplier)
    return float(val * max(0.0, margin_rate))


def calc_futures_cost(
    price: float,
    qty: int,
    multiplier: float,
    fee_type: str = "rate",
    fee_unit: float = FUTURES_COMMISSION_RATE_DEFAULT,
    is_close: bool = False,
    is_today: bool = False,
    symbol_prefix: str = "IF",
    fee_multiplier: float = 1.0,
) -> float:
    """计算单笔期货交易手续费（严格遵循中国期货交易所真实规则）。

    规则：
    1. 中金所股指期货 (IF, IC, IM, IH)：
       - 开仓：万分之 0.23 (0.000023)
       - 平今仓 (当日开平)：万分之 3.45 (0.000345，高出15倍的日内投机惩罚费率)
       - 平昨仓：万分之 0.23 (0.000023)
    2. 国债期货 (T, TF)：每手固定 3.0 元
    3. 商品期货：按品种规格配置 fixed 或 rate，支持 fee_multiplier 压力测试乘数
    """
    if qty <= 0 or price <= 0:
        return 0.0

    prefix = symbol_prefix.strip().upper() if symbol_prefix else ""
    mult = max(0.1, fee_multiplier)

    # 中金所股指期货微观结构特化
    if prefix in {"IF", "IC", "IM", "IH"}:
        if is_close and is_today:
            actual_rate = 0.000345 * mult  # 万分之3.45 平今惩罚
        else:
            actual_rate = 0.000023 * mult  # 万分之0.23 开仓/平昨
        val = calc_contract_value(price, qty, multiplier)
        return float(val * actual_rate)

    if fee_type == "fixed":
        return float(qty * fee_unit * mult)

    val = calc_contract_value(price, qty, multiplier)
    return float(val * fee_unit * mult)


def calc_slippage_price(
    price: float,
    direction: int,
    tick_size: float = 0.2,
    slippage_ticks: float = 1.0,
) -> float:
    """计算含真实市场微观摩擦滑点的实际撮合成交价。

    direction:
        +1: 做多买入 / 空头平仓买入（以更高价买入成交：price + slippage）
        -1: 做空卖出 / 多头平仓卖出（以更低价卖出成交：price - slippage）
    slippage_ticks: 滑点跳数（对标高流动性合约通常为 0.5~1.0 tick，主力换月时可达 2~3 ticks）
    """
    if slippage_ticks <= 0 or tick_size <= 0:
        return round(price, 4)

    slip = float(slippage_ticks * tick_size)
    if direction > 0:
        return round(price + slip, 4)
    elif direction < 0:
        return round(max(0.0001, price - slip), 4)
    return round(price, 4)


def calc_basis(spot_price: float, futures_price: float) -> float:
    """基差 = 现货价格 - 期货价格（正值表示贴水，负值表示升水）。"""
    return round(float(spot_price - futures_price), 4)


def calc_basis_rate(spot_price: float, futures_price: float) -> float:
    """基差率 (%) = (现货价格 - 期货价格) / 现货价格 * 100%。"""
    if spot_price <= 0:
        return 0.0
    return round(float((spot_price - futures_price) / spot_price * 100.0), 4)


def calc_annualized_basis_rate(spot_price: float, futures_price: float, days_to_expiry: int) -> float:
    """年化基差率 (%) = (现货 - 期货) / 现货 * (365 / max(到期天数, 1)) * 100%。"""
    if spot_price <= 0:
        return 0.0
    dte = max(1, days_to_expiry)
    raw_rate = (spot_price - futures_price) / spot_price
    return round(float(raw_rate * (365.0 / dte) * 100.0), 4)


def get_contract_delivery_date(symbol_prefix: str, year: int, month: int) -> date:
    """计算期货合约的标准交割日（按中国期货交易所真实制度规则）。

    规则：
    - CFFEX 股指期货 (IF, IC, IM, IH): 交割月第三个周五；
    - CFFEX 国债期货 (T, TF): 交割月第二个周五；
    - SHFE/CZCE/DCE 商品期货: 交割月中旬（15日，若遇周末顺延）；
    - INE 原油期货 (SC): 交割月前一月的最后一个交易日。
    """
    prefix = (symbol_prefix or "").strip().upper()

    if prefix in {"IF", "IC", "IM", "IH"}:
        first_day = date(year, month, 1)
        first_friday_day = 1 + (4 - first_day.weekday()) % 7
        third_friday_day = first_friday_day + 14
        return date(year, month, third_friday_day)

    if prefix in {"T", "TF"}:
        first_day = date(year, month, 1)
        first_friday_day = 1 + (4 - first_day.weekday()) % 7
        second_friday_day = first_friday_day + 7
        return date(year, month, second_friday_day)

    if prefix == "SC":
        prev_month = month - 1 if month > 1 else 12
        prev_year = year if month > 1 else year - 1
        if prev_month in {1, 3, 5, 7, 8, 10, 12}:
            last_day = 31
        elif prev_month in {4, 6, 9, 11}:
            last_day = 30
        else:
            last_day = 29 if (prev_year % 4 == 0 and prev_year % 100 != 0) or (prev_year % 400 == 0) else 28
        d = date(prev_year, prev_month, last_day)
        if d.weekday() == 5:
            d = date(prev_year, prev_month, last_day - 1)
        elif d.weekday() == 6:
            d = date(prev_year, prev_month, last_day - 2)
        return d

    # 默认商品期货：交割月 15 日（遇周末顺延）
    d = date(year, month, 15)
    if d.weekday() == 5:
        d = date(year, month, 17)
    elif d.weekday() == 6:
        d = date(year, month, 16)
    return d


def calc_days_to_expiry(delivery_date: date, as_of: date | None = None) -> int:
    """计算距离交割日的自然日天数（用于精准年化基差与展期收益率核算）。"""
    ref_date = as_of or datetime.now().date()
    delta = (delivery_date - ref_date).days
    return max(1, delta)


def calc_roll_yield(near_price: float, far_price: float, near_dte: int, far_dte: int) -> float:
    """计算年化展期收益率 (Annualized Roll Yield %)。

    公式：RollYield = (P_near - P_far) / P_near * (365 / max(1, far_dte - near_dte)) * 100%
    - 正值表示 Backwardation (贴水结构)，多头展期吃贴水有利；
    - 负值表示 Contango (升水结构)，空头展期有利。
    """
    if near_price <= 0:
        return 0.0
    gap_dte = max(1, far_dte - near_dte)
    raw_spread_ratio = (near_price - far_price) / near_price
    return round(float(raw_spread_ratio * (365.0 / gap_dte) * 100.0), 4)



def calc_hedge_contracts(portfolio_value: float, futures_price: float, multiplier: float, beta: float = 1.0) -> int:
    """根据股票组合市值与 Beta 测算对冲所需的期货合约张数（手数）。"""
    contract_val = calc_contract_value(futures_price, 1, multiplier)
    if contract_val <= 0:
        return 0
    target_hedge_value = portfolio_value * beta
    return max(0, int(round(target_hedge_value / contract_val)))


def calc_underwater_drawdowns(
    equity_curve: list[float],
    dates: list[str],
    top_n: int = 5,
) -> tuple[list[dict[str, Any]], float, float]:
    """从区间最高点计算的回测表（Drawdown from Peak / High-Water Mark Breakdown）。

    机构量化研报标配指标：
    精确还原历史上的各次回撤周期（高点日期、谷底日期、修复日期、回撤幅度、修复历时）。

    :return: (top_drawdown_periods, current_drawdown_pct, max_drawdown_pct)
    """
    if not equity_curve or len(equity_curve) != len(dates):
        return [], 0.0, 0.0

    n = len(equity_curve)
    high_water_mark = equity_curve[0]
    peak_idx = 0
    in_drawdown = False
    trough_idx = 0
    trough_val = equity_curve[0]

    drawdown_events: list[dict[str, Any]] = []

    for i in range(n):
        val = equity_curve[i]
        if val >= high_water_mark:
            # 创出新高，若此前处于回撤状态，则回撤结束并修复
            if in_drawdown:
                dd_pct = (trough_val - high_water_mark) / high_water_mark * 100.0
                drawdown_events.append({
                    "peak_date": dates[peak_idx],
                    "peak_value": round(high_water_mark, 4),
                    "trough_date": dates[trough_idx],
                    "trough_value": round(trough_val, 4),
                    "recovery_date": dates[i],
                    "max_drawdown_pct": round(dd_pct, 2),
                    "drawdown_days": max(1, trough_idx - peak_idx),
                    "recovery_days": max(1, i - trough_idx),
                    "total_duration_days": max(1, i - peak_idx),
                    "is_recovered": True,
                })
                in_drawdown = False
            high_water_mark = val
            peak_idx = i
            trough_idx = i
            trough_val = val
        else:
            # 处于回撤中
            in_drawdown = True
            if val < trough_val:
                trough_val = val
                trough_idx = i

    # 如果回测结束时仍在回撤中（未恢复）
    if in_drawdown and high_water_mark > 0:
        dd_pct = (trough_val - high_water_mark) / high_water_mark * 100.0
        drawdown_events.append({
            "peak_date": dates[peak_idx],
            "peak_value": round(high_water_mark, 4),
            "trough_date": dates[trough_idx],
            "trough_value": round(trough_val, 4),
            "recovery_date": None,
            "max_drawdown_pct": round(dd_pct, 2),
            "drawdown_days": max(1, trough_idx - peak_idx),
            "recovery_days": max(0, (n - 1) - trough_idx),
            "total_duration_days": max(1, (n - 1) - peak_idx),
            "is_recovered": False,
        })

    # 当前回撤
    latest_val = equity_curve[-1]
    curr_dd_pct = 0.0
    if high_water_mark > 0:
        curr_dd_pct = round((latest_val - high_water_mark) / high_water_mark * 100.0, 2)

    # 最大回撤
    max_dd = 0.0
    if drawdown_events:
        max_dd = min(item["max_drawdown_pct"] for item in drawdown_events)

    # 按回撤幅度排序取前 top_n
    sorted_events = sorted(drawdown_events, key=lambda x: x["max_drawdown_pct"])[:top_n]

    return sorted_events, curr_dd_pct, max_dd


def calc_continuous_contract(
    bars: list[dict[str, Any]],
    roll_dates: list[tuple[str, float, float]] | None = None,
    method: str = "ratio",
) -> list[dict[str, Any]]:
    """期货主力连续合约展期平滑算法（消除换月人为跳空）。

    机构量化标准（Bloomberg / CME / 聚宽）：
    主力合约换月时，由于近月与远月存在基差/期限升贴水，直接拼接会产生虚假的阶跃跳空。
    本算法通过比例后复权（Ratio Adjustment）或价差调整（Panama Canal）进行平滑：
    - ratio 模式：乘积因子保证百分比收益率完全无损；
    - difference 模式：累加价差保证绝对点位变动一致。

    :param bars: 原始主力日线序列，按日期升序排序。
    :param roll_dates: 换月事件列表 [(roll_date, old_price, new_price), ...]
    :param method: 'ratio' (后复权比例) 或 'difference' (巴拿马运河价差)
    :return: 平滑后的连续合约日线序列。
    """
    if not bars:
        return []

    # 若未显式传入换月事件，自动检测隔日跳空异常（涨跌幅超阈值且持仓量突变）
    detected_rolls: list[tuple[str, float, float]] = []
    if roll_dates is None:
        for idx in range(1, len(bars)):
            prev_b = bars[idx - 1]
            curr_b = bars[idx]
            prev_close = float(prev_b.get("close", 0.0))
            curr_open = float(curr_b.get("open", 0.0))
            # 当隔日开盘与昨收偏差极大（> 1.8%）且为典型换月窗口时识别为展期
            if prev_close > 0 and abs(curr_open - prev_close) / prev_close > 0.018:
                detected_rolls.append((str(curr_b.get("date", "")), prev_close, curr_open))
    else:
        detected_rolls = list(roll_dates)

    if not detected_rolls:
        # 无换月跳空，直接返回带有连续合约标记的副本
        return [{**b, "is_continuous": True, "adj_close": b.get("close", 0.0)} for b in bars]

    out_bars: list[dict[str, Any]] = []
    # 构造调整乘数/加数映射
    # 后复权：从最后一次换月往前逐步累积调整
    date_to_cum_factor: dict[str, float] = {}
    cum_factor = 1.0
    cum_diff = 0.0

    # 按换月日期倒序处理
    sorted_rolls = sorted(detected_rolls, key=lambda x: x[0], reverse=True)
    roll_map = {r[0]: (r[1], r[2]) for r in sorted_rolls}

    for b in reversed(bars):
        d = str(b.get("date", ""))
        if d in roll_map:
            p_old, p_new = roll_map[d]
            if p_old > 0:
                step_ratio = p_new / p_old
                cum_factor *= step_ratio
                cum_diff += (p_new - p_old)
        date_to_cum_factor[d] = cum_factor

    for b in bars:
        d = str(b.get("date", ""))
        factor = date_to_cum_factor.get(d, 1.0)
        orig_open = float(b.get("open", 0.0))
        orig_high = float(b.get("high", 0.0))
        orig_low = float(b.get("low", 0.0))
        orig_close = float(b.get("close", 0.0))

        if method == "difference":
            adj_open = round(orig_open + cum_diff, 4)
            adj_high = round(orig_high + cum_diff, 4)
            adj_low = round(orig_low + cum_diff, 4)
            adj_close = round(orig_close + cum_diff, 4)
        else:
            # 默认比例复权
            adj_open = round(orig_open * factor, 4)
            adj_high = round(orig_high * factor, 4)
            adj_low = round(orig_low * factor, 4)
            adj_close = round(orig_close * factor, 4)

        out_bars.append({
            **b,
            "is_continuous": True,
            "orig_close": orig_close,
            "open": adj_open,
            "high": adj_high,
            "low": adj_low,
            "close": adj_close,
            "adj_factor": round(factor, 6),
        })

    return out_bars


def calc_calendar_spread(
    near_bars: list[dict[str, Any]],
    far_bars: list[dict[str, Any]],
    window: int = 20,
) -> dict[str, Any]:
    """计算跨期套利价差序列与统计套利指标 (Calendar Spread Analysis)。

    计算近月合约与远月合约的价差 (Spread = Near - Far) 及滚动 Z-Score：
    - Spread > +2 Std: 远月严重贴水/近月超买，可做空近月、做多远月；
    - Spread < -2 Std: 远月严重升水/近月超卖，可做多近月、做空远月。
    """
    far_map = {str(b.get("date", "")): float(b.get("close", 0.0)) for b in far_bars}
    spread_series: list[dict[str, Any]] = []

    spread_values: list[float] = []
    for b in near_bars:
        d = str(b.get("date", ""))
        if d in far_map:
            p_near = float(b.get("close", 0.0))
            p_far = far_map[d]
            spread = round(p_near - p_far, 4)
            spread_pct = round(spread / p_far * 100.0, 4) if p_far > 0 else 0.0
            spread_values.append(spread)
            spread_series.append({
                "date": d,
                "near_price": p_near,
                "far_price": p_far,
                "spread": spread,
                "spread_pct": spread_pct,
            })

    if not spread_values:
        return {
            "count": 0,
            "series": [],
            "mean_spread": 0.0,
            "std_spread": 0.0,
            "current_spread": 0.0,
            "current_z_score": 0.0,
            "arbitrage_signal": "neutral",
        }

    # 计算统计特征与 Z-Score
    mean_val = float(sum(spread_values) / len(spread_values))
    variance = float(sum((x - mean_val) ** 2 for x in spread_values) / max(1, len(spread_values) - 1))
    std_val = math.sqrt(variance) if variance > 0 else 1.0

    curr_spread = spread_values[-1]
    z_score = round((curr_spread - mean_val) / std_val, 2) if std_val > 0 else 0.0

    signal = "neutral"
    if z_score > 1.8:
        signal = "short_near_long_far"  # 空近多远
    elif z_score < -1.8:
        signal = "long_near_short_far"  # 多近空远

    # 注入滚动 Z-Score
    for i, item in enumerate(spread_series):
        sub_vals = spread_values[max(0, i - window + 1): i + 1]
        if len(sub_vals) >= 3:
            sub_m = sum(sub_vals) / len(sub_vals)
            sub_s = math.sqrt(sum((x - sub_m) ** 2 for x in sub_vals) / (len(sub_vals) - 1))
            item["z_score"] = round((item["spread"] - sub_m) / sub_s, 2) if sub_s > 0 else 0.0
        else:
            item["z_score"] = 0.0

    return {
        "count": len(spread_series),
        "series": spread_series,
        "mean_spread": round(mean_val, 4),
        "std_spread": round(std_val, 4),
        "current_spread": round(curr_spread, 4),
        "current_z_score": z_score,
        "arbitrage_signal": signal,
    }


def calc_mean_reversion_half_life(series: list[float]) -> dict[str, Any]:
    """计算价差序列的均值回归半衰期（Half-Life）与平稳性检验统计量。

    通过 Ornstein-Uhlenbeck 离散 OLS 回归:
    Δy_t = α + β * y_{t-1} + ε_t
    Half-life = -ln(2) / β (当 β < 0 时)
    """
    if not series or len(series) < 10:
        return {
            "half_life_days": None,
            "is_stationary": False,
            "lambda_param": 0.0,
            "reversion_speed": "insufficient_data",
        }

    y = series
    n = len(y)
    dy = [y[i] - y[i - 1] for i in range(1, n)]
    y_lag = y[:-1]

    mean_lag = sum(y_lag) / len(y_lag)
    mean_dy = sum(dy) / len(dy)

    num = sum((y_lag[i] - mean_lag) * (dy[i] - mean_dy) for i in range(len(dy)))
    den = sum((y_lag[i] - mean_lag) ** 2 for i in range(len(dy)))

    if den <= 1e-9:
        return {
            "half_life_days": None,
            "is_stationary": False,
            "lambda_param": 0.0,
            "reversion_speed": "static",
        }

    beta = num / den

    if beta >= 0:
        return {
            "half_life_days": None,
            "is_stationary": False,
            "lambda_param": round(beta, 4),
            "reversion_speed": "divergent",
        }

    half_life = round(-math.log(2) / beta, 1)
    is_stationary = 1.0 <= half_life <= 60.0

    return {
        "half_life_days": half_life,
        "is_stationary": is_stationary,
        "lambda_param": round(beta, 4),
        "reversion_speed": "fast" if half_life < 10 else "moderate" if half_life <= 30 else "slow",
    }


def calc_basis_percentile(current_basis: float, historical_bases: list[float]) -> dict[str, Any]:
    """计算当前基差在历史分布中的分位数 (Percentile) 与极值状态。"""
    if not historical_bases:
        return {
            "percentile": 50.0,
            "regime": "neutral",
            "historical_min": current_basis,
            "historical_max": current_basis,
            "historical_mean": current_basis,
        }

    sorted_bases = sorted(historical_bases)
    n = len(sorted_bases)
    less_or_equal = sum(1 for b in sorted_bases if b <= current_basis)
    percentile = round((less_or_equal / n) * 100.0, 1)

    min_b = round(sorted_bases[0], 2)
    max_b = round(sorted_bases[-1], 2)
    mean_b = round(sum(sorted_bases) / n, 2)

    if percentile >= 85.0:
        regime = "extreme_backwardation"  # 极度贴水
    elif percentile >= 60.0:
        regime = "moderate_backwardation" # 温和贴水
    elif percentile <= 15.0:
        regime = "extreme_contango"      # 极度升水
    elif percentile <= 40.0:
        regime = "moderate_contango"     # 温和升水
    else:
        regime = "neutral"               # 中性区间

    return {
        "percentile": percentile,
        "regime": regime,
        "historical_min": min_b,
        "historical_max": max_b,
        "historical_mean": mean_b,
    }


def calc_cot_summary(records: list[dict[str, Any]]) -> dict[str, Any]:
    """计算主力席位持仓龙虎榜（COT）汇总指标与多空力量比。"""
    if not records:
        return {
            "total_long": 0,
            "total_short": 0,
            "long_change_total": 0,
            "short_change_total": 0,
            "net_position_total": 0,
            "long_short_ratio": 1.0,
            "top_bull_member": "--",
            "top_bear_member": "--",
        }

    total_long = sum(r.get("long_volume", 0) for r in records)
    total_short = sum(r.get("short_volume", 0) for r in records)
    long_chg = sum(r.get("long_change", 0) for r in records)
    short_chg = sum(r.get("short_change", 0) for r in records)
    net_total = total_long - total_short
    ratio = round(total_long / total_short, 3) if total_short > 0 else 1.0

    sorted_by_net = sorted(records, key=lambda x: x.get("net_position", 0), reverse=True)
    top_bull = sorted_by_net[0].get("member_name", "--") if sorted_by_net else "--"
    top_bear = sorted_by_net[-1].get("member_name", "--") if sorted_by_net else "--"

    return {
        "total_long": total_long,
        "total_short": total_short,
        "long_change_total": long_chg,
        "short_change_total": short_chg,
        "net_position_total": net_total,
        "long_short_ratio": ratio,
        "top_bull_member": top_bull,
        "top_bear_member": top_bear,
    }


def calc_sharpe_and_sortino(
    daily_returns: list[float],
    rf_annual: float = RISK_FREE_ANNUAL,
    trading_days: int = TRADING_DAYS_PER_YEAR,
) -> tuple[float, float]:
    """计算扣除无风险利率的年化夏普比率 (Sharpe Ratio) 与索提诺比率 (Sortino Ratio)。

    对标行业严格量化标准：
    - 无风险利率日化: rf_daily = rf_annual / trading_days；
    - 每日超额收益: excess_ret = r_t - rf_daily；
    - 年化夏普: (mean(excess_ret) * sqrt(252)) / std(daily_ret)；
    - 年化索提诺: (mean(excess_ret) * sqrt(252)) / downside_dev (仅惩罚超额收益低于0的下行半方差)。
    """
    if not daily_returns or len(daily_returns) < 2:
        return 0.0, 0.0

    n = len(daily_returns)
    rf_daily = rf_annual / float(trading_days)
    excess_returns = [r - rf_daily for r in daily_returns]

    mean_excess = sum(excess_returns) / n
    mean_ret = sum(daily_returns) / n

    variance = sum((r - mean_ret) ** 2 for r in daily_returns) / max(1, n - 1)
    std_dev = math.sqrt(variance) if variance > 0 else 1e-6

    sharpe = round((mean_excess * math.sqrt(float(trading_days))) / std_dev if std_dev > 0 else 0.0, 2)

    downside_sq = [min(0.0, er) ** 2 for er in excess_returns]
    downside_var = sum(downside_sq) / max(1, n - 1)
    downside_dev = math.sqrt(downside_var) if downside_var > 0 else 1e-6

    sortino = round((mean_excess * math.sqrt(float(trading_days))) / downside_dev if downside_dev > 0 else 0.0, 2)
    return sharpe, sortino


def calc_comprehensive_futures_metrics(
    equity_curve: list[float],
    benchmark_curve: list[float],
    trades: list[dict[str, Any]],
    margin_ratio_curve: list[float],
    rf_annual: float = RISK_FREE_ANNUAL,
    trading_days: int = TRADING_DAYS_PER_YEAR,
) -> dict[str, Any]:
    """工业级期货多空策略全维度绩效度量、基准 Alpha/Beta 归因与统计显著性检验。

    包含对标 Barra / PyFolio / JoinQuant 的完整指标体系：
    1. 基础收益比率: CAGR年化、年化波动率、夏普比率、索提诺比率、卡尔玛比率；
    2. 基准相对与阿尔法: 基准收益率、超额收益率、Alpha(年化)、Beta、跟踪误差(TE)、信息比率(IR)；
    3. 极端尾部风险: VaR 95%、CVaR 95%(Expected Shortfall)、历史最大保证金占用、追保与强平风险触发次数；
    4. 统计显著性与抗过拟合: 盈亏比、胜率、t统计量、p-value、概率夏普比率(PSR)、通胀夏普比率(DSR)。
    """
    if not equity_curve or len(equity_curve) < 2:
        return {}

    n = len(equity_curve)
    init_eq = equity_curve[0]
    final_eq = equity_curve[-1]

    # 1. 累计与 CAGR 年化收益率
    total_ret = (final_eq - init_eq) / init_eq if init_eq > 0 else 0.0
    total_return_pct = round(total_ret * 100.0, 2)
    years = max(1, n - 1) / float(trading_days)
    ann_ret = ((final_eq / init_eq) ** (1.0 / years) - 1.0) if (init_eq > 0 and final_eq > 0) else 0.0
    ann_return_pct = round(ann_ret * 100.0, 2)

    # 每日收益率序列
    daily_returns: list[float] = []
    for i in range(1, n):
        prev = equity_curve[i - 1]
        daily_returns.append((equity_curve[i] - prev) / prev if prev > 0 else 0.0)

    # 年化波动率
    ret_mean = sum(daily_returns) / len(daily_returns) if daily_returns else 0.0
    ret_var = sum((r - ret_mean) ** 2 for r in daily_returns) / max(1, len(daily_returns) - 1)
    ret_std = math.sqrt(ret_var) if ret_var > 0 else 1e-6
    annual_volatility_pct = round(ret_std * math.sqrt(float(trading_days)) * 100.0, 2)

    # 夏普与索提诺
    sharpe, sortino = calc_sharpe_and_sortino(daily_returns, rf_annual=rf_annual, trading_days=trading_days)

    # 最大回撤
    high_water = equity_curve[0]
    max_dd = 0.0
    for eq in equity_curve:
        if eq > high_water:
            high_water = eq
        dd = (eq - high_water) / high_water * 100.0 if high_water > 0 else 0.0
        if dd < max_dd:
            max_dd = dd
    max_drawdown_pct = round(max_dd, 2)
    abs_dd = abs(max_dd) if abs(max_dd) > 0.01 else 0.01
    calmar_ratio = round(ann_return_pct / abs_dd, 2)

    # 2. 基准相对与 Alpha/Beta 归因 (针对买入持有基准)
    has_benchmark = len(benchmark_curve) == n and benchmark_curve[0] > 0
    if has_benchmark:
        b_init = benchmark_curve[0]
        b_final = benchmark_curve[-1]
        b_total_ret = (b_final - b_init) / b_init
        benchmark_return_pct = round(b_total_ret * 100.0, 2)
        b_ann_ret = ((b_final / b_init) ** (1.0 / years) - 1.0) if b_final > 0 else 0.0
        benchmark_ann_return_pct = round(b_ann_ret * 100.0, 2)
        excess_return_pct = round((total_ret - b_total_ret) * 100.0, 2)

        b_daily_rets: list[float] = []
        for i in range(1, n):
            prev_b = benchmark_curve[i - 1]
            b_daily_rets.append((benchmark_curve[i] - prev_b) / prev_b if prev_b > 0 else 0.0)

        b_mean = sum(b_daily_rets) / len(b_daily_rets) if b_daily_rets else 0.0
        b_var = sum((b - b_mean) ** 2 for b in b_daily_rets) / max(1, len(b_daily_rets) - 1)

        cov_sb = sum((daily_returns[i] - ret_mean) * (b_daily_rets[i] - b_mean) for i in range(len(daily_returns))) / max(1, len(daily_returns) - 1)
        beta = round(cov_sb / b_var, 3) if b_var > 1e-12 else 1.0

        alpha_ann = (ret_mean - beta * b_mean) * float(trading_days)
        alpha_annual_pct = round(alpha_ann * 100.0, 2)

        # 超额日收益与跟踪误差
        excess_daily = [daily_returns[i] - b_daily_rets[i] for i in range(len(daily_returns))]
        ex_mean = sum(excess_daily) / len(excess_daily) if excess_daily else 0.0
        ex_var = sum((e - ex_mean) ** 2 for e in excess_daily) / max(1, len(excess_daily) - 1)
        te = math.sqrt(ex_var) * math.sqrt(float(trading_days)) if ex_var > 0 else 1e-6
        tracking_error_pct = round(te * 100.0, 2)
        information_ratio = round((ex_mean * math.sqrt(float(trading_days))) / math.sqrt(ex_var), 2) if ex_var > 1e-12 else 0.0
    else:
        benchmark_return_pct = 0.0
        benchmark_ann_return_pct = 0.0
        excess_return_pct = total_return_pct
        beta = 1.0
        alpha_annual_pct = ann_return_pct
        tracking_error_pct = 0.0
        information_ratio = 0.0

    # 3. 极端尾部风险 (VaR 95% & CVaR 95%)
    sorted_returns = sorted(daily_returns)
    idx_5pct = max(0, int(len(sorted_returns) * 0.05))
    var_95_val = abs(sorted_returns[idx_5pct]) if sorted_returns[idx_5pct] < 0 else 0.0
    var_95_pct = round(var_95_val * 100.0, 2)

    tail_losses = [abs(r) for r in sorted_returns[: idx_5pct + 1] if r < 0]
    cvar_95_val = (sum(tail_losses) / len(tail_losses)) if tail_losses else var_95_val
    cvar_95_pct = round(cvar_95_val * 100.0, 2)

    # 保证金与风控事件统计
    peak_margin_ratio = round(max(margin_ratio_curve), 2) if margin_ratio_curve else 0.0
    margin_call_count = sum(1 for m in margin_ratio_curve if m >= 70.0)
    liquidation_count = sum(1 for m in margin_ratio_curve if m >= 90.0)

    # 4. 交易明细统计与显著性检验
    total_trades = len(trades)
    pnls = [float(t.get("pnl", 0.0)) for t in trades]
    pnl_pcts = [float(t.get("pnl_pct", 0.0)) for t in trades]
    wins = [p for p in pnls if p > 0]
    losses = [p for p in pnls if p < 0]

    win_count = len(wins)
    loss_count = len(losses)
    win_rate_pct = round(win_count / total_trades * 100.0, 1) if total_trades > 0 else 0.0

    gross_profit = sum(wins)
    gross_loss = abs(sum(losses))
    profit_factor = round(gross_profit / gross_loss, 2) if gross_loss > 0 else (99.9 if gross_profit > 0 else 0.0)

    avg_win = sum(wins) / win_count if win_count > 0 else 0.0
    avg_loss = sum(losses) / loss_count if loss_count > 0 else 0.0
    payoff_ratio = round(avg_win / abs(avg_loss), 2) if abs(avg_loss) > 0 else (99.9 if avg_win > 0 else 0.0)

    max_win = max(wins) if wins else 0.0
    max_loss = min(losses) if losses else 0.0
    max_win_contrib_pct = round((max_win / gross_profit * 100.0) if gross_profit > 0 else 0.0, 1)

    # 单笔交易收益率的 t-statistic 与显著性检验
    if total_trades >= 3 and pnl_pcts:
        mean_p = sum(pnl_pcts) / total_trades
        var_p = sum((p - mean_p) ** 2 for p in pnl_pcts) / max(1, total_trades - 1)
        se_p = math.sqrt(var_p) / math.sqrt(total_trades) if var_p > 0 else 1e-6
        t_stat = round(mean_p / se_p, 2)
        # 自由度 df = N - 1，通过标准正态近似计算双侧 p-value (或精确近似)
        z = abs(t_stat)
        p_val = round(math.erfc(z / math.sqrt(2.0)), 4)
    else:
        t_stat = 0.0
        p_val = 1.0

    # 防过拟合指标（复用 metrics.py 经典 de Prado 公式）
    try:
        psr = round(float(probabilistic_sharpe_ratio(equity_curve, sr_benchmark=0.0, rf=rf_annual)), 4)
    except Exception:
        psr = 0.5
    try:
        dsr = round(float(deflated_sharpe_ratio(equity_curve, n_trials=12, rf=rf_annual)), 4)
    except Exception:
        dsr = 0.5

    is_sample_sufficient = total_trades >= 20

    return {
        # 核心指标
        "total_return_pct": total_return_pct,
        "ann_return_pct": ann_return_pct,
        "annual_volatility_pct": annual_volatility_pct,
        "sharpe_ratio": sharpe,
        "sortino_ratio": sortino,
        "calmar_ratio": calmar_ratio,
        "max_drawdown_pct": max_drawdown_pct,
        # 基准相对指标
        "benchmark_return_pct": benchmark_return_pct,
        "benchmark_ann_return_pct": benchmark_ann_return_pct,
        "excess_return_pct": excess_return_pct,
        "beta": beta,
        "alpha_annual_pct": alpha_annual_pct,
        "tracking_error_pct": tracking_error_pct,
        "information_ratio": information_ratio,
        # 风险与流动性
        "var_95_pct": var_95_pct,
        "cvar_95_pct": cvar_95_pct,
        "peak_margin_ratio": peak_margin_ratio,
        "margin_call_count": margin_call_count,
        "liquidation_count": liquidation_count,
        # 统计学显著度
        "total_trades": total_trades,
        "win_count": win_count,
        "loss_count": loss_count,
        "win_rate_pct": win_rate_pct,
        "profit_factor": profit_factor,
        "avg_win": round(avg_win, 2),
        "avg_loss": round(avg_loss, 2),
        "payoff_ratio": payoff_ratio,
        "max_win": round(max_win, 2),
        "max_loss": round(max_loss, 2),
        "max_win_contrib_pct": max_win_contrib_pct,
        "t_statistic": t_stat,
        "p_value": p_val,
        "psr": psr,
        "dsr": dsr,
        "is_sample_sufficient": is_sample_sufficient,
    }


def calc_cost_sensitivity_matrix(
    closes: list[float],
    fast_period: int = 5,
    slow_period: int = 20,
    tick_size: float = 0.2,
    slippages: list[float] | None = None,
    fee_multipliers: list[float] | None = None,
    symbol_prefix: str = "IF",
) -> dict[str, Any]:
    """计算滑点跳数与手续费倍数的二维成本压力测试矩阵 (Cost Stress Testing Matrix)。

    对标专业对冲基金的稳健性审查：
    评估当市场流动性恶化（滑点倍增）或交易所费率上调时，策略的夏普比率断崖点（Break-even Point）。
    """
    if not closes or len(closes) < max(slow_period + 10, 30):
        return {"grid": [], "breakeven_slippage": None}

    slip_list = slippages or [0.0, 1.0, 2.0, 5.0]
    fee_mult_list = fee_multipliers or [0.5, 1.0, 2.0, 5.0]

    n = len(closes)
    # 预先生成基础均线信号
    signals = [0] * n
    for i in range(slow_period, n):
        f_ma = sum(closes[i - fast_period + 1 : i + 1]) / fast_period
        s_ma = sum(closes[i - slow_period + 1 : i + 1]) / slow_period
        signals[i] = 1 if f_ma > s_ma else (-1 if f_ma < s_ma else signals[i - 1])

    matrix: list[dict[str, Any]] = []

    for s_tick in slip_list:
        for f_mult in fee_mult_list:
            equity = 1.0
            peak = 1.0
            max_dd = 0.0
            daily_rets: list[float] = []
            pos = 0

            # 费率设定（股指万 0.23 基准）
            base_fee_rate = 0.000023 * f_mult

            for i in range(slow_period, n):
                curr_p = closes[i]
                prev_p = closes[i - 1]
                sig = signals[i]

                # 换仓摩擦
                friction_pct = 0.0
                if sig != pos:
                    slip_cost_pct = (s_tick * tick_size) / curr_p if curr_p > 0 else 0.0
                    friction_pct = slip_cost_pct + base_fee_rate * 2.0

                p_ret = (curr_p - prev_p) / prev_p if prev_p > 0 else 0.0
                day_ret = pos * p_ret - friction_pct
                equity *= (1.0 + day_ret)

                if equity > peak:
                    peak = equity
                dd = (equity - peak) / peak * 100.0 if peak > 0 else 0.0
                if dd < max_dd:
                    max_dd = dd

                daily_rets.append(day_ret)
                pos = sig

            tot_ret = round((equity - 1.0) * 100.0, 2)
            sh, _ = calc_sharpe_and_sortino(daily_rets)

            matrix.append({
                "slippage_ticks": s_tick,
                "fee_multiplier": f_mult,
                "sharpe": sh,
                "total_return_pct": tot_ret,
                "max_drawdown_pct": round(max_dd, 2),
            })

    # 寻找 1.0x 费率下，夏普比率跌破 0 的滑点临界点
    breakeven = None
    for item in matrix:
        if item["fee_multiplier"] == 1.0 and item["sharpe"] < 0:
            breakeven = item["slippage_ticks"]
            break

    return {
        "grid": matrix,
        "breakeven_slippage_ticks": breakeven,
        "tested_slippages": slip_list,
        "tested_fee_multipliers": fee_mult_list,
    }


def calc_parameter_grid_dual_ma(
    closes: list[float],
    fast_list: list[int] | None = None,
    slow_list: list[int] | None = None,
    tick_size: float = 0.2,
    slippage_ticks: float = 1.0,
    fee_rate: float = 0.00005,
) -> list[dict[str, Any]]:
    """纯函数：网格计算快慢均线组合的收益与夏普比率（真实扣除微观滑点与双边手续费）。"""
    if not closes or len(closes) < 30:
        return []

    fasts = fast_list or [3, 5, 8, 10, 12, 15]
    slows = slow_list or [15, 20, 25, 30, 40, 50, 60]

    n = len(closes)
    grid_results: list[dict[str, Any]] = []

    for f in fasts:
        for s in slows:
            if f >= s:
                continue

            pos = 0
            equity = 1.0
            peak = 1.0
            max_dd = 0.0
            daily_rets: list[float] = []

            for i in range(s, n):
                fast_ma = sum(closes[i - f + 1 : i + 1]) / f
                slow_ma = sum(closes[i - s + 1 : i + 1]) / s
                sig = 1 if fast_ma > slow_ma else -1

                prev_p = closes[i - 1]
                curr_p = closes[i]

                # 换仓摩擦损耗（真实扣除）
                friction = 0.0
                if sig != pos:
                    slip_pct = (slippage_ticks * tick_size) / curr_p if curr_p > 0 else 0.0
                    friction = slip_pct + fee_rate

                p_ret = (curr_p - prev_p) / prev_p if prev_p > 0 else 0.0
                day_ret = pos * p_ret - friction
                equity *= (1.0 + day_ret)

                if equity > peak:
                    peak = equity
                dd = (equity - peak) / peak * 100.0 if peak > 0 else 0.0
                if dd < max_dd:
                    max_dd = dd

                daily_rets.append(day_ret)
                pos = sig

            tot_ret = round((equity - 1.0) * 100.0, 2)
            sh, _ = calc_sharpe_and_sortino(daily_rets)

            grid_results.append({
                "fast": f,
                "slow": s,
                "sharpe": sh,
                "total_return_pct": tot_ret,
                "max_drawdown_pct": round(max_dd, 2),
            })

    return grid_results


def calc_cost_of_carry(
    spot_price: float,
    dte: int,
    category: str = "index",
    interest_rate: float = 0.023,
    dividend_yield: float | None = None,
    storage_cost: float = 0.0,
) -> dict[str, Any]:
    """持有成本模型 (Cost of Carry Model) 与无套利定价。

    理论公式：
    F* = S * (1 + (r - q + u) * (DTE / 365))
    - 股指期货：考虑成分股分红率 q (IF/IH 较高, IC/IM 较低)
    - 商品期货：考虑便利收益率与仓储损耗成本 u
    无套利区间：[F* - friction, F* + friction]
    """
    if spot_price <= 0:
        return {}

    dte_eff = max(1, dte)
    if dividend_yield is None:
        if category == "index":
            dividend_yield = 0.021
        else:
            dividend_yield = 0.0

    net_carry_rate = interest_rate - dividend_yield + storage_cost
    fair_price = round(spot_price * (1.0 + net_carry_rate * (dte_eff / 365.0)), 2)
    fair_basis = round(spot_price - fair_price, 2)

    # 摩擦成本 (税费 + 冲击滑点)，股指约 0.20%，商品约 0.35%
    friction_pct = 0.0020 if category == "index" else 0.0035
    friction_val = round(spot_price * friction_pct, 2)

    upper_band = round(fair_price + friction_val, 2)
    lower_band = round(fair_price - friction_val, 2)

    return {
        "spot_price": spot_price,
        "dte": dte_eff,
        "interest_rate_pct": round(interest_rate * 100.0, 2),
        "dividend_yield_pct": round(dividend_yield * 100.0, 2),
        "net_carry_rate_pct": round(net_carry_rate * 100.0, 2),
        "fair_price": fair_price,
        "fair_basis": fair_basis,
        "friction_val": friction_val,
        "upper_band": upper_band,
        "lower_band": lower_band,
    }


def calc_rollover_diagnostics(
    contracts: list[dict[str, Any]],
    spec: FuturesContractSpec,
) -> dict[str, Any]:
    """计算移仓换月进度指标、持仓迁移分布与沉淀资金规模。

    - 移仓进度 = 次月OI / (当月OI + 次月OI) * 100%
    - 阶段划分：常规持仓 / 移仓启动 / 换月高峰 / 新主力确立
    - 沉淀资金规模 = sum(price * oi * multiplier * margin_rate) / 1e8
    """
    if not contracts or len(contracts) < 2:
        return {}

    c_near = contracts[0]
    c_next = contracts[1]
    near_oi = int(c_near.get("open_interest", 100000))
    next_oi = int(c_next.get("open_interest", 35000))
    total_oi = sum(int(c.get("open_interest", 0)) for c in contracts)

    dte = int(c_near.get("days_to_expiry", 15))
    two_oi = max(1, near_oi + next_oi)
    progress_pct = round((next_oi / two_oi) * 100.0, 1)

    if progress_pct >= 70.0 or dte <= 2:
        stage = "主力切换期"
        stage_desc = f"次月合约 ({c_next.get('contract_code')}) 已确立为新主力，建议完成剩余换月操作"
        status_tag = "danger"
    elif progress_pct >= 45.0 or dte <= 6:
        stage = "换月高峰期"
        stage_desc = f"资金正密集由当月 ({c_near.get('contract_code')}) 向次月 ({c_next.get('contract_code')}) 移仓，警惕近月流动性衰减"
        status_tag = "warn"
    elif progress_pct >= 25.0 or dte <= 12:
        stage = "移仓启动期"
        stage_desc = f"次月合约沉淀资金快速上升，换月窗口开启，可分批顺延主力合约"
        status_tag = "brand"
    else:
        stage = "常规持仓期"
        stage_desc = f"当月合约 ({c_near.get('contract_code')}) 处于主力流动性黄金期，基差平稳，正常持有"
        status_tag = "success"

    # 计算沉淀资金 (亿元)
    capital_yi = 0.0
    for c in contracts:
        pr = float(c.get("price", 0.0))
        oi = int(c.get("open_interest", 0))
        val = pr * oi * spec.multiplier * spec.margin_rate
        capital_yi += val
    capital_yi = round(capital_yi / 1e8, 2)

    return {
        "near_code": c_near.get("contract_code"),
        "next_code": c_next.get("contract_code"),
        "days_to_expiry": dte,
        "delivery_date": c_near.get("delivery_date"),
        "progress_pct": progress_pct,
        "stage": stage,
        "stage_desc": stage_desc,
        "status_tag": status_tag,
        "near_oi": near_oi,
        "next_oi": next_oi,
        "total_oi": total_oi,
        "capital_commitment_yi": capital_yi,
    }




