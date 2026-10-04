"""参数寻优的**检查点**与**取消标志**（P8-c / A+C，2026-10-04 上线前全检）。

## 为什么需要（问题陈述）

``POST /api/v1/backtest/strategy-run`` 带 ``optimize_params`` 时，前端给 600s、
后端 ``_PATH_BUDGETS`` 给 660s。寻优 = **网格组合数 × 单次回测**，是全仓唯一
真正可能跑到分钟级的同步重活。

在本次修复之前，这条路径有两个真实缺口：

1. **白烧**：客户端断开（或 660s 预算触发 504）后，worker 线程仍会**跑完整张网格**。
   ``asyncio.wait_for`` 只取消外层 ``await``，executor 里的线程不会被取消
   （见 ``core/compute_pool.py`` 文件头的实测结论）。
2. **无中间态**：一次 500 组合的寻优跑到第 480 个组合时进程被杀 / 超时，**全部作废**，
   客户端重试要从头再跑一遍。

## 本模块的职责（A + C 方案，**不碰回测引擎核心循环**）

* **A. 网格级取消**：每个参数组合**跑之前**检查一次取消标志；置位则停止搜索并
  返回已完成的部分。粒度是"一个组合"，因此最坏多跑一个组合 —— 这是刻意的取舍：
  不动 ``backtest/engine.py`` 的日循环，回归面最小。
* **C. 检查点**：每完成一个组合就把该组合的**目标值与指标**落库。同一请求重试时
  **跳过已完成的组合**、直接复用其结果，于是"跑到 480/500 被杀"的重试只需再跑 20 个。

## 为什么按「请求签名」做键

``strategy-run`` 的请求体**包含整张网格**（``optimize_params``），因此同一个
"网格 + 区间 + 策略"的重试会得到**同一个签名**。这意味着：

* 无需新增"任务 ID"概念，因而**不改变现有请求/响应契约**；
* 客户端只要用**完全相同的请求体**重试（前端重试按钮天然如此）就能续跑。

签名刻意包含网格定义：改了网格就是不同的搜索，复用旧结果会是**错误**的。

## 存储

优先 Redis（跨 worker / 跨进程可见）；Redis 不可用时降级为**进程内**字典
（单 worker 仍能续跑，且绝不影响主流程 —— 所有失败路径都静默降级）。
"""
from __future__ import annotations

import hashlib
import json
import threading
import time
from typing import Any

from loguru import logger

# 检查点/取消标志的 TTL（秒）。寻优最坏 660s 预算 ⇒ 存活窗口必须显著更长，
# 否则"重试续跑"还没发生键就过期了。取 1 小时：足够人工重试，也不会长期占内存。
CHECKPOINT_TTL_SECONDS = 3600

_NS = "aqp:opt"

# Redis 不可用时的进程内降级存储（单 worker 语义仍完整）。
# 检查点与取消标志共用同一张表 —— 键前缀已区分（``:ckpt:`` / ``:cancel:``）。
_local_lock = threading.Lock()
_local_checkpoints: dict[str, dict[str, Any]] = {}


# ---------------- 签名 ----------------

def signature_of(payload: dict[str, Any]) -> str:
    """把寻优请求的关键字段归一成稳定签名（sha256 前 32 位）。

    ⚠️ **必须包含网格定义**：改了 ``optimize_params`` 就是另一次搜索，
    复用旧检查点会给出**错误的**"已完成"集合。
    ``sort_keys`` 保证同内容不同键序得到同一签名。
    """
    material = json.dumps(payload, sort_keys=True, separators=(",", ":"),
                          default=str)
    return hashlib.sha256(material.encode("utf-8")).hexdigest()[:32]


def _ckpt_key(sig: str) -> str:
    return f"{_NS}:ckpt:{sig}"


def _cancel_key(sig: str) -> str:
    return f"{_NS}:cancel:{sig}"


# ---------------- 同步 Redis（供 worker 线程使用） ----------------
#
# 刻意**不复用** cache.redis_client.RedisClient：它面向 async 场景且带熔断状态，
# 在 compute pool 的工作线程里调用会污染主循环的连接池。这里每次自建一次性连接、
# 3s 超时、失败一律静默降级（与 data/kpi_series._redis_get_sync 同范式）。

def _sync_redis():  # noqa: ANN202
    """返回一次性同步 Redis 客户端；不可用返回 None。"""
    try:
        import redis  # type: ignore
    except Exception:  # noqa: BLE001 redis 包缺失 ⇒ 降级
        return None
    from ..core.config import get_settings

    s = get_settings()
    if not getattr(s, "REDIS_ENABLED", False):
        return None
    try:
        return redis.Redis(
            host=s.REDIS_HOST, port=s.REDIS_PORT, db=s.REDIS_DB,
            password=s.REDIS_PASSWORD, decode_responses=True,
            socket_timeout=3, socket_connect_timeout=3,
        )
    except Exception as e:  # noqa: BLE001
        logger.warning(f"[opt_checkpoint] Redis 连接创建失败，降级为进程内: {e!r}")
        return None


def _sget(key: str) -> str | None:
    cli = _sync_redis()
    if cli is None:
        return _local_get(key)
    try:
        try:
            return cli.get(key)
        finally:
            cli.close()
    except Exception as e:  # noqa: BLE001
        logger.warning(f"[opt_checkpoint] Redis 读失败 {key}：{e!r}（降级进程内）")
        return _local_get(key)


