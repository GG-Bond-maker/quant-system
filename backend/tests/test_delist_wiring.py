"""审计 §8.2 第 6 项（退市信息接线）防回归：enrich_delist 进流水线 + 披露 + 向后兼容。

条目原文（docs/audit-2026-09-18/AQP-全栈审核报告.md）：
  · §6.3:775「退市名单回填 | data/ingest/tasks.py:60 | 唯一调用方是**手工脚本**
    scripts/enrich_delist.py（未进任何流水线/定时任务）；instrument.delist_date
    实测 0/15 | **接线（P1-4 前置）**」
  · §8.2:1046 第 6 项「退市数据接线（enrich_delist 进流水线）| P1-4, B5-14」
  · §7-S2:873-874 ① 建库期「按 delist_date 剔除」空转（非空 **0/5552**）；
    ② 两套面板都**没有**该列 ⇒ ① 在结构上不可能发生
  · B5-14:413 `list_date` **97.8% NULL**（上述退市源自带"上市日期"，可补已退市标的）

本文件的全部取数**注入假源**（monkeypatch / 依赖注入），**绝不发真实网络请求**；
断言只用可离线复算的事实（SQLite 直读计数、parquet footer、状态 JSON）。
"""
from __future__ import annotations

import asyncio
import json
import sqlite3
import sys
from datetime import date
from pathlib import Path

BACKEND_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(BACKEND_ROOT))

import polars as pl  # noqa: E402
import pytest  # noqa: E402
from sqlalchemy import delete  # noqa: E402

from app.core.config import get_settings  # noqa: E402
from app.data.ingest.tasks import (  # noqa: E402
    delist_status_path,
    enrich_delist_dates,
)
from app.db.init_db import init_database  # noqa: E402
from app.db.models import Instrument  # noqa: E402
from app.db.session import get_session_factory  # noqa: E402

# 本地在册标的（决定"能填谁"）+ 一个名单里有、本地没有的代码（必须不新增行）
SYMS = ("600001.SH", "000003.SZ", "600519.SH")
FOREIGN_CODE = "600002"        # 齐鲁退市：名单有、本地无


def _fake_source() -> pl.DataFrame:
    """假退市名单，schema 与 ``akshare_adapter.fetch_delist_list`` 完全一致。

    600002 只存在于源里（本地无该 instrument）⇒ 用来证明"只更新已存在的行"。
    600519 的 list_date 源里给的是 1999-01-01，但本地已有 2001-08-27 ⇒
    用来证明"只补空、绝不覆盖"。
    """
    return pl.DataFrame({
        "code": ["600001", "000003", "600519", FOREIGN_CODE],
        "name": ["邯郸钢铁", "PT金田Ａ", "贵州茅台", "齐鲁退市"],
        "list_date": [date(1998, 1, 22), date(1991, 1, 14),
                      date(1999, 1, 1), date(1998, 4, 8)],
        "delist_date": [date(2009, 12, 29), date(2002, 6, 14),
                        date(2010, 1, 1), date(2006, 4, 24)],
        "source": ["akshare_sh_delist"] * 2 + ["akshare_sz_delist"] * 2,
    })


@pytest.fixture()
def seeded():
    """隔离播种本轮涉及的 instrument 行（只动自己那几个 symbol）。"""
    asyncio.run(init_database())

    async def _seed() -> None:
        factory = get_session_factory()
        async with factory() as sess:
            await sess.execute(delete(Instrument).where(Instrument.symbol.in_(SYMS)))
            sess.add_all([
                Instrument(symbol="600001.SH", code="600001", name="邯郸钢铁",
                           market="SH", instrument_type="stock", is_st=False),
                Instrument(symbol="000003.SZ", code="000003", name="PT金田Ａ",
                           market="SZ", instrument_type="stock", is_st=False),
                # list_date 已有值 ⇒ 回填只补空，不得覆盖
                Instrument(symbol="600519.SH", code="600519", name="贵州茅台",
                           market="SH", instrument_type="stock", is_st=False,
                           list_date=date(2001, 8, 27)),
            ])
            await sess.commit()

    asyncio.run(_seed())
    return SYMS


def _db_rows() -> dict[str, tuple[date | None, date | None]]:
    """直接从 SQLite 读真实值（**独立于被测代码**，用于核对披露计数）。"""
    con = sqlite3.connect(get_settings().SQLITE_PATH)
    try:
        rows = con.execute(
            "SELECT symbol, delist_date, list_date FROM instrument "
            "WHERE symbol IN (?, ?, ?)", SYMS).fetchall()
    finally:
        con.close()
    return {r[0]: (date.fromisoformat(r[1]) if r[1] else None,
                   date.fromisoformat(r[2]) if r[2] else None) for r in rows}


