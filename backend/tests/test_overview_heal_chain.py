"""market/overview 自愈链回归（2026-09-19）：请求预算不得掐死后台重建 + warmer 提前续期。

用户报「部分数据面板加载失败：请求超时」。架构师只读剖析结论是**三处结构性缺陷**：
  缺陷 1：``/overview`` 的 6s 预算被原样传进 SWR 后台重建 ⇒ 对实测 23.5~113.5s 的
          ``_build_overview`` 是 100% 超时 ⇒ 后台重建每轮只写回 unavailable 降级载荷
          ⇒ 缓存永不自愈，用户反复打回 6s 冷路径（「等 1~2 分钟才正常」的真因）。
  缺陷 2：超时分支**零日志** ⇒ 这个必然发生的问题在日志里看起来像偶发。
  缺陷 3：``warm_overview_cache`` 只看「键是否存在」⇒ 只能在**过期后**重建，一轮失败
          就进入真空期；与「提前 60s 续期」的设计意图不符。

测试纪律：
- 被测对象是 ``cached_or_build`` 的后台重建分派 + ``/overview`` 端点装配 + warmer 判据，
  **不是** ``_build_overview`` 本身（那是真实 IO 聚合）。故对 ``_build_overview`` 用
  测试用慢 stub 属**合法依赖注入**；``cached_or_build`` / ``RedisClient`` 一律用真实实现。
- 缓存走**真实** ``RedisClient`` 的进程内 LRU 降级路径（同 ``tests/test_swr_cache.py``
  的 ``_offline_fallback`` 口径——该夹具是模块内的，pytest 不跨模块共享，故此处按同一
  模式重建，未另造第二套语义）。过期场景用可控墙钟，不真实长 sleep。
"""
from __future__ import annotations

import asyncio
import importlib
import sys
import time
from pathlib import Path
from typing import Any

import orjson
import pytest
from loguru import logger

BACKEND_ROOT = Path(__file__).resolve().parents[1]
if str(BACKEND_ROOT) not in sys.path:
    sys.path.insert(0, str(BACKEND_ROOT))

from app.cache import redis_client as rc_mod  # noqa: E402
from app.cache import swr  # noqa: E402
from app.cache.keys import k_market_overview  # noqa: E402
from app.cache.memory import lru_clear  # noqa: E402
from app.cache.redis_client import RedisClient  # noqa: E402
from app.data.parquet_store import today_trade_date_or_last  # noqa: E402

market = importlib.import_module("app.api.v1.market")

SLOW_SECONDS = 1.0
BUDGET_SECONDS = 0.3
RECOMMEND_K = 50


class _Clock:
    """可控墙钟（同 tests/test_swr_cache.py::_Clock）：驱动 LRU 的惰性 TTL 判定。

    ⚠️ 必须是**带 ``time()`` 的对象**，不能直接替换成 lambda——``memory.py`` 用的是
    ``time.time()``，换成裸函数会 AttributeError（第一版就踩了这个）。

    ``freeze=True``：把真实时间源**钉死**在构造时刻（只保留 ``advance`` 语义）。
    **凡断言精确 TTL（``== 100`` 这类）的用例必须冻结**：``lru_ttl`` 用
    ``int(remaining)`` **截断**，而本机 ``time.time()`` 的刻度约 **7.6ms**
    （实测：刻度中位 0.0076s）⇒ 写入与读取偶尔跨刻度，``remaining`` 由 100 变
    ``int(99.992) = 99`` ⇒ **约 0.1% 概率假红**（实测 3/3000；冻结后 0/3000），
    套件负载越高越容易命中。这是**测试脆弱性**（真实时间依赖），非产品缺陷：
    产品侧 ``int()`` 截断与 Redis ``TTL`` 语义一致。
    """

    def __init__(self, freeze: bool = False) -> None:
        self._real = time.time
        self.offset = 0.0
        self._frozen = self._real() if freeze else None

    def time(self) -> float:
        base = self._frozen if self._frozen is not None else self._real()
        return base + self.offset

    def advance(self, seconds: float) -> None:
        self.offset += seconds


class _FrozenClock:
    """冻结墙钟：``time()`` 恒定，使 LRU 剩余 TTL 恰等于写入时的 TTL（边界用例确定性）。"""

    def __init__(self, now: float) -> None:
        self._now = now

    def time(self) -> float:
        return self._now


