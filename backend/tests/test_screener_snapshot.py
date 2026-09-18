"""L2-1 选股快照表测试（Sprint3）。

覆盖：四板块物化写入（幂等）、API 快照优先读取（from_snapshot）、top_k 截取、
板块过滤、refresh=1 跳过快照、predictions 日期错位回落实时。
全部离线合成数据。
"""
from __future__ import annotations

import asyncio
import sys
from datetime import date
from pathlib import Path

import polars as pl
import pytest
from fastapi.testclient import TestClient

BACKEND_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(BACKEND_ROOT))

from app.core.config import get_settings  # noqa: E402
from app.data.screening import (load_screener_snapshot,  # noqa: E402
                                write_screener_snapshot)
from app.db.session import get_session_factory, reset_engine  # noqa: E402
from app.main import app  # noqa: E402

# 测试标的：5 主板 + 2 创业板 + 1 ST（600009）——快照应剔除 ST
SYMS = [f"6000{i:02d}.SH" for i in range(1, 6)] + \
       [f"3000{i:02d}.SZ" for i in range(1, 3)] + ["600009.SH"]
DAY = date(2026, 9, 4)


@pytest.fixture(scope="module")
def snap_client(tmp_path_factory):
    from sqlalchemy import select as _select

    from app.core.auth import hash_password as _hp
    from app.db.init_db import init_database as _init
    from app.db.models import Instrument
    from app.db.models_auth import Role as _Role, User as _User

    # ---- 私有 DATA_ROOT：根治跨文件污染（顺序无关）----
    # 机制：``load_screener_snapshot(None, ...)`` 以「全局最新 predictions 分区日」
    # 作为快照日，并要求它与 SQLite 里最新的 screener_snapshot_stats 日期一致。
    # conftest 只把 DATA_ROOT 指向**单一**会话临时目录，全模块共用；只要有任何
    # 模块种下**更晚**的 predictions 分区，本模块快照日（2026-09-04）就会与之
    # 错位 → 回落实时路径 → 断言崩。实测污染源：test_api（date=20260911）、
    # test_lab_factor（date=20261231）。写方分散在多个模块、且随执行顺序变化，
    # 逐个堵写方是打地鼠；故在读方（本模块）把 DATA_ROOT 指向私有目录，从源头
    # 切断耦合——本模块只读自己种下的数据，天然顺序无关。
    # 仅覆写 DATA_ROOT；SQLite（登录 / screener_snapshot(_stats)）仍复用 conftest
    # 的共享隔离库（仅本模块写 _stats，日期恒为 2026-09-04，与私有根一致）。
    # 手法对齐仓库既有惯例（test_optuna_walkforward.py 亦对 settings 单例 setattr）。
    mp = pytest.MonkeyPatch()
    private_root = tmp_path_factory.mktemp("screener_snapshot_data")
    mp.setattr(get_settings(), "DATA_ROOT", private_root)

    reset_engine()
    asyncio.run(_init())
    factory = get_session_factory()

    async def _seed():
        async with factory() as sess:
            for name in ("viewer", "researcher", "admin"):
                if not (await sess.scalars(
                        _select(_Role).where(_Role.name == name))).first():
                    sess.add(_Role(name=name))
            await sess.commit()
            roles = {r.name: r.id for r in (await sess.scalars(_select(_Role))).all()}
            if not (await sess.scalars(
                    _select(_User).where(_User.username == "snapadmin"))).first():
                sess.add(_User(username="snapadmin", password_hash=_hp("snappass1"),
                               role_id=roles["viewer"]))
            await sess.commit()
            for i, sym in enumerate(SYMS):
                stmt = Instrument.__table__.insert().prefix_with("OR REPLACE")
                await sess.execute(stmt, {"symbol": sym, "code": sym.split(".")[0],
                                          "name": f"标的{i}", "market": sym[-2:]})
            await sess.commit()

    asyncio.run(_seed())

    # predictions：pred_score 降序与 SYMS 顺序一致；600009 分最低
    scores = [0.90 - 0.05 * i for i in range(len(SYMS))]
    pred = pl.DataFrame({"date": [DAY] * len(SYMS), "symbol": SYMS,
                         "pred_score": scores,
                         "model_version": ["lgbm_test"] * len(SYMS)})
    pred_dir = __import__("app.core.config", fromlist=["get_settings"]) \
        .get_settings().DATA_ROOT / "predictions"
    pred_dir.mkdir(parents=True, exist_ok=True)
    pred.write_parquet(pred_dir / f"date={DAY.strftime('%Y%m%d')}.parquet")

    # universe_daily：600009 为 ST；300xxx 为创业板板（chinext_star）
    uni = pl.DataFrame({
        "date": [DAY] * len(SYMS), "symbol": SYMS,
        "name": [f"标的{i}" for i in range(len(SYMS))],
        "industry": ["行业A"] * 4 + ["行业B"] * 4,
        "board": ["main"] * 5 + ["chinext_star"] * 2 + ["main"],
        "is_st": [False] * 7 + [True],
        "is_halted": [False] * len(SYMS),
        "close": [10.0 + i for i in range(len(SYMS))],
        "limit_pct": [10.0] * len(SYMS),
    })
    uni_dir = __import__("app.core.config", fromlist=["get_settings"]) \
        .get_settings().DATA_ROOT / "universe_daily" / "symbol=__all__"
    uni_dir.mkdir(parents=True, exist_ok=True)
    # 必须与生产写入器（parquet_store.path_for_year / write_partition）同名分区：
    # filter_universe 经 path_for_year 读取 ``year=YYYY.snappy.parquet``，
    # 若按旧名 ``year=YYYY.parquet`` 落盘，join 会静默失效（P0 缺陷 E 的根因）。
    uni.write_parquet(uni_dir / f"year={DAY.year}.snappy.parquet")

    # daily_bar：两日收盘供 pct 计算（read_prev_and_today）
    bars = pl.DataFrame({
        "symbol": SYMS * 2,
        "date": [date(2026, 9, 3)] * len(SYMS) + [date(2026, 9, 4)] * len(SYMS),
        "open": [10.0] * (2 * len(SYMS)),
        "high": [10.0] * (2 * len(SYMS)),
        "low": [10.0] * (2 * len(SYMS)),
        "close": [9.0] * len(SYMS) + [9.9] * len(SYMS),  # 次日 +10%
        "volume": [1000.0] * (2 * len(SYMS)),
        "amount": [1e5] * (2 * len(SYMS)),
    })
    from app.data.parquet_store import write_year_batch

    write_year_batch("daily_bar", "ALL", 2026, bars)
    # write_year_batch 按 symbol 分区——上面合表写入会全落 600001 分区，改为逐只
    for sym in SYMS:
        write_year_batch("daily_bar", sym, 2026, bars.filter(pl.col("symbol") == sym))

    with TestClient(app) as c:
        r = c.post("/api/v1/auth/login",
                   json={"username": "snapadmin", "password": "snappass1"})
        c.headers.update({"Authorization": f"Bearer {r.json()['data']['access_token']}"})
        yield c

    reset_engine()
    mp.undo()  # 归还 DATA_ROOT，后续模块仍用 conftest 的共享隔离目录