def _db_counts() -> tuple[int, int]:
    con = sqlite3.connect(get_settings().SQLITE_PATH)
    try:
        total, with_date = con.execute(
            "SELECT COUNT(*), SUM(delist_date IS NOT NULL) FROM instrument").fetchone()
    finally:
        con.close()
    return int(total or 0), int(with_date or 0)


def _status_payload() -> dict:
    return json.loads(delist_status_path().read_text(encoding="utf-8"))


# ---------------- ① 无源 ⇒ 优雅降级且状态可辨 ----------------

def test_no_source_degrades_gracefully_and_discloses_unavailable(seeded) -> None:
    """源不可达：不抛异常、不写库，且状态**明确可辨为"不可用"**。"""
    def _boom() -> pl.DataFrame:
        raise ConnectionError("模拟无外网/接口不可达")

    st = asyncio.run(enrich_delist_dates(fetcher=_boom))

    assert st["availability"] == "unavailable", st
    assert st["reason"] and "ConnectionError" in st["reason"], st
    assert st["n_updated"] == 0 and st["n_matched"] == 0
    # 披露：状态文件必须能判读为「退市信息不可用」，而不是「(0 条退市股)」
    payload = _status_payload()
    assert payload["availability"] == "unavailable"
    assert payload["reason"] == st["reason"]
    assert payload["coverage_pct"] == st["coverage_pct"]
    # 未写库：取数失败不得静默变成"没有退市股"
    assert all(v[0] is None for v in _db_rows().values())


def test_empty_source_is_distinguishable_from_no_delisted_stocks(seeded) -> None:
    """源返回**空表** ⇒ 与"成功且确实没有退市股"必须可区分（reason=empty_source）。"""
    empty = pl.DataFrame(schema={"code": pl.Utf8, "name": pl.Utf8,
                                 "list_date": pl.Date, "delist_date": pl.Date,
                                 "source": pl.Utf8})
    st = asyncio.run(enrich_delist_dates(fetcher=lambda: empty))
    assert st["availability"] == "unavailable", st
    assert st["reason"] == "empty_source", st
    assert _status_payload()["reason"] == "empty_source"


def test_pipeline_step_never_raises_and_reports_status(seeded, monkeypatch) -> None:
    """接线本体：步骤已在步骤集里，且源不可用时**不 fail-fast**、状态可读。"""
    import app.data.ingest.akshare_adapter as ak_adapter
    from app import orchestrator

    assert "enrich_delist" in orchestrator.FULL_STEPS
    assert "enrich_delist" in orchestrator.EVENING_STEPS, "晚间例行必须执行（否则永不回填）"
    assert orchestrator.step_enrich_delist is orchestrator.STEP_FUNCTIONS["enrich_delist"]

    def _boom() -> pl.DataFrame:
        raise ConnectionError("offline")

    # 惰性 import 取的是模块属性 ⇒ 打桩后步骤内部也走假源（零真实网络请求）
    monkeypatch.setattr(ak_adapter, "fetch_delist_list", _boom)
    detail = orchestrator.STEP_FUNCTIONS["enrich_delist"](date(2026, 9, 22), ["600001"])
    assert "availability=unavailable" in detail, detail
    assert "coverage=" in detail and "updated=0" in detail, detail


# ---------------- ② 有源 ⇒ 写入正确、幂等、可重复执行 ----------------

def test_writes_delist_and_fills_null_list_date_only(seeded) -> None:
    st = asyncio.run(enrich_delist_dates(fetcher=_fake_source))

    assert st["availability"] == "ok" and st["reason"] is None, st
    assert st["source"] == "akshare_sh_delist", st
    assert st["n_source_rows"] == 4 and st["n_matched"] == 3, st
    assert st["n_updated"] == 3, st

    rows = _db_rows()
    assert rows["600001.SH"] == (date(2009, 12, 29), date(1998, 1, 22))
    assert rows["000003.SZ"] == (date(2002, 6, 14), date(1991, 1, 14))
    # B5-14 部分回填：只补空的 list_date，已有真实值（2001-08-27）不得被覆盖
    assert rows["600519.SH"] == (date(2010, 1, 1), date(2001, 8, 27))
    # 名单里有、本地没有的代码：不得新增 instrument 行
    assert "600002.SH" not in rows


