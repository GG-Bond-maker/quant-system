"""港股（特别是港股通）交易规则与 AH 溢价核算（纯函数，无 IO）。

口径与规则：
1. 代码规范：5 位数字代码 + ".HK"（如 "00700.HK" / "09988.HK"）；
2. 每手股数（lot_size）：港股非固定 100 股/手，由上市公司自行设定
   （如腾讯 100、比亚迪 500、汇丰 400、中国银行 1000）；
3. 交易规费与税费（买卖双边）：
   - 香港印花税（Stamp Duty）：0.1%（2023-11-17 起由 0.13% 调降至 0.1%）；
   - 证监会交易征费（SFC Transaction Levy）：0.0027%；
   - 会财局交易征费（AFRC Transaction Levy）：0.00015%（2022-01-01 起实施）；
   - 联交所交易费（HKEX Trading Fee）：0.00565%（2023-01-01 起实施）；
   - 默认券商佣金：万 3（0.0003），单笔最低 15 HKD（港股通人民币账户可折算或最低 15 元）；
4. 回转与交收制度：
   - T+0 当日回转（可同日买入卖出多次）；
   - T+2 资金交收；
5. AH 溢价率与套利模型：
   - AH 溢价率 = (A股价格 - H股价格 × 汇率) / (H股价格 × 汇率) × 100%
   - HA 折价率 = (H股价格 × 汇率 - A股价格) / A股价格 × 100%
   - AH 比价 = A股价格 / (H股价格 × 汇率)
"""
from __future__ import annotations

import re
from datetime import date, datetime
from typing import Any

from .trading_rules import COMMISSION_RATE_DEFAULT

# ---------- 港股交易税费与规费常量（事实来源） ----------
# 印花税：2023-11-17 起由 0.13% 调降至 0.1%（买卖双边）
HK_STAMP_DUTY_RATE = 0.001
HK_STAMP_DUTY_RATE_LEGACY = 0.0013
HK_STAMP_DUTY_CUT_DATE = date(2023, 11, 17)

# 监管与交易所规费（买卖双边）
HK_SFC_LEVY_RATE = 0.000027        # 香港证监会交易征费 0.0027%
HK_AFRC_LEVY_RATE = 0.0000015      # 会计及财务汇报局征费 0.00015%
HK_EXCHANGE_FEE_RATE = 0.0000565   # 联交所交易费 0.00565%

# 券商默认佣金与最低消费（佣金率统一继承单一事实源）
HK_COMMISSION_RATE_DEFAULT = COMMISSION_RATE_DEFAULT
HK_COMMISSION_MIN_DEFAULT = 15.0     # 最低 15 港币 / 元（港股通）

# 默认参考汇率（HKD/CNY，可根据实时市场动态覆盖）
DEFAULT_HKD_CNY_FX = 0.9250


# ---------- 标的代码规范化与校验 ----------
def is_hk_symbol(value: str) -> bool:
    """判定是否为合规的港股代码或代码格式（如 '00700.HK' / '00700'）。"""
    if not isinstance(value, str):
        return False
    s = value.strip().upper()
    if s.endswith(".HK"):
        code = s[:-3]
        return bool(re.fullmatch(r"\d{1,5}", code))
    return bool(re.fullmatch(r"\d{5}", s))


def normalize_hk_symbol(value: str) -> str:
    """规范化港股代码为标准 5 位数字 + '.HK'（如 '700' -> '00700.HK', '00700.hk' -> '00700.HK'）。

    Raises:
        ValueError: 输入非合规港股代码格式。
    """
    if not isinstance(value, str):
        raise ValueError(f"非法港股代码: {value!r}")
    s = value.strip().upper()
    if s.endswith(".HK"):
        code_part = s[:-3]
    else:
        code_part = s

    if not re.fullmatch(r"\d{1,5}", code_part):
        raise ValueError(f"非法港股代码: {value!r}（期望 1~5 位数字代码，可带 .HK 后缀）")

    # 港股官方标准为 5 位数字，不足 5 位前补 0
    padded = code_part.zfill(5)
    return f"{padded}.HK"


def hk_symbol_to_code(symbol: str) -> str:
    """提取港股纯数字代码：'00700.HK' -> '00700'。"""
    norm = normalize_hk_symbol(symbol)
    return norm.split(".")[0]


