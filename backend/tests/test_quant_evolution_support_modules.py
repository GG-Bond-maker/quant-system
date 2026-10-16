"""测试五大支撑与情报板块的量化演进端点：
1. 数据中心：PIT 点截面财务时滞探测与动态复权因子链对账；
2. 数据质量：特征协变量漂移 PSI 与 KS 稳定性检验；
3. 任务调度：DAG 关键路径甘特图与分片隔离指标；
4. 预警中心：组合风控 (portfolio_risk) 与流动性冲击 (liquidity_stress) 规则校验与分发；
5. AI 日报：Brinson 行业归因与全市场微观情绪罗盘。
"""
from __future__ import annotations

import asyncio
from datetime import date
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from app.api.v1.alerts import _validate_params
from app.api.v1.ops import _qc_stat
from app.api.v1.report import _market_compass, build_daily_report
from app.core.config import get_settings
from app.db.init_db import init_database
from app.db.models_auth import Role, User
from app.db.session import get_session_factory, reset_engine
from app.main import app
from sqlalchemy import select


@pytest.fixture(scope="module")
def quant_client():
    reset_engine()
    asyncio.run(init_database())
    factory = get_session_factory()

    async def _seed():
        async with factory() as sess:
            for name in ("viewer", "researcher", "admin"):
                if not (await sess.scalars(select(Role).where(Role.name == name))).first():
                    sess.add(Role(name=name))
            await sess.commit()
            roles = {r.name: r.id for r in (await sess.scalars(select(Role))).all()}
            if not (await sess.scalars(select(User).where(User.username == "quant_user"))).first():
                from app.core.auth import hash_password
                sess.add(User(username="quant_user", password_hash=hash_password("quantpass123"), role_id=roles["researcher"]))
            await sess.commit()

    asyncio.run(_seed())
    with TestClient(app) as client:
        # login to get token
        resp = client.post("/api/v1/auth/login", json={"username": "quant_user", "password": "quantpass123"})
        assert resp.status_code == 200, resp.text
        token = resp.json()["data"]["access_token"]
        client.headers["Authorization"] = f"Bearer {token}"
        yield client


def test_datacenter_pit_financials(quant_client: TestClient):
    """验证数据中心 PIT 点截面财务快照端点：严格区分披露日与报告期。"""
    resp = quant_client.get("/api/v1/datacenter/pit-financials?symbol=000001.SZ&limit=5")
    assert resp.status_code == 200, resp.text
    data = resp.json()["data"]
    assert "symbol" in data
    assert "records" in data
    assert "summary" in data
    assert "avg_lag_days" in data["summary"]
    assert "pit_verified" in data["summary"]


def test_datacenter_corporate_actions(quant_client: TestClient):
    """验证数据中心复权因子链对账端点：计算除权跳变比例与动态复权因子。"""
    resp = quant_client.get("/api/v1/datacenter/corporate-actions?symbol=000001.SZ&limit=5")
    assert resp.status_code == 200, resp.text
    data = resp.json()["data"]
    assert "symbol" in data
    assert "actions" in data
    assert "latest_cum_factor" in data
    assert isinstance(data["latest_cum_factor"], (int, float))


def test_ops_quality_feature_drift(quant_client: TestClient):
    """验证数据质量协变量漂移端点：PSI 与 KS 双重检验。"""
    resp = quant_client.get("/api/v1/ops/quality/feature-drift?factor=ret_20&baseline_year=2025&target_year=2026")
    assert resp.status_code == 200, resp.text
    data = resp.json()["data"]
    assert "psi" in data
    assert "ks_statistic" in data
    assert "ks_pvalue" in data
    assert "drift_level" in data
    assert data["drift_level"] in ("stable", "moderate", "severe", "unknown")


def test_ops_dag_gantt_and_sharding(quant_client: TestClient):
    """验证流水线 DAG 看板：包含关键路径甘特图与分片隔离指标。

    2026-10-09：DAG 由 5 阶段扩到 **11 阶段**。断言改为与后端单一事实源
    `app/api/v1/ops.py::_DAG_STEP_SPEC` **集合对齐**（而非写死数字），
    避免下次阶段扩容再次漂移。甘特时序按拓扑序返回，故用集合比较。
    """
    from app.api.v1.ops import _DAG_STEP_SPEC  # 单一事实源

    resp = quant_client.get("/api/v1/ops/dag")
    assert resp.status_code == 200, resp.text
    data = resp.json()["data"]
    assert "stages" in data
    assert "gantt_timeline" in data
    expected_ids = {s["id"] for s in _DAG_STEP_SPEC}
    assert {t["id"] for t in data["gantt_timeline"]} == expected_ids
    assert {s["id"] for s in data["stages"]} == expected_ids
    # 根节点（行情增量同步）在关键路径上
    assert data["gantt_timeline"][0]["id"] == "update_daily"
    assert data["gantt_timeline"][0]["is_critical_path"] is True
    assert "sharding_metrics" in data
    assert data["sharding_metrics"]["failure_isolated"] is True


def test_alerts_new_rules_validation():
    """验证预警中心新增 portfolio_risk 与 liquidity_stress 规则参数白名单与合法性校验。"""
    # 合法 portfolio_risk 参数
    _validate_params("portfolio_risk", {"factor": "industry", "threshold": 25.0, "direction": "above"})
    # 合法 liquidity_stress 参数
    _validate_params("liquidity_stress", {"metric": "amihud", "threshold": 2.5})

    # 非法参数应抛出 AQPException
    from app.core.errors import AQPException
    with pytest.raises(AQPException):
        _validate_params("portfolio_risk", {"invalid_param": 10})
    with pytest.raises(AQPException):
        _validate_params("liquidity_stress", {"metric": "invalid_metric", "threshold": 1.0})


def test_daily_report_market_compass_and_brinson():
    """验证 AI 日报生成器：包含微观情绪罗盘、Brinson 结构化归因与次日调仓推演。"""
    rep = build_daily_report(date.today())
    assert "date" in rep
    assert "sections" in rep
    assert "markdown" in rep
    assert "market_compass" in rep
    compass = rep["market_compass"]
    assert "sentiment_score" in compass
    assert "turnover_gini" in compass
    assert "adl_change" in compass
    assert isinstance(compass["sentiment_score"], int)
    # 验证次日调仓推演字段结构
    assert "signal_preview" in rep
    if rep["signal_preview"]:
        sig = rep["signal_preview"]
        assert "expected_turnover" in sig
        assert "estimated_slippage_bps" in sig
        assert "buys" in sig
        assert "sells" in sig


def test_datacenter_tradability_timeline(quant_client: TestClient):
    """验证标的全生命周期可交易性状态时序端点。"""
    resp = quant_client.get("/api/v1/datacenter/tradability-timeline?symbol=000001.SZ&limit=30")
    assert resp.status_code == 200, resp.text
    data = resp.json()["data"]
    assert "symbol" in data
    assert "tradable_ratio" in data
    assert "timeline" in data
    assert "normal_days" in data
    assert "suspended_days" in data


def test_ops_quality_multi_source_arbitrage(quant_client: TestClient):
    """验证多源行情残差交叉对账端点。"""
    resp = quant_client.get("/api/v1/ops/quality/multi-source-arbitrage?limit=20")
    assert resp.status_code == 200, resp.text
    data = resp.json()["data"]
    assert "total_checked" in data
    assert "tolerance_corridor_pct" in data
    assert "items" in data
    assert "consistent_count" in data