def test_rerun_is_idempotent_and_repeatable(seeded) -> None:
    """同一份名单重复执行结果不变；名单变化时可重复执行并更新（不是一次性）。"""
    st1 = asyncio.run(enrich_delist_dates(fetcher=_fake_source))
    first = _db_rows()

    st2 = asyncio.run(enrich_delist_dates(fetcher=_fake_source))
    assert st2["n_updated"] == st1["n_updated"] == 3
    assert _db_rows() == first, "幂等：重复执行不得改变库内状态"

    changed = _fake_source().with_columns(
        pl.when(pl.col("code") == "600001")
        .then(pl.lit(date(2010, 3, 1)))
        .otherwise(pl.col("delist_date")).alias("delist_date"))
    st3 = asyncio.run(enrich_delist_dates(fetcher=lambda: changed))
    assert st3["n_updated"] == 3
    assert _db_rows()["600001.SH"][0] == date(2010, 3, 1), "名单修订必须能再次落库"


# ---------------- ③ 覆盖率披露字段存在且与真实计数一致 ----------------

def test_coverage_fields_match_real_counts(seeded) -> None:
    st = asyncio.run(enrich_delist_dates(fetcher=_fake_source))
    total, with_date = _db_counts()

    assert st["n_instruments"] == total
    # 披露值必须等于**同源实测**的全库计数（这才是"披露不撒谎"的不变量）
    assert st["n_with_delist_date"] == with_date
    assert st["coverage_pct"] == round(with_date / total * 100, 2)

    # 本用例只能对"自己那 3 个 symbol"负责：测试会话**共享同一个 SQLite**
    # （conftest 只隔离 DATA_ROOT），别的用例可能留下 delist_date 行 ——
    # 实测 `test_delist_liquidation.py:108` 写入的 600099.SH 就不清理 ⇒
    # 原先写死的全库 `== 3` 是**顺序敏感断言**，在全量套件里假红
    # （字典序前缀复现：`assert 4 == 3`）。改为作用域内计数。
    assert sum(1 for v in _db_rows().values() if v[0] is not None) == 3, \
        "本轮 3 个在册标的必须都被回填 delist_date"
    assert all(v[0] is not None for v in _db_rows().values())

    payload = _status_payload()
    for key in ("availability", "source", "n_instruments", "n_with_delist_date",
                "coverage_pct", "as_of", "n_source_rows", "n_matched", "n_updated"):
        assert payload[key] == st[key], f"披露文件字段 {key} 与返回值不一致"

    # API 侧读的是同一事实（现场 SQL 计数 + 状态文件），不是第二套口径
    from app.api.v1.backtest import _delist_coverage

    cov = _delist_coverage()
    assert cov["source"] == "instrument"
    assert cov["n_instruments"] == total and cov["n_with_delist_date"] == with_date
    assert cov["delist_sync"]["availability"] == "ok"
    assert cov["delist_sync"]["coverage_pct"] == st["coverage_pct"]


def test_partial_and_unavailable_states_are_visible_in_note(seeded) -> None:
    """覆盖率部分缺失时，回测 note 必须带真实计数且点明状态（不得静默当已处理）。"""
    from app.api.v1.backtest import _delist_coverage, _universe_note

    one = _fake_source().head(1)                     # 只命中 1/3
    st = asyncio.run(enrich_delist_dates(fetcher=lambda: one))
    assert st["availability"] == "ok" and st["n_matched"] == 1
    assert 0 < st["coverage_pct"] < 100

    note = _universe_note(pl.DataFrame({"symbol": list(SYMS)}), _delist_coverage())
    assert f"{st['n_with_delist_date']}/{st['n_instruments']}" in note, note
    assert "已按 delist_date 剔除" in note, note

    # 源不可用 ⇒ note 必须显式说明"覆盖率不会增长、退市剔除不可依赖"
    def _boom() -> pl.DataFrame:
        raise ConnectionError("offline")

    asyncio.run(enrich_delist_dates(fetcher=_boom))
    note2 = _universe_note(pl.DataFrame({"symbol": list(SYMS)}), _delist_coverage())
    assert "退市名单回填未完成" in note2 and "不可依赖" in note2, note2


# ---------------- ④ 旧 schema 分区仍可读（向后兼容反证） ----------------

