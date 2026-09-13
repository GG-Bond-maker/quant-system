"""Task 6（整改计划 A-P1-2 / Q-P1-4）：退市语义 + 持仓强平减记。

背景：退市/长期停牌的持仓曾被按"最后收盘价"永久估值、卖单被 halted 闸门
永远拒绝 → 净值高估且仓位无法出清；instrument.delist_date 从未填充，
退市证券在宇宙中表现为"永续停牌"（量化专项审查 P1-2 / 审核报告 P1-2）。
"""
from __future__ import annotations

import asyncio
import sys
from datetime import date, timedelta
from pathlib import Path

BACKEND_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(BACKEND_ROOT))

import pandas as pd  # noqa: E402
import polars as pl  # noqa: E402
import pytest  # noqa: E402

from app.backtest.broker import Broker  # noqa: E402
from app.backtest.engine import run_backtest  # noqa: E402
from app.data.parquet_store import write_year_batch  # noqa: E402
from app.db.init_db import init_database  # noqa: E402
from app.db.models import Instrument  # noqa: E402
from app.db.session import get_session_factory  # noqa: E402

A, B = "600001.SH", "600002.SH"
D0 = date(2024, 1, 2)
N_DAYS = 30
ALL_DAYS = [D0 + timedelta(days=i) for i in range(N_DAYS)]


def _uni_row(d: date, sym: str, close: float) -> dict:
    return {"date": d, "symbol": sym, "open": close, "high": close, "low": close,
            "close": close, "volume": 1e6, "amount": close * 1e6,
            "is_halted": False, "limit_up": close * 1.1, "limit_down": close * 0.9,
            "factor": 1.0}


def _make_universe(absent_from: int | None = None) -> pd.DataFrame:
    """A 在 absent_from（日序号）起从宇宙消失（退市）；B 全程在场。"""
    rows = []
    for i, d in enumerate(ALL_DAYS):
        if absent_from is None or i < absent_from:
            rows.append(_uni_row(d, A, 10.0))
        rows.append(_uni_row(d, B, 20.0))
    return pd.DataFrame(rows)


def _make_signal(upto: int | None = None) -> pd.DataFrame:
    rows = []
    for i, d in enumerate(ALL_DAYS):
        if upto is None or i < upto:
            rows.append({"date": d, "symbol": A, "pred_score": 0.9})
        rows.append({"date": d, "symbol": B, "pred_score": 0.5})
    return pd.DataFrame(rows)


def test_broker_liquidate_haircut():
    """broker.liquidate：按最后收盘 × haircut 清仓入现金，持仓清零。"""
    b = Broker(init_cash=1_000_000)
    uni = pd.DataFrame([_uni_row(D0, A, 10.0)]).set_index("symbol")
    b.buy(D0, A, 100_000.0, uni.loc[A])
    b.mark_to_market(D0, uni)
    equity_before = b.total_equity
    shares = 9_900  # 100_000 / 10 -> 9000+900... 实际按整手，读回值断言
    t = b.liquidate(D0 + timedelta(days=1), A, haircut=0.5)
    assert t is not None and t.reason == "delisted_liquidation"
    assert t.qty > 0
    assert t.price == pytest.approx(10.0 * 0.5)  # last_close(10) × 0.5
    assert b.holdings.get(A) is None
    assert b.total_equity == pytest.approx(equity_before - t.qty * 10.0 * 0.5)


def test_engine_liquidates_stale_position_with_haircut():
    """A 退市消失 5 个交易日后按 0.5 折价强平；净值不再永久冻结估值。"""
    uni = _make_universe(absent_from=10)   # A 自第 10 日（0-based）退市
    sig = _make_signal(upto=10)
    res = run_backtest(uni, sig, init_cash=1_000_000, top_k=2,
                       delist_haircut=0.5, delist_grace_days=5)
    liq = [t for t in res.trades if t["reason"] == "delisted_liquidation"]
    assert len(liq) == 1
    assert liq[0]["symbol"] == A
    assert liq[0]["price"] == pytest.approx(5.0)   # 10 × 0.5
    # 强平后持仓中不再有 A
    assert all(A not in h["holdings"] for h in res.holdings_history[-5:])


def test_engine_keeps_position_without_delist():
    """无退市场景（全程在场）：不触发强平，行为与旧版一致。"""
    uni = _make_universe(absent_from=None)
    sig = _make_signal(upto=None)
    res = run_backtest(uni, sig, init_cash=1_000_000, top_k=2,
                       delist_haircut=0.5, delist_grace_days=5)
    assert not [t for t in res.trades if t["reason"] == "delisted_liquidation"]


def test_universe_excludes_rows_after_delist_date():
    """instrument.delist_date 之后日期不再进入回测宇宙。"""
    from app.data.universe import build_universe_backtest

    asyncio.run(init_database())

    async def _ins() -> None:
        factory = get_session_factory()
        async with factory() as sess:
            await sess.execute(Instrument.__table__.delete())
            sess.add(Instrument(symbol="600099.SH", code="600099", name="退市测试",
                                market="SH", instrument_type="stock", is_st=False,
                                list_date=date(2020, 1, 1),
                                delist_date=date(2024, 1, 4)))
            await sess.commit()

    asyncio.run(_ins())
    days = [date(2024, 1, d) for d in (2, 3, 4, 5)]
    bars = pl.DataFrame({
        "symbol": ["600099.SH"] * 4, "code": ["600099"] * 4, "date": days,
        "open": [10.0] * 4, "high": [10.0] * 4, "low": [10.0] * 4,
        "close": [10.0, 10.2, 10.1, 10.3], "volume": [1e6] * 4,
        "amount": [1e7] * 4,
    })
    write_year_batch("daily_bar", "600099.SH", 2024, bars)
    write_year_batch("daily_bar_hfq", "600099.SH", 2024, bars)
    out = build_universe_backtest(symbols=["600099.SH"], persist=False)
    assert out["date"].max() == date(2024, 1, 4)   # delist_date 当日仍在，之后剔除
    assert out.height == 3
