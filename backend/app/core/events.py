"""进程内通知事件总线（P2-15）。

后台任务（数据同步 / GP 因子挖掘等）在 worker 线程完成时通过
publish_threadsafe 发布事件；SSE 端点（api/v1/notify.py）把事件推送到
前端顶栏铃铛。单进程内存态（与 GP 任务同款限制），服务重启即清空，
最近事件缓冲也仅用于断线期间的降级回放。
"""
from __future__ import annotations

import asyncio
import threading
from datetime import datetime

_subs: set[asyncio.Queue] = set()
_lock = threading.Lock()
_loop: asyncio.AbstractEventLoop | None = None
_recent: list[dict] = []
_RECENT_MAX = 20


def bind_loop(loop: asyncio.AbstractEventLoop) -> None:
    """lifespan 启动时绑定主事件循环（供 worker 线程安全投递）。"""
    global _loop
    _loop = loop


def _publish_now(event: dict) -> None:
    """仅在主事件循环线程内执行：广播到所有订阅队列 + 记录最近事件。"""
    with _lock:
        _recent.append(event)
        del _recent[:-_RECENT_MAX]
        subs = list(_subs)
    for q in subs:
        # 每个订阅者独立队列；满则丢弃最旧（慢消费者不拖垮发布方）
        if q.full():
            try:
                q.get_nowait()
            except asyncio.QueueEmpty:  # pragma: no cover
                pass
        q.put_nowait(event)


def _make_event(kind: str, message: str, extra: dict) -> dict:
    return {"kind": kind, "message": message,
            "ts": datetime.now().strftime("%H:%M:%S"), **extra}


def publish_threadsafe(kind: str, message: str, **extra) -> None:
    """worker 线程发布通知（跨线程投递到主循环；循环未绑定/已关闭则丢弃）。"""
    if _loop is None or _loop.is_closed():
        return
    try:
        _loop.call_soon_threadsafe(_publish_now, _make_event(kind, message, extra))
    except RuntimeError:  # pragma: no cover 循环关闭竞态
        pass


async def subscribe() -> asyncio.Queue:
    """SSE 端点订阅：返回独立事件队列，断开时必须 unsubscribe。"""
    q: asyncio.Queue = asyncio.Queue(maxsize=100)
    with _lock:
        _subs.add(q)
    return q


def unsubscribe(q: asyncio.Queue) -> None:
    with _lock:
        _subs.discard(q)


def recent() -> list[dict]:
    """最近事件（SSE 建立前的降级回放源）。"""
    with _lock:
        return list(_recent)
