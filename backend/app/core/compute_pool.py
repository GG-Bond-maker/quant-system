"""重计算专用线程池：与全站默认池隔离，限制泄漏的爆炸半径。

## 为什么需要这个模块（2026-09-30 上线前全检 · 头号 P0）

``asyncio.wait_for(coro, budget)`` 到期时**只取消外层 await**。若 ``coro`` 是
``asyncio.to_thread(fn, ...)``，而 ``fn`` 正阻塞在**不可中断的系统调用**里
（socket read / ``requests`` 未设 timeout 的调用），那么 executor 内的那个 worker
**不会被取消**，会继续占槽直到它自己返回。

而默认 executor 是 ``ThreadPoolExecutor(max_workers=min(32, cpu+4))``（本机 18 核 ⇒
**22** 槽），且**全站共用**——包含 ``/health/ready`` 就绪探针。实测：22 路
``GET /market/overview?refresh=1`` 并发后，``/health/ready`` 挂死 12008ms
（对照 ``/health/live`` 仍 200/10.4ms），容器被判 unhealthy；
且因平台契约"HTTP 恒 200"，**22/22 请求全部返回 200、零 5xx ⇒ 监控完全看不见**。

⇒ 把重计算下沉到**独立池**，使泄漏被隔离在计算池内，不再拖死探针与其它端点。

## ⚠️ 本模块**不**解决"进程无法退出"（B7）—— 实测结论，勿误信直觉

**直觉方案 A：把 worker 变成 daemon 线程。→ 实测无效。**
``concurrent.futures.thread._python_exit``（经 ``threading._register_atexit`` 注册）
在解释器退出时对 ``_threads_queues`` 里**每一个** worker 无条件 ``t.join()``，
**不检查 daemon 标志**。实测：daemon 化后阻塞 worker 仍使进程 ``rc=124``。
（且 ``ThreadPoolExecutor`` 未提供 daemon 开关；重写 ``_adjust_thread_count``
在 ``super()`` 之后改属性会抛 ``RuntimeError: cannot set daemon status of active
thread``，因为父类已 ``t.start()`` —— 这条路根本走不通。）

**直觉方案 B：``pool.shutdown(wait=False)``。→ 实测无效**（仍 ``rc=124``）。

**唯一有效方案：让阻塞 worker 能自己返回。**
即给下游调用设超时 —— 见 ``main.py`` lifespan 的 ``socket.setdefaulttimeout(...)``
与各 HTTP 客户端的 per-call timeout。
实测：加 ``socket.setdefaulttimeout(1.5)`` 后，同样的阻塞场景 **``rc`` 由 124 → 0**。

⇒ 因此本模块的职责**仅为**"隔离爆炸半径"；**B7（进程退出挂起）必须由 socket 超时解决**。
两者互补，但**不可互相替代**：只有专用池 ⇒ 进程仍退不出；只有 socket 超时 ⇒
线程最终能释放、但期间仍占着默认池的槽（含探针）。

## 与默认池的关系

``asyncio.to_thread`` 走的是 event loop 的 **默认 executor**；本模块通过
``loop.run_in_executor(get_compute_pool(), fn, ...)`` 显式指定，故：

* 默认池只承载轻量 ``to_thread`` 调用（如就绪探针），不再被重计算挤占；
* ``asyncio.run`` 收尾的 ``loop.shutdown_default_executor()`` 不会 join 到
  计算池的 worker —— 这也是"隔离"在退出路径上的体现。
"""

from __future__ import annotations

import asyncio
import concurrent.futures
import functools
import logging
import threading
from typing import Any, Callable

logger = logging.getLogger(__name__)

