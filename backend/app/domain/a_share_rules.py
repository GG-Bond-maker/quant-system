"""A 股交易制度小工具（纯函数，无 IO）。"""
from __future__ import annotations

import re
from datetime import date, datetime

# 费率**数字**的唯一事实来源（审计 R2 / §8.2 第 18 项）。本模块只保留
# 「谁收、按成交日怎么分段」的判定，并把这些常量重新导出，使既有调用方
# （含 tests 对 STAMP_DUTY_CUT_DATE 的导入）继续可用。
from .trading_rules import (
    STAMP_DUTY_CUT_DATE,
    STAMP_DUTY_STOCK_RATE,
    STAMP_DUTY_STOCK_RATE_LEGACY,
    TRANSFER_FEE_RATE,
)


def market_prefix(code: str) -> str:
    """6 位数字代码 → 交易所前缀（小写 ``"sh"`` / ``"sz"`` / ``"bj"``）。纯函数，无 IO。

    本函数是本仓「**裸代码 → 交易所**」映射的**唯一事实来源**：:func:`code_to_symbol`
    与 ``data/portfolio_source._market_symbol`` 均委托它，消除此前三套互相不一致的
    实现（``code_to_symbol`` 的 ``else -> .SH`` 兜底 / ``_market_symbol`` 的 ``6/5``
    私有判据 / ``akshare_adapter._sina_symbol`` 的自有判据）。

    前缀规则::

        6 / 9 / 5 开头 -> "sh"   # 沪市：60x 主板、688 科创板、900 沪B、5xx 沪基金/ETF
        0 / 1 / 2 / 3 开头 -> "sz"  # 深市：00x 主板、30x 创业板、200 深B、1xx 深基金/ETF/LOF
        4 / 8 开头 -> "bj"      # 北交所：43x/83x/87x/88x

    兜底：其余前缀（如 ``7xxxxx`` 沪市配股/申购代码）返回 ``"sh"``，与历史
    ``code_to_symbol`` 的 ``else`` 分支**逐字一致**，保证既有调用方**零行为变更**。

    ⚠️ 适用范围（**显式声明，不遮掩**）：

        本仓当前**仅有两类标的**——A 股股票（含沪 B ``900xxx``）与场内基金
        （ETF/LOF/封闭式，见 :func:`is_etf_symbol`）。本函数只按首字符粗判，
        对**可转债**会出错：

            - ``110xxx / 111xxx / 113xxx`` 为**沪市**可转债，本函数按 ``"1"``
              前缀误判为 ``"sz"``（正确应为 ``"sh"``）；
            - ``123xxx / 127xxx / 128xxx`` 为深市可转债，判为 ``"sz"`` 恰好正确。

        现在**不**修的原因：实测全仓无可转债定价/行情路径（``grep 可转债`` 仅命中
        ``data/etf.py:482`` 一个债基名称分类字符串，不参与代码映射）。
        **将来若接入可转债，必须**在此处按 ``"11"`` 段特判为 ``"sh"``，否则会
        静默取错交易所（东财/新浪均以交易所前缀路由行情）。

    Args:
        code: 6 位数字代码字符串（如 ``"600519"`` / ``"159915"``）。

    Returns:
        小写交易所前缀 ``"sh"`` / ``"sz"`` / ``"bj"``。

    Raises:
        ValueError: ``code`` 非 6 位纯数字（错误信息与 :func:`code_to_symbol` 同款）。
    """
    if not re.fullmatch(r"\d{6}", code):
        raise ValueError(f"code 必须 6 位数字: {code}")
    if code.startswith(("6", "9", "5")):
        return "sh"
    if code.startswith(("0", "1", "2", "3")):
        return "sz"
    if code.startswith(("4", "8")):
        return "bj"
    # 兜底：与历史 code_to_symbol 的 else 分支保持一致（7xxxxx 等 -> 沪市）
    return "sh"


def code_to_symbol(code: str) -> str:
    """将 6 位数字代码补全为标准代码：600519.SH / 000001.SZ / 831010.BJ / 159915.SZ。

    前缀规则**委托** :func:`market_prefix`（唯一事实来源），不再各自维护一份：
        6/9/5 开头 -> 上交所 .SH
        0/1/2/3 开头 -> 深交所 .SZ（**含深市 ETF/LOF ``1xxxxx``**）
        4/8 开头 -> 北交所 .BJ

    修正（Batch 1）：历史实现在 ``else`` 分支把 ``1xxxxx`` 兜底成 ``.SH``，使
    ``159915.SZ`` 等深市 ETF/LOF 被**静默拼成不存在的** ``159915.SH``。委托后
    ``1xxxxx`` 归入深市，与 ``data/ingest/etf_instruments.cn_etf_symbol`` 及
    ``akshare_adapter._sina_symbol`` 的口径对齐。

    Args:
        code: 6 位数字代码字符串。

    Returns:
        形如 ``600519.SH`` 的标准 symbol。

    Raises:
        ValueError: ``code`` 非 6 位纯数字（由 :func:`market_prefix` 抛出）。
    """
    return f"{code}.{market_prefix(code).upper()}"


