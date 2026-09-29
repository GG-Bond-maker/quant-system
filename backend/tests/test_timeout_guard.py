"""全局请求超时中间件回归：``core/timeout_guard.py`` + 挂载位置 + ASGI 协议正确性。

覆盖规格要求的 6 条，外加 3 条加固：

1. 快请求（< 预算）不受影响，正常 200；
2. 慢请求超时 → **HTTP 504** + 统一信封（``code == ERR_REQUEST_TIMEOUT == 50400``）；
3. **已 start 后超时 → 不产生 ASGI 协议错误**（mock send 断言不二次 start，最关键）；
4. 豁免路径（SSE ``/api/v1/notify/stream``）不受超时影响；
5. websocket / lifespan scope 直接透传、不加超时；
6. 预算可配置（env ``REQUEST_TIMEOUT_SECONDS`` 生效）；
7. 加固：内层应用**自己**抛的 ``TimeoutError`` 不得被误判成中间件超时（3.11+
   ``asyncio.TimeoutError is TimeoutError``，如 ``parquet_store`` 的写锁等待超时）；
8. 加固：真实 ``app`` 的中间件装配顺序（CORS 外 → TimeoutGuard → PanicGuard 内）；
9. 加固：``CancelledError`` 穿过 ``PanicGuard`` 时不被误当 panic 兜住，仍能回 504。

直接测中间件的用例走**手搭 ASGI 栈 + mock send**（能精确断言"到底发了几条 ASGI 消息"）；
装配/端到端用例走**真实 ``app`` 对象**（``TestClient(app, raise_server_exceptions=False)``）。
"""
from __future__ import annotations

import asyncio
import json
import sys
from pathlib import Path

import pytest
from fastapi.middleware.cors import CORSMiddleware
from fastapi.testclient import TestClient
from loguru import logger

BACKEND_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(BACKEND_ROOT))

from app.core import timeout_guard  # noqa: E402
from app.core.errors import ERR_REQUEST_TIMEOUT  # noqa: E402
from app.core.metrics import PANIC_CONTAINED_TOTAL, HTTP_REQUEST_TIMEOUT_TOTAL  # noqa: E402
from app.core.panic_guard import PanicGuardMiddleware  # noqa: E402
from app.core.timeout_guard import (  # noqa: E402
    TimeoutGuardMiddleware,
    resolve_budget_seconds,
)
from app.main import app  # noqa: E402

_TINY_BUDGET = 0.05
_SLOW = 5.0          # 远超 _TINY_BUDGET；被取消后不会真的等这么久


# ---------------- 手搭 ASGI 栈的脚手架 ----------------

class _SendRecorder:
    """记录中间件实际发出的 ASGI 消息，并**模拟真实服务器的协议状态机**。

    关键：真实 ASGI 服务器（uvicorn / Starlette）在收到第二条
    ``http.response.start`` 时会抛 ``Unexpected ASGI message 'http.response.start',
    after response start``。这里在记录时就复刻该判定 —— 于是"测试通过"本身就
    等价于"没有产生 ASGI 协议错误"，不需要额外断言异常文本。
    """

    def __init__(self) -> None:
        self.messages: list[dict] = []
        self._started = False

    async def __call__(self, message: dict) -> None:
        if message["type"] == "http.response.start":
            if self._started:
                raise AssertionError(
                    "ASGI 协议错误：响应已 start 之后又发出了 http.response.start"
                    "（真实服务器会报 Unexpected ASGI message ... after response start）")
            self._started = True
        self.messages.append(message)

    @property
    def starts(self) -> list[dict]:
        return [m for m in self.messages if m["type"] == "http.response.start"]

    @property
    def bodies(self) -> list[dict]:
        return [m for m in self.messages if m["type"] == "http.response.body"]

    def json_body(self) -> dict:
        assert len(self.bodies) == 1, self.messages
        return json.loads(self.bodies[0]["body"])


async def _noop_receive() -> dict:
    return {"type": "http.request", "body": b"", "more_body": False}


def _http_scope(path: str, *, method: str = "GET") -> dict:
    return {
        "type": "http", "http_version": "1.1", "method": method, "scheme": "http",
        "path": path, "raw_path": path.encode(), "query_string": b"",
        "root_path": "", "headers": [],
        "server": ("testserver", 80), "client": ("1.2.3.4", 1234),
    }


def _counter_total(metric, **labels) -> float:
    """读取带标签的 prometheus Counter 当前值（测试用，取共享存储的真实计数）。"""
    return float(metric.labels(**labels)._value.get())


