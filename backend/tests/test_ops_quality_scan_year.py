"""`/ops/quality-scan` 的 year 默认值缺陷（A-residual 3）· 缺陷本体反证。

缺陷本体：原实现 `year = req.year or 2026` —— **硬编码 2026**。后果：①2026 年之后
默认静默指向过期年份，扫不到任何文件却回 `n_issues: 0`（"假清白"同族形态）；
②请求体 Field 的 description 却写「扫描年份（默认最新一年）」——**文档与实现不符**
（照描述理解会以为取的是数据里的最新一年）。

修法：默认取**数据里真实存在的最近年份**，并披露 `year_source`
（`request` / `latest_available` / `current_year_fallback`），让调用方能分辨
"我要的那一年"与"我替你挑的那一年"。
"""

from __future__ import annotations

import sys
from datetime import date
from pathlib import Path

import polars as pl
import pytest
from fastapi.testclient import TestClient

BACKEND_ROOT = Path(__file__).resolve().parents[1]
if str(BACKEND_ROOT) not in sys.path:
    sys.path.insert(0, str(BACKEND_ROOT))

from app.main import app  # noqa: E402


def _row(sym: str, d: date) -> dict:
    return {"symbol": sym, "date": d, "open": 10.0, "high": 10.0, "low": 10.0,
            "close": 10.0, "volume": 1000.0, "pct": 0.01}


@pytest.fixture()
def data_root(tmp_path, monkeypatch):
    """只播种 2023 / 2024 两年 ⇒ 默认年份必须是 2024（不是硬编码的 2026）。"""
    from app.core.config import get_settings

    root = tmp_path / "parquet"
    for year in (2023, 2024):
        for sym in ("AAA.SZ", "BBB.SZ"):
            d = root / "daily_bar" / f"symbol={sym}"
            d.mkdir(parents=True, exist_ok=True)
            pl.DataFrame([_row(sym, date(year, 3, 1)), _row(sym, date(year, 3, 4))]
                         ).write_parquet(d / f"year={year}.parquet")
    # 仓库既有惯例：直接改 Settings 字段（不要 setenv+cache_clear，会污染后续用例）
    monkeypatch.setattr(get_settings(), "DATA_ROOT", root)
    return root


@pytest.fixture(scope="module")
def client():
    with TestClient(app) as c:
        yield c


def _headers(client: TestClient) -> dict[str, str]:
    import asyncio

    from sqlalchemy import select

    from app.core.auth import hash_password
    from app.db.init_db import init_database
    from app.db.models_auth import Role, User
    from app.db.session import get_session_factory, reset_engine

    reset_engine()
    asyncio.run(init_database())
    factory = get_session_factory()

    async def _ensure() -> None:
        async with factory() as sess:
            role = (await sess.scalars(select(Role).where(Role.name == "researcher"))).first()
            if role is None:
                role = Role(name="researcher")
                sess.add(role)
                await sess.commit()
            user = (await sess.scalars(select(User).where(User.username == "qs_probe"))).first()
            if user is None:
                sess.add(User(username="qs_probe", password_hash=hash_password("qspass12345"),
                              role_id=role.id))
                await sess.commit()

    asyncio.run(_ensure())
    body = client.post("/api/v1/auth/login",
                       json={"username": "qs_probe", "password": "qspass12345"}).json()
    assert body["code"] == 0, body
    return {"Authorization": f"Bearer {body['data']['access_token']}"}


def test_default_year_is_latest_available_not_hardcoded(client: TestClient, data_root) -> None:
    """不传 year ⇒ 取数据里真实的最近一年（2024），**不是**硬编码的 2026。"""
    h = _headers(client)
    r = client.post("/api/v1/ops/quality-scan", json={"dataset": "daily_bar"}, headers=h)
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["code"] == 0, body
    data = body["data"]
    assert data["year"] == 2024, f"默认年份不是数据里的最新一年：{data}"
    assert data["year_source"] == "latest_available", data
    # 关键：必须真的扫到了东西（旧实现在 2026 下会 symbols_scanned=0 + n_issues=0）
    assert data["symbols_scanned"] == 2, data
    assert data["rows_scanned"] == 4, data


def test_explicit_year_is_respected_and_disclosed(client: TestClient, data_root) -> None:
    """显式传 year ⇒ 原样使用并披露来源，不得被"最新一年"覆盖。"""
    h = _headers(client)
    r = client.post("/api/v1/ops/quality-scan",
                    json={"dataset": "daily_bar", "year": 2023}, headers=h)
    body = r.json()
    assert body["code"] == 0, body
    assert body["data"]["year"] == 2023, body["data"]
    assert body["data"]["year_source"] == "request", body["data"]


def test_fallback_year_when_no_partition_matches(client: TestClient, tmp_path, monkeypatch) -> None:
    """数据集目录存在但**没有任何 year 分区** ⇒ 回落到当前年并如实披露来源。

    旧实现在这种目录下同样是"扫不到任何文件 + `n_issues: 0`"，但**不告诉调用方**
    它扫的是哪一年；现在 `year_source=current_year_fallback` 使该情形可辨。
    """
    from app.core.config import get_settings

    root = tmp_path / "empty_dataset"
    (root / "daily_bar" / "symbol=AAA.SZ").mkdir(parents=True)
    monkeypatch.setattr(get_settings(), "DATA_ROOT", root)
    h = _headers(client)
    r = client.post("/api/v1/ops/quality-scan", json={"dataset": "daily_bar"}, headers=h)
    body = r.json()
    assert body["code"] == 0, body
    assert body["data"]["year_source"] == "current_year_fallback", body["data"]
    assert body["data"]["year"] == date.today().year, body["data"]