def normalize_code(value: str) -> str:
    """把 ``600519`` / ``600519.SH`` / `` 600519 `` 统一规范化为纯 6 位代码 ``600519``。

    背景（2026-09-18 晚间例行每晚失败）：流水线 ``codes`` 参数的语义是**纯 6 位代码**
    （CLI 与 ``_run_pipeline_impl`` 的兜底值均为 ``["600519", "000001", "300750"]``），
    但 ``jobs/evening_routine`` 传的是 ``read_all_symbols("daily_bar")`` —— 它返回
    **带交易所后缀**的 symbol（实测 2499 只全部形如 ``000001.SZ``）。后缀值进入
    ``code_to_symbol`` 即触发 ``ValueError: code 必须 6 位数字``，而流水线是
    fail-fast ⇒ validate 首步抛错、后续 6 步全部不执行，整晚例行静默失效。

    本函数是该「入口格式不齐」问题的**统一规范化入口**，供流水线入口
    （``orchestrator._run_pipeline_impl``）在派发步骤前逐项调用，使
    「裸码」「带后缀」「带空白」三种写法收敛为同一种口径。

    Args:
        value: 待规范化的标的代码，接受以下形态：
            - ``"600519"``        —— 纯 6 位数字（原样返回）；
            - ``"600519.SH"``     —— 带交易所后缀（大小写均可，如 ``.sz``）；
            - ``" 600519 "``      —— 首尾空白（先 ``strip()`` 再判定）。

    Returns:
        纯 6 位数字代码字符串。

    Raises:
        ValueError: 非字符串、空串、位数不足/过多、含非数字，或后缀段数异常
            （如 ``"600519.SH.XX"`` 有多个 ``.``）。错误信息**携带原始入参**，
            便于值班从日志直接定位是哪一条脏数据。

    Note:
        与 :func:`symbol_to_code` 的区别：后者是「取 symbol 前缀」的宽松辅助
        （``split(".")[0]``，对 ``600519.SH.XX`` 也返回 ``600519``）；本函数是
        **入口门禁**， deliberately 更严格——多段后缀属格式损坏，静默截断会掩盖
        上游的数据问题，故显式拒绝。
    """
    if not isinstance(value, str):
        raise ValueError(f"非法标的代码: {value!r}（期望形如 600519 或 600519.SH 的字符串）")
    raw = value
    s = value.strip()
    parts = s.split(".")
    # 形态一：NNNNNN（无后缀）；形态二：NNNNNN.XX（单段后缀）
    # 两段以上（如 600519.SH.XX）属格式损坏 —— 不静默截断，直接拒绝。
    if len(parts) == 1:
        code = parts[0]
    elif len(parts) == 2 and parts[1]:
        code = parts[0]
    else:
        raise ValueError(
            f"非法标的代码: {raw!r}（期望 6 位数字，可带单段交易所后缀，如 600519 / 600519.SH）")
    if not re.fullmatch(r"\d{6}", code):
        raise ValueError(
            f"非法标的代码: {raw!r}（期望 6 位数字，可带单段交易所后缀，如 600519 / 600519.SH）")
    return code


def symbol_to_code(symbol: str) -> str:
    """从标准代码 600519.SH 提取 6 位纯数字代码。"""
    s = symbol.split(".")[0]
    if len(s) != 6 or not s.isdigit():
        raise ValueError(f"非法 symbol: {symbol}")
    return s


# ============================================================================
# 品种判定（是谁）与历史分段（什么时候）：费率的**数字**见 domain.trading_rules
# 单一事实来源（审计 B5-10 / R2 / §8.2 第 18 项）
# ============================================================================
# 此前的三套互不一致的场内基金判定：
#   ma_cross._is_etf_symbol : ("5", "15", "16", "56", "58")  → {5*, 15*, 16*}
#   paper._is_etf           : ("5", "15", "16")              → {5*, 15*, 16*}
#   watchlist.is_etf_code   : ("51", "56", "58", "15")[:4] ∪ {"159"}
#                             → {51*, 56*, 58*, 15*, 159*} —— **漏 16x**，
#                               且 `and` / `or` 优先级让 `startswith("159")` 绕过了
#                               长度与数字校验（`"159915.SH"` 也返回 True）；
#   以及 broker 根本不判品种，**对 ETF 卖出照收印花税**。
#
# 统一集合 {5*, 15*, 16*, 18*}：
#   沪市基金 5xxxxx（510/511/512-515 ETF、56x、58x、500xxx 封闭式）；
#   深市 15xxxx（ETF）、16xxxx（LOF）、18xxxx（封闭式基金）。
# 均为「卖出免印花税、不收过户费」的场内基金口径。
ETF_PREFIXES = ("5", "15", "16", "18")


