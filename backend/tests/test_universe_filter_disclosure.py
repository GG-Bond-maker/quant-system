"""P1-34（B7a-03）防回归：**未做 ST/停牌过滤的榜单不得被当成已校验结果**。

## 缺陷

`data/screening.py::filter_universe` 只在 `universe_daily` 当日快照存在时才
join + 过滤 ST/停牌（`if universe.height:`）。当日无快照时（`require_universe`
默认 `False`）：

* 不 join、**不过滤**、也**不披露**，函数照常返回 `(df, pool_size)`；
* `api/v1/screener.py:262` 的调用方无法区分，响应照报 `status="ok"`；
* 实测形态（报告 P1-34）：同一 pred 下 ST 股从「被剔除(count=1)」变成
  「进榜(count=2)」，`coverage` 还是 `2/2`。

这违反 `screening.py:85-88` **自己**写下的红线（未校验的榜单不得被当成已校验结果）。

## 修法

1. `filter_universe` 返回 :class:`screening.FilterUniverseResult`：仍可**两元解包**
   （既有 6 个调用点与大量测试零改动），并额外携带 `universe_ok` / `universe_rows` /
   `universe_reason`；空快照时**记 WARNING**（原实现连日志都没有）。
2. 口径**随快照持久化**（存进 `screener_snapshot_stats.stats_json`，避免迁移生产库），
   读路径回传 ⇒ 主路径（快照优先）也带真值；旧快照无该键 ⇒ `applied=None`（未知，
   不臆断"没过滤"、也不谎称已过滤）。
3. `api/v1/screener.py::_finalize_screener_payload`：口径字段**恒在**；
   `applied=False` 时**不得报 ok** ⇒ `degraded` / `universe_unfiltered`。
"""
from __future__ import annotations

import json
import sqlite3
import sys
from datetime import date
from pathlib import Path

import polars as pl
import pytest

BACKEND_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(BACKEND_ROOT))

from app.api.v1 import screener as screener_api  # noqa: E402
from app.core.config import get_settings  # noqa: E402
from app.data import screening  # noqa: E402
from app.data.parquet_store import path_for_year  # noqa: E402

DAY = date(2026, 9, 18)          # 有 universe_daily 快照
DAY_NO_UNI = date(2026, 9, 19)   # 同年分区存在，但该日无行 ⇒ universe_date_absent
DAY_NO_FILE = date(2027, 1, 4)   # 该年文件都不存在 ⇒ universe_partition_missing

# symbol, is_st, is_halted
_ROWS = [("600001.SH", False, False), ("600004.SH", True, False),
         ("600002.SH", False, True)]


def _pred() -> pl.DataFrame:
    return pl.DataFrame({
        "date": [DAY] * 3,
        "symbol": [r[0] for r in _ROWS],
        "pred_score": [0.9, 0.8, 0.7],
    })


def _write_universe(day: date, rows: list[tuple[str, bool, bool]]) -> None:
    """把某日 universe_daily 快照写进私有 DATA_ROOT（`__all__` 分区）。"""
    p = path_for_year("universe_daily", "__all__", str(day.year))
    p.parent.mkdir(parents=True, exist_ok=True)
    new = pl.DataFrame({
        "date": [day] * len(rows),
        "symbol": [r[0] for r in rows],
        "name": [f"名称{r[0][:6]}" for r in rows],
        "industry": ["测试"] * len(rows),
        "board": ["main"] * len(rows),
        "is_st": [r[1] for r in rows],
        "is_halted": [r[2] for r in rows],
        "close": [10.0] * len(rows),
        "limit_pct": [0.1] * len(rows),
    })
    if p.exists():
        old = pl.read_parquet(p)
        new = pl.concat([old.filter(pl.col("date") != day), new], how="vertical")
    new.write_parquet(p)


class _WarnSpy:
    def __init__(self) -> None:
        self.warnings: list[str] = []

    def warning(self, msg: str, *a, **k) -> None:  # noqa: ANN002, ANN003
        self.warnings.append(str(msg))

    def info(self, *a, **k) -> None:  # noqa: ANN002, ANN003
        pass


# ---------------- 1. filter_universe 层 ----------------

def test_st_halted_rows_are_filtered_when_universe_exists():
    """正向基线：有快照时必须真的剔除 ST/停牌（防止我改坏过滤本身）。"""
    _write_universe(DAY, _ROWS)
    res = screening.filter_universe(_pred(), DAY.isoformat(), "all")
    assert res.universe_ok is True and res.universe_rows == 3
    assert res.universe_reason is None
    assert [r["symbol"] for r in res.df.iter_rows(named=True)] == ["600001.SH"], (
        "ST(600004) 与停牌(600002) 都必须被剔除")