def _sset(key: str, value: str, ttl: int) -> None:
    cli = _sync_redis()
    if cli is None:
        _local_set(key, value)
        return
    try:
        try:
            cli.set(key, value, ex=ttl)
        finally:
            cli.close()
    except Exception as e:  # noqa: BLE001
        logger.warning(f"[opt_checkpoint] Redis 写失败 {key}：{e!r}（降级进程内）")
        _local_set(key, value)


def _local_get(key: str) -> str | None:
    """进程内降级读。

    ⚠️ 存的是**原始字符串**（而不是解析后的 JSON）：本模块有两类值 ——
    检查点是 JSON、取消标志是裸时间戳字符串。若在此处统一 ``json.loads`` 再取
    ``payload`` 字段，取消标志就会被解析成 ``None``，导致
    "置位了却读不出来"（实测踩过）。原样存取对两类值都正确。
    """
    with _local_lock:
        rec = _local_checkpoints.get(key)
        if rec is None:
            return None
        if time.time() - float(rec.get("_ts", 0)) > CHECKPOINT_TTL_SECONDS:
            _local_checkpoints.pop(key, None)
            return None
        return rec.get("raw")


def _local_set(key: str, value: str) -> None:
    with _local_lock:
        _local_checkpoints[key] = {"_ts": time.time(), "raw": value}


# ---------------- 检查点 读/写 ----------------

def load_checkpoint(sig: str) -> dict[str, Any]:
    """读取已完成组合表；无则返回空结构。

    返回 ``{"done": {combo_key: {"objective": float, "metrics": {...},
    "params": {...}}}, "n_done": int}``。
    """
    raw = _sget(_ckpt_key(sig))
    if not raw:
        return {"done": {}, "n_done": 0}
    try:
        data = json.loads(raw)
    except Exception as e:  # noqa: BLE001 坏数据当作没有检查点（绝不因它失败）
        logger.warning(f"[opt_checkpoint] 检查点解析失败 sig={sig}：{e!r}")
        return {"done": {}, "n_done": 0}
    done = data.get("done") or {}
    return {"done": done, "n_done": len(done)}


def save_trial(sig: str, combo_key: str, params: dict[str, Any],
               objective: float, metrics: dict[str, Any],
               total: int | None = None) -> None:
    """记录一个**已完成**的参数组合（读改写；寻优是单线程顺序执行，无并发写冲突）。

    读改写而非 append：Redis 无原生"局部更新 JSON 字段"，而检查点体积很小
    （每组合几个数字），全量重写的代价可忽略。
    """
    raw = _sget(_ckpt_key(sig))
    try:
        data = json.loads(raw) if raw else {}
    except Exception:  # noqa: BLE001
        data = {}
    done = data.get("done") or {}
    done[combo_key] = {
        "params": params,
        "objective": (None if objective != objective  # noqa: PLR0124 NaN
                      else round(float(objective), 8)),
        "metrics": metrics,
    }
    data["done"] = done
    data["n_done"] = len(done)
    data["total"] = total
    data["updated_at"] = time.time()
    _sset(_ckpt_key(sig), json.dumps(data, default=str), CHECKPOINT_TTL_SECONDS)


def clear_checkpoint(sig: str) -> None:
    """清除检查点（搜索成功结束后调用，避免陈旧续跑集合被复用）。"""
    _delete(_ckpt_key(sig))


def _delete(key: str) -> None:
    """删除一个键（Redis 优先，失败/不可用则删进程内）。"""
    cli = _sync_redis()
    if cli is None:
        with _local_lock:
            _local_checkpoints.pop(key, None)
        return
    try:
        try:
            cli.delete(key)
        finally:
            cli.close()
    except Exception as e:  # noqa: BLE001
        logger.warning(f"[opt_checkpoint] 删除键失败 {key}：{e!r}（降级进程内）")
        with _local_lock:
            _local_checkpoints.pop(key, None)


def combo_key(params: dict[str, Any]) -> str:
    """参数组合的稳定键（与签名同法归一，保证可跨进程比对）。"""
    return json.dumps(params, sort_keys=True, separators=(",", ":"), default=str)


# ---------------- 取消标志 ----------------

def request_cancel(sig: str) -> None:
    """置位取消标志（由 ``POST /backtest/strategy-run/cancel`` 调用）。"""
    _sset(_cancel_key(sig), str(time.time()), CHECKPOINT_TTL_SECONDS)


def is_cancelled(sig: str) -> bool:
    """取消标志是否置位（由网格循环**每个组合前**调用）。"""
    if not sig:
        return False
    return _sget(_cancel_key(sig)) is not None


def clear_cancel(sig: str) -> None:
    """清除取消标志（搜索开始/结束时调用，避免上一轮的取消误伤下一轮）。"""
    _delete(_cancel_key(sig))


class CancelledSearch(Exception):
    """网格搜索被显式取消（由本模块的取消标志触发）。

    刻意与 ``ValueError``（参数非法）区分：取消**不是错误**，调用方应返回
    "已完成部分"而不是 ``ERR_BT_OPTIMIZE_FAILED``。
    """
