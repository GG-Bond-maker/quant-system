"""API 查询参数的**语义**校验（形状合法 ≠ 日历合法）。

审计 P1-35（B7a-05）根因：日期参数普遍只用
``Query(pattern=r"^\\d{8}$")`` / ``^\\d{4}-\\d{2}-\\d{2}$`` 校验**形状**，
于是 ``20269999``、``2026-02-30`` 这类"形状对、日历错"的输入会一路穿到
``date(...)`` / ``date.fromisoformat(...)`` 才抛 ``ValueError``，被全局异常
处理器归成 ``code=50000``（未分类系统异常）——**纯参数错误被报成平台故障**，
前端无法区分"我传错了"与"服务挂了"（实测：``kline?start=20269999``、
``overview/daily?date=20269999|20260230``、``screener?date=2026-02-30`` 全为 50000）。

本模块提供**严格**解析：形状或日历非法一律 ``AQPException(ERR_PARAMS=40000)``，
错误消息回显原值与期望格式。放在 ``core`` 而非各端点内联，是为了让"参数错误
= 40000"只有一处实现（R9 错误码治理的同一方向）。
"""
from __future__ import annotations

from datetime import date

from .errors import ERR_PARAMS, AQPException


def parse_iso_date(value: str, *, field: str = "date") -> date:
    """严格解析 ``YYYY-MM-DD``；形状或日历非法都抛 ``ERR_PARAMS(40000)``。

    注意 ``date.fromisoformat`` 本身**也会**拒绝日历非法值（``2026-02-30``
    → ``ValueError``），此前的问题不在解析器，而在调用点**没接住**这个
    ``ValueError``（或把它吞掉后继续用最新数据冒充请求日期）。

    形状也在这里**重复**校验（不允许 ``20260218`` 这种 basic-format，
    也不允许 ``2026-2-1``）：端点上的 ``Query(pattern=...)`` 是第一道闸，
    但作为"错误码=40000"的唯一实现处，它必须自足（直接调用/内部复用时也严格）。
    """
    if (len(value) != 10 or value[4] != "-" or value[7] != "-"
            or not (value[:4] + value[5:7] + value[8:]).isdigit()):
        raise AQPException(
            ERR_PARAMS, f"{field} 不是合法日期：{value!r}（应为 YYYY-MM-DD）")
    try:
        return date.fromisoformat(value)
    except (ValueError, TypeError):
        raise AQPException(
            ERR_PARAMS,
            f"{field} 不是合法日期：{value!r}（应为 YYYY-MM-DD）") from None


def parse_yyyymmdd(value: str, *, field: str = "date") -> date:
    """严格解析 ``YYYYMMDD``；形状或日历非法都抛 ``ERR_PARAMS(40000)``。

    长度必须恰好 8：否则 ``"2026021"`` 会被 ``date(2026, 2, 1)`` 当成
    "2026 年 2 月 1 日"**静默接受**（切片把第 7 位当"日"）。
    """
    if len(value) != 8 or not value.isdigit():
        raise AQPException(
            ERR_PARAMS, f"{field} 不是合法日期：{value!r}（应为 YYYYMMDD）")
    try:
        return date(int(value[:4]), int(value[4:6]), int(value[6:8]))
    except (ValueError, TypeError, IndexError):
        raise AQPException(
            ERR_PARAMS,
            f"{field} 不是合法日期：{value!r}（应为 YYYYMMDD）") from None