def test_missing_universe_partition_is_reported_not_silent(monkeypatch):
    """**缺陷本体**：分区不存在时必须 self-report（原来只回 df，调用方无法知道）。"""
    spy = _WarnSpy()
    monkeypatch.setattr(screening, "logger", spy)
    res = screening.filter_universe(_pred(), DAY_NO_FILE.isoformat(), "all")
    assert res.universe_ok is False
    assert res.universe_reason == "universe_partition_missing"
    # 复现缺陷机制：ST/停牌**确实没有**被过滤（这就是必须披露的原因）
    assert [r["symbol"] for r in res.df.iter_rows(named=True)] == [
        "600001.SH", "600004.SH", "600002.SH"]
    assert spy.warnings and "未生效" in spy.warnings[0]


def test_existing_year_but_absent_date_is_distinguished(monkeypatch):
    """文件在、当日无行 ⇒ 与"文件都没有"区分（两种原因都披露）。"""
    _write_universe(DAY, _ROWS)
    spy = _WarnSpy()
    monkeypatch.setattr(screening, "logger", spy)
    res = screening.filter_universe(_pred(), DAY_NO_UNI.isoformat(), "all")
    assert res.universe_ok is False
    assert res.universe_reason == "universe_date_absent", res.universe_reason
    assert spy.warnings


def test_two_tuple_unpacking_still_works():
    """兼容性：既有调用点/测试全部是 `df, pool = filter_universe(...)`。"""
    _write_universe(DAY, _ROWS)
    df, pool = screening.filter_universe(_pred(), DAY.isoformat(), "all")
    assert pool == 1 and df.height == 1


def test_require_universe_still_raises():
    """`require_universe=True` 的既有 loud 行为不得被本修复弱化。"""
    from app.core.errors import ERR_DATA_EMPTY, AQPException

    with pytest.raises(AQPException) as ei:
        screening.filter_universe(_pred(), DAY_NO_FILE.isoformat(), "all",
                                  require_universe=True)
    assert ei.value.code == ERR_DATA_EMPTY


# ---------------- 2. 快照持久化 ----------------

def test_snapshot_persists_universe_disclosure(tmp_path):
    """快照路径（生产主路径）必须把口径**存下来**，否则读取端无从披露。"""
    _write_universe(DAY, _ROWS)
    staging = tmp_path / "pred.parquet"
    _pred().write_parquet(staging)

    import app.data.screening as sc

    orig = sc.get_settings
    try:
        # 用一个临时 SQLite 建表，避免污染生产库
        db = tmp_path / "snap.db"
        con = sqlite3.connect(db)
        con.execute("CREATE TABLE screener_snapshot (date TEXT, strategy TEXT, board TEXT,"
                    " rank INTEGER, symbol TEXT, name TEXT, industry TEXT,"
                    " pred_score REAL, model_version TEXT, close REAL, pct REAL,"
                    " turnover REAL, amount REAL, limit_pct REAL, risk TEXT,"
                    " PRIMARY KEY(date,strategy,board,rank))")
        con.execute("CREATE TABLE screener_snapshot_stats (date TEXT, strategy TEXT,"
                    " board TEXT, pool_size INTEGER, total INTEGER, trade_date TEXT,"
                    " stats_json TEXT, PRIMARY KEY(date,strategy,board))")
        # `enrich_items` 会查 instrument 表（名称/行业兜底）⇒ 临时库同样要有
        con.execute("CREATE TABLE instrument (symbol TEXT PRIMARY KEY, name TEXT,"
                    " industry TEXT, board TEXT, is_st INTEGER, is_halted INTEGER)")
        con.executemany("INSERT INTO instrument VALUES (?,?,?,?,?,?)",
                        [(s, f"名称{s[:6]}", "测试", "main", 0, 0) for s, _, _ in _ROWS])
        con.commit()
        con.close()

        class _S:
            SQLITE_PATH = db
            DATA_ROOT = orig().DATA_ROOT

        sc.get_settings = lambda: _S()          # type: ignore[assignment]
        summary = sc.write_screener_snapshot(DAY.isoformat(), _pred(), strategy="alpha_basic_v1")
        assert "rows=" in summary
        con = sqlite3.connect(db)
        row = con.execute("SELECT stats_json FROM screener_snapshot_stats "
                          "WHERE date=? AND board='all'", (DAY.isoformat(),)).fetchone()
        con.close()
        assert row is not None
        stored = json.loads(row[0])
        assert stored["universe_filter"] == {
            "applied": True, "rows": 3, "date": DAY.isoformat(), "reason": None}
    finally:
        sc.get_settings = orig               # type: ignore[assignment]


