"""Sprint 4 测试：因子检测报告 / 因子库入库 / 晨报榜单与分数迁移 / Lab 分年稳定性。

全部离线合成数据（合成行情 + 合成预测），不触网。
"""
from __future__ import annotations

import asyncio
import sys
from pathlib import Path

import numpy as np
import pandas as pd
import polars as pl
import pytest
from fastapi.testclient import TestClient

BACKEND_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(BACKEND_ROOT))

from app.core.config import get_settings  # noqa: E402
from app.db.session import get_session_factory, reset_engine  # noqa: E402
from app.main import app  # noqa: E402
from app.ml.gp_miner import factor_report  # noqa: E402

SYMS = [f"6000{i:02d}.SH" for i in range(1, 9)]  # 8 只
N_DAYS = 90


def _synthetic_pdf(seed: int = 11) -> pd.DataFrame:
    """合成 features 面板：symbol/date/close(+动量结构)，供表达式评估。"""
    rng = np.random.default_rng(seed)
    dates = pd.bdate_range("2026-01-05", periods=N_DAYS)
    frames = []
    for i, sym in enumerate(SYMS):
        close = 100 * np.exp(np.cumsum(
            0.0004 * i + 0.02 * rng.standard_normal(N_DAYS)))
        frames.append(pd.DataFrame({
            "symbol": sym, "date": dates, "close": close,
            "mom_20": pd.Series(close).pct_change(20).to_numpy(),
        }))
    return pd.concat(frames, ignore_index=True)


@pytest.fixture(scope="module")
def lab_client():
    """轻量 client + 合成 features/predictions/daily_bar + researcher 账号。"""
    from sqlalchemy import select as _select

    from app.core.auth import hash_password as _hp
    from app.db.init_db import init_database as _init
    from app.db.models import Instrument
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
                    _select(_User).where(_User.username == "labadmin"))).first():
                sess.add(_User(username="labadmin", password_hash=_hp("labpass1"),
                               role_id=roles["researcher"]))
            await sess.commit()
            for sym in SYMS:
                stmt = Instrument.__table__.insert().prefix_with("OR REPLACE")
                await sess.execute(stmt, {"symbol": sym, "code": sym.split(".")[0],
                                          "name": sym, "market": "SH"})
            await sess.commit()

    asyncio.run(_seed())

    s = get_settings()
    # features 快照（/studio/factors 与 /factor-report 的数据源）
    pdf = _synthetic_pdf()
    feat_dir = s.DATA_ROOT / "features" / "version=test"
    feat_dir.mkdir(parents=True, exist_ok=True)
    pdf.to_parquet(feat_dir / "year=2026.parquet", index=False)

    # predictions（近两期 + 分年）与 daily_bar（分年）
    pred_dir = s.DATA_ROOT / "predictions"
    pred_dir.mkdir(parents=True, exist_ok=True)
    rng = np.random.default_rng(7)
    for y in (2025, 2026):
        dates = pd.bdate_range(f"{y}-01-05", periods=N_DAYS)
        for sym in SYMS:
            # 列集与生产 daily_bar 一致（数据层跨文件读投影要求同构 schema）
            close = 100 * np.exp(np.cumsum(0.02 * rng.standard_normal(N_DAYS)))
            df = pl.DataFrame({
                "symbol": sym, "date": dates, "open": close, "high": close,
                "low": close, "close": close, "volume": 1000.0, "amount": 1e5,
            })
            from app.data.parquet_store import write_year_batch

            write_year_batch("daily_bar", sym, y, df)
        # 逐日预测分（randn；与次日收益无真实关系——Lab 只验证聚合口径不验证信号）
        # date dtype 与生产链路对齐（pipeline infer 产出 pandas datetime64）
        pred = pl.DataFrame({
            "date": [d for d in dates for _ in SYMS],
            "symbol": [sym for _ in dates for sym in SYMS],
            "pred_score": rng.standard_normal(N_DAYS * len(SYMS)),
            "model_version": ["lgbm_test"] * (N_DAYS * len(SYMS)),
        })
        pred.write_parquet(pred_dir / f"date={y}1231.parquet")

    with TestClient(app) as c:
        r = c.post("/api/v1/auth/login",
                   json={"username": "labadmin", "password": "labpass1"})
        c.headers.update({"Authorization": f"Bearer {r.json()['data']['access_token']}"})
        yield c

    # 清理因子库（DB 文件由 conftest 统一清理）
    async def _cleanup():
        from sqlalchemy import delete

        from app.db.models import CustomFactor
        factory2 = get_session_factory()
        async with factory2() as sess:
            await sess.execute(delete(CustomFactor))
            await sess.commit()

    asyncio.run(_cleanup())

    # 清理本 fixture 写入的 parquet。此前只清 DB 的 CustomFactor，parquet 一个都没删，
    # 残留的 features/version=test 与 predictions/date=* 会污染同会话的后续模块
    # （test_api 不再 rmtree 共享 DATA_ROOT 后，这些残留会让 test_monitor 读到
    # 异构 schema 的 predictions 而抛 InvalidOperationError）。
    import shutil

    if feat_dir.exists():
        shutil.rmtree(feat_dir, ignore_errors=True)
    for _f in (pred_dir / "date=20251231.parquet", pred_dir / "date=20261231.parquet"):
        _f.unlink(missing_ok=True)
    for _sym in SYMS:
        for _y in (2025, 2026):
            (s.DATA_ROOT / "daily_bar" / f"symbol={_sym}" / f"year={_y}.parquet"
             ).unlink(missing_ok=True)

    reset_engine()


