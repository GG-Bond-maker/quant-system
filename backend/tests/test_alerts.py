"""§4.1 预警中心 MVP 测试（Sprint2）。

覆盖：规则 CRUD + 参数白名单校验、price_pct 触发与冷却去重、score_topk
进入/跌出（合成 predictions）、volume_spike（合成 daily_bar + 快照量）、
data_health 磁盘水位、事件批量已读。全部离线（行情快照 monkeypatch）。
"""
from __future__ import annotations

import asyncio
import sys
from pathlib import Path

import polars as pl
import pytest
from fastapi.testclient import TestClient

BACKEND_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(BACKEND_ROOT))

from app.api.v1 import alerts as alerts_mod  # noqa: E402
from app.core.config import get_settings  # noqa: E402
from app.db.models import Instrument  # noqa: E402
from app.db.session import get_session_factory, reset_engine  # noqa: E402
from app.main import app  # noqa: E402
from sqlalchemy.dialects.sqlite import insert as sqlite_insert  # noqa: E402

SYM = "600519.SH"


@pytest.fixture(scope="module")
def alert_client():
    """轻量 client + 角色用户种子（规则 CRUD 需 researcher 角色）。"""
    from sqlalchemy import select as _select

    from app.core.auth import hash_password as _hp
    from app.db.init_db import init_database as _init
    from app.db.models_auth import Role as _Role, User as _User

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
                    _select(_User).where(_User.username == "alertadmin"))).first():
                sess.add(_User(username="alertadmin", password_hash=_hp("alertpass1"),
                               role_id=roles["researcher"]))
            await sess.commit()
            # instrument 种子（watchlist.symbol 外键依赖）
            ins = sqlite_insert(Instrument)
            stmt = ins.on_conflict_do_update(
                index_elements=["symbol"],
                set_={c: ins.excluded[c] for c in ("code", "name", "market")})
            async with factory() as sess:
                await sess.execute(stmt, [{"symbol": SYM, "code": "600519",
                                           "name": "贵州茅台", "market": "SH"}])
                await sess.commit()

    asyncio.run(_seed())

    with TestClient(app) as c:
        r = c.post("/api/v1/auth/login",
                   json={"username": "alertadmin", "password": "alertpass1"})
        token = r.json()["data"]["access_token"]
        c.headers.update({"Authorization": f"Bearer {token}"})
        yield c

    # 清理本模块写入的规则/事件（DB 文件由 conftest 统一清理）
    async def _cleanup():
        from sqlalchemy import delete

        from app.db.models import AlertEvent, AlertRule, Watchlist
        factory2 = get_session_factory()
        async with factory2() as sess:
            await sess.execute(delete(AlertEvent))
            await sess.execute(delete(AlertRule))
            await sess.execute(delete(Watchlist))
            await sess.commit()

    asyncio.run(_cleanup())
    reset_engine()


def _fake_snapshot(monkeypatch, quotes: list[dict] | None = None):
    """替换 alerts 命名空间下的 quotes_snapshot（顶部导入，直接打桩）。"""
    from app.api.v1 import alerts as alerts_mod

    async def _fake(symbols):
        return {"as_of": "2026-09-04 15:00:00", "source": "tencent",
                "quotes": [q for q in (quotes or [])
                           if q["symbol"] in set(symbols)]}

    monkeypatch.setattr(alerts_mod, "quotes_snapshot", _fake)


# ---------------- 规则 CRUD 与校验 ----------------
def test_rule_crud_roundtrip(alert_client: TestClient):
    body = {"name": "茅台涨跌幅", "rule_type": "price_pct", "scope": "symbol",
            "symbol": SYM, "params": {"threshold": 3}, "channels": ["sse"],
            "cooldown_minutes": 30}
    r = alert_client.post("/api/v1/alerts/rules", json=body)
    assert r.json()["code"] == 0, r.json()["message"]
    rule = r.json()["data"]
    assert rule["rule_type"] == "price_pct" and rule["symbol"] == SYM
    assert rule["params"] == {"threshold": 3}

    # PUT 更新
    body["name"] = "茅台涨跌幅 v2"
    r = alert_client.put(f"/api/v1/alerts/rules/{rule['id']}", json=body)
    assert r.json()["data"]["name"] == "茅台涨跌幅 v2"

    # DELETE
    r = alert_client.delete(f"/api/v1/alerts/rules/{rule['id']}")
    assert r.json()["code"] == 0
    r = alert_client.delete(f"/api/v1/alerts/rules/{rule['id']}")
    assert r.json()["code"] == 40400


