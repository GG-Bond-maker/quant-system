"""Stage 1, 2, 3 扩展功能自动化测试套件：
1. S2-1: 条件单与网格机器人（建单、挂单、行情报单触发、网格步进）
2. S2-2: 多策略收益流集成（日期对齐、相关性矩阵、Euler风险贡献、综合NAV）
3. S3-2: 策略版本快照变更记录（创建版本、查询历史、版本参数与指标 Diff 对比）
"""
import pytest
from datetime import datetime
import numpy as np

from app.db.models import Base
from app.trading.paper import sync_session_factory
from app.trading.conditional import (
    create_conditional_order,
    list_conditional_orders,
    cancel_conditional_order,
    evaluate_conditional_orders,
    create_grid_bot,
    list_grid_bots,
    stop_grid_bot,
    evaluate_grid_bots,
)
from app.domain.portfolio import ensemble_strategy_returns


def test_ensemble_strategy_returns():
    """测试多策略收益流集成（纯函数）。"""
    dates = [f"2024-01-0{i}" for i in range(1, 9)]
    s1 = [{"date": d, "return": 0.01 * (i % 3)} for i, d in enumerate(dates)]
    s2 = [{"date": d, "return": -0.005 if i % 2 == 0 else 0.015} for i, d in enumerate(dates)]

    # 等权集成
    res = ensemble_strategy_returns({"MA_Cross": s1, "RSI_Reversion": s2}, weighting="equal")
    assert "kpi" in res
    assert "strategy_weights" in res
    assert res["strategy_weights"]["MA_Cross"] == 0.5
    assert res["strategy_weights"]["RSI_Reversion"] == 0.5
    assert "correlation_matrix" in res
    assert len(res["correlation_matrix"]["strategies"]) == 2
    assert "risk_contributions" in res
    assert len(res["nav_curve"]) == len(dates)
    assert res["nav_curve"][-1]["strategy"] > 0

    # 风险平价集成
    res_rp = ensemble_strategy_returns({"MA_Cross": s1, "RSI_Reversion": s2}, weighting="risk_parity")
    assert abs(sum(res_rp["strategy_weights"].values()) - 1.0) < 1e-3


def test_conditional_orders_lifecycle():
    """测试云端条件单生命周期：创建 -> 挂单 -> 行情触发 -> 撮合下单。"""
    Session = sync_session_factory()
    with Session() as session:
        # 1. 创建突破买入条件单
        co = create_conditional_order(
            session,
            symbol="600519.SH",
            side="buy",
            trigger_type="price_ge",
            trigger_price=1800.0,
            order_amount=50000.0,
        )
        assert co["status"] == "ACTIVE"
        assert co["trigger_price"] == 1800.0

        # 2. 列表查询
        orders = list_conditional_orders(session, status="ACTIVE")
        assert any(o["id"] == co["id"] for o in orders)

        # 3. 未达到触发价时不触发
        trig1 = evaluate_conditional_orders(session, {"600519.SH": 1795.0})
        assert len(trig1) == 0

        # 4. 达到触发价时触发
        trig2 = evaluate_conditional_orders(session, {"600519.SH": 1805.0})
        assert len(trig2) == 1
        assert trig2[0]["id"] == co["id"]

        # 5. 再次查询状态已更新为 TRIGGERED
        active_now = list_conditional_orders(session, status="ACTIVE")
        assert not any(o["id"] == co["id"] for o in active_now)


def test_grid_bot_lifecycle():
    """测试网格交易机器人生命周期：创建 -> 行情步进撮合 -> 终止。"""
    Session = sync_session_factory()
    with Session() as session:
        bot = create_grid_bot(
            session,
            symbol="510300.SH",
            lower_price=3.0,
            upper_price=4.0,
            grid_count=10,
            amount_per_grid=10000.0,
        )
        assert bot["status"] == "RUNNING"
        assert bot["grid_count"] == 10

        # 首次行情传入：记录基准价
        evaluate_grid_bots(session, {"510300.SH": 3.50})

        # 第二次行情传入：跌破一个网格步长 (0.1)，触发买入
        trig = evaluate_grid_bots(session, {"510300.SH": 3.39})
        assert len(trig) >= 1
        assert trig[0]["side"] == "buy"

        # 停止网格
        ok = stop_grid_bot(session, bot["id"])
        assert ok is True
        running_bots = list_grid_bots(session, status="RUNNING")
        assert not any(b["id"] == bot["id"] for b in running_bots)


def test_strategy_changelog_lifecycle():
    """测试策略版本快照变更记录：创建版本 -> 列表 -> 参数与指标 Diff 对比。"""
    from fastapi.testclient import TestClient
    from app.main import app

    client = TestClient(app)
    # 模拟 researcher 认证头
    from app.core.auth import create_jwt_token
    token, _ = create_jwt_token("test_researcher", "researcher")
    headers = {"Authorization": f"Bearer {token}"}

    strat_id = "test_strat_alpha"
    # 1. 创建 v1.0
    r1 = client.post(
        f"/api/v1/research/strategies/{strat_id}/changelogs",
        json={
            "version_tag": "v1.0.0",
            "commit_message": "初始版本：MA5/20 双均线",
            "params": {"short_ma": 5, "long_ma": 20},
            "metrics": {"sharpe": 1.20, "annual_return": 15.0, "max_drawdown": 12.0},
        },
        headers=headers,
    )
    assert r1.status_code == 200
    d1 = r1.json()["data"]
    v1_id = d1["id"]

    # 2. 创建 v1.1
    r2 = client.post(
        f"/api/v1/research/strategies/{strat_id}/changelogs",
        json={
            "version_tag": "v1.1.0",
            "commit_message": "参数调优：长均线调整为 30，增加止损",
            "params": {"short_ma": 5, "long_ma": 30, "trailing_stop": 0.05},
            "metrics": {"sharpe": 1.55, "annual_return": 18.5, "max_drawdown": 8.0},
        },
        headers=headers,
    )
    assert r2.status_code == 200
    d2 = r2.json()["data"]
    v2_id = d2["id"]

    # 3. 列表查询
    r_list = client.get(
        f"/api/v1/research/strategies/{strat_id}/changelogs",
        headers=headers,
    )
    assert r_list.status_code == 200
    items = r_list.json()["data"]
    assert len(items) >= 2
    assert items[0]["version_tag"] == "v1.1.0"

    # 4. Diff 对比
    r_diff = client.get(
        f"/api/v1/research/strategies/{strat_id}/changelogs/diff?v1_id={v1_id}&v2_id={v2_id}",
        headers=headers,
    )
    assert r_diff.status_code == 200
    diff_data = r_diff.json()["data"]
    assert len(diff_data["param_diffs"]) >= 2
    # long_ma 由 20 变为 30
    long_ma_diff = next(p for p in diff_data["param_diffs"] if p["param"] == "long_ma")
    assert long_ma_diff["v1_val"] == 20
    assert long_ma_diff["v2_val"] == 30

    # sharpe 指标变化 1.55 - 1.20 = 0.35
    sharpe_diff = next(m for m in diff_data["metric_diffs"] if m["metric"] == "sharpe")
    assert round(sharpe_diff["diff"], 2) == 0.35