# ---------------- 因子检测报告（内核） ----------------
def test_factor_report_structure():
    """报告内核：IC 时序 + 5 分组净值/年化 + 衰减四窗口 + 换手率。"""
    pdf = _synthetic_pdf()
    fields = set(pdf.columns) - {"symbol", "date"}
    rep = factor_report("mom_20", pdf, fields, horizon=5, min_days=30)
    assert rep is not None
    assert rep["n_days"] >= 30
    assert len(rep["quintile_navs"]) == 5
    assert set(rep["quintile_annual"]) == {f"q{i}" for i in range(1, 6)}
    assert set(rep["decay"]) == {"1", "5", "10", "20"}
    assert rep["top_turnover"] is not None and 0 <= rep["top_turnover"] <= 1


# ---------------- 因子库 CRUD ----------------
def test_factor_save_list_delete(lab_client: TestClient):
    r = lab_client.post("/api/v1/studio/factors", json={
        "name": "动量二十", "expression": "mom_20", "horizon": 5})
    assert r.json()["code"] == 0, r.json()["message"]
    saved = r.json()["data"]
    assert saved["metrics"]["n_days"] >= 30

    # 重名拒绝
    r2 = lab_client.post("/api/v1/studio/factors", json={
        "name": "动量二十", "expression": "close", "horizon": 5})
    assert r2.json()["code"] != 0

    # 列表可见
    lst = lab_client.get("/api/v1/studio/factors").json()["data"]
    assert any(f["name"] == "动量二十" for f in lst)

    # 非法表达式拒绝（未知字段）
    r3 = lab_client.post("/api/v1/studio/factors", json={
        "name": "坏因子", "expression": "not_a_field + 1", "horizon": 5})
    assert r3.json()["code"] != 0

    r4 = lab_client.delete(f"/api/v1/studio/factors/{saved['id']}")
    assert r4.json()["code"] == 0


# ---------------- 晨报：榜单变动 + 分数迁移 ----------------
def test_report_score_shift_section(lab_client: TestClient):
    from app.api.v1.report import _score_shift_section

    s = get_settings()
    # 快照两期：600001 新进、600002 跌出（top3 内）
    import sqlite3

    with sqlite3.connect(s.SQLITE_PATH) as conn:
        conn.execute("INSERT OR REPLACE INTO screener_snapshot VALUES "
                     "(?,'alpha_basic_v1','all',1,'600001.SH',NULL,NULL,0.9,NULL,"
                     "10.0,1.0,NULL,NULL,10.0,'low')", ("2026-09-04",))
        conn.execute("INSERT OR REPLACE INTO screener_snapshot VALUES "
                     "(?,'alpha_basic_v1','all',2,'600002.SH',NULL,NULL,0.8,NULL,"
                     "10.0,0.5,NULL,NULL,10.0,'low')", ("2026-09-04",))
        conn.execute("INSERT OR REPLACE INTO screener_snapshot VALUES "
                     "(?,'alpha_basic_v1','all',1,'600002.SH',NULL,NULL,0.85,NULL,"
                     "10.0,0.5,NULL,NULL,10.0,'low')", ("2026-09-03",))
        conn.commit()
    sec = _score_shift_section(top_n=2)
    assert sec is not None
    text = "\n".join(sec["lines"])
    assert "600001.SH" in text and "新进" in text
    # 分数迁移：两期 predictions top-K 重合率行
    assert "重合率" in text


def test_report_build_includes_section(lab_client: TestClient):
    """整报生成：新 section 挂载且不因 monitor/paper 缺失而失败。"""
    from app.api.v1.report import build_daily_report

    rep = build_daily_report()
    titles = [x["title"] for x in rep["sections"]]
    assert any("榜单与模型" in t for t in titles)
    assert rep["date"]


# ---------------- Lab：分年稳定性 ----------------
def test_lab_yearly(lab_client: TestClient):
    r = lab_client.get("/api/v1/research/lab/yearly")
    assert r.json()["code"] == 0, r.json()["message"]
    rows = r.json()["data"]
    years = {row["year"] for row in rows}
    assert {2025, 2026} <= years, f"分年聚合缺失: {rows}"
    for row in rows:
        assert -1 <= row["rank_ic"] <= 1
        assert 0 <= row["hit_rate"] <= 1
        assert row["n_days"] >= 10
