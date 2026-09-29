"""P3-3 Prometheus 指标（app/core/metrics.py）：标准 exposition format。

**口径（2026-09-22 §8.2 第 17 项 死代码清理）**：本模块只保留**真的有写入点**的
指标。原先另有 7 个"定义了但全仓从不写入"的指标（`HTTP_ERRORS_TOTAL` /
`REDIS_STATUS` / `REDIS_CIRCUIT_OPEN` / `PIPELINE_TOTAL` / `PIPELINE_DURATION` /
`MODEL_PREDICTION_TOTAL` / `MODEL_PREDICTION_ERROR`）——它们经 `generate_latest()`
恒以 **0** 出现在 `/metrics` 上，会让抓取方把"从未埋点"误读成"业务零发生"
（B1-infra 已记录该误导）。已删除；将来要恢复观测性时，写入点分别是：
中间件（HTTP 错误码）、pipeline step runner（PIPELINE_*）、predict 路径
（MODEL_PREDICTION_*）、Redis 熔断器（REDIS_*）。
"""
from __future__ import annotations

from typing import Any, Mapping

from prometheus_client import (
    CONTENT_TYPE_LATEST,
    Counter,
    Histogram,
    generate_latest,
)


def endpoint_template_from_scope(scope: Mapping[str, Any]) -> str:
    """由 ASGI ``scope`` 还原**低基数**的 Prometheus endpoint 标签。

    ``main.py``（HTTP 计时中间件）与 ``core/panic_guard.py``（兜底异常计数）
    共用本函数，确保两处口径永不漂移。**绝不**回退到带参数的原始 URL
    （证券代码等会把标签基数打爆）。

    为什么不能直接取 ``route.path``（2026-09-29 升级 fastapi 0.141.1 的回归）：
    - 旧版 ``include_router`` **急切拷贝**子路由，``route.path`` 已含完整前缀
      （``/api/v1/stock/{symbol}/profile``）；
    - 新版改为**惰性挂载**，``route.path`` 只剩子路由相对模板
      （``/{symbol}/profile``），前缀由 ``_IncludedRouter`` 链在匹配时叠加。
      于是直接取 ``route.path`` 会**丢掉 ``/api/v1/...`` 前缀**，既与真实路径对不上，
      又可能让不同子路由的同名模板（两个 ``/{symbol}/profile``）在指标上错误合并。

    故以真实请求路径 ``scope['path']`` 为准，把命中的路径参数值回填成 ``{参数名}``
    占位符来还原完整模板——该算法对新旧两代 FastAPI 均成立，且天然保持低基数。
    无参数路由的 ``scope['path']`` 本身即等于模板，同样正确。
    """
    route = scope.get("route")
    if route is None:
        return "/unmatched"
    path = scope.get("path")
    if not (isinstance(path, str) and path.startswith("/")):
        # 少数只有 route、没有 path 的构造场景（如单测替身）：退回路由模板
        template = getattr(route, "path", None)
        return template if isinstance(template, str) and template.startswith("/") else "/unmatched"
    template = path
    for key, value in (scope.get("path_params") or {}).items():
        if isinstance(value, str) and value:
            template = template.replace(f"/{value}", f"/{{{key}}}")
    return template

# ---- API ----
HTTP_REQUESTS_TOTAL = Counter(
    "aqp_http_requests_total", "Total HTTP requests", ["method", "endpoint", "status"])
HTTP_REQUEST_DURATION = Histogram(
    "aqp_http_request_duration_seconds", "HTTP request duration",
    ["method", "endpoint"],
    buckets=(0.01, 0.05, 0.1, 0.25, 0.5, 1.0, 2.5, 5.0, 10.0))

# ---- 全局兜底（core/panic_guard.py）----
# endpoint 为**低基数**路由模板（取不到归 "/unmatched"），绝不是带参数的原始 URL。
PANIC_CONTAINED_TOTAL = Counter(
    "aqp_panic_contained_total",
    "Uncaught BaseException contained by panic guard", ["endpoint"])

# ---- 请求超时兜底（core/timeout_guard.py）----
# endpoint 为**低基数**路由模板（取不到归 "/unmatched"），绝不是带参数的原始 URL。
# phase ∈ {pre_start, post_start}（各 1 个值，基数可控）：
#   pre_start  = 响应未开始，已回写 HTTP 504（客户端能拿到明确错误）；
#   post_start = 响应头已在途，**无法**回写 504（客户端只会看到被截断的响应）。
# 这个区分是排障的关键信号：post_start 计数 > 0 说明有端点在开始流式输出后卡死。
HTTP_REQUEST_TIMEOUT_TOTAL = Counter(
    "aqp_http_request_timeout_total",
    "Requests aborted by the global timeout guard", ["endpoint", "phase"])

# ---- 后台循环兜底（core/resilience.py）----
# loop 为**低基数**固定循环名（如 evening_routine / alert_scheduler），绝不是
# URL / 日期 / symbol —— 否则指标基数爆炸。
LOOP_PANIC_CONTAINED_TOTAL = Counter(
    "aqp_loop_panic_contained_total",
    "Uncaught BaseException contained by resilient background loop", ["loop"])

# ---- Market overview（P2-17：缓存命中率，配合 HTTP_DURATION{endpoint} 看耗时）----
OVERVIEW_CACHE_TOTAL = Counter(
    "aqp_overview_cache_total", "market/overview cache lookups", ["result"])


from fastapi import Response


def metrics_response() -> Response:
    return Response(content=generate_latest(), media_type=CONTENT_TYPE_LATEST)
