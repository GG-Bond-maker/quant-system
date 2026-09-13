"""P3-3 Prometheus 指标（app/core/metrics.py）：标准 exposition format。"""
from __future__ import annotations

from prometheus_client import (
    CONTENT_TYPE_LATEST,
    Counter,
    Gauge,
    Histogram,
    generate_latest,
)

# ---- API ----
HTTP_REQUESTS_TOTAL = Counter(
    "aqp_http_requests_total", "Total HTTP requests", ["method", "endpoint", "status"])
HTTP_REQUEST_DURATION = Histogram(
    "aqp_http_request_duration_seconds", "HTTP request duration",
    ["method", "endpoint"],
    buckets=(0.01, 0.05, 0.1, 0.25, 0.5, 1.0, 2.5, 5.0, 10.0))
HTTP_ERRORS_TOTAL = Counter(
    "aqp_http_errors_total", "Total HTTP errors (code != 0)", ["endpoint"])

# ---- Redis ----
REDIS_STATUS = Gauge("aqp_redis_status", "Redis status (1=ok 0=degraded)")
REDIS_CIRCUIT_OPEN = Gauge("aqp_redis_circuit_open", "Circuit breaker open (1=yes)")

# ---- Market overview（P2-17：缓存命中率，配合 HTTP_DURATION{endpoint} 看耗时）----
OVERVIEW_CACHE_TOTAL = Counter(
    "aqp_overview_cache_total", "market/overview cache lookups", ["result"])

# ---- Pipeline ----
PIPELINE_TOTAL = Counter("aqp_pipeline_total", "Pipeline runs", ["status"])
PIPELINE_DURATION = Histogram(
    "aqp_pipeline_duration_seconds", "Pipeline duration", ["status"],
    buckets=(10, 30, 60, 120, 300, 600, 1200, 3600))

# ---- ML ----
MODEL_PREDICTION_TOTAL = Counter(
    "aqp_model_prediction_total", "ML predictions served", ["model_version"])
MODEL_PREDICTION_ERROR = Counter(
    "aqp_model_prediction_error_total", "ML prediction errors")


from fastapi import Response


def metrics_response() -> Response:
    return Response(content=generate_latest(), media_type=CONTENT_TYPE_LATEST)
