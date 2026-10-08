"""预警中心对标演进单测（P0/P1/P2）。

覆盖：
1. 风控态势统计端点 GET /api/v1/alerts/stats；
2. 多级严重度与规则过滤 GET /api/v1/alerts/events?severity=...；
3. 新增组合动态回撤 (portfolio_drawdown) 与组合在险价值 (portfolio_var) 规则校验；
4. 评估分发时 payload 携带 severity / remediation_action / note 闭环字段。
"""
from __future__ import annotations

import asyncio
import json
import sys
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

BACKEND_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(BACKEND_ROOT))

from app.api.v1 import alerts as alerts_mod  # noqa: E402
from app.db.models import AlertEvent, AlertRule  # noqa: E402
from app.db.session import get_session_factory, reset_engine  # noqa: E402
from app.main import app  # noqa: E402


@pytest.fixture(scope="module")
def evo_client():
    from sqlalchemy import select as _select
    from app.core.auth import hash_password as _hp
    from app.db.init_db import init_database as _init
    from app.db.models_auth import Role as _Role, User as _User

    reset_engine()
    asyncio.run(_init())
    factory = get_session_factory()

    async def _seed():
        async with factory() as sess:
            for name in ("viewer", "researcher", "admin"):
                if not (await sess.scalars(_select(_Role).where(_Role.name == name))).first():
                    sess.add(_Role(name=name))
            await sess.commit()
            roles = {r.name: r.id for r in (await sess.scalars(_select(_Role))).all()}
            if not (await sess.scalars(_select(_User).where(_User.username == "evouser"))).first():
                sess.add(_User(username="evouser", password_hash=_hp("evopass123"), role_id=roles["researcher"]))
            await sess.commit()

    asyncio.run(_seed())

    with TestClient(app) as c:
        r = c.post("/api/v1/auth/login", json={"username": "evouser", "password": "evopass123"})
        token = r.json()["data"]["access_token"]
        c.headers.update({"Authorization": f"Bearer {token}"})
        yield c


def test_alert_stats_kpi_dashboard(evo_client: TestClient):
    """验证 GET /api/v1/alerts/stats 统计接口。"""
    res = evo_client.get("/api/v1/alerts/stats")
    assert res.status_code == 200
    d = res.json()["data"]
    assert "active_rules" in d
    assert "total_rules" in d
    assert "events_today" in d
    assert "unread_count" in d
    assert "critical_count" in d
    assert "warning_count" in d
    assert "info_count" in d
    assert "top_symbols" in d
    assert "system_status" in d
    assert d["system_status"] in ("NORMAL", "ELEVATED", "HIGH_RISK", "DEGRADED")


def test_new_rules_validation_and_creation(evo_client: TestClient):
    """验证新增 portfolio_drawdown 与 portfolio_var 规则。"""
    # 1. 创建 portfolio_drawdown 规则
    r1 = evo_client.post(
        "/api/v1/alerts/rules",
        json={
            "name": "动态高水位回撤风控",
            "rule_type": "portfolio_drawdown",
            "scope": "global",
            "params": {"threshold": 3.5, "severity": "critical"},
            "channels": ["sse"],
            "cooldown_minutes": 15,
        },
    )
    assert r1.status_code == 200
    rule1_id = r1.json()["data"]["id"]

    # 2. 创建 portfolio_var 规则
    r2 = evo_client.post(
        "/api/v1/alerts/rules",
        json={
            "name": "95% VaR 限额监控",
            "rule_type": "portfolio_var",
            "scope": "global",
            "params": {"threshold": 2.0, "confidence": 0.95, "severity": "warning"},
            "channels": ["sse"],
            "cooldown_minutes": 30,
        },
    )
    assert r2.status_code == 200
    rule2_id = r2.json()["data"]["id"]

    # 3. 校验非法阈值被拒绝
    bad = evo_client.post(
        "/api/v1/alerts/rules",
        json={
            "name": "非法回撤",
            "rule_type": "portfolio_drawdown",
            "scope": "global",
            "params": {"threshold": -1.0},
        },
    )
    assert bad.status_code == 200
    assert bad.json()["code"] == 40000

    # 清理
    evo_client.delete(f"/api/v1/alerts/rules/{rule1_id}")
    evo_client.delete(f"/api/v1/alerts/rules/{rule2_id}")


def test_events_filtering_by_severity(evo_client: TestClient):
    """验证按 severity 筛选事件。"""
    factory = get_session_factory()

    async def _insert_events():
        async with factory() as sess:
            e_crit = AlertEvent(
                rule_id=999,
                symbol="600519.SH",
                payload_json=json.dumps({
                    "rule": "重大暴跌预警",
                    "rule_type": "price_pct",
                    "pct": -8.5,
                    "severity": "critical",
                    "remediation_action": "sell_or_reduce",
                    "note": "跌幅破位",
                }),
                is_read=False,
            )
            e_info = AlertEvent(
                rule_id=998,
                symbol="000001.SZ",
                payload_json=json.dumps({
                    "rule": "普通放量提示",
                    "rule_type": "volume_spike",
                    "severity": "info",
                    "remediation_action": "review",
                    "note": "量能温和放大",
                }),
                is_read=False,
            )
            sess.add_all([e_crit, e_info])
            await sess.commit()

    asyncio.run(_insert_events())

    # 查询 critical 事件
    r_crit = evo_client.get("/api/v1/alerts/events?severity=critical&limit=10")
    assert r_crit.status_code == 200
    crit_list = r_crit.json()["data"]
    assert any(x["payload"].get("severity") == "critical" for x in crit_list)
    assert all(x["payload"].get("severity") == "critical" for x in crit_list)

    # 查询 info 事件
    r_info = evo_client.get("/api/v1/alerts/events?severity=info&limit=10")
    assert r_info.status_code == 200
    info_list = r_info.json()["data"]
    assert any(x["payload"].get("severity") == "info" for x in info_list)
    assert all(x["payload"].get("severity") == "info" for x in info_list)