def test_old_partition_without_delist_date_stays_readable(tmp_path, monkeypatch) -> None:
    """delist_date 列是 schema 演进：旧分区无该列**仍可读**、不丢行、值为 null。"""
    data = tmp_path / "parquet"
    # 仓库惯例：改已缓存 Settings 对象（勿 setenv+cache_clear，见 conftest 语义）
    monkeypatch.setattr(get_settings(), "DATA_ROOT", data)

    from app.data.parquet_store import write_partition

    old = pl.DataFrame({"date": [date(2024, 1, 2), date(2024, 1, 3)],
                        "symbol": ["600099.SH"] * 2, "close": [10.0, 10.2]})
    write_partition("universe_daily_bt", "__all__", date(2024, 1, 1), old,
                    dedup_keys=("date", "symbol"))
    new = pl.DataFrame({"date": [date(2025, 1, 2)], "symbol": ["600099.SH"],
                        "close": [11.0], "delist_date": [date(2025, 6, 1)]})
    write_partition("universe_daily_bt", "__all__", date(2025, 1, 1), new,
                    dedup_keys=("date", "symbol"))

    files = sorted((data / "universe_daily_bt" / "symbol=__all__").glob("year=*.parquet"))
    assert len(files) == 2
    assert "delist_date" not in pl.read_parquet_schema(files[0]), "旧分区本就没有该列"
    assert "delist_date" in pl.read_parquet_schema(files[1])

    # 回测读路径（api/v1/backtest.py 的 pl.concat(..., how="diagonal_relaxed")）
    uni = pl.concat([pl.read_parquet(f) for f in files], how="diagonal_relaxed")
    assert uni.height == 3, "缺列不得导致丢行"
    old_rows = uni.filter(pl.col("date") < date(2025, 1, 1))
    assert old_rows.height == 2 and old_rows["delist_date"].null_count() == 2

    # 披露层如实区分"部分分区已重建"
    from app.api.v1.backtest import _bt_panel_delist_column_state

    assert _bt_panel_delist_column_state() == ("mixed", 2, 1)

    # 同年分区被重建时：_align_concat 给旧行补 null，不丢行、不报错
    write_partition("universe_daily_bt", "__all__", date(2024, 1, 1),
                    pl.DataFrame({"date": [date(2024, 1, 4)], "symbol": ["600099.SH"],
                                  "close": [10.4],
                                  "delist_date": pl.Series([None], dtype=pl.Date)}),
                    dedup_keys=("date", "symbol"))
    year2024 = pl.read_parquet(files[0])
    assert "delist_date" in year2024.columns and year2024.height == 3
    assert year2024.filter(pl.col("date") < date(2024, 1, 4))["delist_date"].null_count() == 2


def test_builder_emits_delist_date_column(tmp_path, monkeypatch) -> None:
    """§7-S2 第②行反证：面板现在**携带** delist_date，退市剔除结构性可验证。"""
    asyncio.run(init_database())

    async def _seed() -> None:
        factory = get_session_factory()
        async with factory() as sess:
            await sess.execute(delete(Instrument).where(Instrument.symbol == "600099.SH"))
            sess.add(Instrument(symbol="600099.SH", code="600099", name="退市测试",
                                market="SH", instrument_type="stock", is_st=False,
                                list_date=date(2020, 1, 1),
                                delist_date=date(2024, 1, 4)))
            await sess.commit()

    asyncio.run(_seed())

    data = tmp_path / "parquet"
    monkeypatch.setattr(get_settings(), "DATA_ROOT", data)
    from app.data.parquet_store import write_year_batch
    from app.data.universe import build_universe_backtest

    days = [date(2024, 1, d) for d in (2, 3, 4, 5)]
    bars = pl.DataFrame({
        "symbol": ["600099.SH"] * 4, "code": ["600099"] * 4, "date": days,
        "open": [10.0] * 4, "high": [10.0] * 4, "low": [10.0] * 4,
        "close": [10.0, 10.2, 10.1, 10.3], "volume": [1e6] * 4, "amount": [1e7] * 4,
    })
    write_year_batch("daily_bar", "600099.SH", 2024, bars)
    write_year_batch("daily_bar_hfq", "600099.SH", 2024, bars)

    out = build_universe_backtest(symbols=["600099.SH"], persist=False)
    assert "delist_date" in out.columns
    assert out["date"].max() == date(2024, 1, 4), "delist_date 当日仍在、之后剔除"
    assert out["delist_date"].to_list() == [date(2024, 1, 4)] * 3