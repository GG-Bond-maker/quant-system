"""stale-while-revalidate + refresh=1 统一封装（路线图 L1-2 / §3.2，Sprint1）。

语义（验收口径）：
1. 主键命中 -> 直接返回（from_cache=True）；
2. 主键过期但影子键（stale 窗口内）仍命中 -> 回旧值并标 stale=True，
   同时后台重建回写；rebuild:{key} 锁保证并发下只重建一次；
3. 全部未命中 -> 同步重建（与原缓存 miss 路径相同）；
4. refresh=1 -> 抢 refresh:{key} 锁（TTL 5s）防抖：抢到则同步重算回写并标
   from_cache="refreshed"；未抢到说明 5s 内已有人刷新，回当前缓存值并标
   refreshed_recently=True。

红线：refresh 只绕过缓存**读**，不绕过限速（_throttle）与质量门禁——
重建路径与普通 miss 完全相同，限速在数据层生效。
"""
from __future__ import annotations

import asyncio
from typing import Any, Awaitable, Callable

import numpy as np
import orjson
from loguru import logger

from ..core.resilience import is_fatal_base_exception, log_contained
from .redis_client import RedisClient

# 后台重建任务引用集：防止 create_task 无引用被 GC 中途取消
_bg_tasks: set[asyncio.Task] = set()

# ---------------- 降级 / 空态缓存策略（集中定义，便于后续统一调整） ----------------
# 背景：一次外部源抖动若把「不可用」空态按正常 TTL 落地，该键会在整个 TTL 内
# 持续返回空态且不自愈（实测 TTL：screener/market/overview=300s，stocks/watchlist=60s，
# datacenter=120s；stock.north=21600s、stock.events=3600s 更甚）。因此：
#   - status == "unavailable" ：**不写缓存**，下一次请求立即重新构建（快速自愈）；
#   - status == "degraded"    ：用**很短的 TTL**（≤ DEGRADED_TTL_SECONDS）落地，
#                               且不写影子键（避免空态被 SWR 再次读出）；
#   - 无 status 字段 / 其它值  ：按调用方 ttl 正常落地（向后兼容）。
# 阈值集中在下方常量，调参只改这里。
UNAVAILABLE_STATUS = "unavailable"
DEGRADED_STATUS = "degraded"
DEGRADED_TTL_SECONDS = 15      # degraded 落地 TTL 上限（≤15s，尽快自愈）
DEGRADED_STALE_WINDOW = 0      # degraded 不留影子键（不供旧值）


def default_cacheable(data: Any) -> bool:
    """默认有效性谓词：``status == "unavailable"`` 视为不可缓存，其余可缓存。

    这是 ``write_cache`` / ``cached_or_build`` 的默认谓词——**无 status 字段的
    载荷一律视为有效**（历史调用方行为完全不变，向后兼容）。

    ⚠️ 何时该用 ``negative_ttl`` 而不是改这个谓词（P-5，2026-10-04）：
        本谓词把 ``unavailable`` 一律当作"暂时抖动、要立刻自愈"，这对
        ``/market/overview`` 这类**本该有数据**的端点是正确的。
        但对 ``/etf/overview/series`` 这类**合法且稳定为空**的状态（本地没有
        历史落库，要靠每日归档慢慢累积），"不可缓存"就变成了**每次请求全量重算**
        ——实测日志里 ``skip caching non-cacheable payload: aqp:etf:overview_series:30``
        出现 **89 次**（另一轮 60 次、12 次），每次都重跑 ``kpi_series``。
        这种情形应显式传 ``negative_ttl``（见 :func:`cached_or_build`），
        而**不要**放宽本谓词 —— 那会让真正的抖动也被缓存。
    """
    if not isinstance(data, dict):
        return True
    return data.get("status") != UNAVAILABLE_STATUS


def _effective_ttl(data: Any, ttl: int, stale_window: int) -> tuple[int, int]:
    """按状态收敛生效 TTL：degraded 用短 TTL 且不写影子键。"""
    if isinstance(data, dict) and data.get("status") == DEGRADED_STATUS:
        return min(ttl, DEGRADED_TTL_SECONDS), DEGRADED_STALE_WINDOW
    return ttl, stale_window