def test_rule_validation(alert_client: TestClient):
    """参数白名单与值域校验（违规 40000，不入库）。"""
    cases = [
        {"name": "x", "rule_type": "bogus", "params": {}},
        {"name": "x", "rule_type": "price_pct", "scope": "symbol",
         "params": {}},                              # symbol 缺失
        {"name": "x", "rule_type": "price_pct", "scope": "symbol",
         "symbol": SYM, "params": {"foo": 1}},       # 未知参数
        {"name": "x", "rule_type": "price_pct", "scope": "symbol",
         "symbol": SYM, "params": {"threshold": 99}},  # 值域外
        {"name": "x", "rule_type": "price_cross", "scope": "symbol",
         "symbol": SYM, "params": {"price": 100, "direction": "sideways"}},
        {"name": "x", "rule_type": "price_pct", "scope": "symbol",
         "symbol": SYM, "params": {"threshold": 3}, "channels": ["sms"]},
    ]
    for payload in cases:
        r = alert_client.post("/api/v1/alerts/rules", json=payload)
        assert r.json()["code"] == 40000, f"{payload} -> {r.json()}"


# ---------------- price_pct：触发 + 冷却 ----------------
def test_price_pct_trigger_and_cooldown(alert_client: TestClient, monkeypatch):
    r = alert_client.post("/api/v1/alerts/rules", json={
        "name": "茅台大涨", "rule_type": "price_pct", "scope": "symbol",
        "symbol": SYM, "params": {"threshold": 3}, "cooldown_minutes": 60})
    rule_id = r.json()["data"]["id"]

    quote = {"symbol": SYM, "price": 1330.0, "pct": 4.2,
             "prev_close": 1276.4, "as_of": "2026-09-04 15:00:00"}
    _fake_snapshot(monkeypatch, quotes=[quote])

    # 第 1 轮：触发
    events1 = asyncio.run(alerts_mod.evaluate_rules())
    assert len(events1) == 1
    assert events1[0]["rule_id"] == rule_id and events1[0]["pct"] == 4.2

    # 冷却期内第 2 轮：不重复触发
    events2 = asyncio.run(alerts_mod.evaluate_rules())
    assert events2 == []

    # 历史可查（站内信落库）
    r = alert_client.get("/api/v1/alerts/events", params={"limit": 10})
    rows = r.json()["data"]
    assert any(e["rule_id"] == rule_id and e["symbol"] == SYM for e in rows)

    # 批量已读
    ids = [e["id"] for e in rows if not e["is_read"]]
    r = alert_client.post("/api/v1/alerts/events/read", json={"ids": ids})
    assert r.json()["data"]["marked"] == len(ids)
    r = alert_client.get("/api/v1/alerts/events", params={"unread": 1})
    assert all(e["rule_id"] != rule_id for e in r.json()["data"])

    # 未触发不进历史：涨跌幅未达阈值
    alert_client.delete(f"/api/v1/alerts/rules/{rule_id}")


def test_price_pct_below_threshold_no_event(alert_client: TestClient, monkeypatch):
    r = alert_client.post("/api/v1/alerts/rules", json={
        "name": "阈值高不触发", "rule_type": "price_pct", "scope": "symbol",
        "symbol": SYM, "params": {"threshold": 9}, "cooldown_minutes": 0})
    rule_id = r.json()["data"]["id"]
    _fake_snapshot(monkeypatch, quotes=[
        {"symbol": SYM, "price": 100.0, "pct": 1.0, "prev_close": 99.0}])
    events = asyncio.run(alerts_mod.evaluate_rules())
    assert not any(e["rule_id"] == rule_id for e in events)
    alert_client.delete(f"/api/v1/alerts/rules/{rule_id}")


