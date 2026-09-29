"""实时行情/预警 SSE 广播枢纽（§3.4，Sprint2）。

设计纪律（路线图验收口径）：
- 服务端**单个**抓取任务广播给所有订阅者（asyncio.Queue 每 client 一份；
  快照语义，无需 Last-Event-ID——EventSource 自动重连后拿最新快照即对齐）；
- 多订阅者共享同一次外部抓取：行情抓取复用 /market/quotes 的进程级
  QUOTES_TTL 缓存（market.quotes_snapshot），外部请求次数与订阅数无关；
- 抓取节奏：每 ttl 秒（默认 30，钳制 [15, 120]），≥ 全局限速允许值；
- 最后一个订阅者断开时抓取任务自停（引用计数归零），无僵尸抓取。

alerts 频道：预警引擎（api/v1/alerts.py）触发时 publish_alert 推送，
不在本模块轮询——事件驱动，零外部请求。
"""
from __future__ import annotations

import asyncio
import hashlib
import time as _time
from datetime import datetime

from loguru import logger

from ..core.resilience import is_fatal_base_exception, log_contained

QUOTES_TTL_MIN = 15
QUOTES_TTL_MAX = 120
QUOTES_TTL_DEFAULT = 30
# 单次**外部请求**的标的数上限：也是 `/market/quotes` 的公开契约
# （超限由 `market.py:664` 明确回 ERR_PARAMS=40000，不是静默行为）。
QUOTES_MAX_SYMBOLS = 200
# P1-15（2026-09-21 修复）：快照**分片**大小。腾讯/新浪批量接口是「一次请求拉 N 只」
# （`realtime.fetch_tencent_quotes_batch`），原实现直接把超过 200 只的输入**静默截断**
# ⇒ watchlist 类预警规则（`alerts.py:520` 把全部报价类规则的标的合并成一次快照）
# 第 201 只起**永不触发且完全不可观测**。现在按 200 一片**逐片抓取并合并**，
# 不再丢标的。
QUOTES_SHARD_SIZE = QUOTES_MAX_SYMBOLS
# 单次快照的**总体**上限（分片数 = 800/200 = 4）。这仍是一个真实存在的边界
# （限速 `realtime._MIN_INTERVAL = 0.25s` ⇒ 4 片约 1~2.5s，需落在 30s 推送窗口内），
# 但**绝不静默**：超限时记 WARNING 并在响应里给出
# `truncated` / `dropped_count` / `limit` / `requested` / `returned`（§4.13.3 截断契约）。
QUOTES_MAX_SYMBOLS_TOTAL = 800

# quotes 订阅表：queue -> 该订阅者关注的 symbols（空集合 = 全部）
_quotes_subs: dict[asyncio.Queue, set[str]] = {}
# alerts 订阅表：queue -> None（事件广播，无过滤）
_alerts_subs: set[asyncio.Queue] = set()

# 单例抓取任务；有订阅者时存在，最后一个订阅者离开时自停
_quotes_task: asyncio.Task | None = None


def clamp_quotes_ttl(ttl: int | None) -> int:
    """钳制 quotes 推送间隔到 [15, 120]s（≥ 全局限速允许值）。"""
    if ttl is None or ttl < QUOTES_TTL_MIN:
        return QUOTES_TTL_MIN if ttl is None else max(QUOTES_TTL_MIN, ttl)
    return min(ttl, QUOTES_TTL_MAX)


# ---------------- 共享行情快照缓存（/market/quotes 与 SSE 广播共用） ----------------
# {symbols 排序哈希: (expire_at, payload)}：多页面轮询/订阅对外部源的压力
# 与调用方数量无关——外部请求次数 = 缓存过期次数。
# 用 threading.Lock 而非 asyncio.Lock：临界区是纯 dict 读写（无 await），
# 且模块级 asyncio.Lock 不可跨事件循环复用（pytest 多 loop / lifespan 重启会崩）。
_quotes_cache: dict[str, tuple[float, dict]] = {}
_QUOTES_CACHE_LOCK = __import__("threading").Lock()