def _is_negative_payload(data: Any) -> bool:
    """是否为 ``status == "unavailable"`` 的**负缓存候选**载荷。"""
    return isinstance(data, dict) and data.get("status") == UNAVAILABLE_STATUS


def _negative_effective_ttl(ttl: int, stale_window: int,
                            negative_ttl: int) -> tuple[int, int]:
    """负缓存的生效 TTL：取 ``min(ttl, negative_ttl)``，且**不留影子键**。

    不留影子键的理由：负缓存的意义只是"短时间内不必重复重算"，不是"可以拿它当
    旧值继续服务"。留影子键会让 SWR 把空态当有效旧值回给用户。
    """
    return max(1, min(ttl, negative_ttl)), 0


def build_unavailable_payload(key: str) -> dict[str, Any]:
    """构造**诚实**的"暂不可用"降级载荷（P-6，2026-10-04）。

    用途：single-flight 等待超预算、且 leader 尚未写回缓存时，给调用方一个
    **结构化**答复，而不是让请求挂到全局 240s 兜底。

    设计约束（与 ``data/kpi_series.py`` 的反合成红线一致）：
    - ``status="unavailable"`` ⇒ ``default_cacheable`` 会拒绝落地，下次请求立即
      重试（不会把一次抖动固化成一整个 TTL 的空态）；
    - **绝不编造任何业务数值** —— 只给状态与原因，不含 items/bars 之类的假数据；
    - ``reason`` 说明真实原因，便于前端与排障区分"真的没数据"与"还在算"。
    """
    return {
        "status": UNAVAILABLE_STATUS,
        "reason": "计算仍在进行中（并发冷启动合并等待超预算），请稍后重试",
        "cache_key": key,
    }


def _jsonify(data: Any) -> Any:
    """递归把 numpy 标量/数组转为 orjson 可序列化的 Python 原生类型。

    背景（2026-10-01，ETF 周 K 500）：指标/行情计算（pandas/numpy）返回的
    载荷混入 ``numpy.float64`` 等标量，``orjson.dumps`` 对 numpy 类型直接抛
    ``TypeError: Type is not JSON serializable``，导致整条写缓存路径失败
    （实测 ``GET /etf/detail/511880?kline_period=week`` code=50000）。在
    **序列化层**统一兜底而非逐调用点修补：bool_/integer/floating 等标量 ->
    原生 bool/int/float，ndarray -> list，dict/list/tuple 递归。
    """
    if isinstance(data, np.generic):            # numpy 标量（bool_/integer/floating/str_…）
        return data.item()
    if isinstance(data, np.ndarray):
        return [_jsonify(v) for v in data.tolist()]
    if isinstance(data, dict):
        return {k: _jsonify(v) for k, v in data.items()}
    if isinstance(data, (list, tuple)):
        return [_jsonify(v) for v in data]
    return data


