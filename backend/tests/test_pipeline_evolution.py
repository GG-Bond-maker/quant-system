"""测试任务调度 DAG 演进、图论 CPM、SLA 建模、断点续跑与管道锁透视。"""
import os
import pytest
from httpx import ASGITransport, AsyncClient

from app.api.v1.ops import _DAG_STEP_SPEC, _compute_cpm_and_sla
from app.main import app
from app.orchestrator import FULL_STEPS


@pytest.fixture
def auth_headers() -> dict[str, str]:
    return {"Authorization": f"Bearer {os.environ.get('ADMIN_TOKEN', 'aqp-dev-token-change-me')}"}


def test_dag_cpm_mathematical_consistency():
    """验证图论 CPM 前向/后向推导数学自洽性：
    1. 11 步与 FULL_STEPS 严格对齐
    2. ES <= EF, LS <= LF
    3. Total Float TF = LS - ES = LF - EF >= 0
    4. Free Float FF <= TF
    5. 关键路径节点必定满足 TF == 0
    6. 关键路径总时长严格等于项目完工时长
    """
    assert [s["id"] for s in _DAG_STEP_SPEC] == FULL_STEPS

    base_durations = {s["id"]: s["base_duration_s"] for s in _DAG_STEP_SPEC}
    res = _compute_cpm_and_sla(base_durations)

    timeline = {t["id"]: t for t in res["timeline"]}
    crit_path = res["critical_path"]
    proj_dur = res["project_duration_s"]

    # 验证关键路径上每个节点
    for cid in crit_path:
        t = timeline[cid]
        assert t["is_critical_path"] is True
        assert t["total_float_s"] == 0
        assert t["slack_s"] == 0

    # 验证主干生产链必定在关键路径上
    for essential in ["update_daily", "validate", "build_features", "infer", "screener_dump", "build_cs_mirror"]:
        assert essential in crit_path

    # 旁路公告同步具备充足浮动时差 (Slack > 0)
    assert timeline["sync_announcements"]["total_float_s"] > 0
    assert timeline["sync_announcements"]["is_critical_path"] is False

    # 验证所有节点的时差等式
    for t in res["timeline"]:
        assert t["ef_s"] == t["es_s"] + t["duration_s"]
        assert t["lf_s"] == t["ls_s"] + t["duration_s"]
        assert t["total_float_s"] == t["ls_s"] - t["es_s"]
        assert t["free_float_s"] <= t["total_float_s"]
        assert t["ef_s"] <= proj_dur
        assert t["lf_s"] <= proj_dur

    # 关键路径累计时长等于项目完工总工期
    crit_duration_sum = sum(timeline[cid]["duration_s"] for cid in crit_path)
    assert crit_duration_sum == proj_dur

    # SLA 预测指标自洽性
    sla = res["sla_metrics"]
    assert sla["budget_seconds"] == 10800
    assert sla["p50_duration_s"] <= sla["p95_duration_s"]
    assert sla["buffer_remaining_s"] == sla["budget_seconds"] - sla["p95_duration_s"]
    assert 0 <= sla["compliance_probability"] <= 100
    assert sla["sla_status"] in ("COMPLIANT", "AT_RISK", "BREACHED")


@pytest.mark.asyncio
async def test_dag_endpoint_full_contract(auth_headers):
    """验证 GET /api/v1/ops/dag 契约完整性：11 步真实拓扑、CPM、SLA、守护任务与锁状态。"""
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
        resp = await ac.get("/api/v1/ops/dag", headers=auth_headers)
        assert resp.status_code == 200
        data = resp.json()["data"]

        # 1. 阶段步骤数完整对齐 11 步
        assert len(data["stages"]) == 11
        stage_ids = [s["id"] for s in data["stages"]]
        assert stage_ids == FULL_STEPS

        # 2. 检查拓扑边
        assert len(data["dag_edges"]) >= 10
        edge_pairs = [(e["source"], e["target"]) for e in data["dag_edges"]]
        assert ("update_daily", "validate") in edge_pairs
        assert ("build_features", "infer") in edge_pairs
        assert ("infer", "screener_dump") in edge_pairs
        assert ("screener_dump", "build_cs_mirror") in edge_pairs

        # 3. 甘特图时间线
        assert len(data["gantt_timeline"]) == 11
        assert "critical_path" in data
        assert len(data["critical_path"]) > 0

        # 4. SLA 预测与分片监控
        assert "sla_metrics" in data
        assert data["sla_metrics"]["budget_seconds"] == 10800
        assert "sharding_metrics" in data
        assert data["sharding_metrics"]["total_shards"] == 8

        # 5. 定时常驻触发器
        assert "scheduled_triggers" in data
        triggers = data["scheduled_triggers"]
        assert len(triggers) == 4
        trigger_ids = [t["id"] for t in triggers]
        assert "pre_market_init" in trigger_ids
        assert "midday_qc" in trigger_ids
        assert "evening_routine" in trigger_ids
        assert "weekend_reconcile" in trigger_ids

        # 6. 管道锁透明度
        assert "lock_status" in data
        lock = data["lock_status"]
        assert "is_locked" in lock
        assert "owner" in lock
        assert "elapsed_seconds" in lock
        assert "status" in lock


@pytest.mark.asyncio
async def test_dag_rerun_parameters_validation(auth_headers):
    """验证 POST /api/v1/ops/dag/rerun 参数容错与防护：
    1. 格式错误日期返回 40000
    2. 未知断点步骤返回 40000
    3. 未知指定步骤子集返回 40000
    """
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
        # 非法日期
        resp = await ac.post("/api/v1/ops/dag/rerun", json={"trade_date": "invalid-date"}, headers=auth_headers)
        assert resp.status_code == 200
        assert resp.json()["code"] == 40000

        # 未知断点步骤
        resp2 = await ac.post("/api/v1/ops/dag/rerun", json={"trade_date": "2026-09-30", "resume_from_step": "ghost_step"}, headers=auth_headers)
        assert resp2.status_code == 200
        assert resp2.json()["code"] == 40000

        # 未知步骤子集
        resp3 = await ac.post("/api/v1/ops/dag/rerun", json={"trade_date": "2026-09-30", "steps": ["non_existent"]}, headers=auth_headers)
        assert resp3.status_code == 200
        assert resp3.json()["code"] == 40000
