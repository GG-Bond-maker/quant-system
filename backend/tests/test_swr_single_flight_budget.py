"""single-flight 等待方的**有界等待**回归（P-6，2026-10-04 上线前全检）。

## 缺陷

``cache/swr.py::_single_flight`` 的等待方是裸 ``await asyncio.shield(task)`` ——
**没有任何上限**。leader 卡住时，所有等待方会一直等到**全局 240s 超时兜底**
才被取消；而前端对同路径只等 60s（``production.ts:183`` = ``TIMEOUT.medium``）。
实测后果（``backend-run-1003.log:1024/1043``）::

    [timeout-guard] GET /api/v1/ops/lineage 超时（>240s）：已取消处理并回写 HTTP 504

两次。用户早在 60s 就断了，服务端白烧 180s，且用户只拿到网络错误而非结构化错误。

## 修好之后

等待方最多等 ``single_flight_wait_budget`` 秒，超时**降级返回**
``status="unavailable"``（不编造业务数值、且按 ``default_cacheable`` 不落地）；
leader 的共享任务**不受影响**，照常跑完并回写缓存 —— 所以"下一次请求"能命中。

覆盖：
1. 等待方超预算即返回 ``None``（不挂到 240s）；
2. leader 未被取消，仍在后台完成；
3. 超预算后缓存最终被 leader 写入（下次请求可直接命中）；
4. 预算充足时等待方**正常共享**结果（不误伤原有的合并语义）；
5. 降级载荷不可缓存（不会把一次抖动固化成一个 TTL 的空态）。
"""
from __future__ import annotations

import asyncio
import sys
from pathlib import Path

BACKEND_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(BACKEND_ROOT))

from app.cache import swr  # noqa: E402

_KEY = "test:single_flight:budget"


def _reset_inflight() -> None:
    swr._inflight.clear()  # noqa: SLF001 - 测的就是这张表


async def test_waiter_returns_none_when_budget_exceeded():
    """等待方超预算 ⇒ 返回 None（而不是被挂到全局 240s 兜底）。"""
    _reset_inflight()
    started = asyncio.Event()

    async def _slow_build() -> dict:
        started.set()
        await asyncio.sleep(2.0)
        return {"status": "ok", "value": 42}

    # 先让 leader 起跑（第一次调用即 leader，无预算约束）
    leader = asyncio.create_task(swr._single_flight(  # noqa: SLF001
        _KEY, _slow_build, 60, 0, None, None, wait_budget=None))
    await started.wait()

    # 第二个调用成为等待方，预算 0.1s
    waiter = await asyncio.wait_for(
        swr._single_flight(_KEY, _slow_build, 60, 0, None, None,  # noqa: SLF001
                           wait_budget=0.1),
        timeout=1.0)
    assert waiter is None, "等待超预算必须返回 None（降级信号）"

    # leader 不受影响，仍会完成
    result = await leader
    assert result["value"] == 42
    assert not result.get("stale")


async def test_leader_still_writes_cache_after_waiter_gives_up():
    """等待方放弃后，leader 仍把结果写入缓存 ⇒ 下次请求可命中。"""
    _reset_inflight()
    key = f"{_KEY}:writeback"
    started = asyncio.Event()

    async def _slow_build() -> dict:
        started.set()
        await asyncio.sleep(1.5)
        return {"status": "ok", "value": 7}

    leader = asyncio.create_task(swr._single_flight(  # noqa: SLF001
        key, _slow_build, 60, 0, None, None, wait_budget=None))
    await started.wait()
    got = await swr._single_flight(key, _slow_build, 60, 0, None, None,  # noqa: SLF001
                                   wait_budget=0.05)
    assert got is None

    await leader
    val, _stale = await swr.RedisClient.get_stale(key)
    assert val is not None, "leader 必须照常回写缓存（否则超预算就白算了）"