async def write_cache(key: str, data: dict[str, Any], ttl: int,
                      stale_window: int = 0, *,
                      is_cacheable: Callable[[Any], bool] | None = None,
                      negative_ttl: int | None = None) -> None:
    """重算结果回写：主键 TTL=ttl；影子键 TTL=ttl+stale_window（SWR 供旧值）。

    Args:
        key: 缓存键。
        data: 待回写载荷（dict）。
        ttl: 主键秒数。
        stale_window: 影子键额外保留秒数。
        is_cacheable: 有效性谓词；默认 ``default_cacheable``（unavailable 不落地）。
            谓词返回 False 时**跳过整次回写**（不写主键，也不写影子键）。
        negative_ttl: 见 :func:`cached_or_build` 的同名参数。给定时，
            ``status == "unavailable"`` 的载荷改用此短 TTL 落地，而不是完全不写。
    """
    if negative_ttl is not None and _is_negative_payload(data):
        ttl, stale_window = _negative_effective_ttl(ttl, stale_window, negative_ttl)
        # 负缓存必须真的落地：绕开"unavailable 不可缓存"这个默认判定。
        predicate: Callable[[Any], bool] = lambda _d: True  # noqa: E731
    else:
        predicate = is_cacheable or default_cacheable
    try:
        cacheable = predicate(data)
    except Exception as e:  # noqa: BLE001 谓词异常按「可缓存」处理，不阻断回写
        logger.warning(f"[swr] is_cacheable {key} raised: {e!r}（按可缓存处理）")
        cacheable = True
    if not cacheable:
        logger.info(f"[swr] skip caching non-cacheable payload: {key} "
                    f"(status={data.get('status') if isinstance(data, dict) else None!r})")
        return
    ttl, stale_window = _effective_ttl(data, ttl, stale_window)
    try:
        payload = orjson.dumps(data)
    except TypeError as e:
        # fast-path 失败（如 numpy 标量/数组）-> 递归转原生类型后重试；
        # 正常载荷零额外开销，见 _jsonify。
        logger.warning(f"[swr] write_cache {key} non-JSON-native payload ({e!r}), jsonify retry")
        payload = orjson.dumps(_jsonify(data))
    await RedisClient.set(key, payload, ex=ttl,
                          stale_ex=ttl + stale_window if stale_window > 0 else None)


def _spawn_rebuild(
    key: str,
    build: Callable[[], Awaitable[dict[str, Any]]],
    ttl: int,
    stale_window: int,
    lock_ttl: int,
    after_build: Callable[[dict[str, Any]], Awaitable[None]] | None,
    is_cacheable: Callable[[Any], bool] | None = None,
    negative_ttl: int | None = None,
) -> None:
    """后台重建：抢 rebuild:{key} 锁（默认 120s，覆盖 overview ~50s 量级的构建），
    未抢到说明已有并发重建在跑，直接放弃。"""

    async def _run() -> None:
        lock_key = f"rebuild:{key}"
        if not await RedisClient.try_lock(lock_key, ttl=lock_ttl):
            return
        try:
            data = await build()
            await write_cache(key, data, ttl, stale_window,
                              is_cacheable=is_cacheable, negative_ttl=negative_ttl)
            if after_build is not None:
                try:
                    await after_build(data)
                except Exception as e:  # noqa: BLE001 副作用失败不影响缓存回写成果
                    logger.warning(f"[swr] after_build {key} failed: {e!r}")
        except Exception as e:  # noqa: BLE001 后台重建失败只记日志，旧值仍在服务
            logger.warning(f"[swr] background rebuild {key} failed: {e!r}")
        except BaseException as exc:  # noqa: BLE001 panic 等非 Exception 兜底
            # [AQP panic 收口 D] 兜住并留痕；**不吞掉**下方 finally —— unlock 仍执行。
            if is_fatal_base_exception(exc):
                raise
            log_contained("swr_rebuild", exc)
        finally:
            await RedisClient.unlock(lock_key)

    t = asyncio.get_running_loop().create_task(_run())
    _bg_tasks.add(t)
    t.add_done_callback(_bg_tasks.discard)


# ---------------- single-flight：请求路径的冷启动惊群保护 ----------------
# 背景（2026-09-27，/ops/lineage 实测）：冷缓存下 N 路并发**各自**跑一次全量重建。
# 单请求冷扫 29.3s，4 路并发因磁盘争用劣化到 60.9~63.0s（> 前端 60s 预算 ⇒ 全部超时），
# 且重复 IO 是纯粹的浪费。此处让**同 key 只放行一次重建**，其余调用等待并共享结果。
# 与 ``_spawn_rebuild`` 分工互补：那里合并的是 stale 路径的**后台**重建，
# 这里合并的是全 miss / 冷启动时**请求路径**的同步重建。
_inflight: dict[str, asyncio.Task] = {}

