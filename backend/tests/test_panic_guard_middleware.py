"""B 段（panic 全局兜底中间件）回归：``core/panic_guard.py`` + 注册位置 + 指标/留痕。

覆盖架构评估 §4.4 的 6 条（错误码改用 team-lead 裁定的 50001）：
1. 真实 polars Rust panic 路由 → HTTP200 + ``code==50001`` + ``trace_id`` 存在；
2. 普通 ``Exception`` 语义未被抢占（仍 ``code==50000``）；``AQPException`` 仍回原码；
3. 带 ``Origin`` 的 panic 响应含 ``access-control-allow-origin``（证明守卫在 CORS 内侧）；
4. 变异证明（见报告，手工执行）；
5. 回归：``/screener``、``/screener/watchlist``、``/market/overview``、
   ``/market/overview/daily`` 正常数据下 200 且信封完整。

全部走**真实 ``app`` 对象**（不是手工搭的 app）；``TestClient(app, raise_server_exceptions=False)``。
"""
from __future__ import annotations

import sys
from pathlib import Path

import polars as pl
import pytest
from fastapi.testclient import TestClient
from loguru import logger

BACKEND_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(BACKEND_ROOT))

from app.core.config import get_settings  # noqa: E402
from app.core.errors import ERR_PANIC_CONTAINED, ERR_SYSTEM, AQPException  # noqa: E402
from app.main import app  # noqa: E402

_ADMIN = {"Authorization": "Bearer aqp-dev-token-change-me"}
_PROBE = "/__panic_guard_probe__"
_EXC_PROBE = "/__plain_exc_probe__"
_AQP_PROBE = "/__aqp_exc_probe__"


def _raise_real_panic():
    """触发**真实** polars Rust ``PanicException``：dtype=Null 列的单列 sort（帧需 ≥2 列）。"""
    pl.DataFrame({
        "a": [1, 2],
        "pred_score": pl.Series([None, None], dtype=pl.Null),
    }).sort("pred_score")   # ← pyo3_runtime.PanicException（BaseException 子类）


def _raise_plain_exc():
    raise RuntimeError("boom-plain")


def _raise_aqp_exc():
    raise AQPException(51001, "boom-aqp")


@pytest.fixture()
def probe_routes():
    """在**真实 app** 上挂 3 条临时探针路由，teardown 精确移除（不留残留）。"""
    app.add_api_route(_PROBE, _raise_real_panic, methods=["GET"])
    app.add_api_route(_EXC_PROBE, _raise_plain_exc, methods=["GET"])
    app.add_api_route(_AQP_PROBE, _raise_aqp_exc, methods=["GET"])
    try:
        yield
    finally:
        keep = {_PROBE, _EXC_PROBE, _AQP_PROBE}
        app.router.routes = [
            r for r in app.router.routes if getattr(r, "path", None) not in keep]


# ---------------- 1) panic 被兜底为契约内 50001 ----------------
def test_panic_contained_as_http200_code_50001(probe_routes):
    with TestClient(app, raise_server_exceptions=False) as c:
        r = c.get(_PROBE, headers=_ADMIN)
    body = r.json()
    assert r.status_code == 200, r.text
    assert body["code"] == ERR_PANIC_CONTAINED == 50001
    assert body["trace_id"], "兜底信封必须带 trace_id"
    assert {"code", "message", "data", "trace_id", "ts"} <= set(body)


def test_panic_guard_emits_stack_log(probe_routes):
    """兜底必须**全量堆栈留痕**，否则就是静默成 200。

    注意：``TestClient`` 启动会跑 ``lifespan → setup_logging``（``logger.remove()`` 清空
    全部 sink），故捕获 sink 必须在**进入 with 之后**再挂，否则会被清掉。
    """
    msgs: list[str] = []
    with TestClient(app, raise_server_exceptions=False) as c:
        sink_id = logger.add(lambda m: msgs.append(str(m.record["message"])), level="ERROR")
        try:
            c.get(_PROBE, headers=_ADMIN)
        finally:
            logger.remove(sink_id)
    assert any("[panic-guard]" in m for m in msgs), f"未捕获到 panic-guard 堆栈日志: {msgs!r}"
    assert any("PanicException" in m for m in msgs), "堆栈应含异常类型（PanicException）"


# ---------------- 2) 语义未被抢占 ----------------
def test_plain_exception_still_50000(probe_routes):
    """普通 ``Exception`` 仍走 ``errors.py`` 既有处理器（50000），守卫先放行不抢语义。"""
    with TestClient(app, raise_server_exceptions=False) as c:
        r = c.get(_EXC_PROBE, headers=_ADMIN)
    assert r.status_code == 200
    assert r.json()["code"] == ERR_SYSTEM == 50000


def test_aqp_exception_keeps_original_code(probe_routes):
    """``AQPException`` 仍回**原业务码**（此例 51001），守卫不干扰。"""
    with TestClient(app, raise_server_exceptions=False) as c:
        r = c.get(_AQP_PROBE, headers=_ADMIN)
    assert r.status_code == 200
    assert r.json()["code"] == 51001


# ---------------- 3) CORS：panic 响应仍带 access-control-allow-origin ----------------
def test_panic_response_carries_cors_header(probe_routes):
    origin = get_settings().cors_origins_list[0]
    with TestClient(app, raise_server_exceptions=False) as c:
        r = c.get(_PROBE, headers={**_ADMIN, "Origin": origin})
    assert r.status_code == 200
    assert r.json()["code"] == ERR_PANIC_CONTAINED
    assert r.headers.get("access-control-allow-origin") == origin


# ---------------- 5) 回归：正常端点 200 + 信封完整 ----------------
@pytest.mark.parametrize("path", [
    "/api/v1/screener",
    "/api/v1/screener/watchlist?symbols=600000.SH",
    "/api/v1/market/overview",
    "/api/v1/market/overview/daily",
])
def test_normal_endpoints_healthy_envelope(path):
    with TestClient(app, raise_server_exceptions=False) as c:
        r = c.get(path, headers=_ADMIN)
    assert r.status_code == 200, (path, r.text)
    body = r.json()
    assert {"code", "message", "data", "trace_id", "ts"} <= set(body), (path, body)
    assert body["code"] == 0, (path, body)
