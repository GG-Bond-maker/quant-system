"""``/desk/*`` 成对端点响应契约测试（A-5 收口，2026-10-02）。

## 守的是什么

审计 **A-5**：``/desk/kill-switch``、``/desk/exclusion`` 这类路径在**同一前缀下同时存在
GET 与 POST/PUT**，两者都返回 ``APIResponse[dict]`` 且字段名相近。在缺少响应模型（A-1）的
前提下，前端极易把"读"的返回类型套到"写"上 —— 于是**写操作之后**读某个字段恒为 ``undefined``，
而且不会有任何编译期或运行期报错。

实测本仓的两处**形状确实不同**（这正是必须显式建模/分型的地方）：

| 路径 | 方法 | 字段集合 |
|---|---|---|
| ``/desk/kill-switch`` | GET | ``{kill_switch, pending_orders}`` |
| ``/desk/kill-switch`` | POST | ``{kill_switch, cancelled_orders}`` |
| ``/desk/exclusion`` | POST | ``{ok, id, updated}`` |
| ``/desk/exclusion/toggle`` | POST | ``{ok, id, active}`` |

⇒ 本文件把两张形状**分别冻结**，并显式断言"它们不相等"。任何人把写端点改成返回读形状
（或反之）都会 FAIL。

## 测试口径

这些端点是 **DB 支撑**的（走 ``_db()`` 会话 + 测试 SQLite），不涉及外部网络 ⇒ 无需打桩，
用 conftest 已隔离的测试库真实执行。``require_role`` 是 FastAPI ``Depends``，
直接调用路由函数时显式传入 ``_user`` 即可（不触发鉴权链）。
"""
from __future__ import annotations

from typing import Any

import pytest

from app.api.v1 import desk as desk_api
from app.core.errors import ok

_USER = {"role": "researcher", "username": "contract-test"}


def _data(resp: Any) -> Any:
    return resp.data


# --------------------------------------------------------------------------- #
# 统一信封
# --------------------------------------------------------------------------- #
def test_unified_envelope_keys():
    """统一响应契约 ``{code,message,data,trace_id,ts}`` —— 信封本身也是契约。"""
    dumped = ok({"x": 1}).model_dump()
    assert {"code", "message", "data", "trace_id", "ts"} <= set(dumped)


# --------------------------------------------------------------------------- #
# kill-switch：GET 读状态 vs POST 改状态（A-5 核心）
# --------------------------------------------------------------------------- #
@pytest.mark.asyncio
async def test_kill_switch_get_shape():
    d = _data(await desk_api.get_kill_switch(_user=_USER))
    assert set(d) == {"kill_switch", "pending_orders"}
    assert isinstance(d["kill_switch"], bool)
    # 待成交数必须是非负整数（不能用 None/字符串冒充）
    assert isinstance(d["pending_orders"], int) and d["pending_orders"] >= 0


@pytest.mark.asyncio
async def test_kill_switch_post_shape_differs_from_get():
    """🔴 写返回**不是**读返回：第二个字段从 ``pending_orders`` 换成 ``cancelled_orders``。"""
    post_d = _data(await desk_api.set_kill_switch(
        desk_api.KillSwitchRequest(active=False, reason="contract test"), _user=_USER))

    assert set(post_d) == {"kill_switch", "cancelled_orders"}
    assert post_d["kill_switch"] is False
    assert isinstance(post_d["cancelled_orders"], int)

    get_d = _data(await desk_api.get_kill_switch(_user=_USER))
    assert set(get_d) != set(post_d), "读/写形状不得相同（否则前端会串用 TS 类型）"
    assert "pending_orders" not in post_d
    assert "cancelled_orders" not in get_d


@pytest.mark.asyncio
async def test_kill_switch_roundtrip_is_consistent():
    """写入后读回必须一致（避免"写了但读不到"的假成功）。"""
    await desk_api.set_kill_switch(
        desk_api.KillSwitchRequest(active=True, reason="contract roundtrip"), _user=_USER)
    assert _data(await desk_api.get_kill_switch(_user=_USER))["kill_switch"] is True

    await desk_api.set_kill_switch(
        desk_api.KillSwitchRequest(active=False, reason="contract roundtrip"), _user=_USER)
    assert _data(await desk_api.get_kill_switch(_user=_USER))["kill_switch"] is False


# --------------------------------------------------------------------------- #
# exclusion：列表项 vs 两个写端点（三个不同形状）
# --------------------------------------------------------------------------- #
_EXCLUSION_ITEM_KEYS = {"id", "symbol", "category", "reason", "active", "created_at"}


@pytest.mark.asyncio
async def test_exclusion_add_then_list_item_shape():
    add_d = _data(await desk_api.add_exclusion(
        desk_api.ExclusionAddRequest(
            symbol="600000", category="manual_blacklist", reason="contract test"),
        _user=_USER))

    assert set(add_d) == {"ok", "id", "updated"}
    assert add_d["ok"] is True
    assert isinstance(add_d["id"], int)

    rows = _data(await desk_api.list_exclusion(_user=_USER))
    assert isinstance(rows, list) and rows, "刚加入的条目必须出现在列表里"
    assert set(rows[0]) == _EXCLUSION_ITEM_KEYS
    # ``created_at`` 已按 str 序列化（DB 是 TEXT 列，直接给 datetime 会破坏契约）
    assert isinstance(rows[0]["created_at"], str)
    assert isinstance(rows[0]["active"], bool)


@pytest.mark.asyncio
async def test_exclusion_add_and_toggle_have_distinct_shapes():
    """``/exclusion`` 与 ``/exclusion/toggle`` 都是 POST，但形状不同（A-5 同前缀成对）。"""
    add_d = _data(await desk_api.add_exclusion(
        desk_api.ExclusionAddRequest(
            symbol="000001", category="st", reason="contract test"), _user=_USER))

    tog_d = _data(await desk_api.toggle_exclusion(
        desk_api.ExclusionToggleRequest(id=add_d["id"], active=False), _user=_USER))

    assert set(tog_d) == {"ok", "id", "active"}
    assert tog_d["id"] == add_d["id"]
    assert tog_d["active"] is False
    assert set(add_d) != set(tog_d), "两个 POST 形状不得相同"
    assert "updated" not in tog_d
    assert "active" not in add_d


@pytest.mark.asyncio
async def test_exclusion_add_is_idempotent_on_duplicate():
    """重复加入同一 (symbol, category) 必须**更新**而非新增，且 ``updated`` 如实回显。"""
    req = desk_api.ExclusionAddRequest(
        symbol="600519", category="manual_blacklist", reason="dup test")

    first = _data(await desk_api.add_exclusion(req, _user=_USER))
    second = _data(await desk_api.add_exclusion(req, _user=_USER))

    assert first["updated"] is False
    assert second["updated"] is True
    assert first["id"] == second["id"]