# single-flight 等待方的**默认**等待上限（秒）。
#
# P-6（2026-10-04 上线前全检）：等待方原先无上限，leader 卡住时会一直等到全局
# 240s 兜底 —— 而前端对本路径只等 60s，用户早已断开、服务端白烧。
#
# 2026-10-04（T-1 续）：本默认值必须**低于** ``timeout_guard._PATH_MAX_BUDGETS``
# 的**最紧**一条（现在最紧是 ``/api/v1/market/quotes`` 的 16s），让等待方在服务端
# 504 之前先降级返回（``test_default_wait_budget_is_below_tightest_capped_budget``
# 断言此关系）。故默认取 12s（16s 之下留 4s 余量）。
#
# ⚠️ 默认值偏保守：**重活路径**（leader 构建本身要数十秒）必须显式传
# ``single_flight_wait_budget`` 覆盖，否则等待方会在 leader 出结果前先降级：
#   * ``ops.py`` 的 ``/ops/lineage`` 传 38s（冷扫 32~38s；其上限 48s）；
#   * ``market.py`` 的三个 overview 块传 ``OVERVIEW_SINGLE_FLIGHT_WAIT_SECONDS``（30s）。
SINGLE_FLIGHT_WAIT_BUDGET_SECONDS = 12.0


async def _single_flight(
    key: str,
    build: Callable[[], Awaitable[dict[str, Any]]],
    ttl: int,
    stale_window: int,
    after_build: Callable[[dict[str, Any]], Awaitable[None]] | None,
    is_cacheable: Callable[[Any], bool] | None,
    wait_budget: float | None = None,
    negative_ttl: int | None = None,
) -> dict[str, Any] | None:
    """同 key 并发只执行一次 ``build``，其余调用等待并共享同一结果。

    Why ``Task`` + ``shield``：
        - 重建放进独立 Task，生命周期**不绑定任何单个请求**：某等待方被取消
          （客户端断开）不会连坐取消重建，缓存照常回写；
        - 等待方 ``await asyncio.shield(task)``，取消等待方不波及共享任务；
        - 任务内部完成 ``write_cache`` / ``after_build``，因此即使发起方中途取消，
          缓存回写也不会丢（否则冷扫 30s 的成果会随一次断连被丢弃）；
        - 完成后按**身份**从表中摘除，避免摘掉后来者新建的任务；
        - 回调里消费一次 ``exception()``，避免无人 await 时
          "Task exception was never retrieved" 告警。

    ⚠️ **等待方必须有预算**（P-6 修复，2026-10-04 上线前全检）
        原实现是裸 ``await asyncio.shield(task)`` —— 等待方**没有任何上限**。
        leader 卡住时，所有等待方会一直等到**全局 240s 兜底**才被取消，
        而前端超时只有 60s（实测 ``/api/v1/ops/lineage`` 两次
        ``超时（>240s）`` 504 与此现象一致）：用户早就断了、服务端白烧。
        现在等待方最多等 ``wait_budget`` 秒；超时返回 ``None``，由调用方
        降级（见 :func:`cached_or_build`）。leader 自身的共享任务**不受影响**，
        照常跑完并回写缓存。

    Returns:
        每个调用方各自的**浅拷贝**——调用方会写入 from_cache/stale 标记，
        共享同一 dict 会让并发响应互相污染。
        ``None`` = 等待超时（降级信号；``build`` 仍在后台继续）。
    """

    async def _run() -> dict[str, Any]:
        data = await build()
        await write_cache(key, data, ttl, stale_window, is_cacheable=is_cacheable,
                          negative_ttl=negative_ttl)
        if after_build is not None:
            try:
                await after_build(data)
            except Exception as e:  # noqa: BLE001 副作用失败不影响缓存回写成果
                logger.warning(f"[swr] after_build {key} failed: {e!r}")
        return data

    task = _inflight.get(key)
    if task is None:
        # 无 await 插入：get 与 set 之间不会被事件循环切换，故「谁当 leader」是原子的。
        task = asyncio.get_running_loop().create_task(_run())
        _inflight[key] = task

        def _cleanup(t: asyncio.Task) -> None:
            if _inflight.get(key) is t:
                _inflight.pop(key, None)
            if not t.cancelled():
                t.exception()  # 消费异常，避免 never-retrieved 告警

        task.add_done_callback(_cleanup)
        return dict(await asyncio.shield(task))

    # ---- 等待方（非 leader）：有界等待 ----
    if wait_budget is None or wait_budget <= 0:
        return dict(await asyncio.shield(task))
    try:
        return dict(await asyncio.wait_for(asyncio.shield(task), timeout=wait_budget))
    except (asyncio.TimeoutError, TimeoutError):
        logger.warning(
            f"[swr] single-flight 等待超预算（>{wait_budget:g}s）：{key} "
            f"降级返回，重建仍在后台继续")
        return None


