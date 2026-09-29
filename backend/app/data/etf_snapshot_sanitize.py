"""ETF 快照存档的**旧口径假零净化**与**恢复合并**（单一事实源）。

## 背景（2026-09-28 事故）

``aqp:etf:snap:history`` 被「清除缓存」误删（8 条 → 1 条），事后只能从 Redis
AOF 里恢复出 8 条**旧口径**存档。旧口径的 ``net_inflow_yi`` 有个致命歧义：

- ``0.0`` 既可能是**真实零净流入**，也可能是**取数失败被写成 0**；
- 二者在旧存档里**无法区分**（当时没有 ``flow`` 子对象记录来源/状态）。

按本项目诚实性红线，不可分辨的零**不得冒充真实指标**，故一律如实置 ``null``
并附 ``flow.status="unavailable"`` + 写明原因；非零旧值也**不得**断言
``status="ok"`` —— 它同样来源不可考，只能用口径标注字段说明。

## 为什么是模块而不是一次性脚本

净化规则要能被测试锁住、且对**任何**读取路径生效（历史脏数据不只存在 Redis 里
这一份，将来任何来源的旧格式存档都必须同样处理）。故规则落在
:func:`sanitize_etf_snapshots`，由 ``kpi_series._read_etf_snapshots`` 在读路径
统一调用；本模块同时提供恢复用的 :func:`merge_etf_snapshots` /
:func:`build_restored_archive`，供运维脚本复用同一套规则。

## 幂等性

:func:`sanitize_etf_snapshots` **必须幂等**（读路径每次都跑）：
已净化的行（``net_inflow_yi=None`` + ``flow`` 子对象）走「新口径」分支原样返回，
重复调用不会再改一次。
"""
from __future__ import annotations

import copy
from datetime import datetime, timezone
from typing import Any

# 存档键 TTL：与 ``api/v1/etf.py::append_etf_snapshot`` 的写入口径保持一致
# （60 天）。两处必须同源，否则恢复写入会把 TTL 悄悄改掉。
SNAP_TTL_SECONDS = 3600 * 24 * 60

# 净化的目标字段（顶层资金流净额）。
FLOW_FIELD = "net_inflow_yi"

# 旧口径「假零」的统一说明文案。
# ⚠️ 必须写明「无法区分真实零净流入与取数失败」—— 只写「数据缺失」会掩盖
#    真正的问题（旧口径把失败写成 0），与项目「如实披露」红线冲突。
LEGACY_FLOW_UNAVAILABLE_REASON = (
    "旧口径归档（2026-09-28 存档被误删后由 Redis AOF 恢复）："
    "net_inflow_yi=0.0 无法区分「真实零净流入」与「取数失败被写成 0」，"
    "故如实置为不可用，不合成替代指标"
)

# 非零旧值的口径标注：来源与口径不可考 ⇒ 只能标 legacy_unverified，**不得**
# 写 status="ok"（那是在断言一件无法核验的事）。
LEGACY_FLOW_CALIBER = "legacy_unverified"
LEGACY_FLOW_CALIBER_NOTE = (
    "旧口径归档（2026-09-28 存档被误删后由 Redis AOF 恢复）：数值直接取自"
    "历史快照，当时未记录资金流来源与口径，无法核验，故不标注为 ok"
)


def _is_legacy_zero(value: Any) -> bool:
    """是否为「旧口径的零」（含 ``-0.0`` / 整型 ``0``）。

    字符串 ``"0.0"`` 不算：它是另一个层面的脏数据，交给上层按 ``None`` 处理，
    本模块不替它背书。
    """
    return isinstance(value, (int, float)) and not isinstance(value, bool) and value == 0


def _has_flow_block(snap: dict[str, Any]) -> bool:
    """是否已有 ``flow`` 子对象（新口径：可信度由写入方自己标注）。"""
    return isinstance(snap.get("flow"), dict)


