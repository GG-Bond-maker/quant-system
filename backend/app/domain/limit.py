"""
A 股涨跌停价计算（纯函数，无 IO）。

覆盖场景与规则（2023 注册制改革后现行规则）：
- 沪深主板（60/00 开头）：±10%；ST/*ST：±5%；
- 创业板（300/301 开头）与科创板（688 开头）：±20%
  （⚠️ 创业板/科创板的 ST 股仍为 ±20%，交易所实际规则如此）；
  上市前 5 个交易日不设涨跌幅限制；
- 北交所（4/8 开头）：±30%；上市首日不设涨跌幅限制。

⚠️ 精度陷阱：必须使用 Decimal + ROUND_HALF_UP（四舍五入到 0.01 元），
Python 内置 round() 是银行家舍入（四舍六入五成双），对 xx.xx5 类价格会少 0.01 元，
导致计算出的涨跌停价与交易所公布值不一致。
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import date
from decimal import ROUND_HALF_UP, Decimal

# 各板块涨跌幅（Decimal 保证精确）
MARK_UP: dict[str, Decimal] = {
    "main": Decimal("0.10"),
    "main_st": Decimal("0.05"),
    "chinext_star": Decimal("0.20"),
    "bse": Decimal("0.30"),
}

# 主板全面注册制实施节点：此前新股首日 ±44%/-36%，此后前 5 日无涨跌幅限制
MAIN_BOARD_REGISTRATION_DATE = date(2023, 4, 1)

_TWO_DEC = Decimal("0.01")
# 不设涨跌停时用极大/极小值表示（避免引入 inf 到 Decimal 运算）
_INF_UP = Decimal("99999999.99")
_INF_DOWN = Decimal("0.00")


@dataclass
class InstrumentAttrs:
    """计算涨跌停价所需的最小标的属性集。"""

    symbol: str                      # 600519.SH
    code: str                        # 600519
    is_st: bool = False
    list_date: date | None = None
    instrument_type: str = "stock"   # stock/etf/index


def _twop(v: Decimal) -> Decimal:
    """四舍五入到 0.01 元（ROUND_HALF_UP，非银行家舍入）。"""
    return v.quantize(_TWO_DEC, rounding=ROUND_HALF_UP)


def determine_board(attrs: InstrumentAttrs) -> str:
    """返回板块类别：main / chinext_star / bse / other。"""
    code = attrs.code
    # ETF / 指数默认按 main 处理（涨跌幅差异由业务层另行处理）
    if attrs.instrument_type != "stock":
        return "main"
    # 科创板 688xxx（688+3 位数字）；创业板 30xxxx（30+4 位数字）
    if re.fullmatch(r"(30\d{4}|688\d{3})", code):
        return "chinext_star"
    if re.fullmatch(r"[48]\d{5}", code):
        return "bse"
    return "main"


def is_new_issue_first_n_days(attrs: InstrumentAttrs, today: date, n: int) -> bool:
    """判断 today 是否处于上市后前 n 个自然日内。"""
    if not attrs.list_date or today < attrs.list_date:
        return False
    return (today - attrs.list_date).days < n


def calc_limit_prices(
    prev_close: float | Decimal | None,
    attrs: InstrumentAttrs,
    today: date,
) -> tuple[Decimal, Decimal, Decimal]:
    """计算涨跌停价，返回 (limit_up_price, limit_down_price, pct)。

    :param prev_close: 上一交易日收盘价（停牌/首日无昨收时由业务层处理，此处拒绝 None）
    """
    if prev_close is None:
        raise ValueError("prev_close 不能为 None（停牌/首日无昨收，需业务层判断）")
    pc = Decimal(str(prev_close))
    if pc <= 0:
        raise ValueError("prev_close 必须 > 0")

    board = determine_board(attrs)
    # 不设涨跌停的特殊期
    if board == "chinext_star" and is_new_issue_first_n_days(attrs, today, 5):
        return _twop(_INF_UP), _INF_DOWN, Decimal(0)
    if board == "bse" and is_new_issue_first_n_days(attrs, today, 1):
        return _twop(_INF_UP), _INF_DOWN, Decimal(0)
    # 主板新股：全面注册制（2023-04 后上市）前 5 日无涨跌幅；
    # 此前的旧规为首日 +44% / -36%（发行价基准），此后 ±10%
    if board == "main" and attrs.list_date is not None:
        if attrs.list_date >= MAIN_BOARD_REGISTRATION_DATE:
            if is_new_issue_first_n_days(attrs, today, 5):
                return _twop(_INF_UP), _INF_DOWN, Decimal(0)
        elif is_new_issue_first_n_days(attrs, today, 1):
            up = _twop(pc * Decimal("1.44"))
            dn = _twop(pc * Decimal("0.64"))
            return up, dn, Decimal("0.44")

    if board == "main":
        pct = MARK_UP["main_st"] if attrs.is_st else MARK_UP["main"]
    elif board == "chinext_star":
        # 创业板/科创板：ST 股仍为 ±20%
        pct = MARK_UP["chinext_star"]
    elif board == "bse":
        pct = MARK_UP["bse"]
    else:
        pct = MARK_UP["main"]

    up = pc * (Decimal(1) + pct)
    dn = pc * (Decimal(1) - pct)
    return _twop(up), _twop(dn), pct