def test_loader_reports_unknown_for_legacy_snapshot(tmp_path, monkeypatch):
    """旧快照（stats_json 无该键）⇒ 读出的口径是 None（未知），不得臆断。"""
    import app.data.screening as sc

    db = tmp_path / "legacy.db"
    con = sqlite3.connect(db)
    con.execute("CREATE TABLE screener_snapshot (date TEXT, strategy TEXT, board TEXT,"
                " rank INTEGER, symbol TEXT, name TEXT, industry TEXT, pred_score REAL,"
                " model_version TEXT, close REAL, pct REAL, turnover REAL, amount REAL,"
                " limit_pct REAL, risk TEXT, PRIMARY KEY(date,strategy,board,rank))")
    con.execute("CREATE TABLE screener_snapshot_stats (date TEXT, strategy TEXT,"
                " board TEXT, pool_size INTEGER, total INTEGER, trade_date TEXT,"
                " stats_json TEXT, PRIMARY KEY(date,strategy,board))")
    con.execute("INSERT INTO screener_snapshot_stats VALUES (?,?,?,?,?,?,?)",
                (DAY.isoformat(), "alpha_basic_v1", "all", 2, 1, DAY.isoformat(),
                 json.dumps({"n": 1})))
    con.execute("INSERT INTO screener_snapshot VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                (DAY.isoformat(), "alpha_basic_v1", "all", 1, "600001.SH", "名称", "测试",
                 0.9, None, 10.0, 1.0, 0.1, 1e8, 0.1, "strong"))
    con.commit()
    con.close()

    orig = sc.get_settings
    monkeypatch.setattr(sc, "get_settings", lambda: type("_S", (), {
        "SQLITE_PATH": db, "DATA_ROOT": orig().DATA_ROOT})())
    snap = sc.load_screener_snapshot(DAY.isoformat(), "alpha_basic_v1", "all", 1)
    assert snap is not None
    assert snap["universe_filter"] is None, "旧快照必须报 None（未知），不得报 True"


# ---------------- 3. 响应层：不得报 ok ----------------

def _base_payload(**over: object) -> dict:
    d = {
        "date": DAY.isoformat(),
        "items": [{"symbol": "600001.SH", "close": 10.0, "pct": 1.0, "amount": 1e8,
                   "score": 0.9, "industry": "测试", "signal_strength": "strong",
                   "turnover": 0.01}],
        "stats": {"today": {"pool_size": 1}, "prev": None, "prev_date": None},
    }
    d.update(over)
    return d


def test_unfiltered_list_is_never_ok():
    """**修复的核心断言**：确知未过滤 ⇒ degraded/universe_unfiltered。"""
    out = screener_api._finalize_screener_payload(_base_payload(
        universe_filter={"applied": False, "rows": 0, "date": DAY.isoformat(),
                         "reason": "universe_partition_missing"}))
    assert out["status"] == "degraded", out
    assert out["reason"] == "universe_unfiltered"
    assert "ST" in out["message"]


def test_filtered_list_stays_ok():
    """反向断言：真过滤过就仍是 ok（不要把正常路径也降级）。"""
    out = screener_api._finalize_screener_payload(_base_payload(
        universe_filter={"applied": True, "rows": 10, "date": DAY.isoformat(),
                         "reason": None}))
    assert out["status"] == "ok" and out["reason"] is None


def test_unknown_disclosure_does_not_fake_ok_nor_degrade():
    """未知（旧快照）⇒ 披露 applied=None，但不臆断"没过滤过"（避免误伤正常路径）。"""
    out = screener_api._finalize_screener_payload(_base_payload())
    assert out["status"] == "ok"
    assert out["universe_filter"]["applied"] is None
    assert out["universe_filter"]["reason"] == "unknown_snapshot_without_disclosure"


def test_unavailable_outranks_unfiltered():
    """空榜终态优先：unavailable 不得被 universe 降级覆盖（状态优先级保持）。"""
    out = screener_api._finalize_screener_payload(_base_payload(
        status="unavailable", reason="market_data_missing", items=[],
        universe_filter={"applied": False, "rows": 0, "date": DAY.isoformat(),
                         "reason": "universe_date_absent"}))
    assert out["status"] == "unavailable" and out["reason"] == "market_data_missing"


def test_unavailable_body_carries_disclosure():
    """口径字段不随数据可用性变化：空态响应也带 `universe_filter`。"""
    body = screener_api._unavailable_body(
        DAY.isoformat(), "alpha_basic_v1", 50, "all",
        reason="model_not_ready", message="x")
    assert body["universe_filter"]["applied"] is False
    assert body["universe_filter"]["reason"] == "not_evaluated"
    assert get_settings() is not None      # 触发配置加载，确保测试在隔离 DATA_ROOT 下