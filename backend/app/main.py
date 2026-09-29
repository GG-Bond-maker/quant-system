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
from pathlib import Path
from time import perf_counter
import os
import socket
import sqlite3
from typing import Any

import asyncio

from fastapi import Depends, FastAPI, Request
from fastapi.encoders import jsonable_encoder
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from starlette.responses import Response
from loguru import logger

from .api.v1.router import v1_router
from .core.config import get_settings
from .core.errors import (APIResponse, ERR_NOT_READY, fail, ok,
                          register_error_handlers)
from .core.logging import setup_logging
from .core.metrics import endpoint_template_from_scope
from .core.panic_guard import PanicGuardMiddleware
from .core.resilience import is_fatal_base_exception, log_contained
from .core.timeout_guard import TimeoutGuardMiddleware
from .core.trace import new_trace_id, set_trace_id
from .data.calendar_store import refresh_calendar_cache
from .db.init_db import init_database


@asynccontextmanager
async def lifespan(app: FastAPI):
    """应用生命周期：启动时初始化资源，关闭时清理。"""
    s = get_settings()
    setup_logging(s)
    logger.info(f"starting {s.APP_NAME} env={s.ENV}")
    # akshare 内部用 requests（**默认无 timeout**）⇒ 对端挂死即让 worker 永久阻塞在
    # socket read 上；而 asyncio.wait_for 到期**只取消外层 await**，该 worker 不可取消、
    # 继续占槽（2026-09-30 全检头号 P0）。更严重的是它会**阻止进程退出**：
    # concurrent.futures 的 atexit 钩子会 join 这些 worker，实测 rc=124
    # ⇒ docker stop 挂到 SIGKILL、滚动更新/重启全部超时。
    # 这是 B7 的**唯一有效修复**（daemon 线程与 pool.shutdown 均实测无效）。
    # 只作用于未显式设置 timeout 的 socket 调用（httpx/自建客户端已各自设超时，不受影响）。
    _socket_timeout = float(s.SOCKET_DEFAULT_TIMEOUT_SECONDS)
    if _socket_timeout > 0:
        socket.setdefaulttimeout(_socket_timeout)
        logger.info(f"socket default timeout = {_socket_timeout}s")
    await init_database()
    # 显式预加载交易日历到进程内存（data 层加载 -> domain 纯函数消费）
    await refresh_calendar_cache()
    # autoSync 后台调度（替代前端 setInterval，浏览器关闭后仍能触发）
    # Task 16：同步状态机/autoSync 调度已下沉 services，lifespan 直接消费服务层
    from .services.sync_service import auto_sync_scheduler, restore_sync_state
    # market/overview 后台预热 + 定时刷新（TTL 300s 内主动续期，
    # 避免无 Redis 时过期后的首个用户请求扛 ~48s 全量重建）
    from .api.v1.market import warm_overview_cache
    # ETF 概览后台预热（同构 market 预热）：冷路径实测 6.22s > 请求预算 4.5s，无预热
    # 时首个用户请求必然降级；preheat 写入请求路径同一 SWR 缓存键 k_etf_overview。
    from .api.v1.etf import warm_etf_overview_cache
    # /ops/lineage 启动预热（2026-09-27 P1）：全量 parquet footer 扫描实测 32~38s，
    # 远超前端 15s 默认超时。冷扫只跑一次挪到后台；之后由 SWR（TTL 300s）在访问时
    # 后台重建，无需常驻定时器，避免对 93% 高水位磁盘持续施加 32s 级 IO。
    from .api.v1.ops import warm_lineage_cache
    # 数据中心统计（datasets/quality/mirror_status）**周期**预热（2026-09-29）：
    # 三者冷扫合计对同一批 parquet 抢 IO，单请求实测 50~74s，远超前端预算 ⇒
    # /datacenter 页「请求超时」红条。用户截图是在后端启动约 10h 后拍的（1800s 统计
    # TTL 早已过期）⇒ 一次性启动预热无用，只有周期续期能让统计长期保温。
    from .api.v1.datacenter import warm_datacenter_stats
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

    # 启动时回收遗留的僵尸后台任务：background_tasks.status='running' 经进程退出后
    # **永不自愈**（claim_task 只按已知 task_id 调用、update_task 不续租），会经
    # GET /sync/tasks/{id} 一直暴露。同样 to_thread（task_store._conn() 是同步 sqlite3）
    # + **独立 try/except**：两个回收互不影响——任一失败都不阻断另一个、更不影响启动。
    try:
        from .services.task_store import reap_stale_running_tasks
        await asyncio.to_thread(reap_stale_running_tasks)
    except Exception as e:  # noqa: BLE001 启动绝不因回收失败而中断
        logger.warning(f"[tasks] reap stale running tasks failed: {e!r}")

    # 单实例护栅：pipeline_slot 是**进程级** threading.Lock、上面的启动残留回收也假设
    # 单实例；多 worker 部署会让"互斥"与"回收"双双静默失效（回收还会误杀其它 worker
    # 在飞的合法任务）。这里**只告警、不阻断启动**，同样 to_thread + 独立 try/except。
    try:
        from .core.pipeline_lock import warn_if_multi_worker
        await asyncio.to_thread(warn_if_multi_worker)
    except Exception as e:  # noqa: BLE001 启动绝不因护栅失败而中断
        logger.warning(f"[pipeline_lock] multi-worker guard failed: {e!r}")

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

    async def _etf_overview_warmer() -> None:
        # 与 _overview_warmer **对称**的一路：ETF 概览冷路径实测 6.22s > 请求预算
        # 4.5s，无预热时**首个**用户请求必然降级（前端黄条）。此处把这段慢构建挪到
        # 后台，并把结果写入请求路径同一 SWR 缓存键（k_etf_overview）。
        # 独立成 task（而非并入上面的循环）以便两路预热**互不影响**——任一路失败
        # 都不拖累另一路。
        # [AQP panic 收口 D] 同 _overview_warmer：普通异常记 warning；BaseException
        # （如 polars panic）走 resilience 留痕；必须放行的（CancelledError 等）仍抛出。
        while True:
            try:
                await warm_etf_overview_cache()
            except Exception as e:  # noqa: BLE001 单轮预热失败不终止循环
                logger.warning(f"[etf] warmer round failed: {e!r}")
            except BaseException as exc:  # noqa: BLE001
                if is_fatal_base_exception(exc):
                    raise
                log_contained("etf_overview_warmer", exc)
            # 主键 TTL=_ETF_CACHE_TTL(300s)，间隔 240s（提前约 60s 续期，阈值见
            # etf._ETF_WARM_RENEW_THRESHOLD_SECONDS=120）。
            await asyncio.sleep(240)

    async def _datacenter_stats_warmer() -> None:
        # 数据中心统计（datasets/quality/mirror_status/storage_stats）**周期**预热：
        # 冷扫合计对同一批 parquet 抢磁盘 IO，单请求实测 50~74s，远超前端预算 ⇒
        # /datacenter 页「请求超时」红条。用户截图是后端启动约 10h 后拍的，1800s 统计
        # TTL 早已过期 ⇒ 一次性预热无用；只有周期续期能让统计长期保温，使首个页面
        # 加载永不触发并发冷风暴。
        # 间隔 1500s < 统计 TTL 1800s（提前 300s 续期）。
        # [AQP panic 收口 D] 同 _overview_warmer：普通异常记 warning；BaseException
        # （如 polars panic）走 resilience 留痕；必须放行的（CancelledError 等）仍抛出。
        while True:
            try:
                await warm_datacenter_stats()
            except Exception as e:  # noqa: BLE001 单轮预热失败不终止循环
                logger.warning(f"[datacenter] warmer round failed: {e!r}")
            except BaseException as exc:  # noqa: BLE001
                if is_fatal_base_exception(exc):
                    raise
                log_contained("datacenter_stats_warmer", exc)
            await asyncio.sleep(1500)

    # §4.1 预警调度：盘中每 30s / 盘后每小时评估 alert_rules（Sprint2）
    from .api.v1.alerts import alert_scheduler
    alert_task = asyncio.create_task(alert_scheduler())

    sched_task = asyncio.create_task(auto_sync_scheduler())
    # 预热可经 WARM_OVERVIEW_ON_STARTUP 关闭：测试环境下每次 TestClient 启动
    # 都会触发一次 48s+ 的真实外部聚合（to_thread 不可取消，会拖死关闭流程）
    warm_task = (asyncio.create_task(_overview_warmer())
                 if s.WARM_OVERVIEW_ON_STARTUP else None)
    # ETF 概览预热复用同一开关：测试环境（WARM_OVERVIEW_ON_STARTUP=false）下同样关闭，
    # 避免每次 TestClient 启动触发真实外部聚合。
    etf_warm_task = (asyncio.create_task(_etf_overview_warmer())
                     if s.WARM_OVERVIEW_ON_STARTUP else None)
    # /ops/lineage 一次性预热（不常驻）：把 32s 冷扫挪到后台，首个用户请求即命中缓存；
    # 后续由 SWR 在 TTL 过期后按访问后台重建。同样受 WARM_OVERVIEW_ON_STARTUP 开关约束，
    # 避免测试环境每次启动都跑一遍全量 footer 扫描。
    lineage_warm_task = (asyncio.create_task(warm_lineage_cache())
                         if s.WARM_OVERVIEW_ON_STARTUP else None)
    # 数据中心统计**周期**预热：受同一开关约束（测试环境 WARM_OVERVIEW_ON_STARTUP=false
    # 时不启动，避免每次 TestClient 启动都跑一遍 50s+ 的全量 parquet 扫描）。
    dc_warm_task = (asyncio.create_task(_datacenter_stats_warmer())
                    if s.WARM_OVERVIEW_ON_STARTUP else None)
    logger.info("startup done")
    yield
    # 有界等待后台任务退出：asyncio.wait 带超时，任务拒绝退出也不阻塞关闭
    # 显式标注为 Task[Any]：各后台任务的返回类型不同（如 warm_overview_cache -> bool），
    # 不标注会让 mypy 按首元素推断成 list[Task[None]] 而对后续 append 报错
    # （2026-09-30 修复：CI mypy 门禁的既有报错）。
    bg: list[asyncio.Task[Any]] = [alert_task, sched_task, routine_task, catchup_task]
    if warm_task is not None:
        bg.append(warm_task)
    if etf_warm_task is not None:
        bg.append(etf_warm_task)
    if lineage_warm_task is not None:
        bg.append(lineage_warm_task)
    if dc_warm_task is not None:
        bg.append(dc_warm_task)
    for t in bg:
        t.cancel()
    await asyncio.wait(bg, timeout=10)
    # 计算池收尾（2026-09-30 全检）：不等阻塞 worker —— 等就退不出了。
    # ⚠️ 真正的"进程可退出"保障来自上面的 socket.setdefaulttimeout，不是本调用。
    from .core.compute_pool import shutdown_compute_pool
    shutdown_compute_pool()
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