# ---------------- volume_spike：daily_bar 历史均量对比 ----------------
def test_volume_spike_trigger(alert_client: TestClient, monkeypatch):
    import numpy as np

    from app.data.parquet_store import write_year_batch

    r = alert_client.post("/api/v1/alerts/rules", json={
        "name": "茅台放量", "rule_type": "volume_spike", "scope": "symbol",
        "symbol": SYM, "params": {"window": 20, "k": 3.0}, "cooldown_minutes": 0})
    rule_id = r.json()["data"]["id"]

    # 合成 30 日日线：前 29 日均量 1 万手，末日仅作窗口尾（当前量来自快照）
    import pandas as pd

    dates = pd.bdate_range(end="2026-09-04", periods=30)
    df = pl.from_pandas(pd.DataFrame({
        "symbol": SYM, "date": dates, "close": 100.0,
        "volume": np.full(30, 10_000.0), "amount": 1e6,
    }))
    write_year_batch("daily_bar", SYM, 2026, df)

    _fake_snapshot(monkeypatch, quotes=[
        {"symbol": SYM, "price": 100.0, "volume": 50_000.0}])  # 5 倍均量
    events = asyncio.run(alerts_mod.evaluate_rules())
    hit = [e for e in events if e["rule_id"] == rule_id]
    assert len(hit) == 1
    assert hit[0]["volume_hand"] == 50000 and hit[0]["k"] == 3.0

    # 现量未超阈值：不触发
    _fake_snapshot(monkeypatch, quotes=[
        {"symbol": SYM, "price": 100.0, "volume": 12_000.0}])
    events = asyncio.run(alerts_mod.evaluate_rules())
    assert not any(e["rule_id"] == rule_id for e in events)
    alert_client.delete(f"/api/v1/alerts/rules/{rule_id}")


# ---------------- score_topk：进入/跌出 ----------------
def test_score_topk_enter_and_leave(alert_client: TestClient, monkeypatch):
    from datetime import date

    pred_root = get_settings().DATA_ROOT / "predictions"
    pred_root.mkdir(parents=True, exist_ok=True)
    # 前日 top-K 含 600519；今日不含（跌出）
    # model_version 为生产 predictions 必备列（monitor 按列读取，缺列会污染共享 DATA_ROOT）
    pl.DataFrame({"symbol": ["600519.SH"], "pred_score": [0.5],
                  "date": [date(2026, 9, 3)],
                  "model_version": ["lgbm_test"]}).write_parquet(
        pred_root / "date=20260903.parquet")
    pl.DataFrame({"symbol": ["000001.SZ"], "pred_score": [0.6],
                  "date": [date(2026, 9, 4)],
                  "model_version": ["lgbm_test"]}).write_parquet(
        pred_root / "date=20260904.parquet")

    # universe_daily 快照：`93fa52d` 后 filter_universe(require_universe=True) 强制
    # 校验股票池，缺快照会让 score_topk 规则降级跳过。本用例自种快照以走到成功
    # 路径（此前依赖其他模块的残留数据，属顺序敏感 flake）。
    uni_dir = get_settings().DATA_ROOT / "universe_daily" / "symbol=__all__"
    uni_dir.mkdir(parents=True, exist_ok=True)
    pl.DataFrame({
        "symbol": ["600519.SH", "000001.SZ"] * 2,
        "date": ["2026-09-03", "2026-09-03", "2026-09-04", "2026-09-04"],
        "is_st": [False] * 4,
        "is_halted": [False] * 4,
    }).write_parquet(uni_dir / "year=2026.snappy.parquet")

    # watchlist 作用域
    from app.db.models import Watchlist

    async def _watch():
        factory = get_session_factory()
        async with factory() as sess:
            stmt = sqlite_insert(Watchlist).values(
                user_id="default", symbol=SYM).on_conflict_do_nothing()
            await sess.execute(stmt)
            await sess.commit()

    asyncio.run(_watch())
    r = alert_client.post("/api/v1/alerts/rules", json={
        "name": "跌出 Top50", "rule_type": "score_topk", "scope": "watchlist",
        "params": {"k": 1}, "cooldown_minutes": 0})
    rule_id = r.json()["data"]["id"]
    events = asyncio.run(alerts_mod.evaluate_rules())
    hit = [e for e in events if e["rule_id"] == rule_id]
    assert len(hit) == 1
    assert hit[0]["action"] == "leave" and hit[0]["symbol"] == SYM
    alert_client.delete(f"/api/v1/alerts/rules/{rule_id}")


