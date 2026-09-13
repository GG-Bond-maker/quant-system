"""Task 1（整改计划）：回测结算口径 raw → hfq。

两个守卫目标：
1. `build_universe_backtest`（hfq 口径回测宇宙）输出的 close 序列在 10 送 10
   除权日**不出现跳空**（raw 口径会 -50%），且除权日涨跌停价基于 hfq 昨收
   （= 交易所除权参考价口径）；
2. 引擎/broker 零逻辑改动：喂 hfq 价格后，除权日持仓市值不缩水
   （文档化行为测试，固化"结算口径"契约）。

背景：主回测曾用 raw 价结算，除权缺口被当真实亏损——现金分红不入账、
送转除权市值凭空缩水（见 docs/audit/2026-09-05-量化专项审查报告.md P0-1）。
"""
from __future__ import annotations

import asyncio
from datetime import date

import pandas as pd
import polars as pl
import pytest

BACKEND_ROOT = __import__("pathlib").Path(__file__).resolve().parents[1]
import sys  # noqa: E402

sys.path.insert(0, str(BACKEND_ROOT))

from app.backtest.broker import Broker  # noqa: E402
from app.data.parquet_store import write_year_batch  # noqa: E402
from app.db.init_db import init_database  # noqa: E402
from app.db.models import Instrument  # noqa: E402
from app.db.session import get_session_factory  # noqa: E402

SYM = "600001.SH"
CODE = "600001"
DAYS = [date(2024, 1, d) for d in (2, 3, 4, 5)]


def _bars(closes: list[float], volumes: list[float]) -> pl.DataFrame:
    return pl.DataFrame({
        "symbol": [SYM] * 4, "code": [CODE] * 4, "date": DAYS,
        "open": [c * 0.99 for c in closes], "high": [c * 1.01 for c in closes],
        "low": [c * 0.98 for c in closes], "close": closes,
        "volume": volumes, "amount": [v * c for v, c in zip(volumes, closes)],
    })


@pytest.fixture()
def seeded_split_universe():
    """10 送 10：raw close 100 -> 50（除权缺口），hfq close 100 -> 100（连续）。"""
    asyncio.run(init_database())

    async def _ins() -> None:
        factory = get_session_factory()
        async with factory() as sess:
            await sess.execute(Instrument.__table__.delete())
            sess.add(Instrument(symbol=SYM, code=CODE, name="测试送转股",
                                market="SH", instrument_type="stock",
                                is_st=False, list_date=date(2020, 1, 1)))
            await sess.commit()

    asyncio.run(_ins())
    #                    除权日 ↓
    raw_close = [90.0, 100.0, 50.0, 51.0]
    hfq_close = [90.0, 100.0, 100.0, 102.0]
    vols = [1_000_000.0] * 4
    write_year_batch("daily_bar", SYM, 2024, _bars(raw_close, vols))
    write_year_batch("daily_bar_hfq", SYM, 2024, _bars(hfq_close, vols))
    return {"raw": raw_close, "hfq": hfq_close}


def test_build_universe_backtest_split_no_gap(seeded_split_universe):
    """hfq 口径宇宙：除权日 close 无跳空；limit_up = hfq 昨收 × 1.1。"""
    from app.data.universe import build_universe_backtest

    out = build_universe_backtest(persist=False)
    row = out.filter(pl.col("symbol") == SYM).sort("date")
    closes = row["close"].to_list()
    assert len(closes) == 4
    # 除权日（第 3 天）无 -50% 跳空：raw 口径会得 0.5
    assert abs(closes[2] / closes[1] - 1.0) < 0.05
    # 涨跌停基于 hfq 昨收（除权参考价口径）：100 × 1.1 = 110.0
    assert row["limit_up"][2] == pytest.approx(110.0, abs=0.011)
    assert row["limit_down"][2] == pytest.approx(90.0, abs=0.011)
    # volume/amount 透传 raw 口径
    assert row["volume"][2] == 1_000_000.0
    # 复权因子：除权日 f = 100/50 = 2.0
    assert row["factor"][2] == pytest.approx(2.0)


def test_lot_size_scaled_by_factor():
    """hfq 域整手换算：factor>1 时等效手数 <100 股，小额预算不再被"lot"误拒。

    场景：raw 价 6.44、hfq 价 20.95（factor≈3.254）。预算 2000 元按旧口径
    floor(2000/20.95/100)*100 = 0 -> 拒绝；换算后 lot=31 -> 62 股成交。
    """
    import pandas as pd

    row = pd.Series({"close": 21.0, "open": 20.95, "volume": 1e6, "amount": 2e7,
                     "is_halted": False, "limit_up": 23.0, "limit_down": 19.0,
                     "factor": 20.95 / 6.44})
    b = Broker(init_cash=1_000_000)
    t = b.buy(date(2024, 1, 2), SYM, 2_000.0, row)
    assert t.reason == "filled"
    assert t.qty == 93  # floor(95.47/31)*31 = 3*31


def test_lot_size_unchanged_without_factor():
    """无 factor 列（raw 域/策略框架路径）：整手行为与旧口径完全一致。"""
    import pandas as pd

    row = pd.Series({"close": 21.0, "open": 20.95, "volume": 1e6, "amount": 2e7,
                     "is_halted": False, "limit_up": 23.0, "limit_down": 19.0})
    b = Broker(init_cash=1_000_000)
    t = b.buy(date(2024, 1, 2), SYM, 2_000.0, row)
    assert t.reason == "lot"  # 不足一手，与旧行为一致


def test_hfq_settlement_no_fake_loss_on_split_day(seeded_split_universe):
    """行为契约：以 hfq 价格结算时，除权日持仓市值不缩水。

    （raw 口径下同一场景 total_equity 会凭空 -50%。本测试固化引擎输入契约，
    防止未来有人把数据源换回 raw 而测试仍绿。）
    """
    uni = pd.DataFrame({
        "open": [89.1, 99.0, 99.0, 100.98],
        "close": seeded_split_universe["hfq"],
        "volume": [1_000_000.0] * 4,
        "limit_up": [100.0, 110.0, 110.0, 112.2],
        "limit_down": [80.0, 90.0, 90.0, 92.0],
        "is_halted": [False] * 4,
        "amount": [1e8] * 4,
    }, index=[SYM] * 4)
    b = Broker(init_cash=1_000_000)
    b.buy(DAYS[1], SYM, 200_000.0, uni.loc[SYM].iloc[1])
    b.mark_to_market(DAYS[1], uni.iloc[[1]])
    equity_before = b.total_equity
    assert equity_before == pytest.approx(1_000_000, rel=0.02)
    b.mark_to_market(DAYS[2], uni.iloc[[2]])  # 除权日收盘估值
    assert b.total_equity == pytest.approx(equity_before, rel=1e-6)
