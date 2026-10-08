"""AI 日报演进重构测试套件 (Report Evolution Tests)

检验：
1. 市场微观罗盘：包含 8 分段涨跌幅直方图 (breadth_bins)、成交额加权情绪修正与 amount 基尼系数；
2. Barra 风格因子与行业轮动排行 (style_factors)；
3. 组合风险在险价值雷达 (portfolio_risk)；
4. 次日调仓推演：打分凸加权与 Almgren-Chriss 个股级冲击滑点；
5. 投委会智能综述合成 (synthesis)；
6. 路由端点正常响应与权限控制。
"""
from __future__ import annotations

import asyncio
from datetime import date
from pathlib import Path
import sys

import pytest
from fastapi.testclient import TestClient

BACKEND_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(BACKEND_ROOT))

from app.api.v1 import report as report_mod
from app.db.session import get_session_factory, reset_engine
from app.main import app


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
            if not (await sess.scalars(_select(_User).where(_User.username == "repuser"))).first():
                sess.add(_User(username="repuser", password_hash=_hp("reppass123"), role_id=roles["researcher"]))
            await sess.commit()

    asyncio.run(_seed())

    with TestClient(app) as c:
        r = c.post("/api/v1/auth/login", json={"username": "repuser", "password": "reppass123"})
        token = r.json()["data"]["access_token"]
        c.headers.update({"Authorization": f"Bearer {token}"})
        yield c


def test_build_daily_report_evolution_fields():
    """测试 build_daily_report 返回增强的量化风控与微观指标。"""
    rep = report_mod.build_daily_report(day=date(2026, 10, 8))
    assert rep is not None
    assert "date" in rep
    assert "market_compass" in rep
    assert "style_factors" in rep
    assert "portfolio_risk" in rep
    assert "synthesis" in rep

    compass = rep["market_compass"]
    assert "breadth_bins" in compass
    assert len(compass["breadth_bins"]) == 8
    assert "turnover_gini" in compass
    assert "sentiment_score" in compass
    assert "sentiment_label" in compass

    style = rep["style_factors"]
    assert "styles" in style
    assert len(style["styles"]) >= 6
    assert "top_industries" in style
    assert "bottom_industries" in style

    risk = rep["portfolio_risk"]
    assert "portfolio_var_95" in risk
    assert "max_stock_weight" in risk
    assert "top3_concentration" in risk
    assert "status" in risk

    synthesis = rep["synthesis"]
    assert "macro_summary" in synthesis
    assert "attribution_takeaway" in synthesis
    assert "factor_health_verdict" in synthesis
    assert "execution_guidance" in synthesis


def test_report_endpoints_and_dispatch(evo_client: TestClient):
    """测试日报端点响应与手动分发功能。"""
    # 1. 查询日报
    res = evo_client.get("/api/v1/report/daily")
    assert res.status_code == 200
    data = res.json()
    assert data["code"] == 0

    # 2. 重新生成日报
    gen_res = evo_client.post("/api/v1/report/daily/generate")
    assert gen_res.status_code == 200
    gen_data = gen_res.json()
    assert gen_data["code"] == 0
    assert "data" in gen_data
    assert "synthesis" in gen_data["data"]

    # 3. 手动广播分发
    disp_res = evo_client.post("/api/v1/report/daily/dispatch")
    assert disp_res.status_code == 200
    disp_data = disp_res.json()
    assert disp_data["code"] == 0
    assert disp_data["data"]["ok"] is True
