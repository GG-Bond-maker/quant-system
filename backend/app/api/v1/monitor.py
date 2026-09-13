"""因子健康度监控 API（前沿演进 Phase 1 / 维度五）。

GET  /monitor/health  读取最近一次监控快照（含滚动 IC / 半衰期 / PSI / 状态机）；
POST /monitor/run     立即运行监控（researcher；含漂移告警与自动重训触发）。
"""
from __future__ import annotations

import asyncio

from fastapi import APIRouter, Depends

from ...core.auth import require_role
from ...core.errors import APIResponse, AQPException, ERR_DATA_EMPTY, ok
from ...ml import monitor

router = APIRouter()


@router.get("/health")
async def monitor_health(    _user: dict = Depends(require_role("viewer")),
) -> APIResponse[dict]:
    """最近一次监控快照；尚未运行时返回 ok 快照（state=unknown）供前端渲染。"""
    snap = monitor.get_health_snapshot()
    if snap is None:
        return ok({"ok": True, "state": "unknown", "ic_state": "unknown",
                   "drift_state": "unknown",
                   "note": "尚未运行监控（POST /monitor/run 或等待晚间例行）",
                   "retrain": None})
    snap = dict(snap)
    snap["retrain"] = monitor.get_retrain_record()
    return ok(snap)


@router.post("/run")
async def monitor_run(
    _user: dict = Depends(require_role("researcher")),
) -> APIResponse[dict]:
    """立即运行一次监控（真实计算：滚动 RankIC + 半衰期 + PSI）。"""
    snap = await asyncio.to_thread(monitor.run_monitor, "manual")
    if not snap.get("ok"):
        raise AQPException(ERR_DATA_EMPTY, snap.get("error") or "监控数据不足")
    snap["retrain"] = monitor.get_retrain_record()
    return ok(snap)
