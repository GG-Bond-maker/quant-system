"""请求级 trace_id 传播（contextvars）。

背景（2026-09-12 架构审查 O-01）：``APIResponse.trace_id`` 原先只在响应体里
``uuid4`` 随机生成，从不进日志——用户报 trace_id 时运维在日志里 grep 不到任何东西。

本模块是唯一事实源：
- 中间件（main.py）生成/复用 trace_id 并绑定到 contextvar；
- ``APIResponse.trace_id`` 从同一 contextvar 取值（响应体与响应头一致）；
- loguru patcher（core/logging.py）把 contextvar 注入每条日志记录。

限制：``contextvars`` 不跨线程传播，``asyncio.to_thread`` 里产生的日志拿不到
trace_id（显示占位符 ``-``）。如需覆盖线程池路径，须在提交线程时显式传递
``contextvars.copy_context()``，成本较高，暂不实现（已知限制，见审查报告）。
"""
from __future__ import annotations

import uuid
from contextvars import ContextVar

_trace_id: ContextVar[str | None] = ContextVar("aqp_trace_id", default=None)


def current_trace_id() -> str | None:
    """当前请求的 trace_id；非请求上下文返回 ``None``。"""
    return _trace_id.get()


def set_trace_id(value: str | None) -> None:
    """绑定 / 清理 trace_id（中间件在请求结束时传 ``None`` 清理，防串号）。"""
    _trace_id.set(value)


def new_trace_id() -> str:
    """生成 12 位 hex trace_id（与 APIResponse 原口径一致）。"""
    return uuid.uuid4().hex[:12]
