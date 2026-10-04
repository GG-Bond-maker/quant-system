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
import threading
from collections.abc import AsyncGenerator, Callable
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Query
from fastapi.encoders import jsonable_encoder
from fastapi.responses import JSONResponse, Response, StreamingResponse
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from loguru import logger

from ...core import events
from ...core.auth import (
    ROLE_VIEWER,
    consume_sse_ticket,
    ensure_role,
    issue_sse_ticket,
    require_auth,
    require_role,
)
from ...core.errors import APIResponse, ERR_RATE_LIMITED, fail, ok
from ...data import quotes_hub

router = APIRouter()

_SSE_HEADERS = {"Cache-Control": "no-cache", "X-Accel-Buffering": "no"}
_VALID_CHANNELS = {"notify", "quotes", "alerts"}
_HEARTBEAT_SECONDS = 15
_stream_bearer_scheme = HTTPBearer(auto_error=False)

# ---------------- SSE 连接上限（2026-10-04 上线前全检 P2-5） ----------------
# /notify/stream 被 core/timeout_guard.py::_EXEMPT_PATHS 豁免超时 ⇒ 连接永不因超时
# 回收；viewer 级账号即可批量建连耗尽内存（core/events.py 的 _subs、quotes_hub 的
# fan-in 队列都随连接数线性增长）。这里加**每用户 + 全局**连接上限。
# 取值理由：
#   * 全局 200 —— 单进程内存态事件总线 + 行情广播的承载量随连接数线性增长；200 远高于
#     正常在线人数，又能在被刷时及时止住，避免耗尽内存/任务表；
#   * 每用户 5 —— 正常用户 1~2 个标签页（每页 1 条 SSE），5 条留足余量；恶意单账号
#     批量建连会在第 6 条被拒，无法靠多开页面打满全局额度。
# 用 threading.Lock（而非 asyncio.Lock）：计数是 O(1) 且只在事件循环线程访问；更重要的是
# 释放发生在生成器 finally 里，可能在**协程取消**路径上执行 —— 同步释放不会被取消打断，
# 保证名额一定归还。超限时**建连前**直接回统一信封（先建流再关会让客户端反复重连风暴）。
_GLOBAL_CONN_LIMIT = 200
_PER_USER_CONN_LIMIT = 5
_conn_lock = threading.Lock()
_active_conns = 0
_user_conns: dict[str, int] = {}


def _acquire_sse_slot(username: str) -> bool:
    """尝试占用一个 SSE 连接名额；超限返回 False（不改变任何计数）。"""
    global _active_conns
    with _conn_lock:
        if _active_conns >= _GLOBAL_CONN_LIMIT:
            return False
        if _user_conns.get(username, 0) >= _PER_USER_CONN_LIMIT:
            return False
        _active_conns += 1
        _user_conns[username] = _user_conns.get(username, 0) + 1
        return True


def _release_sse_slot(username: str) -> None:
    """释放一个 SSE 连接名额（与 :func:`_acquire_sse_slot` 严格成对）。"""
    global _active_conns
    with _conn_lock:
        _active_conns = max(0, _active_conns - 1)
        remaining = _user_conns.get(username, 0) - 1
        if remaining > 0:
            _user_conns[username] = remaining
        else:
            _user_conns.pop(username, None)


