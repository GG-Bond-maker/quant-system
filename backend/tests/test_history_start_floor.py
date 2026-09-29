"""有效历史起点（2022-01-01）闸门：早年行情不得再污染宇宙骨架。

背景（P1-42 延伸，2026-09-21）：骨架是「行情里出现过的交易日 × instrument 全表」
的笛卡尔积，而 ``list_date`` 有 97.8% 为 NULL（B5-14）⇒ 仅有个别老标的带 2018+
分区时，早年每天都会为**所有**标的生成占位行（实测 universe_daily 的 2018–2021
共 2,313,074 行、占其 44.8%）。配套动作：``scripts/purge_pre2022.py`` 已把既有
2022 前分区移入隔离目录；本用例守住"不能再被重建出来"。
"""
from __future__ import annotations

import sys
from datetime import date
from pathlib import Path

BACKEND_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(BACKEND_ROOT))

import polars as pl  # noqa: E402
import pytest  # noqa: E402

SYM = "600001.SH"
CODE = "600001"
OLD_DAY = date(2019, 6, 3)
NEW_DAYS = [date(2022, 3, 1), date(2022, 3, 2)]


def _bars(days: list[date]) -> pl.DataFrame:
    return pl.DataFrame({
        "symbol": [SYM] * len(days), "code": [CODE] * len(days), "date": days,
        "open": [10.0] * len(days), "high": [10.5] * len(days),
        "low": [9.8] * len(days), "close": [10.2] * len(days),
        "volume": [1_000_000.0] * len(days),
        "amount": [1.02e7] * len(days),
    })


@pytest.fixture()
def seeded_two_era():
    """一只在 2019 年**已上市**的标的 + 2019/2022 两代行情分区。

    刻意让 ``list_date=2019-01-01``（非 NULL）：这样 2019 行被排除**只可能**源于
    HISTORY_START 闸门，而不是"上市前日期过滤"顺手挡掉的——修前该用例必红。
    沿用 test_backtest_settlement.py 的真库 + 真分区模式。
    """
    import asyncio

    from app.data.parquet_store import write_year_batch
    from app.db.init_db import init_database
    from app.db.models import Instrument
    from app.db.session import get_session_factory

    asyncio.run(init_database())

    async def _seed() -> None:
        factory = get_session_factory()
        async with factory() as sess:
            await sess.execute(Instrument.__table__.delete())
            sess.add(Instrument(symbol=SYM, code=CODE, name="跨代测试股",
                                market="SH", instrument_type="stock",
                                is_st=False, list_date=date(2019, 1, 1)))
            await sess.commit()

    asyncio.run(_seed())
    for year, days in ((2019, [OLD_DAY]), (2022, NEW_DAYS)):
        write_year_batch("daily_bar", SYM, year, _bars(days))
        write_year_batch("daily_bar_hfq", SYM, year, _bars(days))
    return {"old": OLD_DAY, "new": NEW_DAYS}


def test_history_start_constant_is_2022() -> None:
    from app.data.universe import HISTORY_START

    assert HISTORY_START == date(2022, 1, 1)


def test_both_builders_expose_start_with_the_floor_default() -> None:
    """两个构建器都要有 ``start`` 形参且默认等于闸门（否则调用方静默绕过）。"""
    import inspect

    from app.data import universe as uni

    for fn in (uni.build_universe_history, uni.build_universe_backtest):
        sig = inspect.signature(fn)
        assert "start" in sig.parameters, f"{fn.__name__} 缺少 start 形参"
        assert sig.parameters["start"].default == uni.HISTORY_START, (
            f"{fn.__name__} 的 start 默认值不是闸门 ⇒ 默认调用会纳入早年数据")


@pytest.mark.parametrize("builder", ["build_universe_history",
                                     "build_universe_backtest"])
def test_early_bars_are_excluded_from_grid(builder: str,
                                           seeded_two_era) -> None:
    """**闸门本体**：行情里有 2019 年的行，骨架里不得出现 2019 年。

    端到端：真 instrument 表 + 真 parquet 分区（2019 与 2022 各一），
    两个构建器同判据。修前该用例为红（2019 行会进骨架）。
    """
    from app.data import universe as uni

    out = getattr(uni, builder)(persist=False)
    assert not out.is_empty(), f"{builder} 产出为空，用例前提不成立"
    lo = out["date"].min()
    assert lo >= date(2022, 1, 1), (
        f"{builder} 骨架含 {lo}（2022 前）⇒ 早年占位行会再次生成")
    assert date(2019, 6, 3) not in set(out["date"].to_list())


def test_start_can_be_widened_explicitly(seeded_two_era) -> None:
    """显式传更早的 start 时**允许**纳入（闸门是默认值，不是硬编码禁区）。"""
    from app.data import universe as uni

    out = uni.build_universe_backtest(persist=False, start=date(2019, 1, 1))
    assert out["date"].min() == date(2019, 6, 3), (
        "显式放宽 start 后 2019 行仍应可纳入（否则闸门变成了硬编码禁区）")


def test_purge_script_year_parser_handles_filename_partition(tmp_path) -> None:
    """``scripts/purge_pre2022.py`` 的年份解析必须认 `year=2018.snappy.parquet`。

    这是该脚本第一版的真实缺陷：文件名本身带 ``year=``，用
    ``split('=')[1].isdigit()`` 会取到 ``"2018.snappy"`` ⇒ 全部按年分区的数据集
    被漏判（dry-run 只报出 cs，漏掉 daily_bar/_hfq/_qfq/features/universe_daily）。
    """
    sys.path.insert(0, str(BACKEND_ROOT / "scripts"))
    import purge_pre2022 as pp

    assert pp.year_of(Path("daily_bar/symbol=000001.SZ/year=2018.snappy.parquet")) == 2018
    assert pp.year_of(Path("cs/x/year=2021/date=20210104/a.parquet")) == 2021
    assert pp.year_of(Path("predictions/date=20220301.snappy.parquet")) == 2022
    assert pp.year_of(Path("whatever/no_partition.parquet")) is None


def test_purge_script_quarantine_default_is_outside_data_root() -> None:
    """隔离目录必须在 DATA_ROOT 之外，否则会被当成分区数据集再次扫到。"""
    sys.path.insert(0, str(BACKEND_ROOT / "scripts"))
    import purge_pre2022 as pp

    from app.core.config import get_settings

    data_root = Path(get_settings().DATA_ROOT)
    q = Path(pp.DEFAULT_QUARANTINE)
    assert data_root not in q.parents, (
        f"隔离目录 {q} 位于 DATA_ROOT 之内 ⇒ 会被构建器/盘点脚本误当成数据集")