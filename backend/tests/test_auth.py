"""P3-1 Auth/RBAC 测试：AUTH-LOGIN / BAD-PASSWORD / NO-TOKEN / RBAC 三角色。"""
from __future__ import annotations

import asyncio
import sys
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

BACKEND_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(BACKEND_ROOT))

# 注：本文件早于 conftest.py 的会话级隔离机制（第九阶段 HIGH-002）。
# 曾在模块顶层改写 SQLITE_URL/DATA_ROOT 指向 data/sqlite/aqp_test.db 与
# data/test_parquet——模块导入顺序靠前的它会把全量套件中任何在
# get_settings cache_clear 之后读取配置的测试污染到生产侧路径，
# 是 test_delist_liquidation 全量跑失败（隔离跑通过）的根因。
# 现统一使用 conftest 的临时目录隔离，不再在测试模块内改写环境变量。

from app.core.auth import hash_password, verify_password  # noqa: E402
from app.main import app  # noqa: E402


@pytest.fixture(scope="module")
def client():
    with TestClient(app) as c:
        yield c


def test_password_hash_and_verify():
    """AUTH-HASH：hash → verify 循环。"""
    h = hash_password("s3cret")
    assert verify_password("s3cret", h) is True
    assert verify_password("wrong", h) is False
    assert "s3cret" not in h  # 明文不出现


def test_auth_login_success(client: TestClient):
    """AUTH-LOGIN：有效凭据 -> access_token + role。"""
    # 确保 admin 存在（init_db 由 lifespan 自动建表，admin 需手动创建一次）
    asyncio.run(_ensure_admin())
    r = client.post("/api/v1/auth/login", json={"username": "testadmin", "password": "testpass123"})
    body = r.json()
    assert body["code"] == 0, body["message"]
    assert "access_token" in body["data"]
    assert body["data"]["user"]["role"] == "admin"


def test_auth_bad_password(client: TestClient):
    """AUTH-BAD-PASSWORD：错误密码 -> 业务码 40104（ERR_CREDENTIALS）。

    错误码体系精细化后登录失败不再走通用 UNAUTHORIZED/50000，
    而是专用凭据错误码；且用户不存在与密码错误同提示（防枚举）。
    """
    r = client.post("/api/v1/auth/login", json={"username": "testadmin", "password": "wrongpass"})
    body = r.json()
    assert body.get("code") == 40104
    assert body.get("message") == "用户名或密码错误"


def test_auth_no_token(client: TestClient):
    """AUTH-NO-TOKEN：无 Authorization -> 40101（需保护的接口）。"""
    # /api/v1/auth/me 需要认证
    r = client.get("/api/v1/auth/me")
    body = r.json()
    assert r.status_code == 200  # HTTP 恒 200
    assert body.get("code") == 50000 or "UNAUTHORIZED" in str(body.get("detail", body))


def test_auth_me_with_token(client: TestClient):
    """AUTH-ME：有效 JWT -> 返回用户信息。"""
    r = client.post("/api/v1/auth/login", json={"username": "testadmin", "password": "testpass123"})
    token = r.json()["data"]["access_token"]
    r2 = client.get("/api/v1/auth/me", headers={"Authorization": f"Bearer {token}"})
    assert r2.json()["code"] == 0
    assert r2.json()["data"]["role"] == "admin"


def test_auth_register_status(client: TestClient):
    """AUTH-REG-STATUS：默认开放自助注册，且默认角色为 viewer。"""
    r = client.get("/api/v1/auth/register/status")
    body = r.json()
    assert body["code"] == 0, body
    assert body["data"]["enabled"] is True
    assert body["data"]["default_role"] == "viewer"
    assert body["data"]["min_password_length"] == 6


def test_auth_register_success_and_autologin(client: TestClient):
    """AUTH-REG-OK：注册成功直接签发 JWT，角色 viewer，且该 token 可用于 /me。"""
    asyncio.run(_ensure_admin())
    r = client.post("/api/v1/auth/register",
                    json={"username": "reg_user_1", "password": "regpass123"})
    body = r.json()
    assert body["code"] == 0, body
    assert body["data"]["user"] == {"username": "reg_user_1", "role": "viewer"}
    assert body["data"]["access_token"]

    me = client.get("/api/v1/auth/me",
                    headers={"Authorization": f"Bearer {body['data']['access_token']}"})
    assert me.json()["code"] == 0, me.json()
    assert me.json()["data"]["username"] == "reg_user_1"

    # 注册后可用同一凭据登录（密码哈希写入正确）
    lg = client.post("/api/v1/auth/login",
                     json={"username": "reg_user_1", "password": "regpass123"})
    assert lg.json()["code"] == 0, lg.json()
    assert lg.json()["data"]["user"]["role"] == "viewer"