def test_score_topk_degraded_is_observable(alert_client: TestClient, monkeypatch,
                                           tmp_path):
    """universe 快照缺失时，score_topk 必须留下降级痕迹，不得静默失效。

    `93fa52d` 给 filter_universe 加了 require_universe=True，异常曾被 except 吞掉
    直接返回空集 → 用户配的 Top-K 预警既不触发、也不报错。修复后必须能通过
    ``GET /alerts/health`` 观测到降级状态。
    """
    from datetime import date
    from types import SimpleNamespace

    root = tmp_path / "degraded"
    (root / "predictions").mkdir(parents=True)
    pl.DataFrame({"symbol": ["600519.SH"], "pred_score": [0.5],
                  "date": [date(2026, 9, 4)],
                  "model_version": ["lgbm_test"]}).write_parquet(
        root / "predictions" / "date=20260904.parquet")
    # 只给 predictions，不给 universe_daily → 必然触发降级。
    # P0 修复（2026-09-14）后 filter_universe 的分区路径经
    # ``parquet_store.path_for_year`` 解析——它读的是 **parquet_store** 模块的
    # get_settings。故除 alerts/screening 外，还必须 patch parquet_store 的
    # get_settings，否则会读回共享 DATA_ROOT 里其他用例种下的快照而绕过降级路径。
    from app.data import parquet_store as parquet_store_mod
    from app.data import screening as screening_mod

    monkeypatch.setattr(alerts_mod, "get_settings",
                        lambda: SimpleNamespace(DATA_ROOT=root))
    monkeypatch.setattr(screening_mod, "get_settings",
                        lambda: SimpleNamespace(DATA_ROOT=root))
    monkeypatch.setattr(parquet_store_mod, "get_settings",
                        lambda: SimpleNamespace(DATA_ROOT=root))

    cur_k, _prev_k, _snap, degraded = alerts_mod._load_latest_predictions(50)
    assert degraded == "universe_snapshot_missing"
    assert cur_k == set()
    # 核心断言：降级状态必须可观测（此前为静默 return 空集）
    assert alerts_mod._TOPK_HEALTH["degraded"] is True
    assert alerts_mod._TOPK_HEALTH["reason"] == "universe_snapshot_missing"

    r = alert_client.get("/api/v1/alerts/health")
    assert r.json()["code"] == 0, r.text
    assert r.json()["data"]["degraded"] is True


# ---------------- data_health：磁盘水位 ----------------
def test_data_health_disk(alert_client: TestClient, monkeypatch):
    from app.api.v1 import alerts as alerts_mod

    r = alert_client.post("/api/v1/alerts/rules", json={
        "name": "磁盘水位", "rule_type": "data_health", "scope": "global",
        "params": {"metric": "disk", "threshold": 90}, "cooldown_minutes": 0})
    rule_id = r.json()["data"]["id"]

    monkeypatch.setattr(alerts_mod, "_disk_usage_percent",
                        lambda root: 96.5)
    events = asyncio.run(alerts_mod.evaluate_rules())
    hit = [e for e in events if e["rule_id"] == rule_id]
    assert len(hit) == 1 and hit[0]["usage_percent"] == 96.5

    # 未达阈值不触发
    monkeypatch.setattr(alerts_mod, "_disk_usage_percent", lambda root: 50.0)
    events = asyncio.run(alerts_mod.evaluate_rules())
    assert not any(e["rule_id"] == rule_id for e in events)
    alert_client.delete(f"/api/v1/alerts/rules/{rule_id}")