async def test_waiter_shares_result_when_within_budget():
    """预算充足时，等待方仍与 leader 共享同一次构建（不误伤合并语义）。"""
    _reset_inflight()
    key = f"{_KEY}:shared"
    calls = 0
    started = asyncio.Event()

    async def _build() -> dict:
        nonlocal calls
        calls += 1
        started.set()
        await asyncio.sleep(0.3)
        return {"status": "ok", "value": 99}

    leader = asyncio.create_task(swr._single_flight(  # noqa: SLF001
        key, _build, 60, 0, None, None, wait_budget=None))
    await started.wait()
    shared = await swr._single_flight(key, _build, 60, 0, None, None,  # noqa: SLF001
                                      wait_budget=5.0)
    await leader

    assert shared is not None and shared["value"] == 99
    assert calls == 1, f"同 key 并发只应构建一次，实际 {calls} 次"


def test_unavailable_fallback_is_not_cacheable():
    """降级载荷必须**不可缓存**（否则一次抖动会固化成一整个 TTL 的空态）。"""
    payload = swr.build_unavailable_payload("k")
    assert payload["status"] == "unavailable"
    assert swr.default_cacheable(payload) is False
    # 且不得编造任何业务数值
    assert "items" not in payload and "bars" not in payload


def test_default_wait_budget_is_below_tightest_capped_budget():
    """默认等待预算必须**低于**最紧的服务端上限，才能先于 504 降级返回。"""
    from app.core.timeout_guard import _PATH_MAX_BUDGETS

    tightest = min(_PATH_MAX_BUDGETS.values())
    assert swr.SINGLE_FLIGHT_WAIT_BUDGET_SECONDS < tightest, (
        f"single-flight 等待预算 {swr.SINGLE_FLIGHT_WAIT_BUDGET_SECONDS}s 未低于"
        f"最紧上限 {tightest}s ⇒ 等待方仍会被 504 先杀掉，降级逻辑永远走不到"
    )


# ---------------- P-5：负缓存（合法且稳定为空的状态不该每次重算） ----------------

async def test_negative_ttl_caches_unavailable_payload():
    """``negative_ttl`` 给定时，unavailable 载荷应**落地**（短 TTL）。"""
    key = "test:swr:negcache:on"
    await swr.write_cache(key, {"status": "unavailable", "reason": "x"},
                          300, 1800, negative_ttl=60)
    val, _stale = await swr.RedisClient.get_stale(key)
    assert val is not None, "负缓存必须真的写进去（否则仍会每次重算）"
    ttl = await swr.RedisClient.ttl(key)
    assert 0 < ttl <= 60, f"负缓存 TTL 应收敛到 <=60s，实际 {ttl}"


async def test_without_negative_ttl_unavailable_still_not_cached():
    """**不改默认行为**：不给 ``negative_ttl`` 时，unavailable 仍不落地。

    这条是关键回归 —— 它保证 P-5 的修法只作用于显式传参的调用点，
    不会让「真正的源抖动」也被缓存固化。
    """
    key = "test:swr:negcache:off"
    await swr.write_cache(key, {"status": "unavailable"}, 300, 1800)
    val, _stale = await swr.RedisClient.get_stale(key)
    assert val is None, "未显式要求负缓存时，unavailable 不得落地（自愈语义）"


async def test_negative_cache_keeps_no_shadow_key():
    """负缓存**不留影子键** —— 否则 SWR 会把空态当"有效旧值"回给用户。"""
    key = "test:swr:negcache:shadow"
    await swr.write_cache(key, {"status": "unavailable"}, 300, 1800, negative_ttl=60)
    ttl = await swr.RedisClient.ttl(key)
    assert ttl <= 60, f"负缓存不应带 stale_window（否则 TTL 会是 300+1800），实际 {ttl}"


async def test_degraded_ttl_still_short_after_p5():
    """P-5 不得影响既有的 degraded 短 TTL 策略。"""
    key = "test:swr:negcache:degraded"
    await swr.write_cache(key, {"status": "degraded"}, 300, 1800)
    ttl = await swr.RedisClient.ttl(key)
    assert 0 < ttl <= swr.DEGRADED_TTL_SECONDS, f"degraded 应用短 TTL，实际 {ttl}"