def test_write_snapshot_four_boards(snap_client):
    """写入：四板块齐全、ST 被剔除、行数正确、幂等（重写不翻倍）。"""
    pred = pl.read_parquet(
        __import__("app.core.config", fromlist=["get_settings"]).get_settings()
        .DATA_ROOT / "predictions" / f"date={DAY.strftime('%Y%m%d')}.parquet")
    summary = write_screener_snapshot(DAY.isoformat(), pred)
    assert "boards=4" in summary

    # 重写幂等
    write_screener_snapshot(DAY.isoformat(), pred)

    snap = load_screener_snapshot(None, "alpha_basic_v1", "all", 50)
    assert snap is not None and snap["from_snapshot"] is True
    syms = [it["symbol"] for it in snap["items"]]
    assert "600009.SH" not in syms, "ST 未被剔除"
    assert "300001.SZ" in syms  # all 榜含创业板
    assert snap["stats"]["today"]["pool_size"] == 7  # 8 - 1 ST
    assert snap["stats"]["today"]["total"] == min(7, 50)


def test_api_snapshot_first(snap_client: TestClient):
    """读取：/screener 命中快照（from_snapshot=True），结构兼容实时路径。"""
    r = snap_client.get("/api/v1/screener", params={"top_k": 5, "board": "all"})
    body = r.json()
    assert body["code"] == 0, body["message"]
    d = body["data"]
    assert d["from_snapshot"] is True
    assert d["count"] == 5
    assert len(d["items"]) == 5
    # 富化列来自快照（pct 由 daily_bar 两日收盘计算 = +10%）
    first = d["items"][0]
    assert first["pct"] == pytest.approx(10.0)
    assert {"symbol", "name", "score", "signal_strength", "close"} <= set(first)
    assert d["stats"]["today"]["pool_size"] == 7


def test_api_board_filter_from_snapshot(snap_client: TestClient):
    """板块过滤走快照：main 榜不含创业板标的。"""
    r = snap_client.get("/api/v1/screener", params={"top_k": 10, "board": "main"})
    d = r.json()["data"]
    assert d["from_snapshot"] is True
    syms = [it["symbol"] for it in d["items"]]
    assert syms and all(not s.startswith("300") for s in syms)
    assert "600009.SH" not in syms


