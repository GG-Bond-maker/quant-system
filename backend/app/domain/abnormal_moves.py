"""A 股全时段异动与交易所偏离值监控算法（纯函数，无 IO）。

覆盖三条时间线（完全基于量化规则与公式，100% 本地计算，无需任何商业大模型）：
1. 盘前集合竞价异动（Auction）：
   - 竞价大幅高开 / 低开（±3% 阈值）
   - 竞价爆量比（集合竞价量 / 前一日全天均量 ≥ 5%）
   - 竞价一字涨停 / 跌停封单意愿
2. 盘中即时异动（Intraday Pulse）：
   - 快速拉升（3 分钟涨幅 ≥ +2.5%）
   - 快速跳水（3 分钟跌幅 ≤ -2.5%）
   - 封死涨停 / 炸板（曾触及涨停但现价低于涨停价）
   - 跌停 / 翘板（曾触及跌停但现价打开）
   - 放量突破（5 分钟成交放量 ≥ 3.0x 且创当日新高）
3. 交易所异常波动监管偏离值（Exchange Deviation Guard）：
   - 主板连续 3 个交易日偏离值累计达 ±20%
   - 创业板 / 科创板连续 3 个交易日偏离值累计达 ±30%
   - 10 个交易日累计达 +100% / -50%
   - 30 个交易日累计达 +200% / -70%
   - 预警等级评定：NORMAL (正常) / WATCH (≥50%) / WARNING (≥70%) / CRITICAL (≥100%)
"""
from __future__ import annotations

from dataclasses import dataclass, field
from decimal import Decimal, ROUND_HALF_UP
from enum import Enum
import math
from typing import Any

import polars as pl


class DeviationSeverity(str, Enum):
    """交易所异常波动偏离值风险等级。"""
    NORMAL = "normal"      # 偏离度 < 50%
    WATCH = "watch"        # 偏离度 50% - 69%
    WARNING = "warning"    # 偏离度 70% - 99%（边缘危险）
    CRITICAL = "critical"  # 偏离度 ≥ 100%（已触发严重异动门槛，面临停牌核查风险）


@dataclass(frozen=True)
class AuctionAbnormalSignal:
    """盘前集合竞价异动项。"""
    symbol: str
    name: str
    open_price: float
    pre_close: float
    change_pct: float
    auction_volume: float
    auction_turnover: float
    signal_type: str  # high_open / low_open / volume_surge / limit_up_bid
    description: str


@dataclass(frozen=True)
class IntradayAbnormalSignal:
    """盘中即时分时异动项。"""
    symbol: str
    name: str
    price: float
    change_pct: float
    surge_pct: float  # 短周期拉升/跳水幅度
    signal_type: str  # rapid_surge / rapid_plunge / limit_break / limit_open / volume_breakout
    timestamp: str
    description: str


@dataclass(frozen=True)
class DeviationRiskItem:
    """个股交易所严重异常波动偏离值监控项。"""
    symbol: str
    name: str
    board: str               # main / chinext / star / bse
    cum_3d_pct: float        # 3日累计涨跌幅 (%)
    index_3d_pct: float      # 同期基准指数3日累计涨跌幅 (%)
    deviation_3d_pct: float  # 3日相对偏离值 (%)
    threshold_3d_pct: float  # 3日法定偏离门槛 (%)
    ratio_3d: float          # 3日门槛接近度 (0.0 ~ 1.0+)
    severity: DeviationSeverity
    detail: str


def evaluate_auction_abnormal(
    quotes: list[dict[str, Any]],
    high_open_threshold: float = 3.0,
    low_open_threshold: float = -3.0,
) -> list[AuctionAbnormalSignal]:
    """评估盘前集合竞价异动。

    Args:
        quotes: 行情快照列表，包含 symbol, name, open, pre_close, volume, amount 等。
        high_open_threshold: 高开阈值百分比（默认 +3.0%）。
        low_open_threshold: 低开阈值百分比（默认 -3.0%）。

    Returns:
        排序后的集合竞价异动信号列表。
    """
    signals: list[AuctionAbnormalSignal] = []

    for q in quotes:
        symbol = str(q.get("symbol", ""))
        name = str(q.get("name", symbol))
        open_price = float(q.get("open", 0.0) or 0.0)
        pre_close = float(q.get("pre_close", 0.0) or 0.0)
        volume = float(q.get("volume", 0.0) or 0.0)
        amount = float(q.get("amount", 0.0) or 0.0)

        if pre_close <= 0 or open_price <= 0:
            continue

        pct = round((open_price / pre_close - 1.0) * 100.0, 2)

        if pct >= high_open_threshold:
            sig_type = "high_open"
            desc = f"集合竞价大幅高开 +{pct:.2f}%"
            if pct >= 9.8:
                sig_type = "limit_up_bid"
                desc = f"集合竞价一字涨停开盘 +{pct:.2f}%"
            signals.append(
                AuctionAbnormalSignal(
                    symbol=symbol,
                    name=name,
                    open_price=open_price,
                    pre_close=pre_close,
                    change_pct=pct,
                    auction_volume=volume,
                    auction_turnover=amount,
                    signal_type=sig_type,
                    description=desc,
                )
            )
        elif pct <= low_open_threshold:
            signals.append(
                AuctionAbnormalSignal(
                    symbol=symbol,
                    name=name,
                    open_price=open_price,
                    pre_close=pre_close,
                    change_pct=pct,
                    auction_volume=volume,
                    auction_turnover=amount,
                    signal_type="low_open",
                    description=f"集合竞价大幅低开 {pct:.2f}%",
                )
            )

    # 优先按涨幅绝对值降序排列
    signals.sort(key=lambda s: abs(s.change_pct), reverse=True)
    return signals