# 计算池并发度。
#
# 【P-3 修复 2026-10-04 上线前全检】此前这里是**独立的**默认值 6，读独立环境变量
# ``AQP_COMPUTE_CONCURRENCY``；而 ``core/compute_guard.py`` 的信号量读
# ``Settings.COMPUTE_CONCURRENCY``（当时默认 2、且被 ``le=2`` 硬顶）。
# 于是**两套闸门语义相同却各自为政**：
#   * 运维按文档把 ``AQP_COMPUTE_CONCURRENCY`` 调到 6，以为放开了重计算并发，
#     但 research/backtest/export/desk/portfolio/datacenter 走的是那个**恒为 2**
#     的信号量 —— 实测日志里 ``code=40103 计算资源繁忙，请稍后重试`` 出现 10 次；
#   * 两个数字谁生效取决于"请求走的是 to_thread 还是 run_in_executor"，无法推理。
#
# 现在默认**同源**：取 ``Settings.COMPUTE_CONCURRENCY``；仅当显式设置
# ``AQP_COMPUTE_CONCURRENCY`` 时才覆盖（保留按机型微调的能力）。
#
# 取值的量纲理由：这是**I/O 与 CPU 混合**的池（parquet 读 + 外部 HTTP），不是纯
# CPU 池，故可以大于核数；但必须**显著小于**默认池的 ``min(32, cpu+4)``（本机 18 核
# ⇒ 22），否则池满时仍会与 ``/health/ready`` 探针争抢。
_DEFAULT_COMPUTE_CONCURRENCY = 6


def _resolve_concurrency() -> int:
    """解析并发度：``AQP_COMPUTE_CONCURRENCY``（显式覆盖）优先，否则取 Settings。"""
    import os

    raw = os.environ.get("AQP_COMPUTE_CONCURRENCY", "").strip()
    if raw:
        try:
            value = int(raw)
        except ValueError:
            logger.warning("[compute_pool] AQP_COMPUTE_CONCURRENCY=%r 非整数，改用 "
                           "Settings.COMPUTE_CONCURRENCY", raw)
        else:
            if value >= 1:
                return value
            logger.warning("[compute_pool] AQP_COMPUTE_CONCURRENCY=%d < 1，改用 "
                           "Settings.COMPUTE_CONCURRENCY", value)

    # 默认与 compute_guard 同源（见上方注释）。Settings 解析失败也不该让池起不来。
    try:
        from .config import get_settings

        return max(1, int(get_settings().COMPUTE_CONCURRENCY))
    except Exception as exc:  # noqa: BLE001
        logger.warning("[compute_pool] 读取 Settings.COMPUTE_CONCURRENCY 失败（%r），"
                       "退回默认 %d", exc, _DEFAULT_COMPUTE_CONCURRENCY)
        return _DEFAULT_COMPUTE_CONCURRENCY


_COMPUTE_CONCURRENCY = _resolve_concurrency()

# ⚠️ 池必须是**可重建**的，不能是一个"用完即死"的模块级单例。
# 原因：lifespan 收尾会调用 :func:`shutdown_compute_pool`，而 shutdown 之后的
# ``ThreadPoolExecutor`` 永久不可用（再 submit 会抛
# ``RuntimeError: cannot schedule new futures after shutdown``）。
# 生产进程只有一次 lifespan 所以看不出问题，但**测试里同一个进程会多次进出
# lifespan**（每个 TestClient 一次）⇒ 第二次起全部提交失败，
# 表现为 overview/etf 的超时分支静默变成 `source=error` 而非 `source=timeout`。
# 因此这里用"懒创建 + shutdown 后置空"的访问器，使进程可安全地多轮启停。
_pool: concurrent.futures.ThreadPoolExecutor | None = None
_pool_lock = threading.Lock()


def get_compute_pool() -> concurrent.futures.ThreadPoolExecutor:
    """返回当前计算池；若尚未创建或已被 shutdown，则新建一个。"""
    global _pool
    with _pool_lock:
        if _pool is None:
            _pool = concurrent.futures.ThreadPoolExecutor(
                max_workers=_COMPUTE_CONCURRENCY,
                thread_name_prefix="aqp-compute",
            )
        return _pool


