"""回归守卫（2026-09-23 报障）：实时块载荷必须带**顶层** ``status``。

背景：用户报「市场概览：实时数据源响应超时」且**刷新无效**。根因之一是
``_build_rt`` 载荷没有顶层 ``status`` ⇒ ``swr.default_cacheable`` 判为
「无 status ⇒ 可缓存」⇒ ``_effective_ttl`` 的 degraded 分支（``DEGRADED_TTL_SECONDS``）
**永不触发** ⇒ 一次超时被按 ``RT_TTL(45s)`` + 影子键(600s) 钉住，缓存不自愈，
用户不管刷新多少次看到的都是那句降级结论。

测试纪律：
- ``tests/test_swr_degrade_caching.py`` 只守住了**消费侧**契约（给定 status 就用短 TTL），
  本文件守住**生产侧**：``_build_rt`` 必须真的产出该字段。
- 缺了本文件，消费侧的守卫形同虚设——实测撤掉那一行（变异 M3）时，覆盖 swr /
  overview / market 的 10 个测试文件（71 用例）保持**全绿**。
- 被测对象是 ``_build_rt`` 的**载荷装配**，不是它内部的真实 IO 聚合，故对四个子块
  用 stub 属合法依赖注入（与 ``tests/test_overview_heal_chain.py`` 对
  ``_build_overview`` 打桩同一口径）；``cached_or_build`` / ``RedisClient``
  一律用真实实现。
"""
from __future__ import annotations

from datetime import date
from typing import Any

import pytest

from app.api.v1 import market as market_api
from app.cache import redis_client as rc_mod
from app.cache import swr
from app.cache.memory import lru_clear
from app.cache.redis_client import STALE_SUFFIX

_TRADE_DATE = date(2026, 9, 23)


@pytest.fixture(autouse=True)
def _offline_fallback(monkeypatch):
    """强制 Redis 不可用：走进程内 LRU + 进程内锁，离线且确定。

    （同 ``tests/test_swr_cache.py`` / ``test_swr_degrade_caching.py`` 的口径——
    夹具不跨模块共享，故此处按同一模式重建，未另造第二套语义。）
    """
    monkeypatch.setattr(rc_mod.RedisClient, "_ensure", staticmethod(lambda: None))
    rc_mod._circuit_breaker["fail_count"] = 0
    rc_mod._circuit_breaker["open_until"] = 0.0
    rc_mod._local_locks.clear()
    lru_clear()
    yield
    rc_mod._local_locks.clear()
    lru_clear()


def _stub_producers(monkeypatch, *, money_flow_ok: bool) -> None:
    """把四个真实 IO 子块换成确定性 stub（仅测试载荷装配语义）。"""
    monkeypatch.setattr(market_api, "_build_indices",
                        lambda: {"status": "ok", "items": [{"code": "sh000001"}]})
    monkeypatch.setattr(
        market_api, "_build_money_flow",
        lambda: ({"status": "ok"} if money_flow_ok
                 else {"status": "degraded", "reason": "资金源部分可用(1/3)"}))
    monkeypatch.setattr(market_api, "_build_heat", lambda: {"status": "ok"})
    monkeypatch.setattr(market_api, "_build_anomalies", lambda heat: {"status": "ok"})


def test_build_rt_emits_top_level_status_ok(monkeypatch):
    """三块全 ok ⇒ 顶层 status 必须是 "ok"。"""
    _stub_producers(monkeypatch, money_flow_ok=True)
    out = market_api._build_rt(_TRADE_DATE)
    assert out["status"] == "ok"


def test_build_rt_emits_top_level_status_degraded(monkeypatch):
    """任一子块 degraded ⇒ 顶层 status 必须是 "degraded"。

    这一行是 swr 短 TTL 分支的**唯一触发条件**；撤掉它，降级载荷就会按正常
    TTL（45s）+ 影子键（600s）落地，缓存不再自愈。
    """
    _stub_producers(monkeypatch, money_flow_ok=False)
    out = market_api._build_rt(_TRADE_DATE)
    assert out["status"] == "degraded"
    assert out["money_flow"]["status"] == "degraded"
    assert out["data_freshness"]["status"] == "degraded"


async def test_degraded_rt_payload_lands_with_short_ttl(monkeypatch):
    """生产→消费 端到端：**真实** ``_build_rt`` 的降级载荷落到 swr 必须走短 TTL 且不写影子键。

    这是用户可见症状（"刷新无效"）的直接守卫：只要生产侧丢掉 status，
    本用例即变红，而不仅仅是断言一个内部字段。
    """
    _stub_producers(monkeypatch, money_flow_ok=False)
    payload = market_api._build_rt(_TRADE_DATE)

    key = "aqp:t:rt-payload-degraded-contract"
    captured: dict[str, int] = {}
    real_lru_set = rc_mod.lru_set

    def _spy_lru_set(k, value, ttl=3600):  # noqa: ANN001
        captured[k] = ttl
        return real_lru_set(k, value, ttl=ttl)

    monkeypatch.setattr(rc_mod, "lru_set", _spy_lru_set)

    async def _build() -> dict[str, Any]:
        return payload

    data = await swr.cached_or_build(key, _build, ttl=45, stale_window=600)
    assert data["status"] == "degraded"
    assert captured.get(key) == swr.DEGRADED_TTL_SECONDS, (
        "降级载荷必须用短 TTL 落地，否则一次超时会被钉住、缓存不自愈")
    assert key + STALE_SUFFIX not in captured, "降级载荷不得写影子键"
