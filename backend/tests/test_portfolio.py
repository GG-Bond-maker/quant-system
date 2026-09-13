"""组合回测引擎与接口测试。"""
from __future__ import annotations

import sys
from pathlib import Path

import numpy as np
import pandas as pd
import pytest
from fastapi.testclient import TestClient

BACKEND_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(BACKEND_ROOT))

from app.api.v1 import portfolio as portfolio_api  # noqa: E402
from app.domain import portfolio as portfolio_mod  # noqa: E402
from app.main import app  # noqa: E402


def _login_researcher(c) -> None:
    """种一个 researcher 账号并注入 Authorization 头。

    `93fa52d` 给 ``POST /api/v1/portfolio/backtest`` 加了
    ``require_role("researcher")``，匿名调用会吃 40100。
    """
    import asyncio

    from sqlalchemy import select as _select

    from app.core.auth import hash_password as _hp
    from app.db.init_db import init_database
    from app.db.models_auth import Role as _Role, User as _User
    from app.db.session import get_session_factory

    username, password = "portfoliotester", "portfoliotestpass1"
    # 幂等建表：roles/users 属 auth 模型，TestClient 未进 lifespan 时不会创建
    asyncio.run(init_database())

    async def _seed():
        factory = get_session_factory()
        async with factory() as sess:
            for name in ("viewer", "researcher", "admin"):
                if not (await sess.scalars(
                        _select(_Role).where(_Role.name == name))).first():
                    sess.add(_Role(name=name))
            await sess.commit()
            roles = {r.name: r.id for r in (await sess.scalars(_select(_Role))).all()}
            if not (await sess.scalars(
                    _select(_User).where(_User.username == username))).first():
                sess.add(_User(username=username, password_hash=_hp(password),
                               role_id=roles["researcher"]))
            await sess.commit()

    asyncio.run(_seed())
    r = c.post("/api/v1/auth/login", json={"username": username,
                                           "password": password})
    assert r.json()["code"] == 0, f"researcher 登录失败：{r.text}"
    c.headers.update({"Authorization": f"Bearer {r.json()['data']['access_token']}"})


@pytest.fixture
def client():
    c = TestClient(app)
    _login_researcher(c)
    return c


def _make_prices(start: str, end: str, n_assets: int = 3) -> tuple[list[pd.Series], pd.Series]:
    """构造确定性价格序列（工作日）。"""
    dates = pd.bdate_range(start, end)
    rng = np.random.default_rng(42)
    assets = []
    for i in range(n_assets):
        rets = rng.normal(0.0008, 0.012, len(dates))
        prices = 10 * (1 + i * 0.2) * np.exp(np.cumsum(rets))
        assets.append(pd.Series(prices, index=dates.date, name=f"CODE{i}"))
    bm_rets = rng.normal(0.0005, 0.010, len(dates))
    benchmark = pd.Series(100 * np.exp(np.cumsum(bm_rets)), index=dates.date, name="benchmark")
    return assets, benchmark


def test_run_portfolio_backtest_monthly_rebalance(monkeypatch):
    """月度再平衡回测输出结构与指标正确。"""
    assets, benchmark = _make_prices("2023-01-01", "2023-12-29", n_assets=3)
    calls: dict[str, int] = {"asset": 0, "bm": 0}

    def fake_asset(code, asset_type, start, end):
        calls["asset"] += 1
        idx = [s.name for s in assets].index(code)
        return assets[idx]

    def fake_bm(code, start, end):
        calls["bm"] += 1
        return benchmark

    result = portfolio_mod.run_portfolio_backtest(
        price_loader=fake_asset,
        benchmark_loader=fake_bm,
        assets=[
            {"code": "CODE0", "type": "stock", "weight": 0.3},
            {"code": "CODE1", "type": "stock", "weight": 0.4},
            {"code": "CODE2", "type": "stock", "weight": 0.3},
        ],
        start_date="2023-01-01",
        end_date="2023-12-29",
        rebalance="M",
        initial_cash=1_000_000,
    )

    assert result["status"] == "ok"
    assert result["trading_days"] == len(assets[0])
    assert len(result["nav_curve"]) == len(assets[0])
    assert len(result["drawdown_curve"]) == len(assets[0])
    assert len(result["annual_returns"]) == 1
    assert "metrics" in result

    m = result["metrics"]
    assert -1 < m["cagr"] < 1
    assert 0 <= m["max_drawdown"] < 1
    assert -10 < m["sharpe"] < 10
    assert "alpha" in m and "beta" in m

    # 持仓漂移记录数量 = 交易日数量
    assert len(result["holdings_drift"]) == len(assets[0])
    # 权重和 <= 1：整手约束与摩擦成本留下少量现金尾差（原 1e-5 精度对应零摩擦旧引擎）
    last_w = result["holdings_drift"][-1]["weights"]
    assert 0.9 < sum(last_w.values()) <= 1.0 + 1e-9
    # 摩擦成本字段存在且非负
    fc = result["friction_costs"]
    assert set(fc) == {"commission", "stamp_duty", "slippage"}
    assert fc["commission"] >= 0 and fc["stamp_duty"] >= 0 and fc["slippage"] >= 0