# ---------- 港股每手股数（lot_size） ----------
# 核心港股通权重股与知名标的每手股数表
HK_LOT_SIZES: dict[str, int] = {
    "00700.HK": 100,    # 腾讯控股
    "09988.HK": 100,    # 阿里巴巴-W
    "03690.HK": 100,    # 美团-W
    "01810.HK": 200,    # 小米集团-W
    "09618.HK": 50,     # 京东集团-SW
    "09888.HK": 50,     # 百度集团-SW
    "01024.HK": 100,    # 快手-W
    "02015.HK": 100,    # 理想汽车-W
    "09868.HK": 100,    # 小鹏汽车-W
    "09866.HK": 10,     # 蔚来-SW
    "00005.HK": 400,    # 汇丰控股
    "00388.HK": 100,    # 香港交易所
    "02318.HK": 500,    # 中国平安 (H)
    "01211.HK": 500,    # 比亚迪股份 (H)
    "00981.HK": 500,    # 中芯国际 (H)
    "00857.HK": 2000,   # 中国石油股份 (H)
    "00939.HK": 1000,   # 建设银行 (H)
    "01398.HK": 1000,   # 工商银行 (H)
    "03988.HK": 1000,   # 中国银行 (H)
    "01288.HK": 1000,   # 农业银行 (H)
    "02628.HK": 1000,   # 中国人寿 (H)
    "03968.HK": 500,    # 招商银行 (H)
    "00763.HK": 200,    # 中兴通讯 (H)
    "02333.HK": 500,    # 长城汽车 (H)
    "00168.HK": 2000,   # 青岛啤酒股份 (H)
    "00386.HK": 2000,   # 中国石油化工股份 (H)
    "00883.HK": 1000,   # 中国海洋石油 (H)
    "01088.HK": 500,    # 中国神华 (H)
    "02899.HK": 2000,   # 紫金矿业 (H)
    "02269.HK": 500,    # 药明生物
    "01093.HK": 2000,   # 石药集团
    "02382.HK": 100,    # 舜宇光学科技
    "00267.HK": 1000,   # 中信股份
    "01177.HK": 1000,   # 中国生物制药
    "00027.HK": 1000,   # 银河娱乐
    "01928.HK": 400,    # 金沙中国有限公司
}

DEFAULT_HK_LOT_SIZE = 100


def get_hk_lot_size(symbol: str) -> int:
    """获取指定港股的每手股数（lot_size），未收录标的兜底 100 股。"""
    try:
        norm = normalize_hk_symbol(symbol)
        return HK_LOT_SIZES.get(norm, DEFAULT_HK_LOT_SIZE)
    except Exception:
        return DEFAULT_HK_LOT_SIZE


def round_to_lot(quantity: int, lot_size: int, mode: str = "floor") -> int:
    """将委托股数规整为每手股数的整倍数。

    :param quantity: 原始委托股数
    :param lot_size: 每手股数（> 0）
    :param mode: "floor"（向下取整，默认）、"ceil"（向上取整）、"round"（四舍五入）
    :returns: 合规整手股数（>= 0）
    """
    if lot_size <= 0:
        raise ValueError(f"每手股数必须大于 0: {lot_size}")
    if quantity <= 0:
        return 0

    lots = quantity / lot_size
    if mode == "floor":
        n_lots = int(lots)
    elif mode == "ceil":
        import math
        n_lots = math.ceil(lots)
    elif mode == "round":
        n_lots = round(lots)
    else:
        raise ValueError(f"未知取整模式: {mode}（支持 floor/ceil/round）")

    return int(n_lots * lot_size)


# ---------- 交易成本与摩擦核算 ----------
def hk_stamp_duty_rate(on_date: date | datetime | None = None) -> float:
    """获取指定交易日的香港印花税率（双边各征）。

    2023-11-17 起调降至 0.1%（0.001）；此前为 0.13%（0.0013）。
    """
    if on_date is None:
        return HK_STAMP_DUTY_RATE
    d = on_date.date() if isinstance(on_date, datetime) else on_date
    if d < HK_STAMP_DUTY_CUT_DATE:
        return HK_STAMP_DUTY_RATE_LEGACY
    return HK_STAMP_DUTY_RATE


