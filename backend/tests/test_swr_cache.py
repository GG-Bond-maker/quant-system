"""
SWR 缓存（stale-while-revalidate + refresh 防抖）单元测试 — Sprint 1（QA 严过关）。

被测对象：
- app/cache/redis_client.py：set(stale_ex=) 影子键双写 / get_stale /
  try_lock / unlock / delete（含影子键）；
- app/cache/swr.py：write_cache / cached_or_build（主键命中 / stale 回旧值 +
  后台重建 / 全 miss 同步重建 / refresh 防抖 / after_build 副作用隔离 / 埋点）。

环境约定（全程离线，不得联网）：
- autouse 夹具把 ``RedisClient._ensure`` 钉死为返回 None，即强制走
  「Redis 不可用 -> 进程内 LRU + 进程内时间戳锁」兜底路径；
- LRU 的 TTL 是读取时惰性判定的墙钟（time.time()），过期场景一律用
  monkeypatch 替换目标模块的时间源（_Clock），不真实 sleep，保证确定性。
"""
from __future__ import annotations

import asyncio
import sys
import time
from pathlib import Path
from typing import Any, Callable

import pytest

BACKEND_ROOT = Path(__file__).resolve().parents[1]
if str(BACKEND_ROOT) not in sys.path:
    sys.path.insert(0, str(BACKEND_ROOT))

from app.cache import redis_client as rc_mod  # noqa: E402
from app.cache import swr  # noqa: E402
from app.cache.memory import lru_clear  # noqa: E402
from app.cache.redis_client import STALE_SUFFIX, RedisClient  # noqa: E402

# ---------------------------------------------------------------------------
# 夹具与工具
# ---------------------------------------------------------------------------


class _Clock:
    """可控墙钟：monkeypatch 目标模块的 ``time`` 引用替换为本实例。

    被测模块均以 ``time.time()`` 取墙钟，替换后 ``advance()`` 即可模拟时间流逝。
    """

    def __init__(self) -> None:
        self._real = time.time
        self.offset = 0.0

    def time(self) -> float:  # 与 time.time() 签名一致
        return self._real() + self.offset

    def advance(self, seconds: float) -> None:
        self.offset += seconds


@pytest.fixture(autouse=True)
def _offline_fallback(monkeypatch):
    """强制 Redis 不可用路径：进程内 LRU + 进程内锁，全程离线且确定。

    同时清空熔断器 / 进程内锁 / LRU，保证用例间互不残留。
    """
    monkeypatch.setattr(rc_mod.RedisClient, "_ensure", staticmethod(lambda: None))
    rc_mod._circuit_breaker["fail_count"] = 0
    rc_mod._circuit_breaker["open_until"] = 0.0
    rc_mod._local_locks.clear()
    swr._inflight.clear()
    lru_clear()
    yield
    rc_mod._local_locks.clear()
    swr._inflight.clear()
    lru_clear()


def _builder(calls: list[int], value: dict[str, Any]) -> Callable[[], Any]:
    """构造 async build 协程工厂：每次调用向 calls 追加一次记录。"""

    async def _build() -> dict[str, Any]:
        calls.append(1)
        return dict(value)

    return _build