def sanitize_etf_snapshots(snapshots: list[dict] | None) -> list[dict]:
    """净化 ETF 快照存档里的**旧口径假零**（纯函数、幂等、不改入参）。

    规则（只针对**没有** ``flow`` 子对象的旧口径行）：

    1. ``net_inflow_yi`` 为零 ⇒ 置 ``None``，并补
       ``flow={status: "unavailable", net_inflow_yi: None, reason: ...}``。
       理由：``0.0`` 无法区分真实零净流入与取数失败，冒充真实指标即踩红线。
    2. ``net_inflow_yi`` 非零 ⇒ **保留数值**，但只加口径标注字段
       （``net_inflow_caliber="legacy_unverified"`` + 说明），
       **不写** ``flow.status="ok"`` —— 来源不可考就不能断言可用。
    3. ``net_inflow_yi`` 已是 ``None`` 且无 ``flow`` ⇒ **原样保留**：null 本身
       就是"无数据"，再补一个 reason 等于替历史数据编造原因，同样踩红线。
    4. 已有 ``flow`` 子对象 ⇒ 新口径行，**一个字节都不动**。

    Args:
        snapshots: 原始快照列表（元素非 dict 时跳过）。

    Returns:
        净化后的**新**列表（深拷贝，入参不被修改）；顺序与输入一致。
    """
    out: list[dict] = []
    for raw in snapshots or []:
        if not isinstance(raw, dict):
            continue
        snap: dict[str, Any] = copy.deepcopy(raw)
        if _has_flow_block(snap):
            # 新口径（写入方已标注可信度）⇒ 不作任何推断，原样返回
            out.append(snap)
            continue
        value = snap.get(FLOW_FIELD)
        if value is None:
            # 规则 3：null 即"无数据"，不为它编造 reason
            out.append(snap)
            continue
        if _is_legacy_zero(value):
            # 规则 1：不可分辨的零 ⇒ 如实置不可用
            snap[FLOW_FIELD] = None
            snap["flow"] = {
                "status": "unavailable",
                "net_inflow_yi": None,
                "reason": LEGACY_FLOW_UNAVAILABLE_REASON,
            }
        else:
            # 规则 2：数值保留，但只标口径、不标 ok
            snap["net_inflow_caliber"] = LEGACY_FLOW_CALIBER
            snap["net_inflow_note"] = LEGACY_FLOW_CALIBER_NOTE
        out.append(snap)
    return out


def merge_etf_snapshots(existing: list[dict] | None,
                        recovered: list[dict] | None,
                        *,
                        recovered_at: str | None = None) -> list[dict]:
    """按 ``date`` 合并现有存档与恢复出来的存档（同日以**现有**行为准）。

    为什么同日以现有为准：现有行是**误删之后**由真实访问/定时任务重写出来的
    （含 ``archived_post_close`` 等新口径字段），比 AOF 里挖出来的旧行更新、
    更可信；恢复只用于补回**已经不存在**的日期。

    Args:
        existing: Redis 中当前的存档（可为 None/空）。
        recovered: 从 AOF 恢复出来的存档（可为 None/空）。
        recovered_at: 恢复时间戳（ISO 秒级）；None 时取当前 UTC 时间。

    Returns:
        按 ``date`` 升序的合并列表（深拷贝，入参不被修改）。仅存在于
        ``recovered`` 的行会被打上 ``recovered=True`` 与 ``recovered_at``。
    """
    stamp = recovered_at or datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S")
    by_date: dict[str, dict] = {}
    for snap in existing or []:
        if not isinstance(snap, dict):
            continue
        d = str(snap.get("date") or "")
        if d:
            by_date[d] = copy.deepcopy(snap)
    for snap in recovered or []:
        if not isinstance(snap, dict):
            continue
        d = str(snap.get("date") or "")
        if not d or d in by_date:
            # 该日期已有（更新的）现有行 ⇒ 以现有行为准，不覆盖
            continue
        merged = copy.deepcopy(snap)
        merged["recovered"] = True
        merged["recovered_at"] = stamp
        by_date[d] = merged
    return sorted(by_date.values(), key=lambda s: str(s.get("date") or ""))


def build_restored_archive(existing: list[dict] | None,
                           recovered: list[dict] | None,
                           *,
                           recovered_at: str | None = None) -> list[dict]:
    """合并 + 净化（恢复写回 Redis 前**唯一**应使用的入口）。

    顺序固定为「先合并、后净化」：现有行本身已是新口径（净化对其幂等），
    统一过一遍净化可保证**任何**来源的旧格式行都不会带着假零落库。
    """
    return sanitize_etf_snapshots(
        merge_etf_snapshots(existing, recovered, recovered_at=recovered_at))