def calculate_hk_trading_costs(
    amount: float,
    direction: str = "buy",
    on_date: date | datetime | None = None,
    commission_rate: float = HK_COMMISSION_RATE_DEFAULT,
    min_commission: float = HK_COMMISSION_MIN_DEFAULT,
) -> dict[str, float]:
    """精确核算港股交易成本（买卖双边均产生印花税与规费）。

    :param amount: 成交金额（成交价 × 成交股数）
    :param direction: 交易方向 'buy' 或 'sell'
    :param on_date: 成交日期（用于印花税分段判定）
    :param commission_rate: 券商佣金率，默认万 3
    :param min_commission: 券商最低佣金，默认 15
    :returns: 各分项费用与总成本字典
    """
    if amount <= 0:
        return {
            "commission": 0.0,
            "stamp_duty": 0.0,
            "sfc_levy": 0.0,
            "afrc_levy": 0.0,
            "hkex_fee": 0.0,
            "total_cost": 0.0,
            "cost_rate": 0.0,
        }

    # 1. 券商佣金（含最低消费）
    raw_comm = amount * commission_rate
    commission = max(raw_comm, min_commission)

    # 2. 印花税（双边）
    sd_rate = hk_stamp_duty_rate(on_date)
    stamp_duty = round(amount * sd_rate, 2)

    # 3. 监管与交易所规费（双边）
    sfc_levy = round(amount * HK_SFC_LEVY_RATE, 2)
    afrc_levy = round(amount * HK_AFRC_LEVY_RATE, 2)
    hkex_fee = round(amount * HK_EXCHANGE_FEE_RATE, 2)

    total_cost = commission + stamp_duty + sfc_levy + afrc_levy + hkex_fee
    cost_rate = total_cost / amount if amount > 0 else 0.0

    return {
        "commission": round(commission, 2),
        "stamp_duty": stamp_duty,
        "sfc_levy": sfc_levy,
        "afrc_levy": afrc_levy,
        "hkex_fee": hkex_fee,
        "total_cost": round(total_cost, 2),
        "cost_rate": round(cost_rate, 6),
    }


# ---------- AH 股代码对与溢价套利模型 ----------
# 经典优质 AH 股映射对照（A股代码 -> H股代码及基本面简称）
AH_PAIR_MAPPINGS: list[dict[str, str]] = [
    {"a_symbol": "601318.SH", "h_symbol": "02318.HK", "name": "中国平安", "industry": "保险"},
    {"a_symbol": "002594.SZ", "h_symbol": "01211.HK", "name": "比亚迪", "industry": "汽车"},
    {"a_symbol": "688981.SH", "h_symbol": "00981.HK", "name": "中芯国际", "industry": "半导体"},
    {"a_symbol": "601288.SH", "h_symbol": "01288.HK", "name": "农业银行", "industry": "银行"},
    {"a_symbol": "601398.SH", "h_symbol": "01398.HK", "name": "工商银行", "industry": "银行"},
    {"a_symbol": "601939.SH", "h_symbol": "00939.HK", "name": "建设银行", "industry": "银行"},
    {"a_symbol": "601988.SH", "h_symbol": "03988.HK", "name": "中国银行", "industry": "银行"},
    {"a_symbol": "600036.SH", "h_symbol": "03968.HK", "name": "招商银行", "industry": "银行"},
    {"a_symbol": "601857.SH", "h_symbol": "00857.HK", "name": "中国石油", "industry": "石油石化"},
    {"a_symbol": "600028.SH", "h_symbol": "00386.HK", "name": "中国石化", "industry": "石油石化"},
    {"a_symbol": "600938.SH", "h_symbol": "00883.HK", "name": "中国海油", "industry": "石油石化"},
    {"a_symbol": "601628.SH", "h_symbol": "02628.HK", "name": "中国人寿", "industry": "保险"},
    {"a_symbol": "601088.SH", "h_symbol": "01088.HK", "name": "中国神华", "industry": "煤炭"},
    {"a_symbol": "601899.SH", "h_symbol": "02899.HK", "name": "紫金矿业", "industry": "有色金属"},
    {"a_symbol": "000063.SZ", "h_symbol": "00763.HK", "name": "中兴通讯", "industry": "通信"},
    {"a_symbol": "601633.SH", "h_symbol": "02333.HK", "name": "长城汽车", "industry": "汽车"},
    {"a_symbol": "600600.SH", "h_symbol": "00168.HK", "name": "青岛啤酒", "industry": "食品饮料"},
    {"a_symbol": "601766.SH", "h_symbol": "01766.HK", "name": "中国中车", "industry": "交运设备"},
    {"a_symbol": "601600.SH", "h_symbol": "02600.HK", "name": "中国铝业", "industry": "有色金属"},
    {"a_symbol": "600019.SH", "h_symbol": "02601.HK", "name": "宝钢股份", "industry": "钢铁"},
    {"a_symbol": "600585.SH", "h_symbol": "00914.HK", "name": "海螺水泥", "industry": "建筑材料"},
    {"a_symbol": "601800.SH", "h_symbol": "01800.HK", "name": "中国交建", "industry": "建筑装饰"},
    {"a_symbol": "601186.SH", "h_symbol": "01186.HK", "name": "中国铁建", "industry": "建筑装饰"},
    {"a_symbol": "601390.SH", "h_symbol": "00390.HK", "name": "中国中铁", "industry": "建筑装饰"},
    {"a_symbol": "600115.SH", "h_symbol": "00670.HK", "name": "中国东航", "industry": "交通运输"},
    {"a_symbol": "601111.SH", "h_symbol": "00753.HK", "name": "中国国航", "industry": "交通运输"},
    {"a_symbol": "600029.SH", "h_symbol": "01055.HK", "name": "南方航空", "industry": "交通运输"},
    {"a_symbol": "000338.SZ", "h_symbol": "02338.HK", "name": "潍柴动力", "industry": "机械设备"},
    {"a_symbol": "600887.SH", "h_symbol": "02319.HK", "name": "伊利蒙牛对标", "industry": "食品饮料"},
    {"a_symbol": "600875.SH", "h_symbol": "01072.HK", "name": "东方电气", "industry": "电力设备"},
]

