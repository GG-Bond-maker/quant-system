"""数据中心统计的进程级 TTL 缓存（自 api/v1/datacenter 下沉）。

为何独立成模块：sync_service 在同步任务写库后需要 invalidate_stats_cache()
强制下次统计重扫；若缓存原语留在 api 层，services→api 反向依赖会把
刚拆掉的环重新焊上。datacenter 路由层从本模块重导出，行为与键名不变。
"""
from __future__ import annotations

import threading
import time
from typing import Any, Callable

_cache: dict[str, tuple[float, Any]] = {}
_CACHE_LOCK = threading.Lock()
# 每 key 的 single-flight 闸门（2026-09-26）：同 key 的**并发冷启动**只放一个线程
# 真正计算，其余线程等它算完后直接读缓存。
_inflight: dict[str, threading.Lock] = {}
# 缓存代际（2026-09-26 收口）：每次 invalidate_stats_cache() 自增，受 _CACHE_LOCK 保护。
# 用途：把"扫描期间发生失效"的旧值挡在缓存之外（详见 cached() 的 docstring）。
_generation = 0


def cached(key: str, ttl: float, fn: Callable[[], Any]) -> Any:
    """带 TTL 的进程级缓存；**同一 key 的并发冷启动只算一次**（single-flight）。

    为什么必须加闸门（2026-09-26 修复的真 bug）：
        本函数原先只是"锁内查、锁外算" —— ``fn()`` 在 ``_CACHE_LOCK`` **之外**执行，
        既没有 per-key 锁也没有 single-flight。于是 N 个并发冷请求 = N 次全量重算。
        数据中心页会把 overview / datasets / quality / … **并行**发起（见
        ``frontend/src/pages/DataCenter/index.tsx``），而 ``_scan_all_datasets``（实测
        74s）与 ``_quality_calc``（实测 86s）都要遍历 3.3 万个 parquet ⇒ 多个全量扫描
        同时抢磁盘 IO，最慢时单个请求 180s 不返回，前端 15s 超时直接把面板判为失败。
        ⚠️ 旧 docstring 自称"并发下只算一次"，与实现不符 —— 属"注释谎报行为"。

    为什么还要加代际守卫（2026-09-26 收口，修一个**时序**缺陷）：
        单飞之后仍有一个窗口会固化陈值：``fn()`` 要跑 24~32s，若它执行**期间**外部
        调用了 ``invalidate_stats_cache()``（同步/流水线写库后都会调），旧实现会在
        ``fn()`` 返回后**无条件** ``_cache[key] = val`` —— 即"失效"被这次在飞计算的
        写回**覆盖**，把「失效前那一刻的旧快照」重新塞进缓存，并被服务到 TTL 到期
        （收口前 TTL 已从 120s/300s 上调到 1800s ⇒ 最坏陈旧 2 分钟被放大到 30 分钟）。
        加代际守卫后：``fn()`` 返回时若 ``_generation`` 已变（期间发生过失效），
        **不写回缓存**。

    语义（务必按此理解，避免误读为"会重算"）：
        - 命中未过期缓存即刻返回；
        - ``fn`` 抛异常照旧向上传播，且**不写缓存**（下次调用重算）；
        - **代际变了 ⇒ 本次调用方仍拿到刚算出的那个值**（它是在写入发生前发起的一次
          读取，"读到旧值"对调用方是可接受的）；但该值**不进缓存** ⇒ 下一个请求
          必然重算。即陈旧只影响**那一个**在飞请求，**不再被固化**到 TTL。
        - 这里**绝不**做"发现代际变了就再循环重算一次"——那会把一次读放大成多次全量
          扫描，正是本项目要消灭的 IO 风暴。
        - TTL、返回值、键的语义都不变。
    """
    now = time.monotonic()
    with _CACHE_LOCK:
        hit = _cache.get(key)
        if hit and now - hit[0] < ttl:
            return hit[1]
        gate = _inflight.get(key)
        if gate is None:
            gate = _inflight[key] = threading.Lock()

    with gate:
        # 等闸门期间可能已有同 key 的线程算完并写入 ⇒ 二次检查，避免重复计算。
        now = time.monotonic()
        with _CACHE_LOCK:
            hit = _cache.get(key)
            if hit and now - hit[0] < ttl:
                return hit[1]
            # 记下本次计算开始时的代际：fn() 返回后据此判断期间是否发生过失效。
            gen = _generation
        try:
            val = fn()
            with _CACHE_LOCK:
                if _generation == gen:
                    # 代际未变 ⇒ 没有插入的失效，可安全写回。
                    _cache[key] = (time.monotonic(), val)
                # 代际已变 ⇒ 期间发生过失效：不写回（避免把失效前的旧值固化）。
                # 本次调用方仍拿到 val（见 docstring）；下一个请求会重算。
            return val
        finally:
            # 无论成功/抛异常都摘掉闸门：否则异常会让该 key 在这个 dict 里永久残留。
            # （闸门本身由 ``with`` 释放；这里只是清理登记项。等待者持有旧锁对象引用，
            #   它们随后做的二次检查会读到刚写入的新值，不会重复计算。）
            with _CACHE_LOCK:
                _inflight.pop(key, None)


# 兼容别名：datacenter 内部历史名（路由层重导出沿用）
_cached = cached


def cache_peek(key: str, ttl: float) -> tuple[bool, Any]:
    """只读探测：``key`` 是否有**未过期**的缓存值（不触发计算、不建 inflight 闸门）。

    供"先判断要不要干活，再决定是否占用重量级资源闸门"的调用方使用
    （见 ``api/v1/datacenter.py::_gated_scan``）。
    """
    now = time.monotonic()
    with _CACHE_LOCK:
        hit = _cache.get(key)
        if hit and now - hit[0] < ttl:
            return True, hit[1]
    return False, None


def invalidate_stats_cache() -> None:
    """同步任务写入数据后调用，强制下一次统计重扫。

    除了清空 ``_cache``（保留 ``logs``，日志面板不依赖统计），还自增 ``_generation``：
    让**此刻正在飞**的 ``cached()`` 计算在返回时放弃写回（见 :func:`cached`）——
    否则那次计算会把"失效前"的旧快照覆盖回来，把失效变成空操作。
    """
    global _generation
    with _CACHE_LOCK:
        _generation += 1
        for k in [k for k in _cache if k != "logs"]:
            _cache.pop(k, None)