async def quotes_snapshot(symbols: list[str]) -> dict:
    """批量实时快照（QUOTES_TTL 进程缓存；降级链：腾讯→新浪→degraded，不造数）。

    **分片（P1-15）**：输入超过 :data:`QUOTES_SHARD_SIZE`（200）时按片抓取后合并，
    不再静默丢弃；仅当超过 :data:`QUOTES_MAX_SYMBOLS_TOTAL` 时截断，且**必然**
    带 ``truncated=True`` + ``dropped_count`` + WARNING。

    Returns:
        ``{as_of, source, quotes, requested, returned, truncated, dropped_count,
        limit, n_shards}``；quotes 为 quote dict 数组（volume 单位=手，与 daily_bar
        对齐；amount=元）。

        * ``source``：单片时即该源（tencent/sina/degraded），多片源不一致时为
          ``"mixed"``（只有内部 >200 只的调用方可能看到，API 侧恒为单片）。
        * ``requested``/``returned``/``truncated``/``dropped_count``/``limit``：
          §4.13.3 截断契约（与 ``alerts.py`` 的 ``truncated``+``*_limit`` 同形）。
    """
    from ..core.config import get_settings
    from .realtime import fetch_quotes_batch

    syms = list(dict.fromkeys(s.strip().upper() for s in symbols if s.strip()))
    requested = len(syms)
    if not syms:
        return {"as_of": None, "source": "degraded", "quotes": [],
                "requested": 0, "returned": 0, "truncated": False,
                "dropped_count": 0, "limit": QUOTES_MAX_SYMBOLS_TOTAL,
                "n_shards": 0}
    dropped = 0
    if requested > QUOTES_MAX_SYMBOLS_TOTAL:
        dropped = requested - QUOTES_MAX_SYMBOLS_TOTAL
        logger.warning(
            f"[quotes_hub] 快照请求 {requested} 只 > 单次总上限 "
            f"{QUOTES_MAX_SYMBOLS_TOTAL} ⇒ 丢弃 {dropped} 只（响应 truncated=true）；"
            f"被丢弃标的的预警规则**本轮不会被评估**")
        syms = syms[:QUOTES_MAX_SYMBOLS_TOTAL]

    cache_key = hashlib.sha1(",".join(sorted(syms)).encode()).hexdigest()
    ttl = get_settings().QUOTES_TTL
    now = _time.time()

    def _disclose(payload: dict) -> dict:
        """挂上**本次调用**的截断口径（缓存命中也必须反映本次的 requested）。"""
        got = len(payload.get("quotes") or [])
        return {**payload, "requested": requested, "returned": got,
                "truncated": dropped > 0, "dropped_count": dropped,
                "limit": QUOTES_MAX_SYMBOLS_TOTAL}

    with _QUOTES_CACHE_LOCK:
        hit = _quotes_cache.get(cache_key)
        if hit and now - hit[0] < ttl:
            return _disclose(hit[1])

    def _fetch_payload() -> dict:
        """线程池执行：**逐片**抓取并合并 + as_of 取快照集中最新。"""
        quotes: list[dict] = []
        srcs: list[str] = []
        for i in range(0, len(syms), QUOTES_SHARD_SIZE):
            part, src = fetch_quotes_batch(syms[i:i + QUOTES_SHARD_SIZE])
            quotes.extend(part)
            srcs.append(src)
        as_of = max((q.get("as_of") or "" for q in quotes), default="")
        uniq = list(dict.fromkeys(srcs))
        source = uniq[0] if len(uniq) == 1 else ("mixed" if uniq else "degraded")
        return {"as_of": as_of or None, "source": source, "quotes": quotes,
                "n_shards": len(srcs)}

    payload = await asyncio.to_thread(_fetch_payload)
    with _QUOTES_CACHE_LOCK:
        _quotes_cache[cache_key] = (_time.time(), payload)
        # 顺手清理过期项，防 dict 膨胀（symbols 集合有限，量级很小）
        for k in [k for k, (exp, _) in _quotes_cache.items()
                  if _time.time() - exp > ttl * 10]:
            _quotes_cache.pop(k, None)
    return _disclose(payload)


# ---------------- quotes ----------------
async def subscribe_quotes(symbols: list[str] | None = None) -> asyncio.Queue:
    """订阅行情快照：返回独立队列；断开时必须 unsubscribe_quotes。"""
    q: asyncio.Queue = asyncio.Queue(maxsize=20)
    _quotes_subs[q] = {s.strip().upper() for s in (symbols or []) if s.strip()}
    await _ensure_quotes_task()
    return q


def unsubscribe_quotes(q: asyncio.Queue) -> None:
    _quotes_subs.pop(q, None)


def _union_symbols() -> list[str]:
    """全部订阅者关注 symbols 的并集（去重保序）；空则 []（抓空集=全市场，不应发生）。"""
    out: list[str] = []
    for syms in _quotes_subs.values():
        for s in syms:
            if s not in out:
                out.append(s)
    return out


async def _ensure_quotes_task() -> None:
    """首订阅者到达时启动抓取循环（幂等）。"""
    global _quotes_task
    if _quotes_task is not None and not _quotes_task.done():
        return
    _quotes_task = asyncio.create_task(_quotes_loop())