# 快速查询字典
A_TO_H_MAP = {p["a_symbol"]: p["h_symbol"] for p in AH_PAIR_MAPPINGS}
H_TO_A_MAP = {p["h_symbol"]: p["a_symbol"] for p in AH_PAIR_MAPPINGS}


def get_ah_counterpart(symbol: str) -> str | None:
    """查询对应标的的 A/H 镜像标的代码。"""
    sym = symbol.strip().upper()
    if sym in A_TO_H_MAP:
        return A_TO_H_MAP[sym]
    if sym in H_TO_A_MAP:
        return H_TO_A_MAP[sym]
    return None


def calculate_ah_spread(
    a_price: float | None,
    h_price: float | None,
    hkd_cny_fx: float = DEFAULT_HKD_CNY_FX,
) -> dict[str, Any]:
    """计算 AH 价差与溢价率。

    数学公式：
    - H 股人民币等值价 = H股价格 × 汇率
    - AH 溢价率 (%) = (A股价格 - H股价格 × 汇率) / (H股价格 × 汇率) * 100%
    - HA 折价率 (%) = (H股价格 × 汇率 - A股价格) / A股价格 * 100%
    - AH 比率 = A股价格 / (H股价格 × 汇率)

    套利信号裁决规则：
    - AH 溢价率 > 60%: 'a_extreme_premium'（A股极度溢价，H股显著低估，多H空A或配置H股优选）
    - AH 溢价率 20% ~ 60%: 'a_moderate_premium'（A股常规温和溢价，属于正常历史估值中枢区间）
    - AH 溢价率 0% ~ 20%: 'ah_near_parity'（平价收敛区间）
    - AH 溢价率 < 0%: 'h_premium'（少见，H股较A股溢价）
    """
    if (
        a_price is None
        or h_price is None
        or a_price <= 0
        or h_price <= 0
        or hkd_cny_fx <= 0
    ):
        return {
            "a_price": a_price,
            "h_price": h_price,
            "h_price_cny": None,
            "hkd_cny_fx": hkd_cny_fx,
            "ah_premium_rate": None,
            "ha_discount_rate": None,
            "ah_ratio": None,
            "arbitrage_signal": "unavailable",
            "signal_desc": "行情或汇率数据不完整",
        }

    h_price_cny = round(h_price * hkd_cny_fx, 4)
    ah_premium_rate = round(((a_price - h_price_cny) / h_price_cny) * 100, 2)
    ha_discount_rate = round(((h_price_cny - a_price) / a_price) * 100, 2)
    ah_ratio = round(a_price / h_price_cny, 3)

    if ah_premium_rate > 60.0:
        sig = "a_extreme_premium"
        desc = f"A股极度溢价(+{ah_premium_rate}%)，H股性价比突出，存在收敛套利空间"
    elif ah_premium_rate >= 20.0:
        sig = "a_moderate_premium"
        desc = f"A股温和溢价(+{ah_premium_rate}%)，处于典型历史中枢区间"
    elif ah_premium_rate >= 0.0:
        sig = "ah_near_parity"
        desc = f"AH接近平价(+{ah_premium_rate}%)，价差收敛"
    else:
        sig = "h_premium"
        desc = f"H股较A股溢价({ah_premium_rate}%)，A股估值具备相对优势"

    return {
        "a_price": round(a_price, 3),
        "h_price": round(h_price, 3),
        "h_price_cny": h_price_cny,
        "hkd_cny_fx": round(hkd_cny_fx, 4),
        "ah_premium_rate": ah_premium_rate,
        "ha_discount_rate": ha_discount_rate,
        "ah_ratio": ah_ratio,
        "arbitrage_signal": sig,
        "signal_desc": desc,
    }
