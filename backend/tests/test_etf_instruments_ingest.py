"""ETF 目录入库回归（任务4 P0：ETF 搜索「永久搜不到」）。

背景
----
``instrument`` 表实测只有股票行（``('stock', 5552)``、``etf`` = 0 行），
``GET /api/v1/portfolio/search?q=510300`` 实测返回 0 条。**根因在入库不在查询**：
入库链路把 ``instrument_type`` 硬编码为 ``"stock"`` 且只 upsert 股票目录。

本文件是**不依赖网络**的单元测试：注入伪造 ETF 目录（避免触发
``data/etf.build_catalog()`` 的真实网络拉取）→ 幂等 upsert → 断言
``instrument`` 表出现 ``instrument_type='etf'`` 的行 → 端到端断言
``portfolio._search_assets("510300")`` 能返回 ETF。

隔离与自清
----------
复用 conftest 的**会话隔离 SQLite**；测试结束删除本用例插入的 symbol，
不污染同会话其它用例。全程不触碰生产 ``data/``。
"""
from __future__ import annotations

import asyncio
import sqlite3
import sys
from pathlib import Path

BACKEND_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(BACKEND_ROOT))

from app.api.v1.portfolio import _search_assets  # noqa: E402
from app.core.config import get_settings  # noqa: E402
from app.data.ingest import etf_instruments as etf_ing  # noqa: E402

#: 伪造目录：3 只中国场内 ETF（覆盖 5/1 两种沪深前缀）+ 1 只美国 ETF。
_FAKE_CATALOG: list[dict] = [
    {"code": "510300", "name": "沪深300ETF", "country": "cn"},
    {"code": "588000", "name": "科创50ETF", "country": "cn"},
    {"code": "159915", "name": "创业板ETF", "country": "cn"},
    {"code": "SPY", "name": "标普500ETF-SPDR", "country": "us"},
]
_INSERTED_SYMBOLS = ("510300.SH", "588000.SH", "159915.SZ", "SPY")


def _cleanup() -> None:
    """删除本用例插入的 ETF 行（自清，绝不污染同会话其它用例）。"""
    db = get_settings().SQLITE_PATH
    if not db.exists():
        return
    try:
        with sqlite3.connect(db, timeout=30) as conn:
            conn.executemany(
                "DELETE FROM instrument WHERE symbol = ?",
                [(s,) for s in _INSERTED_SYMBOLS],
            )
            conn.commit()
    except sqlite3.Error:
        # 自清失败不应让用例变红（隔离目录会随会话销毁）；仅静默忽略。
        pass


def test_build_etf_instrument_frame_maps_symbols_and_types() -> None:
    """目录 → instrument 行：类型恒为 etf，中国 ETF 补全正确交易所后缀。"""
    frame = etf_ing.build_etf_instrument_frame(_FAKE_CATALOG)
    assert frame.height == 4, frame
    assert set(frame["instrument_type"].to_list()) == {"etf"}
    by_code = {r["code"]: r for r in frame.to_dicts()}
    assert by_code["510300"]["symbol"] == "510300.SH"
    assert by_code["588000"]["symbol"] == "588000.SH"
    assert by_code["159915"]["symbol"] == "159915.SZ", "15xxxx 为深市 ETF，不得误判 .SH"
    assert by_code["SPY"]["symbol"] == "SPY"
    assert by_code["SPY"]["market"] == "US"


def test_empty_catalog_produces_empty_frame_and_skips_write() -> None:
    """空目录：返回同 schema 空表，且不写库（绝不写入伪造行）。"""
    frame = etf_ing.build_etf_instrument_frame([])
    assert frame.is_empty()
    assert set(frame.columns) == {
        "code", "symbol", "name", "market", "instrument_type", "is_st"}
    assert asyncio.run(etf_ing.upsert_etf_instruments([])) == 0


def test_etf_upsert_is_idempotent_and_enables_portfolio_search() -> None:
    """核心验收：幂等 upsert 后 ``_search_assets("510300")`` 必须返回 ETF 行。"""
    try:
        first = asyncio.run(etf_ing.upsert_etf_instruments(_FAKE_CATALOG))
        assert first == 4, first
        # 幂等：重复 upsert 不新增/不报错（ON CONFLICT(symbol) DO UPDATE）
        second = asyncio.run(etf_ing.upsert_etf_instruments(_FAKE_CATALOG))
        assert second == 4, second

        rows = _none_if_no_table()
        assert rows == 4, f"instrument 表 etf 行数应为 4，实际 {rows}"

        hits = _search_assets("510300", 10)
        assert hits, "upsert 后仍搜不到 ETF（修复前恒 0 条）"
        assert any(h["code"] == "510300" and h["type"] == "etf" for h in hits), hits

        etf_hits = _search_assets("ETF", 10)
        assert any(h["type"] == "etf" for h in etf_hits), etf_hits
    finally:
        _cleanup()


def _none_if_no_table() -> int:
    """直接只读 SQLite 统计本用例写入的 etf 行数（绕过 ORM/cache）。"""
    db = get_settings().SQLITE_PATH
    with sqlite3.connect(f"file:{db}?mode=ro", uri=True) as conn:
        return int(conn.execute(
            "SELECT COUNT(*) FROM instrument WHERE instrument_type='etf' "
            "AND symbol IN (?,?,?,?)", ("510300.SH", "588000.SH", "159915.SZ", "SPY")
        ).fetchone()[0])