@pytest.fixture()
def tiny_budget(monkeypatch):
    """把默认预算压到 0.05s，让"慢请求"在测试里真的超时。"""
    monkeypatch.setattr(timeout_guard, "default_timeout_seconds", lambda: _TINY_BUDGET)
    return _TINY_BUDGET


# ---------------- 1) 快请求不受影响 ----------------

async def test_fast_request_passes_through(tiny_budget):
    async def _app(scope, receive, send):
        await send({"type": "http.response.start", "status": 200, "headers": []})
        await send({"type": "http.response.body", "body": b"ok"})

    rec = _SendRecorder()
    await TimeoutGuardMiddleware(_app)(_http_scope("/api/v1/fast"), _noop_receive, rec)

    assert [m["type"] for m in rec.messages] == [
        "http.response.start", "http.response.body"]
    assert rec.starts[0]["status"] == 200
    assert rec.bodies[0]["body"] == b"ok"


# ---------------- 2) 慢请求 → HTTP 504 + 统一信封 ----------------

async def test_slow_request_returns_504_envelope(tiny_budget):
    async def _app(scope, receive, send):
        await asyncio.sleep(_SLOW)                       # 永不返回（预算内）
        await send({"type": "http.response.start", "status": 200, "headers": []})

    rec = _SendRecorder()
    await TimeoutGuardMiddleware(_app)(_http_scope("/api/v1/slow"), _noop_receive, rec)

    assert len(rec.starts) == 1, rec.messages
    assert rec.starts[0]["status"] == 504
    body = rec.json_body()
    assert body["code"] == ERR_REQUEST_TIMEOUT == 50400
    # 统一信封五件套齐全（前端 isApiEnvelope 才认）
    assert {"code", "message", "data", "trace_id", "ts"} <= set(body)
    assert body["trace_id"], "兜底信封必须带 trace_id"
    assert body["data"]["path"] == "/api/v1/slow"
    assert body["data"]["method"] == "GET"
    assert body["data"]["budget_seconds"] == pytest.approx(_TINY_BUDGET)


# ---------------- 3) 已 start 后超时：不产生 ASGI 协议错误（最关键） ----------------

async def test_no_second_response_start_after_timeout(tiny_budget):
    """响应已 start（body 在途）后超时 ⇒ **绝不能再发任何 ASGI 消息**。

    变异反证：把 ``_on_timeout`` 里 ``if response_started: ... return`` 的早退删掉
    （即无条件回写 504），``_SendRecorder`` 会立刻抛
    ``ASGI 协议错误：响应已 start 之后又发出了 http.response.start``，本用例变红。
    """
    async def _app(scope, receive, send):
        await send({"type": "http.response.start", "status": 200, "headers": []})
        await send({"type": "http.response.body", "body": b"partial", "more_body": True})
        await asyncio.sleep(_SLOW)                       # 已 start，仍在流式发 body
        await send({"type": "http.response.body", "body": b"", "more_body": False})

    rec = _SendRecorder()
    await TimeoutGuardMiddleware(_app)(_http_scope("/api/v1/streamish"),
                                       _noop_receive, rec)

    # 只有应用自己发的那两条；超时后**一条都不许再发**（含 504）
    assert [m["type"] for m in rec.messages] == [
        "http.response.start", "http.response.body"]
    assert len(rec.starts) == 1
    assert rec.starts[0]["status"] == 200, "已 start 的响应不得被改写成 504"
    assert rec.bodies[0]["body"] == b"partial"


async def test_post_start_timeout_is_logged_not_silent(tiny_budget):
    """已 start 的超时虽无法回写，但**必须留痕**（否则等于静默失效）。"""
    async def _app(scope, receive, send):
        await send({"type": "http.response.start", "status": 200, "headers": []})
        await asyncio.sleep(_SLOW)

    msgs: list[str] = []
    sink_id = logger.add(lambda m: msgs.append(str(m.record["message"])), level="ERROR")
    try:
        await TimeoutGuardMiddleware(_app)(_http_scope("/api/v1/streamish"),
                                           _noop_receive, _SendRecorder())
    finally:
        logger.remove(sink_id)

    assert any("[timeout-guard]" in m and "响应已开始" in m for m in msgs), msgs


# ---------------- 4) 豁免路径（SSE）不受超时影响 ----------------

