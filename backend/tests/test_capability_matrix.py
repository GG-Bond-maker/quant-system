"""单元测试：多数据源能力路由矩阵（Capability Routing Matrix）。"""
from __future__ import annotations

import pytest

from app.data.capability_matrix import (
    CapabilityId,
    CapabilityStatus,
    get_capability_matrix,
    probe_capability_health,
)


def test_probe_capability_health() -> None:
    """测试单个数据源能力的健康探针。"""
    for cap_id in CapabilityId:
        status, latency, provider = probe_capability_health(cap_id)
        assert status in (CapabilityStatus.HEALTHY, CapabilityStatus.DEGRADED)
        assert latency >= 0.0
        assert isinstance(provider, str)
        assert len(provider) > 0


def test_get_capability_matrix() -> None:
    """测试获取全量数据源能力矩阵快照与统计指标。"""
    matrix = get_capability_matrix(force_refresh=True)

    assert "capabilities" in matrix
    assert "usable_count" in matrix
    assert "total_count" in matrix
    assert "all_healthy" in matrix
    assert "as_of" in matrix

    caps = matrix["capabilities"]
    assert len(caps) == 5

    cap_ids = {c["id"] for c in caps}
    expected_ids = {"daily", "realtime", "minute", "financials", "futures"}
    assert cap_ids == expected_ids

    assert matrix["usable_count"] >= 4
    assert matrix["total_count"] == 5

    for c in caps:
        assert "label" in c
        assert "provider" in c
        assert "latency_ms" in c
        assert "usable" in c
        assert c["usable"] is True
