"""通知 SSE 一次性 ticket 的认证与消费回归测试。"""
from __future__ import annotations

import asyncio
import json
import sys
import time
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

BACKEND_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(BACKEND_ROOT))

from app.cache.memory import lru_set  # noqa: E402
from app.core.auth import (  # noqa: E402
    _SSE_TICKET_MEMORY_PREFIX,
    _sse_ticket_key,
    consume_sse_ticket,
    create_jwt_token,
)
from app.main import app  # noqa: E402


@pytest.fixture(scope="module")
def notify_client() -> TestClient:
    """启动应用并保持独立 TestClient。"""
    with TestClient(app) as client:
        yield client


def _viewer_headers(username: str) -> dict[str, str]:
    """生成仅供测试使用的 viewer JWT 请求头。"""
    token, _ = create_jwt_token(username, "viewer")
    return {"Authorization": f"Bearer {token}"}


def test_stream_ticket_requires_bearer(notify_client: TestClient) -> None:
    """签发端点不允许匿名调用。"""
    response = notify_client.post("/api/v1/notify/stream-ticket")
    body = response.json()
    assert response.status_code == 200
    assert body["code"] == 40100


def test_ticket_is_bound_and_consumed_once(notify_client: TestClient) -> None:
    """合法 ticket 返回签发用户，并在首次消费后立即失效。"""
    issued = notify_client.post(
        "/api/v1/notify/stream-ticket", headers=_viewer_headers("sse_viewer_a"))
    body = issued.json()
    assert body["code"] == 0, body
    ticket = body["data"]["ticket"]
    assert ticket.startswith(("r.", "m."))
    assert body["data"]["expires_in"] == 60

    first = asyncio.run(consume_sse_ticket(ticket))
    second = asyncio.run(consume_sse_ticket(ticket))
    assert first == {"username": "sse_viewer_a", "role": "viewer"}
    assert second is None


def test_ticket_expired_or_malformed_is_rejected() -> None:
    """过期、错误格式 ticket 都给调用方相同的失败结果。"""
    expired_ticket = f"{_SSE_TICKET_MEMORY_PREFIX}expired-ticket"
    expired_payload = json.dumps({
        "username": "sse_viewer_a",
        "role": "viewer",
        "iat": int(time.time()) - 120,
        "exp": int(time.time()) - 1,
    }).encode("utf-8")
    lru_set(_sse_ticket_key(expired_ticket), expired_payload, ttl=60)

    assert asyncio.run(consume_sse_ticket(expired_ticket)) is None
    assert asyncio.run(consume_sse_ticket("not-a-ticket")) is None


def test_tickets_cannot_switch_user_context(notify_client: TestClient) -> None:
    """每个 ticket 的服务端用户上下文固定，两个用户不能混淆。"""
    first_response = notify_client.post(
        "/api/v1/notify/stream-ticket", headers=_viewer_headers("sse_viewer_a"))
    second_response = notify_client.post(
        "/api/v1/notify/stream-ticket", headers=_viewer_headers("sse_viewer_b"))
    first_ticket = first_response.json()["data"]["ticket"]
    second_ticket = second_response.json()["data"]["ticket"]

    first_user = asyncio.run(consume_sse_ticket(first_ticket))
    second_user = asyncio.run(consume_sse_ticket(second_ticket))
    assert first_user is not None and first_user["username"] == "sse_viewer_a"
    assert second_user is not None and second_user["username"] == "sse_viewer_b"


def test_stream_rejects_missing_or_replayed_ticket(notify_client: TestClient) -> None:
    """stream 对缺失与重放 ticket 均返回统一认证业务错误。"""
    no_ticket = notify_client.get("/api/v1/notify/stream")
    assert no_ticket.json()["code"] == 40100

    issued = notify_client.post(
        "/api/v1/notify/stream-ticket", headers=_viewer_headers("sse_viewer_replay"))
    ticket = issued.json()["data"]["ticket"]
    assert asyncio.run(consume_sse_ticket(ticket)) is not None

    replayed = notify_client.get(f"/api/v1/notify/stream?ticket={ticket}")
    assert replayed.json()["code"] == 40100
