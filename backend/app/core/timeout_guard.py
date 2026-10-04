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

预算（不变量：两侧必须**同向且留有余量**）
------------------------------------------
默认预算取自 ``Settings.REQUEST_TIMEOUT_SECONDS``（默认 240s）。

不变量有两个方向，**两个都必须守**（2026-10-04 上线前全检补齐第二个方向）：

1. **误杀方向**：若服务端预算**小于**前端超时 ⇒ 前端还在等、服务端已经回 504。
   修法：``_PATH_BUDGETS`` 把这些路径的预算**调大**。
2. **白烧方向**：若服务端预算**远大于**前端超时 ⇒ 用户早就断了，服务端还在烧 worker。
   修法：``_PATH_MAX_BUDGETS`` 把这些路径的预算**压低**到前端超时之下，
   让服务端**先**回结构化 504，而不是让前端自己超时。

> 方向 2 曾经完全没有防线，代价实测很具体：``/api/v1/settings``（前端 60s）等 4 条
> 路径落回 240s 默认预算，真实日志里出现 6 次「用户 60s 就断了、服务端烧满 240s」
> 的 504（详见 ``_PATH_MAX_BUDGETS`` 上方注释）。

🔴 **该不变量已机器校验，不再依赖手工清单**（2026-10-02；2026-10-04 扩展）：
前端超时现集中在 ``frontend/src/api/timeouts.ts``（``TIMEOUT`` 档位 + ``LONG_PATHS``
登记表 + ``CAPPED_PATHS`` 反向登记表），由
``frontend/scripts/check-timeout-consistency.mjs`` 与**本文件的
``_PATH_BUDGETS`` / ``_PATH_MAX_BUDGETS`` + ``Settings.REQUEST_TIMEOUT_SECONDS``
双向比对**，并强制 ``src/api/**`` 不得出现裸超时字面量。闸门已接入 CI
（``npm run timeout:check``）。

> **历史教训**：此前这里维护着一份「全仓前端超时实测清单（``frontend/src/api/*.ts``，
> 2026-09-27 核对）」的**手工快照**。手工快照必然漂移 —— 前端把某路径超时调大、或新增
> 一条长超时，后端不会知道，也没有任何东西会 FAIL。
> **不要再往本文件里抄前端超时值**；改超时请改 ``timeouts.ts``（并在超默认预算时同步
> 登记 ``LONG_PATHS``、在被收紧时同步登记 ``CAPPED_PATHS``），闸门会替你把两侧对齐。

当前需要**显式延长**的路径：``/api/v1/backtest/strategy-run``（前端 600s）、
``/api/v1/ops/dag/rerun``（前端 300s）。
当前需要**显式收紧**的路径：见下方 ``_PATH_MAX_BUDGETS``（逐条与前端
``timeouts.ts::CAPPED_PATHS`` 镜像，2026-10-04 起已扩展至重活端点全集）。

豁免 / 延长 / 收紧清单
---------------------
``_EXEMPT_PATHS``：**完全不加超时**（套固定超时会把长连接定时切断）。
``_PATH_BUDGETS``：**按路径覆盖**默认预算（延长）。
``_PATH_MAX_BUDGETS``：**按路径压低**预算到前端超时之下（收紧）。

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

# 按路径**收紧**预算（精确匹配；优先于 ``_PATH_BUDGETS`` 与默认预算）。
#
# 为什么需要"上限"这张表（2026-10-04 上线前全检 T-1）
# ------------------------------------------------
# 原先只有"延长"语义，于是这 4 条路径都落回 **240s 默认预算**，而它们的前端超时
# 只有 60~120s。实测后果（``backend-run-1003.log`` / ``backend-run-1003-r6.log`` /
# ``backend-run.log`` 共 6 次）：
#
#   [timeout-guard] GET /api/v1/settings            超时（>240s）：已取消处理并回写 HTTP 504
#   [timeout-guard] GET /api/v1/ops/lineage         超时（>240s）×2
#   [timeout-guard] GET /api/v1/datacenter/datasets 超时（>240s）×2
#   [timeout-guard] GET /api/v1/datacenter/mirror/status 超时（>240s）
#
# 即**用户早在 60s 就断了，服务端还白烧 180s 的 worker**（且 ``asyncio.to_thread``
# 里的重算**不可取消**，见 ``compute_pool.py`` 文件头 —— 504 之后 CPU/IO 继续烧）。
# 这比"误杀"更糟：误杀至少用户拿到了结构化错误，而这里用户只拿到网络错误。
#
# 修法：给这些路径一个**低于前端超时**的预算，让它**先**返回带 ``code=50400``
# 的统一信封（前端 ``client.ts`` 能解析出真实错误码，而且不会重试），
# 而不是让前端自己超时。
#
# 取值口径：**前端超时 × 0.8**（留 20% 余量，避免"服务端刚回 504、前端也刚好超时"
# 的竞态；``test_timeout_capped_paths`` 会断言该比例）。
_PATH_MAX_BUDGETS: dict[str, float] = {
    # 前端 ``settings.ts:67`` = TIMEOUT.medium 60s。实测 27% 的请求慢于 5s，
    # 并被并发重活拖到 >240s（与 ``[overview] warm cache done in 96.9s`` 同屏）。
    "/api/v1/settings": 48.0,
    # 前端 ``production.ts:183`` = TIMEOUT.medium 60s。冷路径是全量 parquet
    # footer 扫描（注释实测 32~38s），并发时被拖到 >240s。
    "/api/v1/ops/lineage": 48.0,
    # 前端 ``datacenter.ts:40`` = TIMEOUT.extended 90s。冷扫 3.65 万 footer。
    "/api/v1/datacenter/datasets": 72.0,
    # 前端 ``datacenter.ts:106`` = TIMEOUT.heavy 120s。含 45s 扫描闸门等待 + 冷扫。
    "/api/v1/datacenter/mirror/status": 96.0,
    # ---- P-8a 追加（2026-10-04）：新迁入专用计算池的重活端点 ----
    #
    # 这 4 条与上面 4 条**性质不同**，请勿混为一谈：
    #   * 上面 4 条是**数据中心只读扫描**——慢是因为并发争抢磁盘，属"被拖慢"；
    #   * 下面 4 条是**用户发起的重计算**（回测/寻优/压力测试），耗时是**真实工作量**。
    #
    # 因此下面的收紧是**行为变更**，不是纯修复：若某次回测本身就需要 >96s，原先
    # 前端会在 120s 收到结果，现在服务端会在 96s 先回结构化 504。
    # 取舍依据：① 这些端点此前**零延迟样本**（本机 metrics 里从未出现过调用，
    # 我核查过 aqp_http_request_duration_seconds 无这两个 endpoint 标签）；
    # ② 它们此前落回 240s 默认预算，而前端只等 120/180s ⇒ 一旦变慢就是"用户早断、
    # 服务端白烧 120~60s"，且 4 条端点会同时吃专用池（默认 6 槽）把池占满。
    # 若上线后实测确有 >96s 的合法回测，应**上调这里的值**（而不是删掉）；
    # 上限只要仍 < 前端超时，C6 闸门就继续成立。
    #
    # 前端 ``backtest.ts:66`` = TIMEOUT.heavy 120s ⇒ 96s = 120 × 0.8
    "/api/v1/backtest/run": 96.0,
    # 前端 ``backtest.ts:69`` = TIMEOUT.heavy 120s ⇒ 96s = 120 × 0.8
    "/api/v1/backtest/signal-analysis": 96.0,
    # 前端 ``research.ts:108`` = TIMEOUT.export 180s ⇒ 144s = 180 × 0.8
    "/api/v1/research/optimize": 144.0,
    # 前端 ``research.ts:139`` = TIMEOUT.export 180s ⇒ 144s = 180 × 0.8
    "/api/v1/research/stress-test": 144.0,
    # ---- P1 追加（2026-10-04 上线前全检 T-1 续）：重活端点"白烧方向"补登记 ----
    #
    # 上述 8 条之外，审计还点名一批**重活端点**：它们此前全部落回 240s 默认预算，
    # 而前端超时只有 20~180s ⇒ 用户早断、服务端白烧 60~225s 的 worker
    # （``asyncio.to_thread`` / 计算池里的重算**不可取消**，504 之后 CPU/IO 继续烧）。
    #
    # 逐条**回前端源码核实**真实超时（未照抄审计数字）；每条 = 前端超时 × 0.8，
    # 与前端 ``timeouts.ts::CAPPED_PATHS`` **逐条镜像**（闸门 C6 双向校验）。
    # 路径与 ``api/v1/*.py`` 路由**逐字一致**（含 ``/api/v1`` 前缀）。
    #
    # 前端 ``market.ts:91`` = TIMEOUT.quote 20s ⇒ 16s
    "/api/v1/market/quotes": 16.0,
    # 前端 ``screener.ts:26/42/63`` = TIMEOUT.aggregate 30s ⇒ 24s
    "/api/v1/screener": 24.0,
    "/api/v1/screener/stats/series": 24.0,
    "/api/v1/screener/stocks": 24.0,
    # 前端 ``production.ts:276/282/284`` = TIMEOUT.aggregate 30s ⇒ 24s
    # ⚠️ ``/desk/orders`` 的 GET(30s) 与 POST placeOrder(默认 15s) 共用路径；
    # 上限按较长的 GET 取 24s。POST 是轻量 DB 写入，24s 预算对它无害。
    "/api/v1/desk/fills/run": 24.0,
    "/api/v1/desk/orders": 24.0,
    "/api/v1/desk/account": 24.0,
    # 前端 ``production.ts:298`` = TIMEOUT.export 180s ⇒ 144s
    "/api/v1/desk/attribution": 144.0,
    # 前端 ``research.ts:29`` = TIMEOUT.aggregate 30s ⇒ 24s
    "/api/v1/research/overview": 24.0,
    # 前端 ``production.ts:47`` = TIMEOUT.medium 60s ⇒ 48s
    "/api/v1/studio/mining/start": 48.0,
    # 前端 ``production.ts:56/61/67`` = TIMEOUT.heavy 120s ⇒ 96s
    # ⚠️ ``/studio/factors`` 的 GET(默认 15s) 与 POST saveFactor(120s) 共用路径；
    # 上限按 POST 取 96s，GET 是轻量列表查询，不受影响。
    "/api/v1/studio/alpha-eval": 96.0,
    "/api/v1/studio/factor-report": 96.0,
    "/api/v1/studio/factors": 96.0,
    # 前端 ``monitor.ts:127`` = TIMEOUT.heavy 120s ⇒ 96s
    "/api/v1/studio/nl-to-factor": 96.0,
    # 前端 ``monitor.ts:120`` = TIMEOUT.heavy 120s ⇒ 96s
    "/api/v1/monitor/run": 96.0,
    # 前端 ``monitor.ts:125`` = TIMEOUT.medium 60s ⇒ 48s
    "/api/v1/report/daily/generate": 48.0,
    # 前端 ``portfolio.ts:17`` = TIMEOUT.heavy 120s ⇒ 96s
    "/api/v1/portfolio/backtest": 96.0,
    # 前端 ``production.ts:177`` = TIMEOUT.export 180s ⇒ 144s
    "/api/v1/ops/quality-scan": 144.0,
    # 前端 ``datacenter.ts:74`` = TIMEOUT.extended 90s ⇒ 72s
    "/api/v1/datacenter/quality": 72.0,
    # 前端 ``export.ts:15/27/37``（``download()`` 固定 TIMEOUT.export 180s）⇒ 144s
    "/api/v1/export/screener": 144.0,
    "/api/v1/export/backtest": 144.0,
    "/api/v1/export/strategy-backtest": 144.0,
}


def default_timeout_seconds() -> float:
    """默认预算（秒）。

    独立成函数（而不是在 ``__init__`` 里取一次）有两个原因：
    1. 配置改了（``.env`` / 环境变量）不必重建 app；
    2. 测试能 monkeypatch 成小值（如 0.05s）来做真实超时回归。
    """
    return float(get_settings().REQUEST_TIMEOUT_SECONDS)


def resolve_budget_seconds(path: str) -> float | None:
    """返回 ``path`` 的超时预算（秒）；``None`` 表示**豁免**（不施加任何超时）。

    优先级：**豁免** > **上限（``_PATH_MAX_BUDGETS``）** > **延长（``_PATH_BUDGETS``）**
    > 默认预算。

    上限必须优先于延长：两者理论上不该同时命中（一条路径要么被延长、要么被收紧），
    但真同时写错时，"收紧"是安全方向（宁可先回结构化错误，也不要白烧 worker），
    且 ``test_timeout_capped_paths`` 会直接断言两张表的键**互不相交**。
    """
    if path in _EXEMPT_PATHS:
        return None
    capped = _PATH_MAX_BUDGETS.get(path)
    if capped is not None:
        return capped
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
