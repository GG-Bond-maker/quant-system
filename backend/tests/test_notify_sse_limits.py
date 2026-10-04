"""⑤ SSE 连接上限回归（``api/v1/notify.py``，2026-10-04 上线前全检 P2-5）。

``/notify/stream`` 被 ``core/timeout_guard.py::_EXEMPT_PATHS`` 豁免超时 ⇒ 连接永不因
超时回收；viewer 级账号即可批量建连耗尽内存。防线为**每用户 + 全局**连接上限。
此前**零回归覆盖** —— 本文件补上，重点是**反泄漏**（断开后名额必须归还，否则"用满
即永久拒绝"）。

测法：直接 ``await notify_mod.stream(...)``（绕过 FastAPI DI，故显式传 fake ``_user``；
真实路径由 ``require_stream_viewer`` 注入）。生成器用 ``__anext__`` 启动、``aclose``
或 ``athrow`` 关闭来模拟客户端断开。全局/每用户阈值用 monkeypatch 临时调小，
**不真建 200 条**。
"""
from __future__ import annotations

import asyncio
import gc
import json
import sys
from pathlib import Path

import pytest
from starlette.responses import JSONResponse, StreamingResponse

BACKEND_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(BACKEND_ROOT))

from app.api.v1 import notify as notify_mod  # noqa: E402
from app.core.errors import ERR_RATE_LIMITED  # noqa: E402


@pytest.fixture(autouse=True)
def _reset_sse_counters():
    """每条用例前后清零连接计数，避免跨用例串扰（计数是模块级全局）。"""
    notify_mod._active_conns = 0
    notify_mod._user_conns.clear()
    yield
    notify_mod._active_conns = 0
    notify_mod._user_conns.clear()


def _user(name: str) -> dict:
    return {"username": name, "role": "viewer"}


async def _open(username: str) -> StreamingResponse | JSONResponse:
    """直调端点建立一条 SSE 流（端点在建流前占名额）。"""
    return await notify_mod.stream(
        channels="notify", symbols=None, ttl=15, _user=_user(username))


async def _close(stream: StreamingResponse) -> None:
    """启动生成器再关闭（等价客户端正常断开），触发 gen() 的 finally 释放名额。"""
    agen = stream.body_iterator
    await agen.__anext__()  # 首帧 ": connected"
    await agen.aclose()


def test_per_user_limit_sixth_rejected_with_code(monkeypatch):
    """每用户上限：建 5 条 OK、第 6 条 429 + code=40103（不是只看状态码）。"""
    monkeypatch.setattr(notify_mod, "_PER_USER_CONN_LIMIT", 5)

    async def _run():
        opened = [await _open("u1") for _ in range(5)]
        assert all(isinstance(r, StreamingResponse) for r in opened)
        assert notify_mod._active_conns == 5
        sixth = await _open("u1")
        for r in opened:
            await _close(r)
        return sixth

    sixth = asyncio.run(_run())
    assert isinstance(sixth, JSONResponse)
    assert sixth.status_code == 429
    assert json.loads(sixth.body)["code"] == ERR_RATE_LIMITED
    # 清理后名额归零（反泄漏）
    assert notify_mod._active_conns == 0
    assert notify_mod._user_conns == {}


def test_release_allows_reuse(monkeypatch):
    """反泄漏：断 3 条 → 再建 3 条应全 OK（若计数泄漏，第 2 批会被永久拒绝）。"""
    monkeypatch.setattr(notify_mod, "_PER_USER_CONN_LIMIT", 3)

    async def _run():
        for _ in range(3):
            await _close(await _open("u1"))
        assert notify_mod._active_conns == 0
        reopened = [await _open("u1") for _ in range(3)]
        all_ok = all(isinstance(r, StreamingResponse) for r in reopened)
        for r in reopened:
            await _close(r)
        return all_ok

    assert asyncio.run(_run()) is True
    assert notify_mod._active_conns == 0


def test_abnormal_disconnect_releases(monkeypatch):
    """异常断开路径：向生成器 athrow(CancelledError) 也要归还名额（finally 覆盖取消）。"""
    monkeypatch.setattr(notify_mod, "_PER_USER_CONN_LIMIT", 1)

    async def _run():
        r = await _open("u1")
        assert notify_mod._active_conns == 1
        agen = r.body_iterator
        await agen.__anext__()  # 启动（悬停在 yield）
        with pytest.raises(BaseException):
            await agen.athrow(asyncio.CancelledError)  # 模拟取消导致的断开
        return notify_mod._active_conns

    assert asyncio.run(_run()) == 0


def test_global_limit_rejects(monkeypatch):
    """全局上限：把全局阈值调小，第 N+1 条（不同用户）也应被 429/40103 拒绝。"""
    monkeypatch.setattr(notify_mod, "_GLOBAL_CONN_LIMIT", 3)
    monkeypatch.setattr(notify_mod, "_PER_USER_CONN_LIMIT", 100)

    async def _run():
        opened = [await _open(f"u{i}") for i in range(3)]
        assert notify_mod._active_conns == 3
        extra = await _open("u_extra")
        for r in opened:
            await _close(r)
        return extra

    extra = asyncio.run(_run())
    assert isinstance(extra, JSONResponse)
    assert extra.status_code == 429
    assert json.loads(extra.body)["code"] == ERR_RATE_LIMITED
    assert notify_mod._active_conns == 0


def test_unstarted_generator_release_on_gc(monkeypatch):
    """**窄窗**：响应已建、生成器尚未启动即被 GC ⇒ 名额仍必须归还。

    这是 ``gen()`` 的 ``finally`` **覆盖不到**的路径：CPython 对**未启动**的生成器
    调用 ``close()`` 不执行函数体、也就不跑 ``finally``。客户端恰在「响应已建 →
    第一次 ``__anext__``」之间断开时，Starlette 取消响应任务、生成器对象被 GC，
    原先会永久漏掉 1 个名额（泄漏 5 次后该用户被自己的额度永久拒绝）。

    修法是把归还收敛到 ``_SseSlotGuard``（``__del__`` 兜底）。本用例断言这条路径：
    建流后**不启动**生成器，直接丢引用 + 强制 GC，计数必须回零。

    ⚠️ 若把 guard 去掉（回到"只在 gen() 的 finally 里释放"），本用例会失败 ——
    这正是它存在的意义。
    """
    monkeypatch.setattr(notify_mod, "_PER_USER_CONN_LIMIT", 1)

    async def _run():
        r = await _open("u1")
        assert notify_mod._active_conns == 1
        assert isinstance(r, StreamingResponse)
        # 丢弃响应与迭代器引用，且**从未** __anext__（模拟"未启动即断开"）。
        del r
        gc.collect()
        return notify_mod._active_conns

    assert asyncio.run(_run()) == 0
    assert notify_mod._user_conns == {}
