"""全局请求体大小限制中间件（**原始 ASGI**）：为超大请求体兜一个硬上限。

为什么必须是**原始 ASGI 中间件**（与 ``core/panic_guard.py`` / ``core/timeout_guard.py`` 同范式）
------------------------------------------------------------------------------------------------
本项目**严禁** ``starlette.middleware.base.BaseHTTPMiddleware``（理由见
``core/panic_guard.py`` 文件头：它会吞掉/改写异常堆栈，且与流式响应不兼容）。
本中间件同样只实现 ``__init__(app)`` / ``__call__(scope, receive, send)`` 两个钩子，
不继承任何 Starlette 基类。

缺陷背景（本次改造要解决的问题）
--------------------------------
全仓此前**没有任何应用层请求体大小限制**：``nginx.conf`` 虽有 ``client_max_body_size 1m``，
但那只是网关层；直连后端（开发环境 / 网关被绕过 / 未来换部署）即可让匿名端点
（如 ``/api/v1/auth/login``）投递任意大小的 body，在 JSON 解析前就把内存吃满。
本中间件在**进入业务路由之前**按字节数拒收超大 body，回 HTTP 413 + 统一信封。

两种情形分别处理
----------------
1. **有 ``content-length``**：直接比对，超限即回 413（O(1)，不读 body）；
   未超限则**原样透传**（零额外开销）。
2. **无 ``content-length``**（``Transfer-Encoding: chunked`` / HTTP 流）：
   包装 ``receive``，累计已读字节数；一旦超限即回 413，并让应用看到
   ``http.disconnect`` 以停止继续读 body。**不缓冲整个 body**（避免为大 body
   额外分配一份内存），因此不会破坏正常的流式请求处理。

为什么这里**用统一信封**、但**破例回 HTTP 413**
--------------------------------------------
与本项目业务层「HTTP 恒 200，业务错误码在 body 的 ``code`` 字段」的约定一致的处理见
``core/errors.py::ERR_PAYLOAD_TOO_LARGE`` 注释与 ``core/timeout_guard.py`` 模块 docstring：
超限属传输/资源层面的失败，网关、负载均衡与 ``HTTP_REQUESTS_TOTAL{status}`` 指标必须能看见，
把它写成 200 会让监控读成「业务成功」。项目已有同类先例（``/health/ready`` 回 503、
timeout_guard 回 504）。body 仍用统一信封（``fail(...)`` + ``jsonable_encoder``），
前端 ``client.ts`` 能解析出真实 ``code``/``message``。

挂载位置（见 ``main.py``）：在 **CORS 内侧、TimeoutGuard 外侧**。
  * 必须在 CORS 内侧：413 响应才会带上 ``access-control-allow-origin``，浏览器端
    才能读到错误体（否则只是一个不透明的网络错误）；
  * 在 TimeoutGuard 外侧：``content-length`` 超限是 O(1) 拒绝，无需先为它创建
    TimeoutGuard 的预算任务；把最便宜的拦截放在更外层。
    代价：chunked 慢速灌 body 不受请求超时约束 —— 由前置 nginx 的
    ``client_max_body_size`` / ``client_body_timeout`` 兜底（见 ``frontend/nginx.conf``）。
"""
from __future__ import annotations

from fastapi.encoders import jsonable_encoder
from loguru import logger
from starlette.requests import ClientDisconnect
from starlette.responses import JSONResponse
from starlette.types import ASGIApp, Message, Receive, Scope, Send

from .errors import ERR_PAYLOAD_TOO_LARGE, fail
from .trace import current_trace_id

# 8MB：全仓唯一可能较大的合法请求体是 ``/api/v1/datacenter/text/import`` 的公告文档
# （``docs`` ≤ 2000 条，researcher 级），而前置 nginx 的 ``client_max_body_size 1m``
# 已把它压到 1MB ⇒ 8MB 既不会误伤合法请求，又给直连场景留足余量。
DEFAULT_MAX_BODY_BYTES = 8 * 1024 * 1024


def _content_length(scope: Scope) -> int | None:
    """从 ASGI ``scope['headers']`` 取 ``content-length``（缺失/非法返回 None）。"""
    for name, value in scope.get("headers", ()):
        if name == b"content-length":
            try:
                return int(value)
            except (TypeError, ValueError):
                return None
    return None


class BodySizeLimitMiddleware:
    """按字节数拒收超大请求体；超限回 HTTP 413 + 统一信封。"""

    def __init__(self, app: ASGIApp, max_body_bytes: int = DEFAULT_MAX_BODY_BYTES) -> None:
        self.app = app
        self.max_body_bytes = max_body_bytes

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        # 仅处理 HTTP；websocket / lifespan 原样透传。
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        limit = self.max_body_bytes
        declared = _content_length(scope)
        if declared is not None:
            if declared > limit:
                # 有 content-length：无需读 body，直接拒。
                await self._reject(scope, receive, send, declared)
                return
            # 未超限：原样透传，零额外开销。
            await self.app(scope, receive, send)
            return

        # 无 content-length（chunked / 流）：包装 receive 累计字节数。
        state = {"received": 0, "aborted": False, "started": False}

        async def _receive() -> Message:
            message = await receive()
            if message["type"] == "http.request":
                state["received"] += len(message.get("body", b"") or b"")
                if state["received"] > limit and not state["aborted"]:
                    state["aborted"] = True
                    # 仅当应用尚未开始响应时才回写 413（否则会触发 ASGI 协议错误）。
                    if not state["started"]:
                        await self._reject(scope, receive, send, state["received"])
                if state["aborted"]:
                    # 让应用看到断开，停止继续读 body；413 已（或即将）回写。
                    return {"type": "http.disconnect"}
            return message

        async def _send(message: Message) -> None:
            if message["type"] == "http.response.start":
                if state["aborted"]:
                    return  # 已自行回 413，丢弃应用响应，避免重复 response.start
                state["started"] = True
            if state["aborted"]:
                return
            await send(message)

        try:
            await self.app(scope, _receive, _send)
        except ClientDisconnect:
            # 应用读到我们伪造的 ``http.disconnect`` 而抛出；413 已回写，无需再处理。
            pass

    async def _reject(self, scope: Scope, receive: Receive, send: Send,
                      received: int) -> None:
        """回写 HTTP 413 + 统一信封（``code=ERR_PAYLOAD_TOO_LARGE``）。"""
        path = scope.get("path", "-")
        method = scope.get("method", "-")
        logger.warning(
            f"[body-limit] tid={current_trace_id() or '-'} {method} {path} "
            f"请求体过大（{received} > {self.max_body_bytes} 字节）："
            f"回写 HTTP 413 / code={ERR_PAYLOAD_TOO_LARGE}")
        response = JSONResponse(
            status_code=413,
            content=jsonable_encoder(fail(
                ERR_PAYLOAD_TOO_LARGE,
                f"请求体过大（上限 {self.max_body_bytes} 字节）",
                {"path": path, "method": method,
                 "max_body_bytes": self.max_body_bytes, "received_bytes": received},
            )),
        )
        await response(scope, receive, send)
