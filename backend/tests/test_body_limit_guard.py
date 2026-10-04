"""④ 全局请求体大小限制回归（``core/body_limit_guard.py``，2026-10-04 上线前全检 P2-4）。

**为什么单独成文件**：与 ``test_security_headers.py`` 同属**全站横切**基础设施防线，
不属于任何业务模块。此前该防线**零回归覆盖** —— 这正是"看起来有防护、实际没人守"的
典型风险（本项目反复出现的坑）。

覆盖两条**不同代码分支**：
  1. 有 ``content-length``：直接比对（O(1) 拒绝，不读 body）；
  2. chunked / 无 ``content-length``：包装 ``receive`` 累计字节数（超限即中止）。

⚠️ 用例**不**真的构造 8MB 字符串（慢且占内存）：用 fixture 把**中间件实例**的上限
临时调成 1KB。**生产值仍是 8MB（``DEFAULT_MAX_BODY_BYTES``）** —— 调小只为让用例快，
勿据此以为上限是 1KB。
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

BACKEND_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(BACKEND_ROOT))

from app.core.body_limit_guard import (  # noqa: E402
    DEFAULT_MAX_BODY_BYTES,
    BodySizeLimitMiddleware,
)
from app.core.errors import ERR_PAYLOAD_TOO_LARGE  # noqa: E402
from app.main import app  # noqa: E402

# 业务端点：登录匿名可达、无副作用；body 非法 JSON 时回 40000（便于区分"未被 413 拦"）。
_LOGIN = "/api/v1/auth/login"
_SMALL_LIMIT = 1024  # 仅测试用，生产为 DEFAULT_MAX_BODY_BYTES(8MB)


@pytest.fixture(scope="module")
def client():
    with TestClient(app) as c:
        yield c


def _find_body_limit_middleware() -> BodySizeLimitMiddleware | None:
    """沿中间件链找到 BodySizeLimitMiddleware 实例（证明它真的挂在应用上）。"""
    stack = app.middleware_stack
    for _ in range(30):
        if isinstance(stack, BodySizeLimitMiddleware):
            return stack
        stack = getattr(stack, "app", None)
        if stack is None:
            return None
    return None


@pytest.fixture()
def small_body_limit(client):
    """把**实例**上限临时调到 1KB（仅为让用例快；生产值仍是 8MB），用完还原。"""
    inst = _find_body_limit_middleware()
    assert inst is not None, "BodySizeLimitMiddleware 未挂到应用上（接线丢失）"
    old = inst.max_body_bytes
    inst.max_body_bytes = _SMALL_LIMIT
    try:
        yield _SMALL_LIMIT
    finally:
        inst.max_body_bytes = old


def test_middleware_is_wired_and_default_is_8mb(client):
    """接线守卫 + 生产默认值守卫（防止被误改小）。"""
    assert _find_body_limit_middleware() is not None, "BodySizeLimitMiddleware 未接线"
    assert any(m.cls is BodySizeLimitMiddleware for m in app.user_middleware)
    assert DEFAULT_MAX_BODY_BYTES == 8 * 1024 * 1024


def test_small_request_not_rejected(client):
    """小请求不被误伤：正常 POST 必须正常走业务（本例是登录参数错误 40000，绝非 413）。"""
    r = client.post(_LOGIN, json={"username": "nobody", "password": "x"})
    assert r.status_code != 413
    assert r.json().get("code") != ERR_PAYLOAD_TOO_LARGE


def test_content_length_over_limit_rejected(client, small_body_limit):
    """有 content-length 且超限 ⇒ HTTP 413 + code=41300 + 统一信封。"""
    r = client.post(_LOGIN, content=b"x" * (small_body_limit + 1024),
                    headers={"content-type": "application/json"})
    assert r.status_code == 413, r.text
    body = r.json()
    assert body["code"] == ERR_PAYLOAD_TOO_LARGE
    # 统一信封字段齐备（本项目要求 code 可分类，不只是状态码）
    assert {"code", "message", "data", "trace_id", "ts"} <= set(body)
    assert body["data"]["max_body_bytes"] == small_body_limit


def test_chunked_over_limit_rejected(client, small_body_limit):
    """chunked / 无 content-length 超限 ⇒ 同样 413+41300（走包装 receive 分支）。

    这条与上一条是**不同代码分支**：无 content-length ⇒ 中间件包装 ``receive``
    累计字节数，超限即中止并回 413。
    """
    def _gen():
        # 分块发送、不提供 content-length ⇒ httpx 走 chunked（Transfer-Encoding）。
        for _ in range(4):
            yield b"y" * 512  # 4×512 = 2048 > 1024

    r = client.post(_LOGIN, content=_gen(),
                    headers={"content-type": "application/json"})
    assert r.status_code == 413, r.text
    assert r.json()["code"] == ERR_PAYLOAD_TOO_LARGE


def test_boundary_equal_passes_plus_one_rejected(client, small_body_limit):
    """边界：恰好等于上限放行、上限 +1 拒绝（证明是"超限才拒"，不是"接近就拒"）。"""
    exact = client.post(_LOGIN, content=b"x" * small_body_limit,
                        headers={"content-type": "application/json"})
    assert exact.status_code != 413, "恰好等于上限不应被拒"
    assert exact.json().get("code") != ERR_PAYLOAD_TOO_LARGE

    over = client.post(_LOGIN, content=b"x" * (small_body_limit + 1),
                       headers={"content-type": "application/json"})
    assert over.status_code == 413, "上限 +1 必须被拒"
    assert over.json()["code"] == ERR_PAYLOAD_TOO_LARGE