# ---- 全局请求超时兜底（**纯 ASGI**，core/timeout_guard.py）----
# 挂载位置推导（add_middleware 是 insert(0)，**后加的在更外层**；见上一段说明）：
# 注册顺序 PanicGuard → **TimeoutGuard** → CORS ⇒
# user_middleware=[timing, CORS, TimeoutGuard, PanicGuard] ⇒ 最外到内为
#   ServerErrorMiddleware → timing → CORS → **TimeoutGuard** → **PanicGuard**
#   → ExceptionMiddleware → router
# ① TimeoutGuard 必须在 **CORS 内侧**：504 响应才会带上 access-control-allow-origin，
#    浏览器端才能读到错误体（否则前端只能看到不透明的网络错误）；
# ② TimeoutGuard 在 **PanicGuard 外侧**：超时预算是对整条请求生命周期的**硬边界**，
#    且 asyncio.wait_for 的取消以 CancelledError 传播、会穿过 PanicGuard ——
#    已实测 PanicGuard 把 CancelledError 列入 _PASSTHROUGH 原样上抛
#    （core/panic_guard.py:49-54），既不误记成 panic 兜底、也不产生误导指标；
# ③ 必然在 **ExceptionMiddleware 外侧**：否则处理器自身卡死时无人兜底。
# 注：timeout_guard 的豁免/延长清单与默认预算取值理由见该模块 docstring。
app.add_middleware(TimeoutGuardMiddleware)

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

    路由命中后 Starlette 将路由对象写入 ``scope['route']``；404/框架异常等
    没有路由对象时统一归入固定值，**绝不能回退到原始 URL**。

    实现委托给 ``core.metrics.endpoint_template_from_scope``，与
    ``core/panic_guard.py`` 共用同一口径（详见该函数 docstring：
    2026-09-29 升级 fastapi 0.141.1 后惰性挂载会丢失路由前缀，需按真实请求路径还原）。
    """
    return endpoint_template_from_scope(request.scope)


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
async def health_ready() -> Response:
    """Readiness: 检查 SQLite、数据目录、Redis 和核心快照。

    [AQP P1-43 / DEP-10 修复 2026-09-21] 本端点**不再受 `ok()` 的 HTTP 200 铁律约束**：
    未就绪时返回 **HTTP 503** + 业务码 `ERR_NOT_READY(50300)`。

    原实现恒返 HTTP 200（`not_ready` 只写在 body 里）⇒ `curl -fsS` 仅在连接被拒时
    失败，实际退化成 liveness；而 `README.md:200` 把它宣称为 **K8s readiness 探针**
    ⇒ 直接套用会让「sqlite/`DATA_ROOT` 检查失败」的 Pod 被判 **Ready**，继续接流量
    并把错误放大。运维探针是标准 HTTP 语义场景（编排系统只看状态码），与业务端点的
    "HTTP 恒 200 + 业务码"契约**并存不冲突**：业务错误码仍在 40000 段，探针用独立的
    503 段（已双向登记 `core/errors.py` 与前端 `types/api.ts`）。
    """
    s = get_settings()
    # [async 阻塞修复 2026-09-29] 探测体全部是阻塞调用：`BEGIN IMMEDIATE` 最长
    # 阻塞 `timeout=2` 秒，parquet 探测要遍历数据湖目录树（本机实测 3.65 万个
    # parquet，核心四目录 2.2 万个）。原先它们直接跑在 async 函数体内 ⇒ 卡住整个
    # 事件循环（并发请求/SSE/后台调度一起停摆）。整体下沉到 worker 线程，返回结构
    # 与异常语义保持不变（checks 键集合一致，仅 redis 由事件循环内 await 后补入）。
    checks: dict[str, str] = await asyncio.to_thread(_probe_readiness_sync)
    if not s.REDIS_ENABLED:
        checks["redis"] = "disabled"
    else:
        from .cache.redis_client import RedisClient
        checks["redis"] = "ok" if await RedisClient.ping() else "degraded"

    mandatory_ok = checks["sqlite"] == "ok" and checks["data_root"] == "ok"
    body = {"status": "ready" if mandatory_ok else "not_ready", "checks": checks}
    if mandatory_ok:
        return JSONResponse(status_code=200, content=jsonable_encoder(ok(body)))
    failed = [k for k, v in checks.items() if v == "failed"]
    return JSONResponse(
        status_code=503,
        content=jsonable_encoder(fail(
            ERR_NOT_READY,
            f"未就绪：{', '.join(failed) or 'unknown'} 检查失败", body)),
        headers={"Retry-After": "5"},
    )


def _has_parquet(root: Path) -> bool:
    """``os.scandir`` 递归探测：命中首个 ``*.parquet`` 即返回 ``True``。

    替代 ``next(root.rglob("*.parquet"), None)``：pathlib 的 rglob 要为每个条目
    构造 Path 对象并逐层做模式匹配，在数万文件的数据湖上代价显著；本函数命中即
    返回，不排序、不构造多余对象，且语义（"是否存在"）完全一致。
    """
    stack: list[str] = [str(root)]
    while stack:
        current = stack.pop()
        try:
            with os.scandir(current) as entries:
                for entry in entries:
                    try:
                        if entry.is_dir(follow_symlinks=False):
                            stack.append(entry.path)
                        elif (entry.is_file(follow_symlinks=False)
                              and entry.name.endswith(".parquet")):
                            return True
                    except OSError:
                        continue
        except OSError:
            continue
    return False


def _probe_readiness_sync() -> dict[str, str]:
    """readiness 的阻塞探测体（**必须在 worker 线程执行**）。

    返回键与既有实现逐字一致：``sqlite`` / ``data_root`` / ``core_snapshot``
    （``redis`` 由调用方在事件循环内 await 后补入）。
    """
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

    # 快照缺失不阻断进程接流量，但明确告诉编排系统当前是降级态。
    # Only inspect the known core dataset roots and stop at the first file;
    # readiness probes must not rescan the whole data lake on every call.
    try:
        has_snapshot = (data_root / ".manifest.json").is_file()
        if not has_snapshot:
            for dataset_name in ("daily_bar", "daily_bar_hfq", "features", "predictions"):
                dataset_root = data_root / dataset_name
                if dataset_root.is_dir() and _has_parquet(dataset_root):
                    has_snapshot = True
                    break
    except OSError:
        has_snapshot = False
    checks["core_snapshot"] = "ok" if has_snapshot else "degraded"
    return checks


@app.get("/health/live", response_model=APIResponse[dict], tags=["root"])
async def health_live() -> APIResponse[dict]:
    """Liveness: 进程存活即 live。"""
    return ok({"status": "live"})