def test_run_portfolio_backtest_static_holdings_drift(monkeypatch):
    """不调仓时，持仓比例会随价格涨跌自然漂移。"""
    assets, benchmark = _make_prices("2023-01-01", "2023-06-30", n_assets=2)

    result = portfolio_mod.run_portfolio_backtest(
        price_loader=lambda code, typ, s, e: assets[0] if code == "A" else assets[1],
        benchmark_loader=lambda code, s, e: benchmark,
        assets=[{"code": "A", "type": "stock", "weight": 0.5},
                {"code": "B", "type": "stock", "weight": 0.5}],
        start_date="2023-01-01",
        end_date="2023-06-30",
        rebalance="none",
        initial_cash=1_000_000,
    )

    first_w = result["holdings_drift"][0]["weights"]
    last_w = result["holdings_drift"][-1]["weights"]
    # 首日建仓受整手取整 + 最低佣金影响，权重在 0.5 附近（1% 容差）
    assert abs(first_w["A"] - 0.5) < 0.01
    assert abs(first_w["B"] - 0.5) < 0.01
    # 漂移后权重通常不再等于 0.5（只要两只资产收益不同）
    assert abs(last_w["A"] - 0.5) > 1e-4 or abs(last_w["B"] - 0.5) > 1e-4


def test_run_portfolio_backtest_t1_execution(monkeypatch):
    """CRIT-2：调仓信号 T-1 收盘触发，T 日（次一交易日）才撮合。

    构造 A 一月内从 10 涨到 20、B 持平 10：2 月首个交易日（调仓信号日）
    权重仍是漂移值（A 超配 > 0.55 越过 10% 容忍带），次一交易日回到 0.5。
    """
    dates = pd.bdate_range("2023-01-01", "2023-02-28")
    n_jan = int((dates < pd.Timestamp("2023-02-01")).sum())
    a = pd.Series(list(np.linspace(10.0, 20.0, n_jan)) + [20.0] * (len(dates) - n_jan),
                  index=dates.date, name="A")
    b = pd.Series(np.full(len(dates), 10.0), index=dates.date, name="B")
    bm = pd.Series(np.full(len(dates), 100.0), index=dates.date, name="benchmark")

    result = portfolio_mod.run_portfolio_backtest(
        price_loader=lambda code, typ, s, e: a if code == "A" else b,
        benchmark_loader=lambda code, s, e: bm,
        assets=[{"code": "A", "type": "stock", "weight": 0.5},
                {"code": "B", "type": "stock", "weight": 0.5}],
        start_date="2023-01-01",
        end_date="2023-02-28",
        rebalance="M",
        initial_cash=1_000_000,
    )

    drift = result["holdings_drift"]
    # 首个交易日当日建仓
    assert abs(drift[0]["weights"]["A"] - 0.5) < 0.01
    # 2 月首个交易日 = 调仓信号日：未执行，权重仍为漂移值（A 超配）
    feb1 = next(r for r in drift if r["date"].startswith("2023-02"))
    assert feb1["weights"]["A"] > 0.55
    # 次一交易日完成撮合：权重回到目标附近
    nxt = drift[drift.index(feb1) + 1]
    assert abs(nxt["weights"]["A"] - 0.5) < 0.01
    # 有卖出发生 -> 股票扣印花税、双边佣金入账
    assert result["friction_costs"]["commission"] > 0
    assert result["friction_costs"]["stamp_duty"] > 0


