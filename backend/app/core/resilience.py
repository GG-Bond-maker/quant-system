"""后台长驻循环的 panic 韧性共享工具（panic 收口 D，2026-09-18）。

为什么需要它（与 B 段 ``core/panic_guard.py`` 的分工）
------------------------------------------------
B 段的 ``PanicGuardMiddleware`` 是 **ASGI 中间件**，只能覆盖 **HTTP 请求路径**。
本项目还有一批长驻后台循环（``asyncio.create_task`` 起的协程 / daemon 线程），
它们**不经过 ASGI 中间件栈** ⇒ B 段一个都兜不到。
而 polars 在 ``dtype == pl.Null`` 的列上做**单列** ``sort`` 会抛
``pyo3_runtime.PanicException``（MRO=``[PanicException, BaseException, object]``，
**不是 Exception 子类**）⇒ 这些循环里既有的 ``except Exception`` **全部漏接** ⇒
协程/线程**永久停摆**，且**不留任何日志或信号**（静默失效）。

用法（最小改动，不改既有 ``except Exception`` 的语义）
------------------------------------------------
在既有 ``except Exception`` **之后**追加一个 ``except BaseException``：:

    except Exception as e:              # 既有，保持不动
        logger.warning(...)              # 既有，保持不动
    except BaseException as exc:         # noqa: BLE001
        if is_fatal_base_exception(exc):
            raise                        # 必须放行的绝不兜住
        log_contained("<loop-name>", exc)   # 留痕；随后循环继续下一轮

⚠️ 顺序不可颠倒：``except Exception`` 必须**先于** ``except BaseException``，
否则前者永远不可达（BaseException 更宽泛）。

两条硬边界
----------
1. **必须放行的绝不放行错**：:func:`is_fatal_base_exception` 判定为真的
   （``KeyboardInterrupt`` / ``SystemExit`` / ``GeneratorExit`` /
   ``asyncio.CancelledError``）一律 **raise** —— ``CancelledError`` 是 lifespan
   优雅关闭的机制，兜住会导致进程关不掉；
2. **``finally`` 语义不变**：``except BaseException`` 不会吞掉 ``finally``，
   但兜住后**循环状态可能仍被判定为「成功」** ⇒ 调用方必须自行把终态标脏
   （如 ``sync_service._sync_worker`` 要同时置 ``_sync.error``，否则本轮 panic
   会被记成 SUCCESS —— 那是数据造假，见该处的注释）。
"""
from __future__ import annotations

import asyncio
import traceback

from loguru import logger

from .metrics import LOOP_PANIC_CONTAINED_TOTAL

# 必须**放行**（不兜、原样抛出）的 BaseException 类型。
# - KeyboardInterrupt / SystemExit：进程要退出，不得吞掉；
# - GeneratorExit：生成器被关闭的控制流信号；
# - asyncio.CancelledError：lifespan 优雅关闭 / task.cancel() 的机制，
#   兜住会导致任务永远取消不掉（关停卡死）。
_FATAL_BASE_EXCEPTIONS: tuple[type[BaseException], ...] = (
    KeyboardInterrupt,
    SystemExit,
    GeneratorExit,
    asyncio.CancelledError,
)

# 日志前缀：运维与测试检索的锚点（勿改，测试按它断言日志真的落盘）
LOG_PREFIX = "[resilient-loop]"


def is_fatal_base_exception(exc: BaseException) -> bool:
    """该 ``BaseException`` 是否**必须放行**（不得兜住）。

    Args:
        exc: 被 ``except BaseException`` 捕获到的异常实例。

    Returns:
        ``True`` ⇒ 调用方必须 ``raise``（放行）；``False`` ⇒ 可安全兜住并继续。
    """
    return isinstance(exc, _FATAL_BASE_EXCEPTIONS)


def format_exception_stack(exc: BaseException) -> str:
    """把异常的**完整堆栈**格式化成一个字符串。

    为什么是字符串而不是 ``logger.opt(exception=True)``：本仓
    ``core/logging.py`` 的三个 sink 全部 ``enqueue=True``（L46 / L59 / L72）⇒
    每条 record 都要 **pickle** 才能投递；而 ``PanicException`` **不可 pickle** ⇒
    该 record 会被 handler 丢弃、**整条日志连同堆栈一起丢失**（B 段已实测踩过：
    "PicklingError: Can't pickle <class 'pyo3_runtime.PanicException'>"）。
    故必须先把堆栈 format 成字符串，随 message 一起记。

    Args:
        exc: 任意异常实例。

    Returns:
        形如 ``Traceback (most recent call last): ...`` 的多行字符串（已去尾换行）。
    """
    return "".join(
        traceback.format_exception(type(exc), exc, exc.__traceback__)).rstrip()


def log_contained(name: str, exc: BaseException) -> None:
    """兜住一个 ``BaseException``：记日志 + Prometheus 指标 +1，然后**正常返回**。

    返回即意味着调用方的循环可以进入下一轮（``while True`` 不会因此退出）。

    Args:
        name: 循环名（**低基数**固定字符串，如 ``"evening_routine"``）；
            绝不要塞 URL / 日期 / symbol，否则指标基数爆炸。
        exc: 被兜住的异常实例。
    """
    stack = format_exception_stack(exc)
    # ⚠️ 严禁 logger.opt(exception=True)（见 format_exception_stack 的说明）
    logger.error(f"{LOG_PREFIX} {name} 单轮抛出不可捕获异常"
                 f"（{type(exc).__name__}），已兜住并继续\n{stack}")
    LOOP_PANIC_CONTAINED_TOTAL.labels(loop=name).inc()