async def test_exempt_sse_path_has_no_timeout(tiny_budget):
    # 静态事实：SSE 长连接在豁免表里（预算为 None ⇒ 完全不加超时）
    assert resolve_budget_seconds("/api/v1/notify/stream") is None

    async def _app(scope, receive, send):
        await asyncio.sleep(0.2)                         # 4× 于被压小的预算
        await send({"type": "http.response.start", "status": 200, "headers": []})
        await send({"type": "http.response.body", "body": b"ok"})

    rec = _SendRecorder()
    await TimeoutGuardMiddleware(_app)(_http_scope("/api/v1/notify/stream"),
                                       _noop_receive, rec)
    assert len(rec.starts) == 1
    assert rec.starts[0]["status"] == 200, "豁免路径不得被切成 504"


# ---------------- 5) 非 http scope 直接透传 ----------------

@pytest.mark.parametrize("scope_type", ["websocket", "lifespan"])
async def test_non_http_scope_passes_through(tiny_budget, scope_type):
    ran: list[str] = []

    async def _app(scope, receive, send):
        await asyncio.sleep(0.2)                         # 4× 于被压小的预算
        ran.append(scope["type"])

    rec = _SendRecorder()
    await TimeoutGuardMiddleware(_app)({"type": scope_type, "path": "/x"},
                                       _noop_receive, rec)

    assert ran == [scope_type], "非 http scope 被套了超时（lifespan 会阻断启动）"
    assert rec.messages == []


# ---------------- 6) 预算可配置（env → Settings） ----------------

def test_budget_is_configurable_via_settings():
    """``REQUEST_TIMEOUT_SECONDS`` 改环境变量后必须生效（含 lru_cache 失效）。"""
    import os

    from app.core.config import get_settings

    old = os.environ.get("REQUEST_TIMEOUT_SECONDS")
    os.environ["REQUEST_TIMEOUT_SECONDS"] = "7.5"
    get_settings.cache_clear()
    try:
        assert timeout_guard.default_timeout_seconds() == pytest.approx(7.5)
        assert resolve_budget_seconds("/api/v1/whatever") == pytest.approx(7.5)
        # 显式覆盖优先于默认值（前端超时更长的那两条）
        assert resolve_budget_seconds("/api/v1/ops/dag/rerun") == pytest.approx(330.0)
        assert resolve_budget_seconds(
            "/api/v1/backtest/strategy-run") == pytest.approx(660.0)
    finally:
        if old is None:
            os.environ.pop("REQUEST_TIMEOUT_SECONDS", None)
        else:
            os.environ["REQUEST_TIMEOUT_SECONDS"] = old
        get_settings.cache_clear()


def test_default_budget_exceeds_every_frontend_timeout():
    """不变量固化：默认预算 > 全仓前端最长超时（除显式延长的两条外，最长 180s）。

    若将来前端把某路径的超时放宽到 ≥240s 而忘了同步本表，这条会变红。
    """
    assert timeout_guard.default_timeout_seconds() == pytest.approx(240.0)
    frontend_max_excluding_extended = 180.0     # client.ts::download / quality-scan / ...
    assert timeout_guard.default_timeout_seconds() > frontend_max_excluding_extended
    assert resolve_budget_seconds("/api/v1/backtest/strategy-run") > 600.0
    assert resolve_budget_seconds("/api/v1/ops/dag/rerun") > 300.0


# ---------------- 7) 加固：内层自己的 TimeoutError 不得被误判 ----------------

async def test_inner_timeout_error_is_not_misreported(monkeypatch):
    """3.11+ ``asyncio.TimeoutError is TimeoutError`` ⇒ 必须用 inner.cancelled() 区分。

    内层应用自己抛 ``TimeoutError``（如 ``data/parquet_store.py:119/150`` 的写锁
    等待超时）必须**原样上抛**给既有处理器，而不是被改写成 504。
    """
    monkeypatch.setattr(timeout_guard, "default_timeout_seconds", lambda: 30.0)

    async def _app(scope, receive, send):
        raise TimeoutError("等待跨进程写锁超时（10s）")

    rec = _SendRecorder()
    with pytest.raises(TimeoutError, match="写锁"):
        await TimeoutGuardMiddleware(_app)(_http_scope("/api/v1/parquet-write"),
                                           _noop_receive, rec)
    assert rec.messages == [], "内层异常不得被本中间件改写成 504"


# ---------------- 8) 加固：真实 app 的中间件装配顺序 ----------------

def test_real_app_middleware_order():
    """CORS 外 → TimeoutGuard → PanicGuard 内（user_middleware 索引 0 最外）。"""
    classes = [m.cls for m in app.user_middleware]
    assert TimeoutGuardMiddleware in classes, classes
    assert classes.index(CORSMiddleware) < classes.index(TimeoutGuardMiddleware), classes
    assert classes.index(TimeoutGuardMiddleware) < classes.index(PanicGuardMiddleware), (
        classes)