@pytest.fixture(autouse=True)
def _offline_fallback(monkeypatch):
    """强制 Redis 不可用路径（进程内 LRU + 进程内锁），并清空跨用例残留。"""
    monkeypatch.setattr(rc_mod.RedisClient, "_ensure", staticmethod(lambda: None))
    rc_mod._circuit_breaker["fail_count"] = 0
    rc_mod._circuit_breaker["open_until"] = 0.0
    rc_mod._local_locks.clear()
    lru_clear()
    yield
    rc_mod._local_locks.clear()
    lru_clear()


async def _wait_bg_done(max_ms: int = 8000) -> None:
    """让出事件循环直到 swr 后台重建任务全部结束（同 test_swr_cache 口径）。"""
    for _ in range(max_ms // 10):
        if not swr._bg_tasks:
            return
        await asyncio.sleep(0.01)
    raise AssertionError(f"后台重建任务未在 {max_ms}ms 内结束: {swr._bg_tasks}")


def _overview_key() -> str:
    """端点实际使用的缓存键（与 market_overview 内部同源计算）。"""
    return k_market_overview(today_trade_date_or_last().strftime("%Y%m%d"), RECOMMEND_K)


def _slow_stub(calls: list[int], payload: dict[str, Any]) -> Any:
    """_build_overview 的替身：sleep SLOW_SECONDS 后返回 payload（同步，走 to_thread）。"""

    def _build(td: Any, recommend_k: int) -> dict[str, Any]:
        calls.append(recommend_k)
        time.sleep(SLOW_SECONDS)
        return dict(payload)

    return _build


GOOD_OVERVIEW = {"indices": {"status": "ok", "items": [{"code": "000300"}]},
                 "heat": {"status": "ok"}, "money_flow": {"status": "ok"},
                 "anomalies": {"status": "ok"}, "sectors": {"status": "ok"},
                 "ai_stats": {"status": "ok"}, "sentiment": {"status": "ok"},
                 "pred_dates": [], "data_freshness": {"status": "ok", "source": "local"}}


# ---------------------------------------------------------------------------
# 缺陷 2：请求路径预算必须生效，且超时必须留日志（此前完全无日志）
# ---------------------------------------------------------------------------
async def test_overview_request_path_enforces_budget(monkeypatch) -> None:
    """冷路径：0.3s 预算 + 1.0s stub ⇒ 立即返回 unavailable 降级载荷（预算生效）。"""
    calls: list[int] = []
    monkeypatch.setattr(market, "_build_overview", _slow_stub(calls, GOOD_OVERVIEW))
    monkeypatch.setattr(market, "OVERVIEW_COMPAT_BUILD_TIMEOUT_SECONDS", BUDGET_SECONDS)

    t0 = time.monotonic()
    resp = await market.market_overview(recommend_k=RECOMMEND_K, date=None, refresh=0)
    elapsed = time.monotonic() - t0

    data = resp.data
    assert elapsed < SLOW_SECONDS * 0.8, f"请求未被预算截断，耗时 {elapsed:.2f}s"
    assert data["indices"]["status"] == "unavailable"
    assert data["data_freshness"]["source"] == "timeout"
    assert calls == [RECOMMEND_K], "stub 应被调用一次（在线程里被 wait_for 掐断）"


async def test_overview_request_timeout_emits_warning_with_wait_time(monkeypatch) -> None:
    """缺陷 2：超时分支必须打 WARNING，且含预算秒数与**实际等待耗时**。

    日志 sink 用进程内 lambda（不 enqueue）：此处断言的是「有没有这条日志、内容对不对」，
    不是 sink 的序列化行为（后者由 panic 收口的测试覆盖）。
    """
    msgs: list[str] = []
    sink_id = logger.add(lambda m: msgs.append(str(m.record["message"])), level="WARNING")
    try:
        monkeypatch.setattr(market, "_build_overview",
                            _slow_stub([], GOOD_OVERVIEW))
        monkeypatch.setattr(market, "OVERVIEW_COMPAT_BUILD_TIMEOUT_SECONDS", BUDGET_SECONDS)
        await market.market_overview(recommend_k=RECOMMEND_K, date=None, refresh=0)
    finally:
        logger.remove(sink_id)

    hits = [m for m in msgs if "请求路径超时" in m]
    assert hits, f"超时分支未留任何日志（缺陷 2 未修复）；实收: {msgs}"
    logged = hits[0]
    assert _overview_key() in logged, "日志应含端点缓存 key 便于定位"
    assert "0.3s" in logged, "日志应含预算秒数（可由常量注入）"
    assert "实际等待" in logged, "日志应含实际等待耗时"
    assert "unavailable" in logged, "日志应说明返回的是降级载荷"


# ---------------------------------------------------------------------------
# 缺陷 1：后台重建必须走无预算 builder，否则缓存永不自愈
# ---------------------------------------------------------------------------
async def test_overview_stale_background_rebuild_is_unbudgeted(monkeypatch) -> None:
    """**本次最关键用例**：主键过期、影子键仍在 ⇒ 后台重建必须写回「好数据」。

    反证：去掉 ``background_build=_build_unbudgeted``（后台重建退回带 0.3s 预算的
    ``_build``）⇒ 本轮断言变红（缓存里落地的是 unavailable 降级载荷，没有 ``indices`` 的
    好数据）。
    """
    clock = _Clock()
    monkeypatch.setattr("app.cache.memory.time", clock)

    key = _overview_key()
    # 旧值：合法的"上一轮好数据"（无顶层 status ⇒ 按默认谓词可缓存）
    old = {"indices": {"status": "ok", "items": [{"code": "OLD"}]}, "v": "old"}
    await swr.write_cache(key, old, ttl=10, stale_window=market.OVERVIEW_STALE_WINDOW)
    clock.advance(11)  # 主键过期、影子键仍在 ⇒ get_stale 命中 stale

    calls: list[int] = []
    monkeypatch.setattr(market, "_build_overview", _slow_stub(calls, GOOD_OVERVIEW))
    monkeypatch.setattr(market, "OVERVIEW_COMPAT_BUILD_TIMEOUT_SECONDS", BUDGET_SECONDS)

    # ① 请求立刻返回旧值（未被 1.0s 的 stub 阻塞）
    t0 = time.monotonic()
    resp = await market.market_overview(recommend_k=RECOMMEND_K, date=None, refresh=0)
    elapsed = time.monotonic() - t0
    assert elapsed < SLOW_SECONDS * 0.5, f"stale 请求被构建阻塞，耗时 {elapsed:.2f}s"
    assert resp.data["v"] == "old"
    assert resp.data["stale"] is True
    assert resp.data["from_cache"] is True

    # ② 稍等后缓存里必须落地 stub 的"好数据"，而不是 unavailable 降级载荷
    await _wait_bg_done()
    assert calls == [RECOMMEND_K], "后台重建必须真的执行了一次构建"

    raw = await RedisClient.get(key)
    assert raw is not None, "后台重建未回写缓存"
    cached = orjson.loads(raw)
    assert cached.get("v") != "old", "缓存仍是旧值：后台重建未回写"
    assert cached["indices"]["items"] == [{"code": "000300"}], (
        f"缓存落地的是降级载荷而非好数据（缺陷 1 未修复）: {cached}")
    assert cached["data_freshness"]["source"] != "timeout", "缓存被超时降级载荷污染"


async def test_overview_background_build_defaults_to_build(monkeypatch) -> None:
    """契约：``background_build`` 缺省（None）时后台重建必须与改动前一致——用 ``build``。

    这条钉住"其它调用方零影响"（rt/daily 等既有调用未传该参数）。
    """
    clock = _Clock()
    monkeypatch.setattr("app.cache.memory.time", clock)

    key = "aqp:t:overview:bg-default"
    await swr.write_cache(key, {"v": 1}, ttl=10, stale_window=1800)
    clock.advance(11)

    used: list[str] = []

    async def _primary() -> dict[str, Any]:
        used.append("primary")
        return {"v": 2}

    async def _bg() -> dict[str, Any]:
        used.append("background")
        return {"v": 3}

    # 不传 background_build -> 后台重建用 build
    data = await swr.cached_or_build(key, _primary, ttl=10, stale_window=1800)
    assert data["stale"] is True
    await _wait_bg_done()
    assert used == ["primary"]
    assert orjson.loads(await RedisClient.get(key))["v"] == 2

    # 传 background_build -> 后台重建只用它（build 不再被后台调用）
    await swr.write_cache(key, {"v": 1}, ttl=10, stale_window=1800)
    clock.advance(11)
    used.clear()
    data2 = await swr.cached_or_build(key, _primary, ttl=10, stale_window=1800,
                                      background_build=_bg)
    assert data2["stale"] is True
    await _wait_bg_done()
    assert used == ["background"]
    assert orjson.loads(await RedisClient.get(key))["v"] == 3


# ---------------------------------------------------------------------------
# 缺陷 3：RedisClient.ttl 语义 + warmer 提前续期
# ---------------------------------------------------------------------------
async def test_redis_ttl_semantics_on_degraded_path(monkeypatch) -> None:
    """ttl 的 -2 / >=0 语义，且与 get() 同口径（都读进程内 LRU）。

    **必须冻结时钟**（``freeze=True``）：本用例断言的是**精确**剩余秒数
    （``== 100`` / ``== 60``），而 ``lru_ttl`` 对剩余量做 ``int()`` 截断，
    真实时间在写入与读取之间跨一个刻度（本机 ~7.6ms）就会把 100 读成 99
    ⇒ 约 0.1% 概率假红。冻结只去掉真实时间漂移，**不放宽任何断言**。
    """
    clock = _Clock(freeze=True)
    monkeypatch.setattr("app.cache.memory.time", clock)

    key = "aqp:t:ttl:semantics"
    assert await RedisClient.get(key) is None
    assert await RedisClient.ttl(key) == -2, "不存在的键必须返回 -2"

    await RedisClient.set(key, b"v", ex=100)
    remaining = await RedisClient.ttl(key)
    assert remaining == 100, f"剩余 TTL 应为 100，实得 {remaining}"
    assert await RedisClient.get(key) == b"v", "get 与 ttl 必须同源（都走 LRU）"

    clock.advance(40)
    assert await RedisClient.ttl(key) == 60
    clock.advance(61)  # 已过期
    assert await RedisClient.ttl(key) == -2
    assert await RedisClient.get(key) is None, "过期后 get 与 ttl 口径必须一致"


async def _seed_and_warm(monkeypatch, seed_ttl: int | None) -> tuple[bool, list[int], str]:
    """辅助：可选地在缓存里种一个 TTL=seed_ttl 的 overview 键，再跑一次 warmer。

    LRU 墙钟被**冻结**为常量，使 ``remaining`` 恰好等于 ``seed_ttl``——否则
    ``lru_ttl`` 的 ``int(expire_at - now)`` 会因微秒流逝把 120 读成 119，边界用例变成
    随机红灯（这是时间源确定性，不是放宽断言）。
    """
    monkeypatch.setattr("app.cache.memory.time", _FrozenClock(time.time()))

    calls: list[int] = []

    # warmer 是阻塞路径，用瞬时 stub（仍走真实 asyncio.to_thread 装配）
    def _fast(td: Any, recommend_k: int) -> dict[str, Any]:
        calls.append(recommend_k)
        return dict(GOOD_OVERVIEW)

    monkeypatch.setattr(market, "_build_overview", _fast)

    key = _overview_key()
    if seed_ttl is not None:
        await swr.write_cache(key, {"indices": GOOD_OVERVIEW["indices"]}, ttl=seed_ttl)
    built = await market.warm_overview_cache(RECOMMEND_K)
    return built, calls, key


async def test_warmer_rebuilds_when_key_absent(monkeypatch) -> None:
    """① 键不存在（TTL=-2）⇒ 重建，返回 True，缓存被写入。"""
    built, calls, key = await _seed_and_warm(monkeypatch, seed_ttl=None)
    assert built is True
    assert calls == [RECOMMEND_K]
    assert await RedisClient.get(key) is not None


async def test_warmer_skips_when_ttl_sufficient(monkeypatch) -> None:
    """② 剩余 TTL 充足（>= 阈值）⇒ 跳过，返回 False，不构建。"""
    built, calls, _key = await _seed_and_warm(monkeypatch, seed_ttl=3000)
    assert built is False, "剩余 TTL 充足时不应重建"
    assert calls == [], "跳过时不得调用 _build_overview"


async def test_warmer_renews_when_ttl_below_threshold(monkeypatch) -> None:
    """③ 剩余 TTL < 阈值 ⇒ **提前续期**（这是缺陷 3 的核心行为变更）。

    旧实现只看「键是否存在」⇒ 这里必然返回 False（键还在）；新实现必须返回 True。
    """
    built, calls, _key = await _seed_and_warm(monkeypatch, seed_ttl=10)
    assert built is True, "剩余 TTL 低于阈值时必须提前续期"
    assert calls == [RECOMMEND_K]


async def test_warmer_threshold_boundary(monkeypatch) -> None:
    """阈值边界：恰好等于阈值 ⇒ 跳过；差 1s ⇒ 续期（谓词为 >=）。"""
    threshold = market.OVERVIEW_WARM_RENEW_THRESHOLD_SECONDS
    built_eq, calls_eq, _ = await _seed_and_warm(monkeypatch, seed_ttl=threshold)
    assert built_eq is False and calls_eq == []

    built_lo, calls_lo, _ = await _seed_and_warm(monkeypatch, seed_ttl=threshold - 1)
    assert built_lo is True and calls_lo == [RECOMMEND_K]


# ---------------------------------------------------------------------------
# 本次修复（2026-09-27）：降级载荷必须带**顶层** status=degraded
# 此前两个降级分支只标了块级 unavailable，顶层无 status ⇒ swr 按调用方 ttl
# 长缓存（overview=300s、daily 最长 3 天），一次超时即长期固化空态、无法自愈。
# 修复后顶层 status=degraded ⇒ swr._effective_ttl 取短 TTL（≤15s）落地、快速自愈。
# ---------------------------------------------------------------------------
async def test_overview_timeout_payload_carries_top_level_degraded(monkeypatch) -> None:
    """兼容概览超时降级载荷：顶层 status=degraded，且 swr 生效 TTL 为短 TTL。

    冻结时钟使 LRU 剩余 TTL 恰等于写入 TTL，从而可断言**精确**落地秒数。
    """
    monkeypatch.setattr("app.cache.memory.time", _FrozenClock(time.time()))
    monkeypatch.setattr(market, "_build_overview", _slow_stub([], GOOD_OVERVIEW))
    monkeypatch.setattr(market, "OVERVIEW_COMPAT_BUILD_TIMEOUT_SECONDS", BUDGET_SECONDS)

    resp = await market.market_overview(recommend_k=RECOMMEND_K, date=None, refresh=0)
    data = resp.data
    assert data["status"] == "degraded", (
        f"降级载荷缺顶层 status=degraded：实得 {data.get('status')!r}")

    ttl, stale = swr._effective_ttl(data, 300, market.OVERVIEW_STALE_WINDOW)
    assert ttl == swr.DEGRADED_TTL_SECONDS, f"生效 TTL 应为短 TTL，实得 {ttl}"
    assert ttl < 300, "顶层 status=degraded 未被 swr 识别（仍按 300s 长缓存）"
    assert stale == 0, "degraded 不应留影子键"

    remaining = await RedisClient.ttl(_overview_key())
    assert remaining == swr.DEGRADED_TTL_SECONDS, (
        f"降级载荷落地 TTL 应为 {swr.DEGRADED_TTL_SECONDS}s，实得 {remaining}")


async def test_daily_timeout_payload_carries_top_level_degraded(monkeypatch) -> None:
    """同修复：``/overview/daily`` 超时降级载荷也要带顶层 status=degraded。

    该块 TTL=``_daily_ttl()``（最长 3 天）——降级载荷若无顶层 status 会被长缓存，
    一次超时即数天不自愈。此处让 stub 抛 ``TimeoutError`` **确定性**命中 ``except``
    降级分支（等价于 5s 预算超时，避免真实等待），断言顶层 status 与生效短 TTL。
    """
    from app.cache.keys import k_market_overview_daily

    monkeypatch.setattr("app.cache.memory.time", _FrozenClock(time.time()))

    def _boom(td: Any, recommend_k: int) -> dict[str, Any]:
        raise TimeoutError("daily stub 预算超时（确定性命中降级分支）")

    monkeypatch.setattr(market, "_build_daily", _boom)

    resp = await market.market_overview_daily(
        recommend_k=RECOMMEND_K, date=None, refresh=0)
    data = resp.data
    assert data["status"] == "degraded", (
        f"daily 降级载荷缺顶层 status=degraded：实得 {data.get('status')!r}")
    assert data["data_freshness"]["source"] == "timeout"

    ttl, stale = swr._effective_ttl(data, market._daily_ttl(),
                                    market.OVERVIEW_STALE_WINDOW)
    assert ttl == swr.DEGRADED_TTL_SECONDS, f"生效 TTL 应为短 TTL，实得 {ttl}"
    assert stale == 0, "degraded 不应留影子键"

    td_str = today_trade_date_or_last().strftime("%Y%m%d")
    remaining = await RedisClient.ttl(k_market_overview_daily(td_str, RECOMMEND_K))
    assert remaining == swr.DEGRADED_TTL_SECONDS, (
        f"daily 降级载荷落地 TTL 应为 {swr.DEGRADED_TTL_SECONDS}s，实得 {remaining}")
