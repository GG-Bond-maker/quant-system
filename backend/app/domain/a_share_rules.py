"""A 股交易制度小工具（纯函数，无 IO）。"""
from __future__ import annotations

import re
from datetime import date


def code_to_symbol(code: str) -> str:
    """将 6 位数字代码补全为标准代码：600519.SH / 000001.SZ / 831010.BJ。

    前缀规则：
        6/9 开头 -> 上交所 .SH
        0/2/3 开头 -> 深交所 .SZ
        4/8 开头 -> 北交所 .BJ
    """
    if not re.fullmatch(r"\d{6}", code):
        raise ValueError(f"code 必须 6 位数字: {code}")
    if code.startswith(("6", "9")):
        return f"{code}.SH"
    if code.startswith(("0", "2", "3")):
        return f"{code}.SZ"
    if code.startswith(("4", "8")):
        return f"{code}.BJ"
    # 兜底
    return f"{code}.SH"


def symbol_to_code(symbol: str) -> str:
    """从标准代码 600519.SH 提取 6 位纯数字代码。"""
    s = symbol.split(".")[0]
    if len(s) != 6 or not s.isdigit():
        raise ValueError(f"非法 symbol: {symbol}")
    return s


def is_t_plus_one() -> bool:
    """A 股所有股票执行 T+1。"""
    return True


def settlement_days(buy_date: date, sell_date: date) -> int:
    """返回 (sell - buy) 自然日差；用于换手率近似（不是严格结算制度）。"""
    return max(0, (sell_date - buy_date).days)


def stamp_duty_rate(instrument_type: str, direction: str) -> float:
    """印花税规则（简化）：股票卖出 0.05%（2023-08 调整后），买入与其他为 0。"""
    if instrument_type == "stock" and direction.lower() == "sell":
        return 0.0005
    return 0.0


def commission_rate_default(instrument_type: str) -> float:
    """默认佣金率：股票/ETF 万 3（最低 5 元在 Broker 层处理）。"""
    if instrument_type in {"stock", "etf"}:
        return 0.0003
    return 0.0001
