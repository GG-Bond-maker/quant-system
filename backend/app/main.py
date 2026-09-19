"""
FastAPI 应用入口（AQP）。

- lifespan：初始化日志 / 数据库（WAL）/ 预加载交易日历；
- CORS：允许前端开发地址跨域；
- 统一响应：register_error_handlers 把所有异常转为 {code, message, data, ...}（HTTP 200）；
- 计时中间件：为每个响应附加 X-Response-Time-MS 头。

运行：
    uvicorn app.main:app --reload --host 0.0.0.0 --port 8000
"""
from __future__ import annotations

from contextlib import asynccontextmanager
from time import perf_counter
import os
import sqlite3

import asyncio

from fastapi import Depends, FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from loguru import logger

from .api.v1.router import v1_router
from .core.config import get_settings
from .core.errors import APIResponse, ok, register_error_handlers
from .core.logging import setup_logging
from .core.panic_guard import PanicGuardMiddleware
from .core.resilience import is_fatal_base_exception, log_contained
from .core.trace import new_trace_id, set_trace_id
from .data.calendar_store import refresh_calendar_cache
from .db.init_db import init_database


@asynccontextmanager
async def lifespan(app: FastAPI):
    """应用生命周期：启动时初始化资源，关闭时清理。"""
    s = get_settings()
    setup_logging(s)
    logger.info(f"starting {s.APP_NAME} env={s.ENV}")
    await init_database()
    # 显式预加载交易日历到进程内存（data 层加载 -> domain 纯函数消费）
    await refresh_calendar_cache()
    # autoSync 后台调度（替代前端 setInterval，浏览器关闭后仍能触发）
    # Task 16：同步状态机/autoSync 调度已下沉 services，lifespan 直接消费服务层
    from .services.sync_service import auto_sync_scheduler, restore_sync_state
    # market/overview 后台预热 + 定时刷新（TTL 300s 内主动续期，
    # 避免无 Redis 时过期后的首个用户请求扛 ~48s 全量重建）
    from .api.v1.market import warm_overview_cache
    # P2-15：SSE 事件总线绑定主循环（worker 线程通知依赖它跨线程投递）
    from .core.events import bind_loop
    bind_loop(asyncio.get_running_loop())

    # P2-14：恢复上次中断同步任务的断点续传记录（SQLite 读，放线程池）
    await asyncio.to_thread(restore_sync_state)

    # 启动时回收「进程在重训期间退出」遗留的陈旧 running 态：_retrain_worker 的
    # finally 只在函数返回/异常传播时执行，**进程被杀死不会执行任何 finally** ⇒ 上一轮
    # 重训线程随进程退出被杀死会永久留下 status="running"，令 maybe_auto_retrain 的并发
    # 守卫静默阻断自动重训 7200s。启动阶段本进程不可能有在飞的重训线程，故无条件回收。
    # kv_get/kv_set 是同步 sqlite3 ⇒ 必须 to_thread（避免阻塞事件循环）；回收失败绝不影响启动。
    try:
        from .ml.monitor import reclaim_stale_retrain
        await asyncio.to_thread(reclaim_stale_retrain)
    except Exception as e:  # noqa: BLE001 启动绝不因回收失败而中断
        logger.warning(f"[monitor] reclaim stale retrain failed: {e!r}")

    # 晚间例行调度（Phase 0）：build_features→infer→监控→AI 日报（17:30，当日幂等）
    from .jobs.evening_routine import evening_routine_scheduler, startup_catchup
    routine_task = asyncio.create_task(evening_routine_scheduler())
    # 启动补跑：当日尚无监控快照/日报时立即补一次（后台，不阻塞启动）
    catchup_task = asyncio.create_task(startup_catchup())

    async def _overview_warmer() -> None:
        while True:
            # [AQP panic 收口 D] 原实现**完全没有** try/except ⇒ 连普通 Exception
            # 都会终结预热协程（静默停摆，缓存到期后首个用户请求要扛 ~48s 全量重建）。
            # 此处一并兜住：普通异常记 warning，BaseException（如 polars panic）
            # 走 resilience 留痕；必须放行的（CancelledError 等）仍原样抛出。
            try:
                await warm_overview_cache()
            except Exception as e:  # noqa: BLE001 单轮预热失败不终止循环
                logger.warning(f"[overview] warmer round failed: {e!r}")
            except BaseException as exc:  # noqa: BLE001
                if is_fatal_base_exception(exc):
                    raise
                log_contained("overview_warmer", exc)
            await asyncio.sleep(240)  # TTL 300s，提前 60s 续期

    # §4.1 预警调度：盘中每 30s / 盘后每小时评估 alert_rules（Sprint2）
    from .api.v1.alerts import alert_scheduler
    alert_task = asyncio.create_task(alert_scheduler())

    sched_task = asyncio.create_task(auto_sync_scheduler())
    # 预热可经 WARM_OVERVIEW_ON_STARTUP 关闭：测试环境下每次 TestClient 启动
    # 都会触发一次 48s+ 的真实外部聚合（to_thread 不可取消，会拖死关闭流程）
    warm_task = (asyncio.create_task(_overview_warmer())
                 if s.WARM_OVERVIEW_ON_STARTUP else None)
    logger.info("startup done")
    yield
    # 有界等待后台任务退出：asyncio.wait 带超时，任务拒绝退出也不阻塞关闭
    bg = [alert_task, sched_task, routine_task, catchup_task]
    if warm_task is not None:
        bg.append(warm_task)
    for t in bg:
        t.cancel()
    await asyncio.wait(bg, timeout=10)
    logger.info("shutdown")