def test_api_refresh_skips_snapshot(snap_client: TestClient):
    """refresh=1 语义 = 强制重算：不走快照（§3.2 红线一致）。"""
    r = snap_client.get("/api/v1/screener",
                        params={"top_k": 5, "board": "all", "refresh": 1})
    d = r.json()["data"]
    assert d.get("from_snapshot") in (False, None)


def test_api_falls_back_when_predictions_ahead(snap_client: TestClient):
    """predictions 出现更新分区而快照未重建：日期错位 → 回落实时（不给旧榜）。"""
    new_day = date(2026, 9, 5)
    pred_dir = __import__("app.core.config", fromlist=["get_settings"]).get_settings() \
        .DATA_ROOT / "predictions"
    pl.DataFrame({"date": [new_day] * len(SYMS), "symbol": SYMS,
                  "pred_score": [0.99] * len(SYMS),
                  "model_version": ["lgbm_test"] * len(SYMS)}) \
        .write_parquet(pred_dir / f"date={new_day.strftime('%Y%m%d')}.parquet")
    # top_k=7 未被前序用例缓存（top_k 入键），确保走本次快照判定逻辑而非 Redis 命中
    r = snap_client.get("/api/v1/screener", params={"top_k": 7, "board": "all"})
    d = r.json()["data"]
    assert d.get("from_snapshot") in (False, None)
    assert d["date"] == "2026-09-05"
    # 清理：移除多写的分区避免影响其他用例
    (pred_dir / f"date={new_day.strftime('%Y%m%d')}.parquet").unlink()


# ---------------- P1-4：数据时效披露（freshness） ----------------
def test_freshness_reports_trading_day_lag(monkeypatch):
    """落后交易日数按真实交易日历计算（合成日历 + 固定 now，测试确定性）。"""
    from datetime import datetime as _dt

    from app.api.v1.screener import _freshness
    from app.data import calendar_store
    from app.domain.calendar import build_calendar

    days = [date(2026, 9, 4), date(2026, 9, 7), date(2026, 9, 8),
            date(2026, 9, 9), date(2026, 9, 10), date(2026, 9, 11)]
    monkeypatch.setattr(calendar_store, "get_calendar", lambda: build_calendar(days))

    # 2026-09-11 盘中（未过 15:30 收盘缓冲）→ expected = 上一交易日 09-10
    now = _dt(2026, 9, 11, 10, 0)
    f = _freshness("2026-09-07", now=now)
    assert f["expected"] == "2026-09-10"
    assert f["lag_trading_days"] == 3          # 09-08 / 09-09 / 09-10
    assert f["is_stale"] is True
    assert "3 个交易日" in f["note"]

    # 最新已收盘交易日（09-10）不陈旧
    f2 = _freshness("2026-09-10", now=now)
    assert f2["lag_trading_days"] == 0
    assert f2["is_stale"] is False

    # 收盘后（15:30 之后且当天为交易日）→ expected = 今天
    f3 = _freshness("2026-09-10", now=_dt(2026, 9, 11, 16, 0))
    assert f3["expected"] == "2026-09-11"
    assert f3["lag_trading_days"] == 1
    assert f3["is_stale"] is True


def test_freshness_unknown_when_calendar_missing(monkeypatch):
    """日历不可用 / 日期非法 → 返回 None 口径（既不误报陈旧也不误报最新）。"""
    from app.api.v1.screener import _freshness
    from app.data import calendar_store
    from app.domain.calendar import build_calendar

    monkeypatch.setattr(calendar_store, "get_calendar", lambda: build_calendar([]))
    f = _freshness("2026-09-07")
    assert f["lag_trading_days"] is None and f["is_stale"] is None
    assert "日历" in f["note"]

    f2 = _freshness(None)
    assert f2["lag_trading_days"] is None and "无数据日期" in f2["note"]

    f3 = _freshness("not-a-date")
    assert f3["lag_trading_days"] is None and f3["is_stale"] is None


def test_api_includes_freshness_block(snap_client: TestClient):
    """选股响应必须带 freshness 披露块（前端据此显示落后交易日数）。"""
    r = snap_client.get("/api/v1/screener", params={"top_k": 6, "board": "all"})
    assert r.status_code == 200, r.text
    d = r.json()["data"]
    assert "freshness" in d, d.keys()
    assert {"as_of", "expected", "lag_trading_days", "is_stale", "note"} <= set(d["freshness"])
    assert d["freshness"]["as_of"] == d["date"]


