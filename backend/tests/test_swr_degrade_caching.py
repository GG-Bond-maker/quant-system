"""回归守卫：降级空态不被固化进缓存（swr 有效性谓词）。

覆盖：
- build 返回 ``status="unavailable"`` 时**不写缓存**——第二次请求仍会重新调用 build
  （一次外部源抖动不再让该键在整个 TTL 内持续空态且不自愈）；
- ``status="degraded"`` 时用**很短 TTL**（DEGRADED_TTL_SECONDS）落地、不写影子键；
- 无 status 字段的载荷行为完全不变（向后兼容）。
"""
from __future__ import annotations

from typing import Any

import pytest

from app.cache import redis_client as rc_mod
from app.cache import swr
from app.cache.memory import lru_clear
from app.cache.redis_client import STALE_SUFFIX


@pytest.fixture(autouse=True)
def _offline_fallback(monkeypatch):
    """强制 Redis 不可用：走进程内 LRU + 进程内锁，离线且确定。"""
    monkeypatch.setattr(rc_mod.RedisClient, "_ensure", staticmethod(lambda: None))
    rc_mod._circuit_breaker["fail_count"] = 0
    rc_mod._circuit_breaker["open_until"] = 0.0
    rc_mod._local_locks.clear()
    lru_clear()
    yield
    rc_mod._local_locks.clear()
    lru_clear()


async def test_unavailable_payload_not_cached_so_next_call_rebuilds():
    """unavailable 不落地：第二次请求会再次调用 build（此处以必抛异常证明）。"""
    key = "aqp:t:swr-degrade-unavailable"
    calls: list[int] = []

    async def _build_unavailable() -> dict[str, Any]:
        calls.append(1)
        return {"status": "unavailable", "reason": "数据源暂时不可用"}

    d1 = await swr.cached_or_build(key, _build_unavailable, ttl=300, stale_window=1800)
    assert d1["status"] == "unavailable"
    assert d1["from_cache"] is False
    assert calls == [1]

    # 第二次 build 换成必抛异常：若空态被固化，这里会命中缓存而不调用 build。
    async def _build_boom() -> dict[str, Any]:
        calls.append(1)
        raise RuntimeError("should have been rebuilt")

    with pytest.raises(RuntimeError):
        await swr.cached_or_build(key, _build_boom, ttl=300, stale_window=1800)
    assert calls == [1, 1], "unavailable 未固化：第二次必须重新调用 build"


async def test_degraded_payload_cached_with_short_ttl_and_no_shadow(monkeypatch):
    """degraded 用短 TTL 落地，且不写影子键。"""
    key = "aqp:t:swr-degrade-degraded"
    captured: dict[str, int] = {}
    real_lru_set = rc_mod.lru_set

    def _spy_lru_set(k, value, ttl=3600):  # noqa: ANN001
        captured[k] = ttl
        return real_lru_set(k, value, ttl=ttl)

    monkeypatch.setattr(rc_mod, "lru_set", _spy_lru_set)

    async def _build_degraded() -> dict[str, Any]:
        return {"status": "degraded", "reason": "部分数据源响应超时"}

    data = await swr.cached_or_build(key, _build_degraded, ttl=300, stale_window=1800)
    assert data["status"] == "degraded"
    assert captured.get(key) == swr.DEGRADED_TTL_SECONDS, "degraded 应用短 TTL"
    assert key + STALE_SUFFIX not in captured, "degraded 不应写影子键"


async def test_payload_without_status_cached_normally():
    """无 status 字段：正常按调用方 ttl 落地（向后兼容）。"""
    key = "aqp:t:swr-degrade-plain"
    calls: list[int] = []

    async def _build() -> dict[str, Any]:
        calls.append(1)
        return {"v": 1}

    d1 = await swr.cached_or_build(key, _build, ttl=300, stale_window=1800)
    assert d1["from_cache"] is False
    assert calls == [1]

    d2 = await swr.cached_or_build(key, _build, ttl=300, stale_window=1800)
    assert d2["v"] == 1
    assert d2["from_cache"] is True
    assert calls == [1], "普通载荷应命中缓存"