def compute_pool_stats() -> dict[str, int]:
    """返回池的轻量统计，供健康检查/诊断使用。

    ``ThreadPoolExecutor`` 不公开队列长度与活跃数，这里只给出
    线程名前缀匹配的存活线程数 —— 它是"泄漏是否发生"最直接的观测点：
    稳态下应 ≈ 空闲数（通常 0~并发数），持续等于 ``max_workers`` 且不回落
    ⇒ 说明 worker 全部被阻塞占死。
    """
    alive = [t for t in threading.enumerate()
             if t.name.startswith("aqp-compute")]
    return {"max_workers": _COMPUTE_CONCURRENCY, "alive_threads": len(alive)}


async def run_in_compute_pool(fn: Callable[..., Any], *args: Any, **kwargs: Any) -> Any:
    """在**专用计算池**里跑同步函数并 await 结果（P-8 统一入口）。

    为什么要有这个统一入口（而不是各处各写 ``loop.run_in_executor``）：
    "哪些活该进专用池"是**全站一致性**问题，散落实现必然漂移 —— 本项目已经
    因此踩过一次坑：``compute_pool`` 存在后，只有 ``market.py`` / ``etf.py``
    迁了过来，``datacenter`` / ``ops`` / ``research`` / ``backtest`` 等重活仍走
    ``asyncio.to_thread``（**event loop 默认池**，本机 22 槽，与 ``/health/ready``
    探针**共用**）。实测后果（``qa-lead-poolexhaust.log``）：22 路并发冷算期间
    ``/health/ready`` 挂死 **12008ms**，而 compose 探针超时只有 5s ⇒ 容器被判
    unhealthy；且因平台契约"HTTP 恒 200"，22/22 全返回 200、**监控完全看不见**。

    与 ``asyncio.to_thread`` 的唯一区别是"提交到哪个池"：
    * ``asyncio.to_thread`` → event loop 默认池（探针、SSE、轻量端点共用）；
    * 本函数 → 专用计算池（仅重计算），故重活泄漏**不会**拖死探针与轻量端点。

    ``**kwargs`` 与 ``asyncio.to_thread`` 对齐（2026-10-04 补）：两者都是"提交
    同步函数"的统一入口，参数契约必须一致，否则迁移时"看着能替换、实际报
    TypeError"（be-pool 迁移 screener 时实测踩到）。``run_in_executor`` 本身不
    接受 kwargs，故仅在有关键字参数时用 ``functools.partial`` 绑定；无 kwargs 时
    走原路径，零额外开销、完全向后兼容。

    注意：这**不**改变取消语义。``asyncio.wait_for`` 到期仍只取消外层 await，
    executor 里阻塞在不可中断系统调用上的 worker 不会被取消（见模块 docstring 的
    实测结论）——专用池的职责只是**隔离爆炸半径**。
    """
    loop = asyncio.get_running_loop()
    target: Callable[..., Any] = functools.partial(fn, **kwargs) if kwargs else fn
    return await loop.run_in_executor(get_compute_pool(), target, *args)


def shutdown_compute_pool(*, wait: bool = False) -> None:
    """关闭计算池并把单例置空（lifespan 收尾调用）。

    ``wait=False`` + ``cancel_futures=True``：**不等**阻塞 worker，直接放弃排队中的
    任务。注意这**不能**让进程退出（见模块 docstring 的实测结论）——它只是避免
    收尾阶段人为地多等一轮；真正的退出保障来自 socket 超时。

    置空后，下一次 :func:`get_compute_pool` 会新建一个池 ⇒ 进程可多轮启停。
    """
    global _pool
    with _pool_lock:
        pool, _pool = _pool, None
    if pool is None:
        return
    try:
        pool.shutdown(wait=wait, cancel_futures=True)
    except Exception as exc:  # noqa: BLE001 - 收尾阶段绝不因清理失败拖垮进程
        logger.warning("[compute_pool] shutdown 异常（已忽略）: %r", exc)