def is_etf_symbol(symbol: str) -> bool:
    """场内基金（ETF/LOF/封闭式）判定。

    接受 ``510300`` / ``510300.SH`` / ``159915.SZ`` / `` 510300 ``（空白与大小写
    不敏感）；**拒绝**多段后缀（``159915.SH.XX``）—— 与同模块
    :func:`normalize_code` 的立场一致："多段后缀属格式损坏，静默截断会掩盖上游
    数据问题"。**非** 6 位数字形态（行业名、空串）一律返回 ``False`` 且不抛异常：
    本函数位于计费热路径与 API 归一化路径，容错优先于严格，但"容错"指不抛异常，
    **不**指把损坏输入当成基金（否则会对本该收税的单子免税）。
    """
    raw = str(symbol).strip()
    parts = raw.split(".")
    if len(parts) > 2 or (len(parts) == 2 and not parts[1]):
        return False
    code = parts[0]
    if len(code) != 6 or not code.isdigit():
        return False
    return code.startswith(ETF_PREFIXES)


# 印花税：股票卖出单边征收。2023-08-28 起由 1‰ 减半至 0.5‰。
# 过户费：2022-04-29 起沪深统一 0.01‰、双边收取；场内基金不收。
# 数字（STAMP_DUTY_STOCK_RATE / STAMP_DUTY_STOCK_RATE_LEGACY / STAMP_DUTY_CUT_DATE /
# TRANSFER_FEE_RATE）已上移到 domain.trading_rules，见文件头 import。


def stamp_duty_rate(instrument_type: str, direction: str,
                    on_date: date | datetime | None = None) -> float:
    """印花税规则：股票卖出计税，场内基金（instrument_type="etf"）与其他为 0。

    Args:
        instrument_type: ``"stock"`` / ``"etf"``（其他值视为非股票）。
        direction: ``"sell"`` / ``"buy"``（大小写不敏感）。
        on_date: 成交日，接受 ``datetime.date`` **或** ``datetime.datetime``
            （含其子类 ``pandas.Timestamp`` —— 回测面板的日期索引即为
            Timestamp）；函数内部统一归一化为 ``date`` 后再与
            :data:`STAMP_DUTY_CUT_DATE` 比较。给出时按**历史分段**
            （2023-08-28 前 1‰、之后 0.5‰）；为 ``None`` 时用**当前**税率
            0.5‰ —— 保持既有调用方的行为不变。

    历史分段的意义（审计 S1/T2）：项目行情覆盖 2016 年起，2023-08-28 之前
    长达 7 年的回测此前一律按 5‰… 按 0.5‰ 计，**把真实成本低估了一半**。

    归一化的意义（线上缺陷）：面板索引是 ``pd.Timestamp``，此前直接与
    ``date`` 常量比较会抛 ``TypeError: Cannot compare Timestamp with
    datetime.date`` ⇒ 每次卖出都崩。本模块是领域层，**不**引入 pandas 依赖，
    故利用 "``Timestamp`` 是 ``datetime`` 子类" 这一事实做归一化。
    """
    if instrument_type != "stock" or direction.lower() != "sell":
        return 0.0
    # 归一化：Timestamp/datetime -> 纯 date，再与 date 常量比较。
    if on_date is not None and isinstance(on_date, datetime):
        on_date = on_date.date()
    if on_date is not None and on_date < STAMP_DUTY_CUT_DATE:
        return STAMP_DUTY_STOCK_RATE_LEGACY
    return STAMP_DUTY_STOCK_RATE


def transfer_fee_rate(instrument_type: str) -> float:
    """过户费：沪深股票双边 0.01‰（2022-04-29 起统一），场内基金为 0。

    ⚠️ 未建模 2022-04-29 之前的沪深差异（沪 0.02‰ / 深免）与 2015 年之前的
    分段：本项目无该区间的费率权威复核，凭空写历史费率只会制造"精确的错误"。
    如后续回测需要覆盖 2022 年前的成本，应先补一份可引用的费率演进依据。
    """
    return TRANSFER_FEE_RATE if instrument_type == "stock" else 0.0


def effective_stamp_duty(symbol: str, on_date: date | datetime | None = None,
                         override: float | None = None) -> float:
    """本笔卖出适用的印花税率（**供计费路径统一调用**）。

    优先级：
        1. 场内基金 ⇒ 0（免征，任何 override 都不覆盖这一法定豁免）；
        2. ``override`` 显式给出 ⇒ 原样使用（尊重调用方/DB 里配置的口径）；
        3. 否则 ⇒ 按 ``on_date`` 取法定分段税率。

    ``on_date`` 接受 ``datetime.date`` 或 ``datetime.datetime``（含
    ``pandas.Timestamp``）；类型归一化在 :func:`stamp_duty_rate` 内完成。
    """
    if is_etf_symbol(symbol):
        return 0.0
    if override is not None:
        return float(override)
    return stamp_duty_rate("stock", "sell", on_date)


def effective_transfer_fee(symbol: str) -> float:
    """本笔成交适用的过户费率（股票双边，场内基金 0）。"""
    return transfer_fee_rate("etf" if is_etf_symbol(symbol) else "stock")