# ---------------- 9) 加固：CancelledError 穿过 PanicGuard ----------------

async def test_cancelled_error_passes_through_panic_guard(tiny_budget):
    """``asyncio.wait_for`` 的取消以 ``CancelledError`` 实现，会**穿过** PanicGuard。

    验证 PanicGuard 不把它误当 panic 兜住：不产生 ``[panic-guard]`` 日志、
    不递增 ``PANIC_CONTAINED_TOTAL``，最终仍由本中间件回 504。
    """
    async def _app(scope, receive, send):
        await asyncio.sleep(_SLOW)
        await send({"type": "http.response.start", "status": 200, "headers": []})

    before = _counter_total(PANIC_CONTAINED_TOTAL, endpoint="/unmatched")
    msgs: list[str] = []
    sink_id = logger.add(lambda m: msgs.append(str(m.record["message"])), level="ERROR")
    try:
        rec = _SendRecorder()
        await TimeoutGuardMiddleware(PanicGuardMiddleware(_app))(
            _http_scope("/api/v1/slow"), _noop_receive, rec)
    finally:
        logger.remove(sink_id)

    assert rec.starts[0]["status"] == 504, (
        "CancelledError 被 PanicGuard 吞掉 ⇒ 不会回 504")
    assert not [m for m in msgs if "[panic-guard]" in m], (
        f"PanicGuard 把 CancelledError 误判成 panic：{msgs!r}")
    assert [m for m in msgs if "[timeout-guard]" in m], msgs
    assert _counter_total(PANIC_CONTAINED_TOTAL, endpoint="/unmatched") == before, (
        "PANIC_CONTAINED_TOTAL 被超时路径误增")


# ---------------- 端到端（真实 app 装配） ----------------

_FAST_PROBE = "/__timeout_guard_fast_probe__"
_SLOW_PROBE = "/__timeout_guard_slow_probe__"


@pytest.fixture()
def probe_routes():
    """在**真实 app** 上挂两条临时探针路由，teardown 精确移除（不留残留）。"""
    async def _fast() -> dict:
        return {"ok": True}

    async def _slow() -> dict:
        await asyncio.sleep(30)                          # 远大于被压小的预算
        return {"ok": True}

    app.add_api_route(_FAST_PROBE, _fast, methods=["GET"])
    app.add_api_route(_SLOW_PROBE, _slow, methods=["GET"])
    try:
        yield
    finally:
        keep = {_FAST_PROBE, _SLOW_PROBE}
        app.router.routes = [
            r for r in app.router.routes if getattr(r, "path", None) not in keep]


def test_end_to_end_fast_probe_ok(probe_routes):
    """端到端：真实中间件栈下，快请求完全不受影响。"""
    with TestClient(app, raise_server_exceptions=False) as c:
        r = c.get(_FAST_PROBE)
    assert r.status_code == 200, r.text
    assert r.json() == {"ok": True}


def test_end_to_end_slow_probe_returns_504_with_cors_and_metric(probe_routes,
                                                               tiny_budget):
    """端到端：真实中间件栈下超时 → HTTP 504 + 信封 + CORS 头 + 计时头 + 指标 +1。"""
    from app.core.config import get_settings

    origin = get_settings().cors_origins_list[0]
    labels = {"endpoint": _SLOW_PROBE, "phase": "pre_start"}
    before = _counter_total(HTTP_REQUEST_TIMEOUT_TOTAL, **labels)

    msgs: list[str] = []
    with TestClient(app, raise_server_exceptions=False) as c:
        sink_id = logger.add(
            lambda m: msgs.append(str(m.record["message"])), level="ERROR")
        try:
            r = c.get(_SLOW_PROBE, headers={"Origin": origin})
        finally:
            logger.remove(sink_id)

    assert r.status_code == 504, r.text
    body = r.json()
    assert body["code"] == ERR_REQUEST_TIMEOUT == 50400
    assert body["data"]["path"] == _SLOW_PROBE
    assert body["trace_id"]
    # ① 504 响应仍带 CORS 头 ⇒ 证明 TimeoutGuard 在 CORS **内侧**
    assert r.headers.get("access-control-allow-origin") == origin
    # ② 计时头仍在 ⇒ 证明 timing 中间件在 TimeoutGuard **外侧**（未被打断）
    assert r.headers.get("X-Response-Time-MS") is not None
    # ③ 指标 +1（低基数 endpoint 标签取到的是路由模板，不是原始 URL）
    assert _counter_total(HTTP_REQUEST_TIMEOUT_TOTAL, **labels) == before + 1
    assert any("[timeout-guard]" in m for m in msgs), msgs
