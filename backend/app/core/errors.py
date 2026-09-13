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
_SENSITIVE_QUERY_KEYS = {"token", "password", "secret", "key", "authorization"}


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
ERR_PARAMS = 40000            # 请求参数错误
ERR_NOT_FOUND = 40400         # 资源不存在
ERR_UNAUTHORIZED = 40100      # 未授权
ERR_TOKEN_EXPIRED = 40101     # Token 已过期
ERR_INVALID_TOKEN = 40102     # Token 无效（签名/签发者不匹配）
ERR_RATE_LIMITED = 40103      # 登录尝试过于频繁
ERR_PIPELINE_BUSY = 40104     # 管道互斥冲突（sync/pipeline/mirror/training 正在执行）
# ⚠️ 已知缺陷（历史遗留，暂未修正）：ERR_CREDENTIALS 与 ERR_PIPELINE_BUSY 撞码。
# 二者不会在同一调用点出现（登录 vs 流水线互斥），且 tests/test_auth.py 已把
# 40104 固化为登录失败断言，改码属破坏性变更；新增认证错误码一律从 40105 起，
# 切勿再复用 40104。
ERR_CREDENTIALS = 40104       # 用户名或密码错误
ERR_USER_EXISTS = 40105       # 用户名已存在（注册）
ERR_REGISTER_DISABLED = 40106 # 自助注册已关闭（ALLOW_REGISTRATION=false）
ERR_REGISTER_LIMITED = 40107  # 注册过于频繁
ERR_FORBIDDEN = 40300         # 禁止访问

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
        logger.warning(f"validation error: {e.errors()}")
        return JSONResponse(
            status_code=200,
            content=jsonable_encoder(fail(ERR_PARAMS, "请求参数错误", e.errors())),
        )

    @app.exception_handler(Exception)
    async def _exception(request: Request, e: Exception) -> JSONResponse:
        # trace_id 进日志（O-01）：运维可用响应体里的 trace_id 直接 grep 到本次请求
        safe_q = _sanitize_query(request.url.query)
        logger.opt(exception=True).error(
            f"unhandled error tid={current_trace_id() or '-'} "
            f"{request.method} {request.url.path}"
            + (f"?{safe_q}" if safe_q else ""))
        return JSONResponse(
            status_code=200,
            content=jsonable_encoder(
                fail(ERR_SYSTEM, f"系统异常: {e.__class__.__name__}")
            ),
        )
