"""全局请求超时兜底中间件（**原始 ASGI**）：为"无服务端预算"的重计算端点兜一个上限。

为什么必须是**原始 ASGI 中间件**（与 ``core/panic_guard.py`` 同范式）
------------------------------------------------------------------
本项目**严禁** ``starlette.middleware.base.BaseHTTPMiddleware``（理由见
``core/panic_guard.py`` 文件头：它会吞掉/改写异常堆栈，且与流式响应不兼容）。
本中间件同样只实现 ``__init__(app)`` / ``__call__(scope, receive, send)`` 两个钩子，
不继承任何 Starlette 基类。

缺陷背景（本次改造要解决的问题）
--------------------------------
全仓此前**没有任何全局请求超时兜底**：前端把长任务接口的超时放宽到 60s/120s/180s
甚至 600s，但服务端**没有任何上限** —— 一次慢查询 / 死循环（含 ``asyncio.to_thread``
里的重计算）会**永久占用一个 worker**，并发额度（``COMPUTE_CONCURRENCY``）被吃光后
整个平台不可用。本中间件给"自己没有服务端预算"的端点兜一个上限。

预算（不变量：**服务端预算必须严格大于前端对同一路径的超时**）
------------------------------------------------------------
默认预算取自 ``Settings.REQUEST_TIMEOUT_SECONDS``（默认 240s）。若服务端预算小于前端
超时，就会出现"前端还在等、服务端已经回 504"的**误杀**。
全仓前端超时实测清单（``frontend/src/api/*.ts``，2026-09-27 核对）：
  - 客户端默认 15s；另有 30s/45s/60s/90s/120s/180s 若干
    （导出 ``client.ts::download`` 180s、``ops/quality-scan`` 180s、
     ``research/optimize``+``stress-test`` 180s、``desk/attribution`` 180s 等）；
  - ``/api/v1/ops/dag/rerun`` → **300s**（``api/production.ts:187``）；
  - ``/api/v1/backtest/strategy-run``（带 ``optimize_params``）→ **600s**
    （``api/strategyBacktest.ts:154``）——**这是全仓最长前端超时**。
⇒ 240s 默认值覆盖所有 ≤180s 的路径；上两条必须显式延长（见 ``_PATH_BUDGETS``）。

豁免 / 延长清单
--------------
``_EXEMPT_PATHS``：**完全不加超时**（套固定超时会把长连接定时切断）。
``_PATH_BUDGETS``：**按路径覆盖**默认预算（延长）。

ASGI 协议正确性（本文件最容易写错的地方）
----------------------------------------
必须包装 ``send`` 记录是否已经发出过 ``http.response.start``。超时发生时：
  - **尚未 start** ⇒ 可以回写一个 504 响应；
  - **已经 start**（响应头已在途、正在流式发 body）⇒ **绝不能再发任何 ASGI 消息**
    （既不能补发 ``http.response.start``，也不能补发 ``http.response.body``），
    否则会触发 ``Unexpected ASGI message 'http.response.start', after response start``
    这类协议错误，把整个请求/连接搞崩 —— 只能记日志后 ``return``。
判定放在 ``_send`` 包装器里（见 ``__call__``），并有专门测试覆盖：
``tests/test_timeout_guard.py::test_no_second_response_start_after_timeout``。

为什么这里**用统一信封**、但**破例回 HTTP 504**
--------------------------------------------
本项目业务层约定「HTTP 恒 200，业务错误码在 body 的 ``code`` 字段」（``core/errors.py``）。
本中间件是**基础设施层兜底**，两处偏离都已注明理由：
1. **HTTP 504 而非 200**：超时是传输/资源层面的失败，网关、负载均衡与
   ``HTTP_REQUESTS_TOTAL{status}`` 指标必须能看见它；把它写成 200 会让监控把
   「worker 被卡死」读成「业务成功」。项目已有同类先例：``main.py`` 的
   ``/health/ready`` 未就绪时回 **HTTP 503 + ERR_NOT_READY(50300)**。
2. **body 仍用统一信封**（``fail(...)`` + ``jsonable_encoder``）：与"业务层来不及走
   信封"无关 —— 构造信封是**纯本地操作**，不 await 任何已卡死的业务协程，
   **不引入任何新的挂起风险**。用信封的收益是实打实的：
   - 前端 ``client.ts`` 的响应拦截器对 **HTTP 504 + 信封**会走 ``isApiEnvelope(body)``
     分支，抛出带真实 ``code``/``message`` 的 ``ApiError``，而不是退化成泛化的
     「服务异常（HTTP 504）」；
   - 同一分支还**短路了** ``isRetryableNetworkError`` 对 504 的自动重试 ——
     否则前端会对一个已经把 worker 卡住的 GET 再重放一次，放大故障。
"""
from __future__ import annotations

import asyncio

from fastapi.encoders import jsonable_encoder
from loguru import logger
from starlette.responses import JSONResponse
from starlette.types import ASGIApp, Message, Receive, Scope, Send

from .config import get_settings
from .errors import ERR_REQUEST_TIMEOUT, fail
from .metrics import HTTP_REQUEST_TIMEOUT_TOTAL

# 低基数 endpoint 标签的取法必须与 panic 兜底**完全一致**（同包内共用同一实现，
# 避免第三份逐字复制；见 core/panic_guard.py::_endpoint_template）。
from .panic_guard import _endpoint_template
from .trace import current_trace_id

# ---------------- 预算表（改这里之前先读模块 docstring 的"预算"一节） ----------------

