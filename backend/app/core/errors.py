"""
统一响应包装 + 全局异常处理器（AQP）。

所有接口返回结构（HTTP 状态码恒为 200，业务错误通过 code 字段区分）：
    { "code": 0, "message": "ok", "data": ..., "trace_id": "...", "ts": ... }
非 0 code 表示业务错误。
"""
from __future__ import annotations

import time
import uuid
from typing import Any, Generic, TypeVar

from fastapi import FastAPI, Request
from fastapi.encoders import jsonable_encoder
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from loguru import logger
from pydantic import BaseModel, Field
from starlette.exceptions import HTTPException as StarletteHTTPException

from .trace import current_trace_id

T = TypeVar("T")

# query 参数脱敏名单：值打码，防 token/password 泄进日志
_SENSITIVE_QUERY_KEYS = {"token", "ticket", "password", "secret", "key", "authorization"}


def _sanitize_query(query: str) -> str:
    """对 query string 中的敏感参数值打码（保留键名）。"""
    if not query:
        return ""
    parts = []
    for pair in query.split("&"):
        key, _, _val = pair.partition("=")
        parts.append(f"{key}=***"
                     if any(s in key.lower() for s in _SENSITIVE_QUERY_KEYS)
                     else pair)
    return "&".join(parts)


def _jsonable_validation_errors(errors: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """把 ``RequestValidationError.errors()`` 规整为**可 JSON 序列化**的结构。

    背景（2026-09-27 审计，与 portfolio 错误码治理同族）：自定义 validator
    ``raise ValueError(...)`` 时，pydantic 会把该**异常对象**塞进 ``ctx["error"]``。
    若原样交给 ``APIResponse`` 再 ``jsonable_encoder``，pydantic 的 JSON 序列化器
    会抛 ``PydanticSerializationError: Unable to serialize unknown type: ValueError``
    ⇒ 校验处理器**自身崩**，异常逃逸到全局兜底：用户把参数填错（如 start>end）
    却被误报成 ``ERR_SYSTEM(50000)``，并打一条假 ERROR 告警。

    here 只把 ``ctx`` 内的异常对象转成字符串（仅作诊断，不影响 code/message/定位
    信息）；并**剥离 pydantic 的 ``input`` 键**（见下）。

    ⚠️ 2026-09-30 上线前全检（F-11 / F-13）：pydantic v2 会把**违规值本体**放进
    ``input`` —— 且**不做任何截断**。实测两条后果：

    * 超长口令（``LoginRequest(password="x"*200``，超 ``max_length=128``）⇒
      ``errors()`` 里带**完整明文口令**；本函数原样返回后，``errors.py`` 的
      ``RequestValidationError`` 处理器以 **WARNING** 落 ``app.log`` /
      ``app.json.log``（retention 30 days），并**回显进响应体** ⇒ 明文凭据入持久化日志。
    * body 顶层类型不匹配时 ``input`` 保留**整个 body**（实测 1MB 输入 ⇒
      ``errors`` 序列化后 **1,000,215 字符**）⇒ 一次匿名请求即可写约 2MB 日志 +
      回吐约 1MB 响应，且因校验发生在 handler **之前**，**绕过登录限速**。

    故这里只保留可安全外发的键：``type`` / ``loc`` / ``msg`` / ``ctx``。
    排查参数错误所需的信息（错在哪个字段、什么错误、约束是什么）**全部保留**，
    丢掉的只有"用户填的原始值"——它本就是不该进日志的东西。
    """
    _SAFE_KEYS = ("type", "loc", "msg", "ctx")
    out: list[dict[str, Any]] = []
    for err in errors:
        item = {k: v for k, v in err.items() if k in _SAFE_KEYS}
        ctx = item.get("ctx")
        if isinstance(ctx, dict):
            item["ctx"] = {k: (str(v) if isinstance(v, BaseException) else v)
                           for k, v in ctx.items()}
        out.append(item)
    return out


class APIResponse(BaseModel, Generic[T]):
    """统一响应容器：所有路由的 response_model 都应使用它。"""

    code: int = Field(default=0, description="0 表示成功；非 0 业务错误码")
    message: str = Field(default="ok")
    data: T | None = Field(default=None)
    # trace_id 与响应头 X-Trace-Id / 日志里的 trace_id 同源（core/trace.py 的
    # contextvar，由 main.py 中间件绑定）；非请求上下文（如脚本/后台任务直接构造
    # 响应）才退回随机生成。
    trace_id: str = Field(
        default_factory=lambda: current_trace_id() or uuid.uuid4().hex[:12])
    ts: int = Field(default_factory=lambda: int(time.time() * 1000))


def ok(data: Any = None, message: str = "ok") -> APIResponse[Any]:
    """成功返回。"""
    return APIResponse(code=0, message=message, data=data)


def fail(code: int, message: str, data: Any = None) -> APIResponse[Any]:
    """失败返回。注意：HTTP 200 + 业务 code。"""
    return APIResponse(code=code, message=message, data=data)


# ---------------- 业务错误码枚举（约定） ----------------
ERR_SYSTEM = 50000            # 未分类异常
# 未捕获的 BaseException（如 polars Rust panic）已被全局兜底中间件
# （core/panic_guard.py）接住。⚠️ 5xxxx 为**系统级**码段；401xx 为**鉴权**码段
# （40100-40107，前端逐个枚举），勿把系统级码放进鉴权段（相邻空位 40108/40109 亦未占用）。
ERR_PANIC_CONTAINED = 50001
ERR_NOT_READY = 50300         # 服务未就绪（**运维探针专用**：/health/ready 同时回 HTTP 503）
# 全局请求超时兜底（core/timeout_guard.py）：**同时回 HTTP 504**。
# 与 50300 同属「基础设施层」码：业务层契约是 HTTP 恒 200，但传输/资源层面的失败
# 必须让网关与监控看得见（理由见 core/timeout_guard.py 模块 docstring 的
# "为什么这里用统一信封、但破例回 HTTP 504"）。50400 与 HTTP 504 同形，
# 与 50300↔HTTP 503 的既有约定一致。
ERR_REQUEST_TIMEOUT = 50400
ERR_PARAMS = 40000            # 请求参数错误
ERR_NOT_FOUND = 40400         # 资源不存在
ERR_UNAUTHORIZED = 40100      # 未授权
ERR_TOKEN_EXPIRED = 40101     # Token 已过期
ERR_INVALID_TOKEN = 40102     # Token 无效（签名/签发者不匹配）
ERR_RATE_LIMITED = 40103      # 登录尝试或计算资源过于频繁
ERR_CREDENTIALS = 40104       # 用户名或密码错误（兼容既有登录客户端）
ERR_USER_EXISTS = 40105       # 用户名已存在（注册）
ERR_REGISTER_DISABLED = 40106 # 自助注册已关闭（ALLOW_REGISTRATION=false）
ERR_REGISTER_LIMITED = 40107  # 注册过于频繁
ERR_FORBIDDEN = 40300         # 禁止访问
ERR_PIPELINE_BUSY = 40900     # 管道互斥冲突（sync/pipeline/mirror/training 正在执行）

# core.auth 抛 HTTPException(200, detail=<常量>)，此处映射为业务错误码。
# 前端据此区分"未登录 / 过期 / 无权限"，从而决定是否跳转登录页。
_AUTH_DETAIL_CODES: dict[str, int] = {
    "UNAUTHORIZED": ERR_UNAUTHORIZED,
    "TOKEN_EXPIRED": ERR_TOKEN_EXPIRED,
    "INVALID_TOKEN": ERR_INVALID_TOKEN,
    "RATE_LIMITED": ERR_RATE_LIMITED,
    "PIPELINE_BUSY": ERR_PIPELINE_BUSY,
    "FORBIDDEN": ERR_FORBIDDEN,
}
ERR_DATA_SOURCE = 51000       # 外部数据源失败
ERR_DATA_EMPTY = 51001        # 本地无数据

# ---------------- 策略回测专用码（40010-40019） ----------------
# [AQP R9/S14 错误码治理 2026-09-21] 这批码原先以**裸字面量**散落在
# `api/v1/backtest.py` 的 10 处 `AQPException(4001x, ...)`：既不在本表、
# 也不在前端 `types/api.ts` 的 `ERR` 表 ⇒ 前端只能拿到文案、**无法按码分类**。
# 现全部登记为本表常量（值保持不变），并在前端双向登记。
#
# 其中 `40017` 原先被**三种不同语义**共用（折数切分区间过短 / optuna 缺候选 /
# 寻优器抛 ValueError），这正是"码不可分类"的另一种形态（同一码多义）。
# 由于两端此前都未登记、全仓无任何消费方（已用 `rg` 与测试全量核对），
# 此处按语义**拆成 40017/40018/40019** —— 与 §8.3「保留值」建议的唯一偏离，
# 理由：保留多义码会把这个批次要消灭的问题原样留下。
ERR_BT_SYMBOL_NO_DATA = 40010     # 标的无本地行情（数据中心未收录）
ERR_BT_SYMBOL_INSUFFICIENT = 40011  # 标的在区间内数据不足（<30 行）
ERR_BT_RANGE_OUT_OF_DATA = 40012  # 回测区间超出本地数据可用范围
ERR_BT_WINDOW_ORDER = 40013       # 开始时间必须早于结束时间
ERR_BT_MA_ORDER = 40014           # 短均线周期必须小于长均线周期
ERR_BT_STRATEGY_UNKNOWN = 40015   # 未知策略类型
ERR_BT_OPTIMIZE_PARAM = 40016     # 策略不支持请求的寻优参数
ERR_BT_WF_TOO_SHORT = 40017       # walk-forward 折数切分后每段 <20 个交易日
ERR_BT_OPTIMIZE_NO_GRID = 40018   # optuna 寻优缺 optimize_params 候选列表
ERR_BT_OPTIMIZE_FAILED = 40019    # 寻优器失败（参数/依赖问题，ValueError）
ERR_TRAIN = 52000             # 训练失败
ERR_INFER = 52001             # 推理失败
ERR_LLM_UNAVAILABLE = 53000   # LLM 服务不可用 / 未配置
ERR_EXPR_INVALID = 53001      # Alpha 表达式非法（AST 白名单校验拒绝）


class AQPException(Exception):
    """业务异常：message 会原样对外返回，code 为业务错误码。"""

    def __init__(self, code: int, message: str, data: Any = None) -> None:
        self.code = code
        self.message = message
        self.data = data
        super().__init__(message)


class DataSourceUnavailable(AQPException, RuntimeError):
    """外部数据源不可用（重试/换源均耗尽）。

    为什么同时继承 ``RuntimeError``：
      * 数据层历史上一律 ``raise RuntimeError``，调用点存在 ``except RuntimeError``
        （换源降级、调度容错），保持兼容可避免改变既有降级语义；
      * 又因为它是 ``AQPException``，全局 ``_aqp`` 处理器会把它转成
        ``code=ERR_DATA_SOURCE(51000)`` 的统一信封 —— 修复审计 P1-32 / R9：
        此前该异常会一路逃逸，最终落到 ``code=50000`` 未分类系统异常，
        前端无法区分"外部源暂时不可用"与"平台自身故障"。
    """

    def __init__(self, message: str, data: Any = None) -> None:
        super().__init__(ERR_DATA_SOURCE, message, data)


def register_error_handlers(app: FastAPI) -> None:
    """把所有异常统一转换为 APIResponse JSON（HTTP 200）。"""

    @app.exception_handler(StarletteHTTPException)
    async def _http(_: Request, e: StarletteHTTPException) -> JSONResponse:
        """HTTPException -> 统一信封。

        ⚠️ 此前未注册该处理器，而 ``core.auth`` 全部用
        ``HTTPException(status_code=200, detail="UNAUTHORIZED")`` 表达认证失败，
        于是这些错误绕过统一包装，直接吐出裸 ``{"detail": "..."}``：
          - 前端 ``isApiEnvelope`` 判定失败（无 code 字段），既抛不出 ApiError，
            也拿不到错误码 —— 登录失败、Token 过期全部被静默吞掉；
          - 前端因此无法识别 401 去跳转登录页，整套 RBAC 形同虚设。
        现在按 detail 常量映射为业务错误码，前端即可正确区分处理。
        """
        detail = str(e.detail) if isinstance(e.detail, str) else ""
        code = _AUTH_DETAIL_CODES.get(detail)
        if code is None:
            # 分流：401/403 -> 未授权；404 -> 资源不存在；其余 4xx（405 方法不允许、
            # 406/415 等）属于「客户端请求本身有问题」，统一归 ERR_PARAMS 参数/方法
            # 错误码；仅 5xx 才是未分类系统异常。此前 4xx 全部落到 ERR_SYSTEM(50000)，
            # 把 405 Method Not Allowed 之类错报为系统异常，掩盖真实语义。
            code = (ERR_UNAUTHORIZED if e.status_code in (401, 403)
                    else ERR_NOT_FOUND if e.status_code == 404
                    else ERR_PARAMS if 400 <= e.status_code < 500
                    else ERR_SYSTEM)
        message = detail or f"HTTP {e.status_code}"
        return JSONResponse(status_code=200,
                            content=jsonable_encoder(fail(code, message)))

    @app.exception_handler(AQPException)
    async def _aqp(_: Request, e: AQPException) -> JSONResponse:
        logger.warning(f"AQPException code={e.code} msg={e.message}")
        return JSONResponse(
            status_code=200,
            content=jsonable_encoder(fail(e.code, e.message, e.data)),
        )

    @app.exception_handler(RequestValidationError)
    async def _validation(_: Request, e: RequestValidationError) -> JSONResponse:
        # 参数校验失败属**客户端错误**：日志级别用 WARNING（不是 ERROR），
        # 且必须保证处理器自身不抛异常（见 _jsonable_validation_errors 的背景），
        # 否则会被全局兜底误报成 ERR_SYSTEM(50000) 并污染 ERROR 告警。
        errors = _jsonable_validation_errors(list(e.errors()))
        logger.warning(f"validation error: {errors}")
        return JSONResponse(
            status_code=200,
            content=jsonable_encoder(fail(ERR_PARAMS, "请求参数错误", errors)),
        )

    @app.exception_handler(Exception)
    async def _exception(request: Request, e: Exception) -> JSONResponse:
        # trace_id 进日志（O-01）：运维可用响应体里的 trace_id 直接 grep 到本次请求
        safe_q = _sanitize_query(request.url.query)
        logger.opt(exception=True).error(
            f"unhandled error tid={current_trace_id() or '-'} "
            f"{request.method} {request.url.path}"
            + (f"?{safe_q}" if safe_q else ""))
        # 内部异常类型与堆栈只写服务端日志；响应不得泄露 Python 类名或实现细节。
        return JSONResponse(
            status_code=200,
            content=jsonable_encoder(
                fail(ERR_SYSTEM, "系统暂不可用，请稍后重试")
            ),
        )