# ---------------- P0：存量快照旧枚举 low/mid/high 翻译为 signal_strength ----------------
def test_translate_signal_strength_legacy_and_idempotent():
    """翻译函数单测：旧枚举按反转表映射，新值/未知值原样通过，None→None。"""
    from app.data.screening import _translate_signal_strength

    # [AQP 改名历史债务] 旧语义 score>=0.3→low(=高分=强信号)，与新语义相反 → 反转表
    assert _translate_signal_strength("high") == "weak"    # 旧 low 档（高分）→ strong 反义
    assert _translate_signal_strength("low") == "strong"   # 旧 high 档（低分）→ weak 反义
    assert _translate_signal_strength("mid") == "neutral"
    # 新值原样通过（幂等、向前兼容）：流水线重写后存的就是这三个
    assert _translate_signal_strength("strong") == "strong"
    assert _translate_signal_strength("neutral") == "neutral"
    assert _translate_signal_strength("weak") == "weak"
    assert _translate_signal_strength(None) is None
    assert _translate_signal_strength("bogus") == "bogus"  # 未知原样，避免静默丢值


def test_legacy_risk_enum_translated_on_read(tmp_path, monkeypatch):
    """存量快照 risk 列旧枚举必须经读路径翻译为 signal_strength；
    high→weak / low→strong / mid→neutral，新值 strong 原样通过；
    紧随其后的 compute_stats 据此统计到正确的 strong_signal。自包含：私有库避免污染。"""
    import sqlite3 as _sql
    from datetime import date as _date

    from app.core.config import get_settings as _gs
    from app.data.screening import STRATEGY, load_screener_snapshot

    settings = _gs()
    root = tmp_path / "legacy_root"
    root.mkdir(parents=True, exist_ok=True)
    db = tmp_path / "legacy.db"
    monkeypatch.setattr(settings, "DATA_ROOT", root)
    # SQLITE_PATH 是只读 property，由 SQLITE_URL 派生 → 通过改 URL 绑定私有库
    monkeypatch.setattr(settings, "SQLITE_URL", f"sqlite+aiosqlite:///{db}")

    day = _date(2026, 9, 4)
    # predictions 分区仅需文件名供 _latest_pred_date 对齐（内容不重要）
    pdir = root / "predictions"
    pdir.mkdir(parents=True, exist_ok=True)
    (pdir / f"date={day.strftime('%Y%m%d')}.parquet").write_bytes(b"")

    con = _sql.connect(db)
    con.execute("PRAGMA busy_timeout=30000")
    con.execute(
        "CREATE TABLE screener_snapshot_stats ("
        "date TEXT, strategy TEXT, board TEXT, pool_size INTEGER, total INTEGER, "
        "trade_date TEXT, stats_json TEXT, PRIMARY KEY (date, strategy, board))")
    con.execute(
        "CREATE TABLE screener_snapshot ("
        "date TEXT, strategy TEXT, board TEXT, rank INTEGER, symbol TEXT, name TEXT, "
        "industry TEXT, pred_score REAL, model_version TEXT, close REAL, pct REAL, "
        "turnover REAL, amount REAL, limit_pct REAL, risk TEXT, "
        "PRIMARY KEY (date, strategy, board, rank))")
    board = "all"
    rows = [
        (str(day), STRATEGY, board, 1, "600001.SH", "A", "行业A", 0.9, "m", 10.0, 1.0, 0.01, 1e5, 10.0, "high"),
        (str(day), STRATEGY, board, 2, "600002.SH", "B", "行业A", 0.5, "m", 10.0, 1.0, 0.01, 1e5, 10.0, "low"),
        (str(day), STRATEGY, board, 3, "600003.SH", "C", "行业B", 0.3, "m", 10.0, 1.0, 0.01, 1e5, 10.0, "mid"),
        (str(day), STRATEGY, board, 4, "600004.SH", "D", "行业B", 0.7, "m", 10.0, 1.0, 0.01, 1e5, 10.0, "strong"),
    ]
    con.executemany(
        "INSERT OR REPLACE INTO screener_snapshot "
        "(date, strategy, board, rank, symbol, name, industry, pred_score, "
        " model_version, close, pct, turnover, amount, limit_pct, risk) "
        "VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)", rows)
    con.execute(
        "INSERT OR REPLACE INTO screener_snapshot_stats "
        "(date, strategy, board, pool_size, total, trade_date, stats_json) "
        "VALUES (?,?,?,?,?,?,?)",
        (str(day), STRATEGY, board, len(rows), len(rows), str(day), "{}"))
    con.commit()
    con.close()

    snap = load_screener_snapshot(None, STRATEGY, board, 20)
    assert snap is not None, "存量快照未读回"
    items = {it["symbol"]: it["signal_strength"] for it in snap["items"]}
    assert items["600001.SH"] == "weak",   "high → weak"
    assert items["600002.SH"] == "strong", "low  → strong"
    assert items["600003.SH"] == "neutral","mid  → neutral"
    assert items["600004.SH"] == "strong", "新值 strong 原样通过（幂等）"
    # compute_stats 在翻译后统计 => 强信号 = low(strong) + strong = 2
    assert snap["stats"]["today"]["strong_signal"] == 2

