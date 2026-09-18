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
