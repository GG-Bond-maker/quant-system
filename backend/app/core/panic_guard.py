"""全局兜底中间件（**原始 ASGI**）：把不可捕获的 ``BaseException`` 转成本项目统一信封错误。

为什么必须是**原始 ASGI 中间件**、且**严禁** ``BaseHTTPMiddleware``：
- polars 在 ``dtype == pl.Null`` 的列上做**单列** ``sort`` 会抛
  ``pyo3_runtime.PanicException``，其 MRO = ``[PanicException, BaseException, object]``
  ——**不是 ``Exception`` 子类**；
- Starlette 的 ``ServerErrorMiddleware``（``middleware/errors.py``）、
  ``ExceptionMiddleware``（``_exception_handler.py``）与 ``BaseHTTPMiddleware``
  （``middleware/base.py``）**均只 ``except Exception``** ⇒ 全部漏接；
- ``app.add_exception_handler(PanicException, …)`` **注册不进去**（Starlette 0.38.6
  ``middleware/exceptions.py`` 断言 ``issubclass(exc_class_or_type, Exception)``，
  启动期即 ``AssertionError``）；
- 于是 panic 一路冒泡到 uvicorn ``except BaseException`` ⇒ **裸 500**
  （``text/plain`` + ``Connection: close``），完全绕过 ``{code,message,data,trace_id,ts}`` 信封。

本中间件在**响应未开始**时，用 ``errors.py`` 既有写法（``fail`` + ``jsonable_encoder``
+ ``JSONResponse(status_code=200)``）返回 ``ERR_PANIC_CONTAINED`` 信封错误；并**全量堆栈
留痕**（``logger.opt(exception=True).error``）+ 打 Prometheus 指标 —— 否则就是把缺陷
**静默成 HTTP 200**（本项目栽过的坑）。

捕获子句顺序（不可乱）：
1. ``except Exception: raise`` —— **先放行**普通异常，交既有 ``errors.py`` 处理器
   （``Exception → 50000`` / ``AQPException → 原码`` / HTTPException / 校验错误），不抢语义；
2. ``except BaseException`` → 先排除并 ``raise`` 放行 ``KeyboardInterrupt`` /
   ``SystemExit`` / ``asyncio.CancelledError`` / ``GeneratorExit``；
3. ``BaseExceptionGroup``（anyio task group）：取**第一个叶子异常**的类型做上述判定；
4. ``response_started`` 已 True（SSE 等流中途）⇒ 只能 ``raise``（无法改写已开始的响应）。
"""
from __future__ import annotations

import asyncio
import traceback

from fastapi.encoders import jsonable_encoder
from loguru import logger
from starlette.responses import JSONResponse
from starlette.types import ASGIApp, Message, Receive, Scope, Send

from .errors import ERR_PANIC_CONTAINED, fail
from .metrics import PANIC_CONTAINED_TOTAL
from .trace import current_trace_id

try:  # Python 3.11+ 内建
    _BASE_EXC_GROUP: type[BaseException] | None = BaseExceptionGroup  # noqa: F821
except NameError:  # pragma: no cover - 3.10 及以下无此内建
    _BASE_EXC_GROUP = None

# 明确**放行**（不兜底）的 BaseException：这些不是「未捕获缺陷」，必须原样上抛。
_PASSTHROUGH: tuple[type[BaseException], ...] = (
    KeyboardInterrupt,
    SystemExit,
    asyncio.CancelledError,
    GeneratorExit,
)


def _leaf_exception(exc: BaseException) -> BaseException:
    """``BaseExceptionGroup`` ⇒ 递归取**第一个叶子异常**；普通异常 ⇒ 返回自身。"""
    while (_BASE_EXC_GROUP is not None
           and isinstance(exc, _BASE_EXC_GROUP)
           and exc.exceptions):  # type: ignore[attr-defined]
        exc = exc.exceptions[0]  # type: ignore[attr-defined]
    return exc


def _endpoint_template(scope: Scope) -> str:
    """低基数 Prometheus endpoint 标签。

    与 ``main.py::_metric_endpoint_template`` 逻辑一致（那边收 ``Request``，这里只有
    ``scope``）：取命中路由的**模板 path**；拿不到（404/框架异常）统一归 ``/unmatched``。
    **绝不**回退到带参数的原始 URL（会打爆 Prometheus 基数）。
    """
    route = scope.get("route")
    template = getattr(route, "path", None)
    return template if isinstance(template, str) and template.startswith("/") else "/unmatched"


class PanicGuardMiddleware:
    """兜底未捕获的 ``BaseException``：响应未开始时回契约内信封错误，并全量留痕。"""

    def __init__(self, app: ASGIApp) -> None:
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        # 仅处理 HTTP；lifespan/websocket 原样透传
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        response_started = False

        async def _send(message: Message) -> None:
            nonlocal response_started
            if message["type"] == "http.response.start":
                response_started = True
            await send(message)

        try:
            await self.app(scope, receive, _send)
        except Exception:
            # ① 普通异常先放行：交既有 errors.py 处理器，不抢语义
            raise
        except BaseException as exc:  # noqa: BLE001 - 兜底即是要接住 BaseException
            # ② 放行非「缺陷」的控制流异常（含 BaseExceptionGroup 的首叶子判定）
            if isinstance(_leaf_exception(exc), _PASSTHROUGH):
                raise
            # ③/④ 响应已开始（SSE 等流中途）：无法改写已发送的响应，只能上抛
            if response_started:
                raise
            self._contain(scope, exc)
            response = JSONResponse(
                status_code=200,
                content=jsonable_encoder(
                    fail(ERR_PANIC_CONTAINED, "服务内部错误，请稍后重试")),
            )
            await response(scope, receive, send)

    @staticmethod
    def _contain(scope: Scope, exc: BaseException) -> None:
        """指标 + **全量堆栈**留痕（不可省，否则就是把缺陷静默成 HTTP 200）。

        ⚠️ 这里**不用** ``logger.opt(exception=True)``：它会把**异常类型对象**塞进
        loguru record，而本仓 ``setup_logging`` 的 3 个 sink 均 ``enqueue=True``
        （需 pickle record 才能投递给队列线程）；``pyo3_runtime.PanicException``
        **不可 pickle** ⇒ 该 record 会被 handler 丢弃（实测
        ``Logging error in Loguru Handler … PicklingError: Can't pickle
        <class 'pyo3_runtime.PanicException'>``），**堆栈反而丢失**。故把全量堆栈
        格式化成**字符串**随 message 一起记，确保能落盘 / 上控制台。
        """
        endpoint = _endpoint_template(scope)
        try:
            PANIC_CONTAINED_TOTAL.labels(endpoint=endpoint).inc()
        except Exception:  # noqa: BLE001 埋点失败不得阻断兜底响应
            pass
        stack = "".join(traceback.format_exception(type(exc), exc, exc.__traceback__))
        logger.error(
            f"[panic-guard] tid={current_trace_id() or '-'} "
            f"{scope.get('method', '-')} {scope.get('path', '-')} "
            f"contained {type(exc).__name__}: {exc}\n{stack}")
