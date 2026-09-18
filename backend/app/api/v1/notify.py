"""通知中心（P2-15 / §3.4 扩展）：SSE 实时推送 + 最近事件回放。

Sprint2 扩展协议（路线图 §3.4）：
    GET /api/v1/notify/stream?channels=quotes,alerts&symbols=600519.SH,...&ttl=30

    event: quotes   ← 每 ttl 秒一条（服务端单抓取任务广播，多订阅者共享一次抓取）
    event: alerts   ← 预警触发时（预警引擎 publish_alert，事件驱动）
    data: {...}     ← 通知事件（向后兼容：不带 channels 参数时仅此一种）
    : ping          ← 心跳，每 15s
"""
from __future__ import annotations

import asyncio
import json

from fastapi import APIRouter, Depends, HTTPException, Query
from fastapi.responses import StreamingResponse
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer

from ...core import events
from ...core.auth import (
    ROLE_VIEWER,
    consume_sse_ticket,
    ensure_role,
    issue_sse_ticket,
    require_auth,
    require_role,
)
from ...core.errors import APIResponse, ok
from ...data import quotes_hub

router = APIRouter()

_SSE_HEADERS = {"Cache-Control": "no-cache", "X-Accel-Buffering": "no"}
_VALID_CHANNELS = {"notify", "quotes", "alerts"}
_HEARTBEAT_SECONDS = 15
_stream_bearer_scheme = HTTPBearer(auto_error=False)


def _build_stream_viewer_dependency():
    """构造 SSE 建连依赖，并携带与 ``require_role("viewer")`` 完全一致的内省元数据。

    RBAC 运行时扫描（``tests/test_write_endpoints_smoke.py`` 与
    ``tests/test_read_endpoints_rbac.py`` 的 ``_role_of``）通过「路由顶层依赖函数的
    ``__name__ == "checker"`` 且其闭包含一个最低角色字符串」来内省端点角色。

    这里把 ``require_stream_viewer`` 构造成**同样的形状**（内层函数名 ``checker`` +
    冻结在闭包单元里的 ``minimum_role``），使其可被同一套扫描器正确识别为 viewer 级，
    而不是把该端点从扫描范围里排除。语义与 ``require_role`` 保持一致：
    ``minimum_role`` 是模块级 RBAC 层级的单一事实源之一（此处固定为 "viewer"）。
    """
    # 该字符串会成为一个闭包单元（闭包扫描据此读出最低角色），务必真实参与函数体。
    minimum_role = ROLE_VIEWER

    async def checker(
        ticket: str | None = Query(
            None, description="由 POST /notify/stream-ticket 签发的一次性 SSE ticket"),
        credentials: HTTPAuthorizationCredentials | None = Depends(_stream_bearer_scheme),
    ) -> dict:
        """认证 SSE 连接：优先消费一次性 ticket，保留旧 Bearer 客户端兼容性。"""
        if ticket is not None:
            user = await consume_sse_ticket(ticket)
            if user is None:
                # 故意统一为 UNAUTHORIZED，不暴露 ticket 是否存在、过期或已消费。
                raise HTTPException(status_code=200, detail="UNAUTHORIZED")
            return ensure_role(user, minimum_role)
        return ensure_role(require_auth(credentials), minimum_role)

    return checker


require_stream_viewer = _build_stream_viewer_dependency()


@router.post("/stream-ticket", response_model=APIResponse[dict])
async def create_stream_ticket(
    user: dict = Depends(require_role("viewer")),
) -> APIResponse[dict]:
    """用 Bearer 身份换取短期、一次性的 EventSource 建连票据。"""
    return ok(await issue_sse_ticket(user))


@router.get("/stream")
async def stream(
    channels: str | None = Query(
        None, description="订阅频道（逗号分隔）：notify/quotes/alerts；缺省=notify（向后兼容）"),
    symbols: str | None = Query(
        None, description="quotes 频道的标的过滤（逗号分隔）；缺省=全部订阅并集"),
    ttl: int = Query(30, ge=5, le=300,
                     description="quotes 推送间隔秒（服务端钳制 [15,120]）"),
    _user: dict = Depends(require_stream_viewer),
) -> StreamingResponse:
    """SSE 事件流：通知（默认）/ 行情快照广播 / 预警事件，15s 心跳保活。"""
    req_channels = {c.strip() for c in (channels or "notify").split(",") if c.strip()}
    unknown = req_channels - _VALID_CHANNELS
    if unknown:
        payload = json.dumps({"message": f"未知频道: {sorted(unknown)}"}, ensure_ascii=False)
        return StreamingResponse(
            iter([f"event: error\ndata: {payload}\n\n"]),
            media_type="text/event-stream", headers=_SSE_HEADERS)
    quote_syms = ([s.strip().upper() for s in symbols.split(",") if s.strip()]
                  if symbols and "quotes" in req_channels else None)

    q_notify = await events.subscribe() if "notify" in req_channels else None
    q_quotes = (await quotes_hub.subscribe_quotes(quote_syms)
                if "quotes" in req_channels else None)
    q_alerts = await quotes_hub.subscribe_alerts() if "alerts" in req_channels else None

    out: asyncio.Queue = asyncio.Queue(maxsize=200)

    async def _pump(src: asyncio.Queue, tag: str | None) -> None:
        """fan-in：把各来源队列转发到合并队列（tag=None 保持 legacy data: 行）。"""
        while True:
            item = await src.get()
            await out.put((tag, item))

    pump_tasks: list[asyncio.Task] = []
    if q_notify is not None:
        pump_tasks.append(asyncio.create_task(_pump(q_notify, None)))
    if q_quotes is not None:
        pump_tasks.append(asyncio.create_task(_pump(q_quotes, "quotes")))
    if q_alerts is not None:
        pump_tasks.append(asyncio.create_task(_pump(q_alerts, "alerts")))
    push_interval = quotes_hub.clamp_quotes_ttl(ttl)

    async def gen():
        try:
            # 立即输出注释帧，令客户端能确认 text/event-stream 已实际开始传输。
            yield ": connected\n\n"
            while True:
                try:
                    tag, item = await asyncio.wait_for(
                        out.get(), timeout=min(_HEARTBEAT_SECONDS, push_interval))
                except asyncio.TimeoutError:
                    yield ": ping\n\n"  # 注释行心跳，维持连接不被代理断开
                    continue
                body = json.dumps(item, ensure_ascii=False)
                if tag is None:
                    yield f"data: {body}\n\n"
                else:
                    yield f"event: {tag}\ndata: {body}\n\n"
        finally:
            for t in pump_tasks:
                t.cancel()
            if q_notify is not None:
                events.unsubscribe(q_notify)
            if q_quotes is not None:
                quotes_hub.unsubscribe_quotes(q_quotes)
            if q_alerts is not None:
                quotes_hub.unsubscribe_alerts(q_alerts)

    return StreamingResponse(gen(), media_type="text/event-stream",
                             headers=_SSE_HEADERS)


@router.get("/recent", response_model=APIResponse[list])
async def recent(    _user: dict = Depends(require_role("viewer")),
) -> APIResponse[list]:
    """最近事件（前端建立 SSE 前先拉一次，覆盖刷新期间错过的事件）。"""
    return ok(events.recent())