class _SseSlotGuard:
    """把「已占用一个连接名额」绑定到**流对象的生命周期**上，保证恰好归还一次。

    为什么需要它（2026-10-04 上线前全检残留项）
    ------------------------------------------
    名额在**建流前**占用（这是刻意的：超限时要能直接回 429 统一信封，而不是先建流
    再关 —— 后者会让客户端 EventSource 反复重连形成风暴）。但归还原先只写在
    ``gen()`` 的 ``finally`` 里，而 **CPython 对"尚未启动"的生成器调用 ``close()``
    不会执行函数体、也就不会跑 ``finally``**。于是存在一个窄窗：

        客户端在「响应已建 → 生成器第一次 ``__anext__``」之间断开
        ⇒ Starlette 取消响应任务 ⇒ 生成器对象被 GC ⇒ 名额**永久泄漏**。

    泄漏不可自愈：每用户额度只有 5，泄漏 5 次后该用户会被自己的额度**永久拒绝**；
    全局额度也会被慢慢吃掉。窗口只有几层 await（微秒级），但"永久拒绝"的后果
    与其概率不成比例，故按"零泄漏"标准修。

    修法：归还收敛到 ``_release()``，由**三处**共同触发且**幂等**（``_closed`` 标志）：
      * ``__anext__`` 正常结束（``StopAsyncIteration``）或抛错；
      * ``aclose()``（Starlette 取消/正常收尾路径会调用）；
      * ``__del__`` —— 专门兜住上面那个"未启动即被 GC"的窄窗。

    ⚠️ 因此 ``gen()`` 内部**不得**再自行调用 ``_release_sse_slot``，否则会双重释放
    （把别人的名额也减掉）。名额的唯一归还点就是本类。
    """

    __slots__ = ("_agen", "_on_release", "_closed")

    # 类型必须是 AsyncGenerator（而不是更宽的 AsyncIterator）：Starlette 的取消/收尾
    # 路径会调用 ``aclose()`` / ``athrow()``，这两个方法**不在** ``AsyncIterator`` 上，
    # 只在 ``AsyncGenerator`` 上（``agen`` 实参恒为 ``gen()`` 这个真异步生成器）。
    # 用宽类型会让 mypy 抓不到"协议缺失"，也会诱使后续把 aclose 误删。
    def __init__(self, agen: AsyncGenerator[str, None], on_release: Callable[[], None]) -> None:
        self._agen = agen
        self._on_release = on_release
        self._closed = False

    def __aiter__(self) -> "_SseSlotGuard":
        return self

    async def __anext__(self) -> str:
        try:
            return await self._agen.__anext__()
        except BaseException:
            # 含 StopAsyncIteration（正常结束）与各类异常/取消。
            self._release()
            raise

    async def aclose(self) -> None:
        self._release()
        await self._agen.aclose()

    async def athrow(self, *args: Any) -> Any:
        """转发 ``athrow`` 给内层生成器（Starlette 的取消路径会用到）。

        必须**显式实现**：``__getattr__`` 不存在时 ``body_iterator.athrow(...)`` 会
        抛 ``AttributeError``，而调用方（含回归测试）通常只捕获"取消类异常"，
        于是 AttributeError 会被当成"已断开"吞掉，**名额却没人归还** —— 这正是
        最容易被漏掉的假绿。这里先归还、再转发。
        """
        self._release()
        return await self._agen.athrow(*args)

    def _release(self) -> None:
        if not self._closed:
            self._closed = True
            self._on_release()

    def __del__(self) -> None:
        # 解释器退出阶段全局可能已被清空，兜底释放绝不能抛。
        try:
            self._release()
        except Exception:  # noqa: BLE001 - 兜底路径，任何异常都不得外泄
            pass


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
) -> Response:
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

    # 建流前先占名额：超限直接回统一信封，绝不先建流再关（避免客户端重连风暴）。
    username = str(_user.get("username") or "anonymous")
    if not _acquire_sse_slot(username):
        logger.warning(
            f"[notify] SSE 连接超限拒绝: user={username} "
            f"active={_active_conns}/{_GLOBAL_CONN_LIMIT} "
            f"user_conns={_user_conns.get(username, 0)}/{_PER_USER_CONN_LIMIT}")
        return JSONResponse(
            status_code=429,
            content=jsonable_encoder(fail(
                ERR_RATE_LIMITED,
                "SSE 连接数已达上限，请关闭多余页面后重试",
                {"global_limit": _GLOBAL_CONN_LIMIT,
                 "per_user_limit": _PER_USER_CONN_LIMIT},
            )),
        )

    try:
        q_notify = await events.subscribe() if "notify" in req_channels else None
        q_quotes = (await quotes_hub.subscribe_quotes(quote_syms)
                    if "quotes" in req_channels else None)
        q_alerts = (await quotes_hub.subscribe_alerts()
                    if "alerts" in req_channels else None)

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
    except BaseException:
        # 建流前失败（订阅/建队列异常）：归还名额后原样上抛，避免名额泄漏。
        _release_sse_slot(username)
        raise

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
            # ⚠️ 名额归还**不在这里**：已收敛到 _SseSlotGuard（它额外覆盖"生成器尚未
            # 启动就被 GC"这一 finally 覆盖不到的窄窗）。此处若再释放 = 双重释放。

    return StreamingResponse(
        _SseSlotGuard(gen(), lambda: _release_sse_slot(username)),
        media_type="text/event-stream", headers=_SSE_HEADERS)


@router.get("/recent", response_model=APIResponse[list])
async def recent(    _user: dict = Depends(require_role("viewer")),
) -> APIResponse[list]:
    """最近事件（前端建立 SSE 前先拉一次，覆盖刷新期间错过的事件）。"""
    return ok(events.recent())