def stop_quotes_task() -> None:
    """测试/停机辅助：显式停止抓取循环。"""
    global _quotes_task
    if _quotes_task is not None:
        _quotes_task.cancel()
        _quotes_task = None


async def _quotes_loop() -> None:
    """抓取广播循环：union symbols → 共享缓存抓取 → 按订阅过滤分发。

    退出条件：订阅者归零（引用计数语义，无僵尸抓取）。

    韧性（panic 收口 D 的补漏站）：本循环由 ``asyncio.create_task`` 起，不经 ASGI
    中间件栈 ⇒ B 段 ``PanicGuardMiddleware`` 覆盖不到；而 polars 在 ``dtype == pl.Null``
    列上做单列 sort 抛的 ``pyo3_runtime.PanicException`` 是 ``BaseException`` 子类，
    既有的 ``except Exception`` **漏接** ⇒ 任务直接死亡且**零日志**（只剩「下次有人
    订阅时靠 ``_ensure_quotes_task`` 判 ``done()`` 重建」这条惰性自愈）——正是 D 段
    要封掉的「panic 静默停摆」。
    """

    async def _restart() -> None:
        """崩溃自愈：清空单例引用；若仍有订阅者则**立即**重建。

        两个分支（Exception / BaseException）共用此片段，避免"只改一侧"——
        否则 panic 之后要等到下次订阅才恢复，等于半个静默停摆。
        """
        # `global` 只出现在**唯一**执行赋值的这个作用域：外层的 _quotes_loop 自身
        # 不再对 _quotes_task 赋值，故无需（也不应）再声明一次。
        global _quotes_task
        _quotes_task = None
        if _quotes_subs:
            await _ensure_quotes_task()

    try:
        while _quotes_subs:
            syms = _union_symbols()
            snapshot = await quotes_snapshot(syms) if syms else \
                {"as_of": None, "source": "degraded", "quotes": []}
            event = {"as_of": snapshot.get("as_of"),
                     "source": snapshot.get("source"),
                     "ts": datetime.now().strftime("%H:%M:%S")}
            for q, wanted in list(_quotes_subs.items()):
                quotes = snapshot.get("quotes") or []
                if wanted:
                    quotes = [x for x in quotes if x.get("symbol") in wanted]
                payload = {**event, "quotes": quotes}
                if q.full():
                    try:
                        q.get_nowait()
                    except asyncio.QueueEmpty:  # pragma: no cover
                        pass
                q.put_nowait(payload)
            # 无订阅者是稳态（循环顶部 while 检查），这里按默认节奏 sleep；
            # 抓取共享 /market/quotes 的 QUOTES_TTL 进程缓存，外部压力不随订阅数增长
            await asyncio.sleep(clamp_quotes_ttl(QUOTES_TTL_DEFAULT))
    except asyncio.CancelledError:
        # ⚠️ 必须保持在最前：CancelledError 是 BaseException 子类，且是
        # stop_quotes_task() / lifespan 优雅关闭的机制 ⇒ 静默退出，不记日志、
        # 不 raise、也不落到下方 BaseException 分支。
        pass
    except Exception as e:  # noqa: BLE001 广播循环崩溃自愈：下次订阅重建
        logger.warning(f"[quotes_hub] loop crashed, will restart on next subscribe: {e!r}")
        await _restart()
    except BaseException as exc:  # noqa: BLE001 panic 等非 Exception 兜底
        # [AQP panic 收口 D 补漏] 与其余 7 站同款：必须放行的（CancelledError /
        # KeyboardInterrupt / SystemExit / GeneratorExit）原样抛出，其余留痕后
        # **同样触发自愈重启**（复用同一个 _restart）。
        # 顺序不可颠倒：except Exception 必须在 except BaseException 之前。
        if is_fatal_base_exception(exc):
            raise
        log_contained("quotes_hub", exc)
        await _restart()


# ---------------- alerts ----------------
async def subscribe_alerts() -> asyncio.Queue:
    """订阅预警事件流：返回独立队列；断开时必须 unsubscribe_alerts。"""
    q: asyncio.Queue = asyncio.Queue(maxsize=100)
    _alerts_subs.add(q)
    return q


def unsubscribe_alerts(q: asyncio.Queue) -> None:
    _alerts_subs.discard(q)


def publish_alert(event: dict) -> None:
    """预警事件广播（事件循环线程内调用；满队列丢最旧，慢消费者不拖垮引擎）。"""
    for q in list(_alerts_subs):
        if q.full():
            try:
                q.get_nowait()
            except asyncio.QueueEmpty:  # pragma: no cover
                pass
        q.put_nowait(event)