async def cached_or_build(
    key: str,
    build: Callable[[], Awaitable[dict[str, Any]]],
    *,
    ttl: int = 300,
    stale_window: int = 1800,
    refresh: int = 0,
    rebuild_lock_ttl: int = 120,
    on_metrics: Callable[[str], None] | None = None,
    after_build: Callable[[dict[str, Any]], Awaitable[None]] | None = None,
    is_cacheable: Callable[[Any], bool] | None = None,
    background_build: Callable[[], Awaitable[dict[str, Any]]] | None = None,
    single_flight: bool = False,
    single_flight_wait_budget: float | None = None,
    negative_ttl: int | None = None,
) -> dict[str, Any]:
    """读缓存 -> stale 回旧值 -> refresh 防抖 -> 同步重建 的统一入口。

    Args:
        key: 缓存键（keys.py 集中生成）。
        build: 重算协程（网络阻塞应已在内部 to_thread）。
        ttl: 主键缓存秒数。
        stale_window: 软过期后的旧值保留窗口秒数（硬过期阈值 = ttl + stale_window）。
        refresh: 端点的 refresh 查询参数（0/1）。
        rebuild_lock_ttl: 后台重建锁秒数（需覆盖最坏重建耗时）。
        on_metrics: 命中观测回调，入参 "hit"（含 stale）/ "miss"（同步重建）。
        after_build: 重建成功后的副作用（如 FeatureRun 落库）；失败只记日志。
        is_cacheable: 有效性谓词（默认 ``default_cacheable``：unavailable 空态不落地，
            degraded 用短 TTL）；缺省即套用内置降级策略，无 status 载荷行为不变。
        background_build: **仅供后台重建（stale-while-revalidate）路径**使用的
            重算协程。缺省 None = 与改动前行为完全一致（后台重建复用 ``build``）。
        single_flight: 请求路径的**冷启动惊群保护**（默认 False = 行为与改动前完全一致）。
            置 True 时，全 miss / 冷启动下同 key 的并发请求**只跑一次** ``build``，
            其余等待并共享结果（见 :func:`_single_flight`）。适用前提：``build`` 无
            请求级私有状态、同 key 结果对并发调用方一致。``_build_lineage`` 属此类。
        single_flight_wait_budget: 等待方的等待上限（秒）；``None`` 用模块常量
            ``SINGLE_FLIGHT_WAIT_BUDGET_SECONDS``。仅 ``single_flight=True`` 时生效。
        negative_ttl: **负缓存**（P-5，2026-10-04）。给定时，``status == "unavailable"``
            的载荷不再"完全不写缓存"，而是用 ``min(ttl, negative_ttl)`` 的短 TTL
            落地（不留影子键）。

            为什么需要：``default_cacheable`` 把 ``unavailable`` 视为"要立刻自愈的
            抖动"，这对本该有数据的端点正确；但对**合法且稳定为空**的端点
            （如 ``/etf/overview/series``：本地无历史落库、要靠每日归档累积），
            结果是**每次请求都全量重算** —— 实测该 key 的
            ``skip caching non-cacheable payload`` 出现 89 次。
            这类端点应显式传本参数：既避免重复重算，又只在短时间内固化空态
            （建议 30~60s，远小于正常 TTL 300s）。

            ⚠️ 默认为 ``None`` ⇒ 行为与改动前**完全一致**，不影响任何现有调用点。

    Why ``background_build``（2026-09-19）:
        请求路径与后台重建路径的**时延约束根本不同**。请求路径必须有严格预算
        —— 宁可返回降级载荷也不能把用户挂在慢源上；而**后台重建没有这个约束**
        —— 它正是唯一能把"好数据"重新灌回缓存的路径（``warm_overview_cache``
        之外），若继承请求预算，慢构建（market/overview 实测 23.5~113.5s）会
        100% 超时，后台重建每轮只写回 unavailable 降级载荷 ⇒ 缓存永不自愈，
        用户每次请求都打回冷路径。故两者必须可分离。

    Returns:
        已标注 from_cache / stale / refreshed_recently 的响应数据。

        ``single_flight=True`` 且等待超预算时（见 ``single_flight_wait_budget``），
        可能返回 ``status="unavailable"`` 的降级载荷（不会被缓存，下次立即重试），
        而不是把请求挂到全局超时兜底。
    """

    if single_flight_wait_budget is None:
        single_flight_wait_budget = SINGLE_FLIGHT_WAIT_BUDGET_SECONDS

    def _metrics(kind: str) -> None:
        if on_metrics is not None:
            try:
                on_metrics(kind)
            except Exception:  # noqa: BLE001 埋点失败不影响主流程
                pass

    # ---- 正常读：主键命中直接回；影子键命中回旧值 + 后台重建 ----
    if not refresh:
        val, stale = await RedisClient.get_stale(key)
        if val is not None:
            data = orjson.loads(val)
            data["from_cache"] = True
            if stale:
                data["stale"] = True
                # 后台重建用 background_build（若提供）；缺省时与改动前完全一致（复用 build）。
                _spawn_rebuild(key, background_build if background_build is not None else build,
                               ttl, stale_window,
                               rebuild_lock_ttl, after_build, is_cacheable, negative_ttl)
            _metrics("hit")
            return data

    # ---- refresh=1：防抖锁，5s 内重复刷新合并为一次重算 ----
    if refresh:
        if not await RedisClient.try_lock(f"refresh:{key}", ttl=5):
            val, stale = await RedisClient.get_stale(key)
            if val is not None:
                data = orjson.loads(val)
                data["from_cache"] = True
                data["refreshed_recently"] = True
                if stale:
                    data["stale"] = True
                return data
            # 锁被占但缓存全空（极端并发）：退化为普通重建，不返回空响应

    # ---- 同步重建（缓存 miss / refresh 抢到锁 / refresh 且无缓存可回） ----
    _metrics("miss")
    if single_flight:
        # 冷启动惊群：同 key 只放行一次重建，其余等待并共享结果；
        # 回写与 after_build 由共享任务内部完成，避免重复执行副作用。
        data = await _single_flight(key, build, ttl, stale_window,
                                    after_build, is_cacheable,
                                    wait_budget=single_flight_wait_budget,
                                    negative_ttl=negative_ttl)
        if data is None:
            # P-6：等待超预算 ⇒ 降级。优先回 leader **可能**已经写好的缓存值；
            # 真的还没有就回一个**诚实的**不可用载荷（status="unavailable"，
            # 按 default_cacheable 不落地，下次请求立即重试），而不是让
            # 整个请求挂到全局 240s 兜底、白烧 worker。
            val, _stale = await RedisClient.get_stale(key)
            if val is not None:
                data = orjson.loads(val)
                data["from_cache"] = True
                data["stale"] = True
                return data
            fallback = build_unavailable_payload(key)
            fallback["from_cache"] = False
            return fallback
        data["from_cache"] = "refreshed" if refresh else False
        return data
    data = await build()
    await write_cache(key, data, ttl, stale_window, is_cacheable=is_cacheable,
                      negative_ttl=negative_ttl)
    if after_build is not None:
        try:
            await after_build(data)
        except Exception as e:  # noqa: BLE001 副作用失败不影响响应
            logger.warning(f"[swr] after_build {key} failed: {e!r}")
    data["from_cache"] = "refreshed" if refresh else False
    return data