def test_run_portfolio_backtest_late_listing_no_truncation(monkeypatch):
    """CRIT-3：晚上市资产不再静默截断回测起点，权重以现金形式持有并提示。"""
    assets, benchmark = _make_prices("2023-01-01", "2023-06-30", n_assets=2)
    # B 资产 2023-03 起才有行情（晚上市）；列名须与资产代码一致（引擎按列名对齐）
    late = assets[1][assets[1].index >= pd.Timestamp("2023-03-01").date()].rename("B")
    early = assets[0].rename("A")

    result = portfolio_mod.run_portfolio_backtest(
        price_loader=lambda code, typ, s, e: early if code == "A" else late,
        benchmark_loader=lambda code, s, e: benchmark,
        assets=[{"code": "A", "type": "stock", "weight": 0.5},
                {"code": "B", "type": "stock", "weight": 0.5}],
        start_date="2023-01-01",
        end_date="2023-06-30",
        rebalance="none",
        initial_cash=1_000_000,
    )

    # 关键：交易日数未被截断（旧实现 dropna(how="any") 会删掉 1-2 月整段）
    assert result["trading_days"] == len(assets[0])
    assert len(result["nav_curve"]) == len(assets[0])
    # B 上市前权重为 0（其权重以现金形式持有），A 独享已建仓部分
    first_w = result["holdings_drift"][0]["weights"]
    assert first_w["B"] == 0.0
    assert first_w["A"] > 0.45
    # 数据质量提示包含晚上市信息
    assert any("2023-03" in w for w in result["data_warnings"])


def test_backtest_endpoint(monkeypatch, client):
    """API 端点正常返回统一响应包络。"""
    sample = {
        "status": "ok",
        "start_date": "2023-01-01",
        "end_date": "2023-06-30",
        "initial_cash": 1_000_000,
        "rebalance": "M",
        "benchmark": "000300",
        "assets": [{"code": "A", "type": "stock", "weight": 1.0}],
        "trading_days": 120,
        "metrics": {
            "cagr": 0.10, "max_drawdown": 0.05, "sharpe": 1.2,
            "calmar": 2.0, "volatility": 0.15, "alpha": 0.02, "beta": 0.9,
        },
        "nav_curve": [{"date": "2023-01-01", "nav": 1.0, "benchmark": 1.0}],
        "drawdown_curve": [{"date": "2023-01-01", "drawdown": 0.0}],
        "annual_returns": [{"year": "2023", "portfolio": 0.1, "benchmark": 0.05}],
        "holdings_drift": [{"date": "2023-01-01", "weights": {"A": 1.0}}],
    }
    monkeypatch.setattr(portfolio_api, "run_portfolio_backtest", lambda **kwargs: sample)

    resp = client.post("/api/v1/portfolio/backtest", json={
        "assets": [{"code": "600519", "type": "stock", "weight": 1.0}],
        "start_date": "2023-01-01",
        "end_date": "2023-06-30",
        "rebalance": "M",
        "benchmark": "000300",
        "initial_cash": 1_000_000,
    })
    assert resp.status_code == 200
    body = resp.json()
    # 前置守卫：缺了它，鉴权/参数失败会伪装成 "'NoneType' is not subscriptable"，
    # 把真实根因藏起来（93fa52d 收紧权限后曾因此拖长定位时间）。
    assert body["code"] == 0, body
    data = body["data"]
    assert data["metrics"]["cagr"] == pytest.approx(0.10)
    assert data["nav_curve"][0]["nav"] == pytest.approx(1.0)


def test_backtest_endpoint_weight_sum_validation(client):
    """权重和不等于 1 时返回业务错误。"""
    resp = client.post("/api/v1/portfolio/backtest", json={
        "assets": [{"code": "600519", "type": "stock", "weight": 0.5}],
        "start_date": "2023-01-01",
        "end_date": "2023-06-30",
        "rebalance": "M",
    })
    assert resp.status_code == 200
    assert resp.json()["code"] != 0
