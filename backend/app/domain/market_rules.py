"""多市场（A 股 / 港股及港股通）交易规则统一调度器（纯函数，无 IO）。

职责：
1. 市场识别：根据代码后缀或形态识别 CN（SH/SZ/BJ）或 HK；
2. 每手股数（lot_size）统一派发：A 股统一 100 股，港股依据标的元数据定制；
3. 交易成本统一派发：A 股调用 a_share_rules/trading_rules，港股调用 hk_rules；
4. 保持向前兼容性，所有既有单市场调用均无感知平滑迁移。
"""
from __future__ import annotations

from datetime import date, datetime
from typing import Any

from .a_share_rules import (
    effective_stamp_duty,
    effective_transfer_fee,
    is_etf_symbol,
)
from .hk_rules import (
    HK_COMMISSION_MIN_DEFAULT,
    calculate_hk_trading_costs,
    get_hk_lot_size,
    is_hk_symbol,
    round_to_lot,
)
from .trading_rules import COMMISSION_MIN, COMMISSION_RATE_DEFAULT


def detect_market(symbol: str) -> str:
    """识别标的所属市场代码：'CN' | 'HK'。

    :param symbol: 标的代码，如 '600519.SH' / '00700.HK' / '00700'
    :returns: 'CN' 或 'HK'
    """
    s = str(symbol).strip().upper()
    if s.endswith(".HK") or is_hk_symbol(s):
        return "HK"
    return "CN"


def get_market_lot_size(symbol: str) -> int:
    """获取标的的标准每手交易股数。

    - A 股主板/创业板/科创板/北交所/ETF：统一 100 股/手；
    - 港股：根据发行人与联交所设定（如腾讯 100，比亚迪 500，汇丰 400，中国银行 1000 等）。
    """
    m = detect_market(symbol)
    if m == "HK":
        return get_hk_lot_size(symbol)
    return 100


def round_order_quantity(symbol: str, quantity: int, mode: str = "floor") -> int:
    """将报单委托量规整为该市场每手的整数倍。"""
    lot_size = get_market_lot_size(symbol)
    return round_to_lot(quantity, lot_size, mode=mode)


def calculate_trading_friction(
    symbol: str,
    amount: float,
    direction: str = "buy",
    on_date: date | datetime | None = None,
    commission_rate: float | None = None,
    min_commission: float | None = None,
) -> dict[str, float]:
    """统一计算标的的交易摩擦成本（佣金、印花税、经手费、规费）。

    :returns: 包含 'commission', 'stamp_duty', 'transfer_fee', 'other_fees', 'total_cost', 'cost_rate'
    """
    m = detect_market(symbol)
    if m == "HK":
        hk_res = calculate_hk_trading_costs(
            amount=amount,
            direction=direction,
            on_date=on_date,
            commission_rate=commission_rate if commission_rate is not None else COMMISSION_RATE_DEFAULT,
            min_commission=min_commission if min_commission is not None else HK_COMMISSION_MIN_DEFAULT,
        )
        return {
            "commission": hk_res["commission"],
            "stamp_duty": hk_res["stamp_duty"],
            "transfer_fee": 0.0,
            "other_fees": round(hk_res["sfc_levy"] + hk_res["afrc_levy"] + hk_res["hkex_fee"], 2),
            "total_cost": hk_res["total_cost"],
            "cost_rate": hk_res["cost_rate"],
        }

    # A 股摩擦模型
    comm_rate = commission_rate if commission_rate is not None else COMMISSION_RATE_DEFAULT
    comm_min = min_commission if min_commission is not None else COMMISSION_MIN
    raw_comm = amount * comm_rate
    commission = max(raw_comm, comm_min) if amount > 0 else 0.0

    # 印花税：股票卖出收取，ETF免征
    sd_rate = 0.0
    if direction.lower() == "sell":
        sd_rate = effective_stamp_duty(symbol, on_date=on_date)
    stamp_duty = round(amount * sd_rate, 2)

    # 过户费：股票双边 0.01‰，ETF 为 0
    tf_rate = effective_transfer_fee(symbol)
    transfer_fee = round(amount * tf_rate, 2)

    total_cost = commission + stamp_duty + transfer_fee
    cost_rate = total_cost / amount if amount > 0 else 0.0

    return {
        "commission": round(commission, 2),
        "stamp_duty": stamp_duty,
        "transfer_fee": transfer_fee,
        "other_fees": 0.0,
        "total_cost": round(total_cost, 2),
        "cost_rate": round(cost_rate, 6),
    }