app = FastAPI(
    title="AQP API",
    version="1.0.0",
    lifespan=lifespan,
    docs_url="/docs",
    redoc_url="/redoc",
)

# ---- 全局兜底中间件（**必须早于 CORS 注册**）----
# 说明：add_middleware 是 insert(0)（**后加的在更外层**）。先注册守卫、后注册 CORS
# ⇒ user_middleware=[timing, CORS, guard] ⇒ 最外到内为
# ServerErrorMiddleware → timing → **CORS** → **guard** → ExceptionMiddleware → router，
# 守卫落在 CORS **内侧** ⇒ panic 兜底响应仍带 CORS 头。
# （core/panic_guard.py 为**纯 ASGI**，用于兜底 polars Rust panic 等 BaseException——
#  Starlette 的 Exception 中间件接不住，且 add_exception_handler 注册不进 BaseException。）
app.add_middleware(PanicGuardMiddleware)

# ---- CORS ----
_s = get_settings()
app.add_middleware(
    CORSMiddleware,
    allow_origins=_s.cors_origins_list,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


def _metric_endpoint_template(request: Request) -> str:
    """返回低基数 Prometheus endpoint 标签。

    路由命中后 Starlette 将 ``APIRoute`` 写入 scope；其 ``path`` 是含参数占位符
    的模板。404/框架异常等没有路由对象时统一归入固定值，绝不能回退到原始 URL。
    """
    route = request.scope.get("route")
    template = getattr(route, "path", None)
    return template if isinstance(template, str) and template.startswith("/") else "/unmatched"


# ---- 计时 + trace 中间件 ----
@app.middleware("http")
async def add_response_time_header(request: Request, call_next):
    """为每个响应附加耗时头（业务响应包装由 errors.py 的统一处理器负责）。

    trace_id（O-01）：优先复用上游 ``X-Trace-Id``（跨服务串联），否则生成。
    绑定到 contextvar 后，APIResponse 与 loguru 每条日志都取同一个 ID，
    保证「响应体 trace_id == 响应头 == 日志 trace_id」可 grep。
    """
    start = perf_counter()
    trace = request.headers.get("X-Trace-Id") or new_trace_id()
    set_trace_id(trace)
    try:
        response = await call_next(request)
    finally:
        # 请求结束清理，防止协程复用导致的串号
        set_trace_id(None)
    cost_ms = int((perf_counter() - start) * 1000)
    response.headers["X-Response-Time-MS"] = str(cost_ms)
    response.headers["X-Trace-Id"] = trace
    endpoint = _metric_endpoint_template(request)
    HTTP_REQUESTS_TOTAL.labels(
        method=request.method, endpoint=endpoint, status=response.status_code
    ).inc()
    HTTP_REQUEST_DURATION.labels(
        method=request.method, endpoint=endpoint
    ).observe(cost_ms / 1000.0)
    return response


register_error_handlers(app)


# ---------- 根路由 ----------
@app.get("/health", response_model=APIResponse[dict], tags=["root"])
async def health() -> APIResponse[dict]:
    """健康检查：同时返回 Redis 情况（Redis 不通不影响 HTTP 200）。"""
    s = get_settings()
    from .cache.redis_client import RedisClient

    redis_ok = await RedisClient.ping()
    return ok({
        "app": s.APP_NAME,
        "env": s.ENV,
        "db": "ok",  # 若 init_database 失败则启动期直接退出，这里必为 ok
        "redis": "ok" if redis_ok else "degraded",
    })


@app.get("/", response_model=APIResponse[str], tags=["root"])
async def index() -> APIResponse[str]:
    """根路由：提示 Swagger 入口。"""
    return ok("AQP API is running. Visit /docs for swagger.")


# ---------- 挂载 v1 业务路由 ----------
app.include_router(v1_router, prefix="/api/v1")


# ---------- P3-3 Metrics ----------
from .core.metrics import (
    HTTP_REQUEST_DURATION,
    HTTP_REQUESTS_TOTAL,
    metrics_response,
)


_metrics_bearer = HTTPBearer(auto_error=False)


@app.get("/metrics", include_in_schema=False)
async def prometheus_metrics(
    credentials: HTTPAuthorizationCredentials | None = Depends(_metrics_bearer),
):
    """Prometheus exposition format；生产默认要求既有 Bearer 认证。"""
    settings = get_settings()
    if settings.metrics_require_auth:
        from .core.auth import require_auth

        require_auth(credentials)
    return metrics_response()


@app.get("/health/ready", response_model=APIResponse[dict], tags=["root"])
async def health_ready() -> APIResponse[dict]:
    """Readiness: 检查 SQLite、数据目录、Redis 和核心快照。"""
    s = get_settings()
    checks: dict[str, str] = {}
    try:
        with sqlite3.connect(s.SQLITE_PATH, timeout=2) as conn:
            # BEGIN IMMEDIATE verifies that the service can acquire a write
            # lock; a read-only SELECT would miss a broken persistence mount.
            conn.execute("BEGIN IMMEDIATE")
            conn.execute("SELECT 1").fetchone()
            conn.rollback()
        checks["sqlite"] = "ok"
    except (OSError, sqlite3.Error):
        checks["sqlite"] = "failed"

    data_root = s.DATA_ROOT
    checks["data_root"] = (
        "ok" if data_root.is_dir() and os.access(data_root, os.R_OK | os.W_OK)
        else "failed"
    )
    if not s.REDIS_ENABLED:
        checks["redis"] = "disabled"
    else:
        from .cache.redis_client import RedisClient
        checks["redis"] = "ok" if await RedisClient.ping() else "degraded"

    # 快照缺失不阻断进程接流量，但明确告诉编排系统当前是降级态。
    # Only inspect the known core dataset roots and stop at the first file;
    # readiness probes must not rescan the whole data lake on every call.
    try:
        has_snapshot = (data_root / ".manifest.json").is_file()
        if not has_snapshot:
            for dataset_name in ("daily_bar", "daily_bar_hfq", "features", "predictions"):
                dataset_root = data_root / dataset_name
                if dataset_root.is_dir() and next(dataset_root.rglob("*.parquet"), None) is not None:
                    has_snapshot = True
                    break
    except OSError:
        has_snapshot = False
    checks["core_snapshot"] = "ok" if has_snapshot else "degraded"
    mandatory_ok = checks["sqlite"] == "ok" and checks["data_root"] == "ok"
    return ok({"status": "ready" if mandatory_ok else "not_ready", "checks": checks})


@app.get("/health/live", response_model=APIResponse[dict], tags=["root"])
async def health_live() -> APIResponse[dict]:
    """Liveness: 进程存活即 live。"""
    return ok({"status": "live"})
