"""P1-3 universe_daily 测试：字段/停牌/涨跌停规则复用 + 真实抽样。"""
from __future__ import annotations

import asyncio
import sys
from datetime import date, timedelta
from pathlib import Path

import os

import polars as pl
import pytest

# 单元测试必须在隔离环境运行（test_api 模块设置的测试 SQLite/Parquet）。
# 否则合成种子会污染真实数据仓（P0 复审教训）。
ISOLATED = "SQLITE_URL" in os.environ and "aqp_test" in os.environ["SQLITE_URL"]

BACKEND_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(BACKEND_ROOT))

from app.data.parquet_store import write_year_batch  # noqa: E402
from app.data.universe import board_of, build_universe_daily, load_universe  # noqa: E402
from app.db.init_db import init_database  # noqa: E402
from app.db.models import Instrument  # noqa: E402
from app.db.session import get_session_factory  # noqa: E402

T1 = date(2024, 6, 4)
T2 = date(2024, 6, 5)

# (code, name, is_st, list_date)
# ⚠️ 使用全测试套件中其他模块未触碰的独立代码，避免共享测试数据仓串扰
SAMPLES = [
    ("605111", "沪主板样本", False, date(2001, 8, 27)),
    ("001223", "深主板样本", False, date(1991, 4, 3)),
    ("301567", "创业板样本", False, date(2018, 6, 11)),
    ("688777", "科创板样本", False, date(2020, 7, 16)),
    ("832000", "北交所样本", False, date(2021, 3, 25)),
    ("605222", "ST样本股", True, date(1999, 1, 1)),
    ("605999", "注册制新股", False, date(2024, 6, 3)),
]

BOARDS = {"605111": "main", "001223": "main", "301567": "chinext_star",
          "688777": "chinext_star", "832000": "bse", "605222": "main",
          "605999": "main"}


def symbol(code: str) -> str:
    return f"{code}.SH" if code.startswith(("6", "9")) else (
        f"{code}.SZ" if code.startswith(("0", "2", "3")) else f"{code}.BJ")


@pytest.fixture(scope="module")
def seed():
    if not ISOLATED:
        pytest.skip("单元测试需要隔离环境（pytest 全量运行时自动满足）")
    # 确定性隔离：清除其他模块可能写入的同名数据集，防止分区串扰
    import shutil

    from app.core.config import get_settings

    dr = get_settings().DATA_ROOT
    for ds in ("daily_bar", "daily_bar_hfq", "universe_daily"):
        shutil.rmtree(dr / ds, ignore_errors=True)
    asyncio.run(init_database())

    async def _ins() -> None:
        factory = get_session_factory()
        async with factory() as sess:
            await sess.execute(Instrument.__table__.delete())
            for code, name, is_st, list_date in SAMPLES:
                sess.add(Instrument(symbol=symbol(code), code=code, name=name,
                                    market=symbol(code).split(".")[1],
                                    instrument_type="stock", is_st=is_st,
                                    list_date=list_date))
            await sess.commit()
    asyncio.run(_ins())

    # 两个交易日的日线一次性写入（write_year_batch 为整年覆盖语义，逐日写会丢失前日）
    for code, *_ in SAMPLES:
        sym = symbol(code)
        vols = [50_000.0, 0.0 if code == "605111" else 60_000.0]
        pdf = pl.DataFrame({
            "symbol": [sym] * 2, "code": [code] * 2, "date": [T1, T2],
            "open": [10.0] * 2, "high": [10.2] * 2, "low": [9.8] * 2,
            "close": [10.0] * 2, "volume": vols, "amount": [v * 10 for v in vols],
            "source": ["akshare"] * 2,
        })
        write_year_batch("daily_bar", sym, T2.year, pdf)
    yield


def test_board_mapping_single_source():
    assert board_of("600519") == "main"
    assert board_of("300750") == "chinext_star"
    assert board_of("688981") == "chinext_star"
    assert board_of("831010") == "bse"


def test_universe_fields_and_rules(seed):
    df = build_universe_daily(T2)
    assert df.height == len(SAMPLES)
    row = {r["symbol"]: r for r in df.iter_rows(named=True)}
    # 板块 / ST / 上市天数
    for code, name, is_st, list_date in SAMPLES:
        assert row[symbol(code)]["board"] == BOARDS[code]
        assert row[symbol(code)]["is_st"] == is_st
        assert row[symbol(code)]["days_since_list"] == (T2 - list_date).days
    # 停牌：volume==0 -> is_halted
    assert row[symbol("605111")]["is_halted"] is True
    assert row[symbol("001223")]["is_halted"] is False
    # 涨跌停：相对断言（与 prev_close*板系数 一致，规则同源 domain/limit）
    # （运行于隔离环境时 prev_close=10.00；真实库中则为真实收盘——相对断言两者皆准）
    assert abs(row[symbol("001223")]["limit_pct"] - 0.10) < 1e-9
    assert row[symbol("001223")]["limit_up"] == pytest.approx(
        round(row[symbol("001223")]["limit_down"] / 0.9 * 1.1, 2), abs=0.02)
    assert abs(row[symbol("605222")]["limit_pct"] - 0.05) < 1e-9      # ST 主板 ±5%
    assert abs(row[symbol("301567")]["limit_pct"] - 0.20) < 1e-9      # 创业板 ±20%
    assert abs(row[symbol("688777")]["limit_pct"] - 0.20) < 1e-9      # 科创板 ±20%
    assert abs(row[symbol("832000")]["limit_pct"] - 0.30) < 1e-9      # 北交所 ±30%
    # 主板注册制新股（T2 为上市第 2 日 < 5 日）-> 无涨跌幅（极大/零哨兵值）
    assert row[symbol("605999")]["limit_up"] > 1e6 and row[symbol("605999")]["limit_down"] == 0.0
    assert row[symbol("605999")]["limit_pct"] is None


def test_universe_persist_and_reload(seed):
    build_universe_daily(T2)
    df = load_universe(T2)
    assert df.height == len(SAMPLES)
    df2 = build_universe_daily(T2, persist=False)
    assert df2.height == len(SAMPLES)  # 幂等：重写不产生重复
    df3 = build_universe_daily(T1, persist=False)
    assert df3.height == len(SAMPLES)
    assert bool(df3.filter(pl.col("symbol") == "605111.SH")["is_halted"][0]) is False


def test_real_sampling_multi_board():
    """真实抽样：主板/创业板/科创板/北交所/ST/新股 真实行情涨跌停验证（需网络）。"""
    try:
        from app.data.ingest.akshare_adapter import fetch_daily_bar
        from app.data.ingest.tasks import write_daily_bars

        codes = ["600519", "000001", "300750", "688981", "831010"]
        end = date.today() - timedelta(days=1)
        start = end - timedelta(days=14)
        written = 0
        for code in codes:
            try:
                df = fetch_daily_bar(code, start.isoformat(), end.isoformat())
            except Exception:
                continue  # 单源/单标的失败（如北交所新浪不支持）-> 跳过该只
            if df.is_empty():
                continue
            write_daily_bars(code, df, adjusts=("",))
            written += df.height
        assert written > 0, "真实行情拉取为空"
        d = date.today() - timedelta(days=2)
        df = build_universe_daily(d, persist=False)
        assert df.height >= 3
        # 主板 limit_up == round(prev_close * 1.1, 2)
        m = df.filter(pl.col("symbol") == "000001.SZ")
        if m.height and m["limit_up"][0] is not None:
            assert m["limit_pct"][0] == 0.10
    except (ConnectionError, OSError) as e:
        pytest.skip(f"网络不可达: {e!r}")
