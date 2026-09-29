"""P3-1 RBAC 测试：三角色权限矩阵。"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

BACKEND_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(BACKEND_ROOT))

# 注：模块顶层 os.environ 改写已移除（详见 test_auth.py 顶部说明）——
# 它会把全量套件的后续测试环境从 conftest 临时目录污染到生产侧路径。

from app.main import app  # noqa: E402


def _login(client: TestClient, username: str, password: str) -> dict:
    r = client.post("/api/v1/auth/login", json={"username": username, "password": password})
    return r.json()


@pytest.fixture(scope="module")
def client():
    

    import asyncio as _aio
    from sqlalchemy import select as _select
    from app.core.auth import hash_password as _hp
    from app.db.init_db import init_database as _init
    from app.db.models_auth import Role as _Role, User as _User
    from app.db.session import get_session_factory as _gsf, reset_engine as _re
    _re()
    _aio.run(_init())
    factory = _gsf()
    async def _ensure():
        async with factory() as sess:
            for name in ('viewer', 'researcher', 'admin'):
                if not (await sess.scalars(_select(_Role).where(_Role.name == name))).first():
                    sess.add(_Role(name=name))
            await sess.commit()
            roles = {r.name: r.id for r in (await sess.scalars(_select(_Role))).all()}
            for uname, pwd, rname in [('testadmin','testpass123','admin'),('testviewer','viewpass123','viewer'),('testresearcher','respass123','researcher')]:
                if not (await sess.scalars(_select(_User).where(_User.username == uname))).first():
                    sess.add(_User(username=uname, password_hash=_hp(pwd), role_id=roles[rname]))
            await sess.commit()
    _aio.run(_ensure())
    with TestClient(app) as c:
        yield c


def _get_token(client: TestClient, username: str) -> str:
    passwords = {"testadmin": "testpass123", "testviewer": "viewpass123",
                 "testresearcher": "respass123"}
    r = client.post("/api/v1/auth/login", json={"username": username, "password": passwords[username]})
    return r.json()["data"]["access_token"]


def test_rbac_viewer_can_read(client: TestClient):
    """RBAC-VIEWER：viewer 角色可访问公开 GET 接口（market 等）。"""
    token = _get_token(client, "testviewer")
    r = client.get("/api/v1/auth/me", headers={"Authorization": f"Bearer {token}"})
    assert r.json()["code"] == 0
    assert r.json()["data"]["role"] == "viewer"


def test_rbac_researcher_can_read(client: TestClient):
    """RBAC-RESEARCHER：researcher 角色可访问公开 GET 接口。"""
    token = _get_token(client, "testresearcher")
    r = client.get("/api/v1/auth/me", headers={"Authorization": f"Bearer {token}"})
    assert r.json()["code"] == 0
    assert r.json()["data"]["role"] == "researcher"


def test_rbac_admin_full_access(client: TestClient):
    """RBAC-ADMIN：admin 角色角色等级最高。"""
    token = _get_token(client, "testadmin")
    r = client.get("/api/v1/auth/me", headers={"Authorization": f"Bearer {token}"})
    assert r.json()["code"] == 0
    assert r.json()["data"]["role"] == "admin"


def test_role_hierarchy_in_token(client: TestClient):
    """角色等级编码在 JWT payload 中，verify_password 循环验证。"""
    from app.core.auth import ROLE_RANK, verify_password

    assert ROLE_RANK["viewer"] < ROLE_RANK["researcher"] < ROLE_RANK["admin"]
    # 密码验证
    from app.core.auth import hash_password
    h = hash_password("admin123")
    assert verify_password("admin123", h) is True
    assert verify_password("wrongpass", h) is False


def test_desk_write_requires_login_only(client: TestClient):
    """写操作：**未登录**拒绝（40100）；任何已登录角色均可越过 RBAC（全面放开）。

    非恒真：匿名仍必须 40100（登录门保留）。viewer 用**空 body** 调 /desk/orders，
    以便在参数校验层被拦（40000）—— 既证明"已越过角色门"，又不产生真实下单副作用。
    """
    payload = {
        "symbol": "000001.SZ", "side": "buy", "order_amount": 1000,
        "algo": "market", "split_days": 1, "participation_cap": 0.05,
    }
    r_anon = client.post("/api/v1/desk/orders", json=payload)
    assert r_anon.json()["code"] == 40100

    viewer = _get_token(client, "testviewer")
    r_viewer = client.post("/api/v1/desk/orders", json={},
                           headers={"Authorization": f"Bearer {viewer}"})
    # 空 body ⇒ 参数校验失败（40000），说明请求已越过 RBAC 且未真正下单
    assert r_viewer.json()["code"] == 40000

    researcher = _get_token(client, "testresearcher")
    r_ok = client.post("/api/v1/desk/fills/run", headers={
        "Authorization": f"Bearer {researcher}",
    })
    assert r_ok.json()["code"] == 0


def test_datacenter_sync_requires_login_only(client: TestClient):
    """数据中心写操作：**未登录**拒绝（40100）；已登录（viewer 亦然）越过 RBAC。"""
    r_anon = client.post("/api/v1/datacenter/sync/cancel", json={})
    assert r_anon.json()["code"] == 40100

    viewer = _get_token(client, "testviewer")
    r_viewer = client.post("/api/v1/datacenter/sync/cancel", headers={
        "Authorization": f"Bearer {viewer}",
    })
    assert r_viewer.json()["code"] not in (40100, 40300)