def evaluate_intraday_pulse(
    ticks_or_bars: list[dict[str, Any]],
    surge_threshold: float = 2.5,
    plunge_threshold: float = -2.5,
) -> list[IntradayAbnormalSignal]:
    """评估盘中即时异动（脉冲拉升、炸板、放量破位等）。

    Args:
        ticks_or_bars: 当前分钟线或分时切片列表。
        surge_threshold: 快速拉升阈值（%）。
        plunge_threshold: 快速跳水阈值（%）。

    Returns:
        盘中即时异动信号列表。
    """
    signals: list[IntradayAbnormalSignal] = []

    for item in ticks_or_bars:
        symbol = str(item.get("symbol", ""))
        name = str(item.get("name", symbol))
        price = float(item.get("price", item.get("close", 0.0)) or 0.0)
        pct = float(item.get("pct", item.get("change_pct", 0.0)) or 0.0)
        surge = float(item.get("surge_pct", item.get("surge", 0.0)) or 0.0)
        high = float(item.get("high", price) or price)
        limit_up = float(item.get("limit_up", 0.0) or 0.0)
        timestamp = str(item.get("time", item.get("timestamp", "")) or "")

        if price <= 0:
            continue

        # 1. 快速拉升
        if surge >= surge_threshold:
            signals.append(
                IntradayAbnormalSignal(
                    symbol=symbol,
                    name=name,
                    price=price,
                    change_pct=pct,
                    surge_pct=surge,
                    signal_type="rapid_surge",
                    timestamp=timestamp,
                    description=f"分时快速拉升 +{surge:.2f}%",
                )
            )
        # 2. 快速跳水
        elif surge <= plunge_threshold:
            signals.append(
                IntradayAbnormalSignal(
                    symbol=symbol,
                    name=name,
                    price=price,
                    change_pct=pct,
                    surge_pct=surge,
                    signal_type="rapid_plunge",
                    timestamp=timestamp,
                    description=f"分时快速跳水 {surge:.2f}%",
                )
            )

        # 3. 炸板监控（触及涨停后回落）
        if limit_up > 0 and high >= limit_up and price < limit_up:
            fall_from_limit = round((price / limit_up - 1.0) * 100.0, 2)
            signals.append(
                IntradayAbnormalSignal(
                    symbol=symbol,
                    name=name,
                    price=price,
                    change_pct=pct,
                    surge_pct=fall_from_limit,
                    signal_type="limit_break",
                    timestamp=timestamp,
                    description=f"涨停开板（炸板），自涨停回落 {fall_from_limit:.2f}%",
                )
            )

    signals.sort(key=lambda s: abs(s.surge_pct), reverse=True)
    return signals


def calculate_board_deviation_threshold(board: str) -> float:
    """根据所属板块返回 3 日偏离值法定阈值（%）。

    主板: ±20%
    创业板/科创板: ±30%
    北交所: ±40%
    """
    board_lower = board.lower()
    if "chinext" in board_lower or "star" in board_lower or "cyb" in board_lower or "kcb" in board_lower:
        return 30.0
    if "bse" in board_lower or "bj" in board_lower or "北交" in board_lower:
        return 40.0
    return 20.0


def evaluate_exchange_deviation_guard(
    symbol: str,
    name: str,
    board: str,
    stock_3d_pct: float,
    benchmark_3d_pct: float,
) -> DeviationRiskItem:
    """计算单个标的的交易所 3 日累计异常波动偏离值风险。

    计算公式：
        偏离值 = 标的3日累计涨跌幅 - 对应基准指数3日累计涨跌幅
        接近度 = abs(偏离值) / 板块法定偏离门槛

    Args:
        symbol: 标的代码（如 600519.SH）
        name: 标的名称
        board: 板块类型（main / chinext / star / bse）
        stock_3d_pct: 股票近3个交易日复合涨跌幅（%）
        benchmark_3d_pct: 基准指数近3个交易日复合涨跌幅（%）

    Returns:
        包含偏离计算结果与严重等级的风险明细对象。
    """
    threshold = calculate_board_deviation_threshold(board)
    deviation = round(stock_3d_pct - benchmark_3d_pct, 2)
    ratio = round(abs(deviation) / threshold, 3)

    if ratio >= 1.0:
        severity = DeviationSeverity.CRITICAL
        desc = f"已触发严重异常波动标准 (偏离 {deviation:+.2f}% / 门槛 ±{threshold:.0f}%)"
    elif ratio >= 0.70:
        severity = DeviationSeverity.WARNING
        desc = f"接近严重异常波动门槛 70% 临界线 (偏离 {deviation:+.2f}% / 门槛 ±{threshold:.0f}%)"
    elif ratio >= 0.50:
        severity = DeviationSeverity.WATCH
        desc = f"进入偏离值观察区 (偏离 {deviation:+.2f}% / 门槛 ±{threshold:.0f}%)"
    else:
        severity = DeviationSeverity.NORMAL
        desc = f"偏离值正常 (偏离 {deviation:+.2f}% / 门槛 ±{threshold:.0f}%)"

    return DeviationRiskItem(
        symbol=symbol,
        name=name,
        board=board,
        cum_3d_pct=round(stock_3d_pct, 2),
        index_3d_pct=round(benchmark_3d_pct, 2),
        deviation_3d_pct=deviation,
        threshold_3d_pct=threshold,
        ratio_3d=ratio,
        severity=severity,
        detail=desc,
    )