# 完全**豁免**超时的路径（精确匹配）：套固定超时会把长连接定时切断。
_EXEMPT_PATHS: frozenset[str] = frozenset({
    # 通知中心 SSE（``api/v1/notify.py:83-153``）：一次性 ticket 建连后是一条
    # **长连接** —— ``gen()`` 是 ``while True`` + 15s 心跳（``_HEARTBEAT_SECONDS``），
    # 直到客户端主动 close。正常语义就是"永不结束"，加任何固定预算都必然在 N 秒后
    # 误切正常连接（前端 EventSource 会自动重连 → 形成"定时断-重连"风暴）。
    "/api/v1/notify/stream",
})

# 按路径**延长**预算（精确匹配；未命中的用默认预算）。
# 每条都必须 > 前端对同一路径的超时，否则就是误杀（理由见模块 docstring）。
_PATH_BUDGETS: dict[str, float] = {
    # 前端 ``strategyBacktest.ts:154`` 在带 optimize_params 时给 **600s**
    # （寻优 = 网格组合数 × 单次回测耗时，是全仓最长前端超时）。
    # 660s = 600s + 60s 余量；不延长会被 240s 默认值**误杀**。
    "/api/v1/backtest/strategy-run": 660.0,
    # 前端 ``production.ts:187`` 给 **300s**（同步重跑 FULL_STEPS 全量流水线，
    # 含 rebuild_qfq / build_universe / build_cs_mirror，见 ``ops.py:537-568``）。
    # 330s = 300s + 30s 余量。
    "/api/v1/ops/dag/rerun": 330.0,
}


def default_timeout_seconds() -> float:
    """默认预算（秒）。

    独立成函数（而不是在 ``__init__`` 里取一次）有两个原因：
    1. 配置改了（``.env`` / 环境变量）不必重建 app；
    2. 测试能 monkeypatch 成小值（如 0.05s）来做真实超时回归。
    """
    return float(get_settings().REQUEST_TIMEOUT_SECONDS)


def resolve_budget_seconds(path: str) -> float | None:
    """返回 ``path`` 的超时预算（秒）；``None`` 表示**豁免**（不施加任何超时）。"""
    if path in _EXEMPT_PATHS:
        return None
    explicit = _PATH_BUDGETS.get(path)
    if explicit is not None:
        return explicit
    return default_timeout_seconds()


class TimeoutGuardMiddleware:
    """给每个 HTTP 请求套一个有界预算；超时未 start 回 504、已 start 仅留痕。"""

    def __init__(self, app: ASGIApp) -> None:
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        # 仅处理 HTTP；websocket / lifespan **原样透传**。
        # lifespan 尤其不能套超时：启动阶段要等数据库初始化、交易日历预热、后台
        # 调度器起来，套上预算会直接**阻断启动**（asyncio.wait_for 会把 lifespan
        # 协程取消掉，Starlette 视作启动失败）。
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        path = scope.get("path", "")
        budget = resolve_budget_seconds(path)
        if budget is None:
            # 豁免路径（SSE 长连接等）：不加任何超时，原样透传。
            await self.app(scope, receive, send)
            return

        response_started = False

        async def _send(message: Message) -> None:
            """包装 send：记录是否已发出 ``http.response.start``（超时分支的唯一判据）。"""
            nonlocal response_started
            if message["type"] == "http.response.start":
                response_started = True
            await send(message)

        inner = asyncio.ensure_future(self.app(scope, receive, _send))
        try:
            await asyncio.wait_for(inner, timeout=budget)
        except asyncio.TimeoutError:
            # ⚠️ Python 3.11+ ``asyncio.TimeoutError is TimeoutError`` ⇒ 内层应用
            # **自己**抛出的 TimeoutError（如 ``data/parquet_store.py:119/150`` 的写锁
            # 等待超时）会与本中间件的"预算耗尽"**同类**，不能只看异常类型。
            # 用 ``inner.cancelled()`` 区分：
            #   - 预算耗尽 ⇒ wait_for 取消内层 ⇒ inner.cancelled() is True；
            #   - 内层自己抛 TimeoutError ⇒ 任务已完成且未取消 ⇒ 原样上抛，
            #     交既有处理器（保持它的既有语义与错误码，不被误报成 504）。
            if not inner.cancelled():
                raise
            await self._on_timeout(scope, receive, send, response_started, budget)

    async def _on_timeout(self, scope: Scope, receive: Receive, send: Send,
                          response_started: bool, budget: float) -> None:
        """预算耗尽的收尾：已 start 只留痕；未 start 回写 HTTP 504 + 统一信封。"""
        path = scope.get("path", "-")
        method = scope.get("method", "-")
        tid = current_trace_id() or "-"
        phase = "post_start" if response_started else "pre_start"
        try:
            HTTP_REQUEST_TIMEOUT_TOTAL.labels(
                endpoint=_endpoint_template(scope), phase=phase).inc()
        except Exception:  # noqa: BLE001 埋点失败不得阻断兜底响应
            pass

        if response_started:
            # 响应头已经发出（流式 body 在途）：**绝不能再发任何 ASGI 消息**，
            # 否则触发 "Unexpected ASGI message ... after response start"。
            # 只能留痕并返回 —— 客户端会看到被截断的响应（这是不可避免的代价）。
            logger.error(
                f"[timeout-guard] tid={tid} {method} {path} 超时（>{budget:g}s）"
                f"但响应已开始（已发出 http.response.start），无法回写 504：仅留痕")
            return

        logger.error(
            f"[timeout-guard] tid={tid} {method} {path} 超时（>{budget:g}s）："
            f"已取消处理并回写 HTTP 504 / code={ERR_REQUEST_TIMEOUT}")
        response = JSONResponse(
            status_code=504,
            content=jsonable_encoder(fail(
                ERR_REQUEST_TIMEOUT,
                f"服务处理超时（>{budget:g}s），请缩小请求范围或稍后重试",
                {"path": path, "method": method, "budget_seconds": budget},
            )),
        )
        await response(scope, receive, send)