async def _wait_bg_done(max_ms: int = 5000) -> None:
    """让出事件循环，直到 swr 后台重建任务全部结束（轮询，带超时兜底）。"""
    for _ in range(max_ms // 10):
        if not swr._bg_tasks:
            return
        await asyncio.sleep(0.01)
    raise AssertionError(f"后台重建任务未在 {max_ms}ms 内结束: {swr._bg_tasks}")


# ---------------------------------------------------------------------------
# RedisClient.try_lock / unlock（进程内锁兜底路径）
# ---------------------------------------------------------------------------


async def test_try_lock_mutex_and_unlock_reacquire():
    """互斥：抢到后他人抢不到；unlock 后立即可再抢；不同 key 互不干扰。"""
    assert await RedisClient.try_lock("aqp:lock:a", ttl=5) is True
    assert await RedisClient.try_lock("aqp:lock:a", ttl=5) is False
    # 其他 key 不受影响
    assert await RedisClient.try_lock("aqp:lock:b", ttl=5) is True
    # unlock 后立即可再抢
    await RedisClient.unlock("aqp:lock:a")
    assert await RedisClient.try_lock("aqp:lock:a", ttl=5) is True


async def test_try_lock_expires_after_ttl(monkeypatch):
    """TTL 过期后锁自动可再抢（时间 mock，不真实等待）。"""
    clock = _Clock()
    monkeypatch.setattr(rc_mod, "time", clock)

    assert await RedisClient.try_lock("aqp:lock:ttl", ttl=5) is True
    assert await RedisClient.try_lock("aqp:lock:ttl", ttl=5) is False

    clock.advance(4)  # 未到期仍互斥
    assert await RedisClient.try_lock("aqp:lock:ttl", ttl=5) is False

    clock.advance(2)  # 累计 6s > TTL 5s，过期可再抢
    assert await RedisClient.try_lock("aqp:lock:ttl", ttl=5) is True


# ---------------------------------------------------------------------------
# RedisClient.set(stale_ex=) + get_stale + delete（LRU 兜底路径）
# ---------------------------------------------------------------------------


async def test_set_get_stale_lifecycle(monkeypatch):
    """主键未过期 (v, False) -> 主键过期影子键仍在 (v, True) -> 全过期 (None, False)。"""
    clock = _Clock()
    monkeypatch.setattr("app.cache.memory.time", clock)

    await RedisClient.set("aqp:swr:k1", b"v1", ex=10, stale_ex=100)

    # 1) 主键新鲜
    assert await RedisClient.get_stale("aqp:swr:k1") == (b"v1", False)

    # 2) 主键过期（10s 后），影子键仍在 stale 窗口内
    clock.advance(11)
    assert await RedisClient.get_stale("aqp:swr:k1") == (b"v1", True)

    # 3) 影子键也硬过期（累计 211s > 100s）
    clock.advance(200)
    assert await RedisClient.get_stale("aqp:swr:k1") == (None, False)


async def test_set_no_shadow_when_stale_ex_missing_or_le_ex(monkeypatch):
    """stale_ex 缺省 / stale_ex <= ex 时不应写影子键。

    说明：stale_ex <= ex 时影子键 TTL 短于主键，黑盒上 get_stale 无法区分
    「未写」与「写了先过期」，故用 spy 拦截 lru_set 写调用直接验证守卫。
    """
    writes: list[str] = []
    real_lru_set = rc_mod.lru_set

    def _spy_lru_set(key, value, ttl=3600):
        writes.append(key)
        return real_lru_set(key, value, ttl=ttl)

    monkeypatch.setattr(rc_mod, "lru_set", _spy_lru_set)

    await RedisClient.set("aqp:swr:k2", b"v", ex=50, stale_ex=None)
    assert writes == ["aqp:swr:k2"]  # 无影子键写

    writes.clear()
    await RedisClient.set("aqp:swr:k3", b"v", ex=100, stale_ex=50)  # 不满足 > ex
    assert writes == ["aqp:swr:k3"]  # 无影子键写

    writes.clear()
    shadow = "aqp:swr:k5" + STALE_SUFFIX
    await RedisClient.set("aqp:swr:k5", b"v", ex=10, stale_ex=100)  # 满足 > ex
    assert writes == ["aqp:swr:k5", shadow]  # 影子键照常双写


async def test_delete_clears_shadow_key(monkeypatch):
    """delete 同时删主键与影子键：过期临界点上删后不得再回 stale 旧值。"""
    clock = _Clock()
    monkeypatch.setattr("app.cache.memory.time", clock)

    await RedisClient.set("aqp:swr:k4", b"v", ex=10, stale_ex=100)
    clock.advance(11)
    # 删除前：主键已过期但影子键可回旧值
    assert await RedisClient.get_stale("aqp:swr:k4") == (b"v", True)

    await RedisClient.delete("aqp:swr:k4")
    assert await RedisClient.get_stale("aqp:swr:k4") == (None, False)


# ---------------------------------------------------------------------------
# swr.cached_or_build
# ---------------------------------------------------------------------------


async def test_cached_or_build_primary_hit_no_build():
    """① 主键命中：不调 build，from_cache=True，埋点 hit。"""
    key = "aqp:t:hit"
    calls: list[int] = []
    metrics: list[str] = []
    await swr.write_cache(key, {"v": 1}, ttl=300, stale_window=1800)

    data = await swr.cached_or_build(
        key, _builder(calls, {"v": 2}),
        ttl=300, stale_window=1800, on_metrics=metrics.append)

    assert data["v"] == 1
    assert data["from_cache"] is True
    assert "stale" not in data
    assert "refreshed_recently" not in data
    assert calls == []
    assert metrics == ["hit"]


async def test_cached_or_build_stale_hit_background_rebuild(monkeypatch):
    """② 影子键命中：立即回旧值 stale=True 且 build 不被同步调用；
    后台重建完成后再次请求得到新值。"""
    clock = _Clock()
    monkeypatch.setattr("app.cache.memory.time", clock)

    key = "aqp:t:stale"
    calls: list[int] = []
    metrics: list[str] = []
    await swr.write_cache(key, {"v": 1}, ttl=10, stale_window=1800)
    clock.advance(11)  # 主键过期、影子键（1810s）仍在窗口内

    data = await swr.cached_or_build(
        key, _builder(calls, {"v": 2}),
        ttl=10, stale_window=1800, on_metrics=metrics.append)

    assert data["v"] == 1
    assert data["from_cache"] is True
    assert data["stale"] is True
    assert metrics == ["hit"]
    assert calls == []  # 响应未被 build 阻塞

    # 等待后台重建完成并回写
    await _wait_bg_done()
    assert calls == [1]

    calls.clear()
    data2 = await swr.cached_or_build(
        key, _builder(calls, {"v": 3}), ttl=10, stale_window=1800)
    assert data2["v"] == 2  # 后台重建写入的新值
    assert data2["from_cache"] is True
    assert "stale" not in data2


async def test_cached_or_build_full_miss_sync_rebuild():
    """③ 全 miss：同步重建、from_cache=False、埋点 miss，重建后可命中。"""
    key = "aqp:t:miss"
    calls: list[int] = []
    metrics: list[str] = []

    data = await swr.cached_or_build(
        key, _builder(calls, {"v": 9}),
        ttl=300, stale_window=1800, on_metrics=metrics.append)

    assert data["v"] == 9
    assert data["from_cache"] is False
    assert "stale" not in data
    assert calls == [1]
    assert metrics == ["miss"]

    # 回写后第二次请求命中
    data2 = await swr.cached_or_build(
        key, _builder(calls, {"v": 9}), ttl=300, stale_window=1800,
        on_metrics=metrics.append)
    assert data2["v"] == 9
    assert data2["from_cache"] is True
    assert metrics == ["miss", "hit"]


async def test_cached_or_build_refresh_recomputes():
    """④ refresh=1 抢到锁：同步重算并标 from_cache='refreshed'。"""
    key = "aqp:t:refresh"
    calls: list[int] = []
    await swr.write_cache(key, {"v": 1}, ttl=300, stale_window=1800)

    data = await swr.cached_or_build(
        key, _builder(calls, {"v": 2}), ttl=300, stale_window=1800, refresh=1)

    assert data["v"] == 2
    assert data["from_cache"] == "refreshed"
    assert calls == [1]
    assert "refreshed_recently" not in data


async def test_cached_or_build_refresh_debounce_merges():
    """⑤ refresh=1 二连发：锁被占 -> 第二次回缓存并标 refreshed_recently=True，
    build 只被调 1 次。"""
    key = "aqp:t:debounce"
    calls: list[int] = []

    d1 = await swr.cached_or_build(
        key, _builder(calls, {"v": 1}), ttl=300, stale_window=1800, refresh=1)
    assert d1["from_cache"] == "refreshed"

    d2 = await swr.cached_or_build(
        key, _builder(calls, {"v": 2}), ttl=300, stale_window=1800, refresh=1)

    assert d2["v"] == 1  # 回第一次的缓存值
    assert d2["from_cache"] is True
    assert d2["refreshed_recently"] is True
    assert calls == [1]  # 第二次没有重算


async def test_cached_or_build_after_build_failure_sync_path():
    """⑥ after_build 抛异常：不影响响应返回，也不影响缓存已写（下次可命中）。"""
    key = "aqp:t:afterbuild"
    calls: list[int] = []

    async def _after_build(data: dict[str, Any]) -> None:
        raise RuntimeError("side-effect boom")

    data = await swr.cached_or_build(
        key, _builder(calls, {"v": 5}),
        ttl=300, stale_window=1800, after_build=_after_build)

    assert data["v"] == 5
    assert data["from_cache"] is False

    # 缓存已写：after_build 失败未回滚
    data2 = await swr.cached_or_build(
        key, _builder(calls, {"v": 6}), ttl=300, stale_window=1800)
    assert data2["v"] == 5
    assert data2["from_cache"] is True


async def test_cached_or_build_metrics_hit_miss_callback():
    """⑦ on_metrics 回调：hit（主键/stale 命中）与 miss（同步重建）正确分派，
    且埋点回调自身抛异常不影响主流程。"""
    key = "aqp:t:metrics"
    calls: list[int] = []
    metrics: list[str] = []

    def _bad_metrics(_kind: str) -> None:
        raise RuntimeError("metrics boom")

    # miss（同步重建）
    await swr.cached_or_build(key, _builder(calls, {"v": 1}),
                              ttl=300, stale_window=1800, on_metrics=metrics.append)
    assert metrics == ["miss"]

    # hit（主键命中）
    await swr.cached_or_build(key, _builder(calls, {"v": 2}),
                              ttl=300, stale_window=1800, on_metrics=metrics.append)
    assert metrics == ["miss", "hit"]

    # 埋点回调抛异常被吞掉，主流程正常返回
    data = await swr.cached_or_build(key, _builder(calls, {"v": 2}),
                                     ttl=300, stale_window=1800,
                                     on_metrics=_bad_metrics)
    assert data["from_cache"] is True


async def test_cached_or_build_refresh_lock_occupied_empty_cache_falls_back():
    """补充边界：refresh=1 锁被占但缓存全空（极端并发）-> 退化为同步重建，
    不返回空响应。"""
    key = "aqp:t:refreshempty"
    assert await RedisClient.try_lock(f"refresh:{key}", ttl=5) is True

    calls: list[int] = []
    data = await swr.cached_or_build(
        key, _builder(calls, {"v": 7}), ttl=300, stale_window=1800, refresh=1)

    assert data["v"] == 7
    assert data["from_cache"] == "refreshed"
    assert calls == [1]


async def test_cached_or_build_stale_bg_rebuild_after_build_failure(monkeypatch):
    """补充边界：后台重建路径中 after_build 抛异常 -> 只告警，缓存新值照常可读。"""
    clock = _Clock()
    monkeypatch.setattr("app.cache.memory.time", clock)

    key = "aqp:t:bgafter"
    calls: list[int] = []

    async def _after_build(data: dict[str, Any]) -> None:
        raise RuntimeError("bg side-effect boom")

    await swr.write_cache(key, {"v": 1}, ttl=10, stale_window=1800)
    clock.advance(11)  # 进入 stale 窗口

    data = await swr.cached_or_build(
        key, _builder(calls, {"v": 2}),
        ttl=10, stale_window=1800, after_build=_after_build)
    assert data["stale"] is True

    await _wait_bg_done()
    assert calls == [1]

    calls.clear()
    data2 = await swr.cached_or_build(
        key, _builder(calls, {"v": 3}), ttl=10, stale_window=1800)
    assert data2["v"] == 2  # after_build 失败未影响缓存回写
    assert data2["from_cache"] is True


# ---------------------------------------------------------------------------
# single_flight（冷启动惊群保护，2026-09-27 /ops/lineage 收尾）
# ---------------------------------------------------------------------------


async def test_single_flight_shares_one_build_across_concurrent_misses():
    """⑧ single_flight=True：N 路并发全 miss 只跑一次 build，其余共享结果。

    回归锚点：修复前 4 路并发冷启动各跑一次全量重建，实测因磁盘争用从单次
    29.3s 劣化到 60.9~63.0s（超前端 60s 预算）。此用例把「只跑一次」钉死。
    """
    key = "aqp:t:sf"
    calls: list[int] = []
    after_calls: list[int] = []

    async def _slow_build() -> dict[str, Any]:
        calls.append(1)
        await asyncio.sleep(0.05)  # 让出事件循环，确保 N 路都进入等待
        return {"v": 42}

    async def _after(data: dict[str, Any]) -> None:
        after_calls.append(1)

    results = await asyncio.gather(*(
        swr.cached_or_build(key, _slow_build, ttl=300, stale_window=1800,
                            after_build=_after, single_flight=True)
        for _ in range(5)))

    assert calls == [1]                      # 只重建一次
    assert after_calls == [1]                # 副作用也只执行一次
    assert all(r["v"] == 42 for r in results)
    assert all(r["from_cache"] is False for r in results)
    # 各响应是独立副本：写入标记不互相污染
    assert len({id(r) for r in results}) == 5

    # 共享任务已回写缓存 -> 后续请求直接命中，不再重建
    data = await swr.cached_or_build(key, _slow_build, ttl=300,
                                     stale_window=1800, single_flight=True)
    assert data["from_cache"] is True
    assert calls == [1]


async def test_single_flight_failure_propagates_to_all_waiters():
    """⑨ single_flight=True：重建失败时所有并发调用都拿到同一异常，绝不静默回空。"""
    key = "aqp:t:sf_fail"
    calls: list[int] = []

    async def _boom() -> dict[str, Any]:
        calls.append(1)
        await asyncio.sleep(0.02)
        raise RuntimeError("build boom")

    async def _call() -> str | None:
        try:
            await swr.cached_or_build(key, _boom, ttl=300, stale_window=1800,
                                      single_flight=True)
        except RuntimeError as e:
            return str(e)
        return None

    errs = await asyncio.gather(*(_call() for _ in range(4)))
    assert calls == [1]                       # 失败也只跑一次
    assert errs == ["build boom"] * 4         # 异常对每个等待方可见
    assert swr._inflight == {}                # 失败任务已从表中摘除，不留残骸


async def test_single_flight_default_off_keeps_thundering_herd_behavior():
    """⑨b 缺省 single_flight=False：行为与改动前完全一致（N 路各跑一次）。"""
    key = "aqp:t:sf_off"
    calls: list[int] = []

    async def _slow_build() -> dict[str, Any]:
        calls.append(1)
        await asyncio.sleep(0.05)
        return {"v": 7}

    results = await asyncio.gather(*(
        swr.cached_or_build(key, _slow_build, ttl=300, stale_window=1800)
        for _ in range(3)))

    assert calls == [1, 1, 1]                 # 未开启 -> 各建各的（向后兼容）
    assert all(r["v"] == 7 for r in results)