def test_auth_register_duplicate_username(client: TestClient):
    """AUTH-REG-DUP：重名 -> 40105（ERR_USER_EXISTS）。"""
    asyncio.run(_ensure_admin())
    payload = {"username": "reg_dup", "password": "regpass123"}
    assert client.post("/api/v1/auth/register", json=payload).json()["code"] == 0
    body = client.post("/api/v1/auth/register", json=payload).json()
    assert body["code"] == 40105, body
    assert body["message"] == "该用户名已被注册"


def test_auth_register_rejects_bad_input(client: TestClient):
    """AUTH-REG-PARAM：非法用户名 / 弱密码 -> 40000，且不建号。"""
    asyncio.run(_ensure_admin())
    for payload in (
        {"username": "ab", "password": "regpass123"},          # 用户名过短
        {"username": "bad name!", "password": "regpass123"},   # 含非法字符
        {"username": "reg_user_2", "password": "123"},         # 密码过短
        {"username": "reg_user_3", "password": "reg_user_3"},  # 密码同用户名
    ):
        body = client.post("/api/v1/auth/register", json=payload).json()
        assert body["code"] == 40000, (payload, body)
    # 被拒绝的用户名不应可登录
    lg = client.post("/api/v1/auth/login",
                     json={"username": "reg_user_2", "password": "123"})
    assert lg.json()["code"] == 40104, lg.json()


def test_auth_register_disabled(client: TestClient, monkeypatch: pytest.MonkeyPatch):
    """AUTH-REG-OFF：ALLOW_REGISTRATION=false -> 40106，且不建号。"""
    from app.core.config import get_settings

    monkeypatch.setattr(get_settings(), "ALLOW_REGISTRATION", False)
    body = client.post("/api/v1/auth/register",
                       json={"username": "reg_off", "password": "regpass123"}).json()
    assert body["code"] == 40106, body
    # 开关同步反映到 status 端点（前端据此隐藏入口）
    assert client.get("/api/v1/auth/register/status").json()["data"]["enabled"] is False


def test_auth_register_never_grants_admin(client: TestClient, monkeypatch: pytest.MonkeyPatch):
    """AUTH-REG-ROLE：即便默认角色被配成 admin，注册也只能拿到 viewer。"""
    from app.core.config import get_settings

    monkeypatch.setattr(get_settings(), "REGISTER_DEFAULT_ROLE", "admin")
    body = client.post("/api/v1/auth/register",
                       json={"username": "reg_admin", "password": "regpass123"}).json()
    assert body["code"] == 0, body
    assert body["data"]["user"]["role"] == "viewer"


async def _ensure_admin() -> None:
    from sqlalchemy import select

    from app.core.auth import hash_password
    from app.db.init_db import init_database
    from app.db.models_auth import Role, User
    from app.db.session import get_session_factory, reset_engine

    reset_engine()
    await init_database()
    factory = get_session_factory()
    async with factory() as sess:
        for name in ("viewer", "researcher", "admin"):
            if not (await sess.scalars(select(Role).where(Role.name == name))).first():
                sess.add(Role(name=name))
        await sess.commit()
        admin_role = (await sess.scalars(select(Role).where(Role.name == "admin"))).first()
        for uname, pwd, rid in [
            ("testadmin", "testpass123", admin_role.id),
            ("testviewer", "viewpass123", (await sess.scalars(select(Role).where(Role.name == "viewer"))).first().id),
            ("testresearcher", "respass123", (await sess.scalars(select(Role).where(Role.name == "researcher"))).first().id),
        ]:
            if not (await sess.scalars(select(User).where(User.username == uname))).first():
                sess.add(User(username=uname, password_hash=hash_password(pwd), role_id=rid))
        await sess.commit()
