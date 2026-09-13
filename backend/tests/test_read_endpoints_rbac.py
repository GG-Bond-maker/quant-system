"""P2-2 / P2-3 读端点鉴权收紧回归。

背景
----
审计发现多个 **只读** 端点此前**匿名可读**（`GET /api/v1/settings` 及 desk /
studio / ops 下的若干 GET），与前端 `RequireRole` 守卫不一致：前端要登录 +
角色，后端却裸奔——只要直连接口即可绕过角色限制读到模拟盘账户、禁买池、
订单、因子库、数据血缘等敏感信息。

修复
----
复用 `app/core/auth.py` 的 `require_role`，按**前端同级角色**加保护：
    - `GET /api/v1/settings`             → viewer（仅登录后 AuthBootstrap 调用）
    - desk 6 个 GET / studio 1 个 GET / ops 2 个 GET → researcher

本文件固化两层防护：
1. **运行时一致性**：上述端点必须挂载**正确的最低角色**依赖（防止将来被误删）；
2. **行为断言**：GET（无 body）触发——缺 token → 40100；角色不足 → 40300；
   达标角色放行（code 不属于 {40100, 40300}）。

约束：仅用 GET（无请求体）+ 有效 JWT 触发，天然无副作用；
      全部在 conftest 的隔离环境（临时 SQLite / DATA_ROOT）中执行。
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest
from fastapi.routing import APIRoute
from fastapi.testclient import TestClient

BACKEND_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(BACKEND_ROOT))

from app.core.errors import ERR_FORBIDDEN, ERR_UNAUTHORIZED  # noqa: E402
from app.main import app  # noqa: E402

# ---------------- 端点 → 最低角色（与前端 App.tsx 守卫同级） ----------------
# /settings     → RequireAuth（viewer）
# /desk         → RequireRole（researcher）
# /studio       → RequireRole（researcher）
# /dataquality  → RequireRole（researcher，其 ops/quality-scan 本就需 researcher）
# /pipeline     → RequireRole（researcher）
EXPECTED_ROLES: dict[str, str] = {
    "/api/v1/settings": "viewer",
    "/api/v1/desk/kill-switch": "researcher",
    "/api/v1/desk/exclusion": "researcher",
    "/api/v1/desk/exclusion/screen": "researcher",
    "/api/v1/desk/orders": "researcher",
    "/api/v1/desk/account": "researcher",
    "/api/v1/desk/capacity": "researcher",
    "/api/v1/studio/factors": "researcher",
    "/api/v1/ops/lineage": "researcher",
    "/api/v1/ops/dag": "researcher",
    "/api/v1/datacenter/sync/status": "researcher",
    "/api/v1/datacenter/train/status": "researcher",
    # ---- 2026-09-12 T-03：消除读写不对称 / 信息泄露（A 组 researcher）----
    # 注：含路径参数的端点写**具体示例路径**，运行时一致性断言按模板匹配。
    "/api/v1/alerts/rules": "researcher",
    "/api/v1/alerts/events": "researcher",
    "/api/v1/alerts/health": "researcher",
    "/api/v1/datacenter/logs": "researcher",
    "/api/v1/export/screener": "researcher",
    "/api/v1/research/overview": "researcher",
    "/api/v1/research/experiments": "researcher",
    "/api/v1/research/feature-importance": "researcher",
    "/api/v1/research/lab/yearly": "researcher",
    "/api/v1/stock/600519.SH/predict": "researcher",
    # ---- 2026-09-12 T-03 B组（viewer：需登录，不需研究权限）----
    # 注：必选 Query 的端点在行为断言里带最小合法 query（缺参会 422→40000，
    # 先于鉴权触发，测不到 40100）；SSE 流式端点只做运行时一致性、不进行为请求。
    "/api/v1/datacenter/overview": "viewer",
    "/api/v1/datacenter/datasets": "viewer",
    "/api/v1/datacenter/quality": "viewer",
    "/api/v1/datacenter/task-stats": "viewer",
    "/api/v1/datacenter/instruments": "viewer",
    "/api/v1/datacenter/text/status": "viewer",
    "/api/v1/datacenter/mirror/status": "viewer",
    "/api/v1/etf/overview": "viewer",
    "/api/v1/etf/list": "viewer",
    "/api/v1/etf/hot": "viewer",
    "/api/v1/etf/performance": "viewer",
    "/api/v1/etf/scale": "viewer",
    "/api/v1/etf/flow": "viewer",
    "/api/v1/etf/detail/510300": "viewer",
    "/api/v1/monitor/health": "viewer",
    "/api/v1/notify/recent": "viewer",
    "/api/v1/notify/stream": "viewer",
    "/api/v1/portfolio/search?q=600519": "viewer",
    "/api/v1/report/daily": "viewer",
    "/api/v1/screener": "viewer",
    "/api/v1/screener/watchlist?symbols=600519.SH": "viewer",
    "/api/v1/studio/mining/status/nonexistent": "viewer",
    "/api/v1/watchlist/dashboard?symbols=600519.SH": "viewer",
    "/api/v1/watchlist/correlation?symbols=600519.SH": "viewer",
    "/api/v1/market/quotes?symbols=600519.SH": "viewer",
    "/api/v1/stock/600519.SH/kline?start=20240101&end=20240201": "viewer",
    "/api/v1/stock/600519.SH/profile": "viewer",
    "/api/v1/stock/600519.SH/panels": "viewer",
}


def _login(client: TestClient, username: str, password: str) -> dict:
    return client.post(
        "/api/v1/auth/login", json={"username": username, "password": password}).json()


@pytest.fixture(scope="module")
def client():
    """隔离环境中的三角色测试客户端（viewer / researcher / admin）。"""
    import asyncio as _aio

    from sqlalchemy import select as _select

    from app.core.auth import hash_password as _hp
    from app.db.init_db import init_database as _init
    from app.db.models_auth import Role as _Role, User as _User
    from app.db.session import get_session_factory as _gsf, reset_engine as _re

    _re()
    _aio.run(_init())
    factory = _gsf()

    async def _ensure() -> None:
        async with factory() as sess:
            for name in ("viewer", "researcher", "admin"):
                exists = (await sess.scalars(
                    _select(_Role).where(_Role.name == name))).first()
                if not exists:
                    sess.add(_Role(name=name))
            await sess.commit()
            roles = {r.name: r.id for r in (await sess.scalars(_select(_Role))).all()}
            seeds = [
                ("rdadmin", "rdpass123", "admin"),
                ("rdviewer", "rdviewpass123", "viewer"),
                ("rdresearcher", "rdrespass123", "researcher"),
            ]
            for uname, pwd, rname in seeds:
                exists = (await sess.scalars(
                    _select(_User).where(_User.username == uname))).first()
                if not exists:
                    sess.add(_User(username=uname, password_hash=_hp(pwd),
                                   role_id=roles[rname]))
            await sess.commit()

    _aio.run(_ensure())
    with TestClient(app) as c:
        yield c


_PASSWORDS = {
    "rdadmin": "rdpass123",
    "rdviewer": "rdviewpass123",
    "rdresearcher": "rdrespass123",
}


def _auth(client: TestClient, username: str) -> dict[str, str]:
    body = _login(client, username, _PASSWORDS[username])
    assert body["code"] == 0, body
    return {"Authorization": f"Bearer {body['data']['access_token']}"}


# ---------------- 1. 运行时一致性（防止角色依赖被误删） ----------------
def _role_of(route: APIRoute) -> str | None:
    """从路由依赖闭包读出 require_role 的 minimum_role（None = 无角色约束）。"""
    for dep in route.dependant.dependencies:
        fn = getattr(dep, "call", None)
        if getattr(fn, "__name__", "") != "checker":
            continue
        for cell in getattr(fn, "__closure__", None) or []:
            try:
                value = cell.cell_contents
            except ValueError:
                continue
            if isinstance(value, str):
                return value
    return None


def _live_get_roles() -> dict[str, str | None]:
    out: dict[str, str | None] = {}
    for route in app.routes:
        if isinstance(route, APIRoute) and "GET" in (route.methods or ()):
            out[route.path] = _role_of(route)
    return out


def test_read_endpoints_have_expected_role() -> None:
    """上述读端点必须挂载正确的最低角色依赖（缺省/错误即失败）。"""
    live = _live_get_roles()
    mismatched = {
        path: (_match_route(live, path), role)
        for path, role in EXPECTED_ROLES.items()
        if _match_route(live, path) != role
    }
    assert not mismatched, (
        f"读端点 RBAC 与预期不符 (path: (实际, 期望)): {mismatched}")


def _match_route(live: dict[str, str | None], path: str) -> str | None:
    """具体路径 → 路由模板角色：支持路径参数与附带 query 的矩阵 key。

    EXPECTED_ROLES 的 key 允许写具体示例路径（可带 ``?query``）；先精确匹配，
    再按 FastAPI 模板（``{param}`` → 非 ``/`` 段）匹配，query 部分不参与匹配。
    """
    import re as _re

    path_only = path.split("?", 1)[0]
    if path_only in live:
        return live[path_only]
    for template, role in live.items():
        template_only = template.split("?", 1)[0]
        pattern = _re.sub(r"\{[^}]+\}", "[^/]+", template_only)
        if _re.fullmatch(pattern, path_only):
            return role
    return None


# ---------------- 2. 行为断言：匿名 / 低角色 / 达标角色 ----------------
@pytest.mark.parametrize("path,minimum", sorted(EXPECTED_ROLES.items()))
def test_read_endpoint_requires_auth(
    client: TestClient, path: str, minimum: str
) -> None:
    """缺 token：任意读端点（GET 无 body）都必须在鉴权层被拒（40100）。"""
    if path == "/api/v1/notify/stream":
        pytest.skip("SSE 流式端点：请求会挂起，鉴权由运行时一致性用例覆盖")
    r = client.get(path)
    assert r.json()["code"] == ERR_UNAUTHORIZED, (path, r.json())


@pytest.mark.parametrize(
    "path",
    sorted(p for p, role in EXPECTED_ROLES.items() if role == "researcher"),
)
def test_researcher_read_rejects_viewer(client: TestClient, path: str) -> None:
    """角色不足：viewer 访问 researcher 级读端点 → 40300。"""
    r = client.get(path, headers=_auth(client, "rdviewer"))
    assert r.json()["code"] == ERR_FORBIDDEN, (path, r.json())


def test_settings_read_allows_viewer(client: TestClient) -> None:
    """viewer 级读端点：viewer 放行，返回结构正常。"""
    body = client.get("/api/v1/settings", headers=_auth(client, "rdviewer")).json()
    assert body["code"] == 0, body
    assert "settings" in body["data"]


@pytest.mark.parametrize("path,minimum", sorted(EXPECTED_ROLES.items()))
def test_read_endpoint_allows_authorized(
    client: TestClient, path: str, minimum: str
) -> None:
    """达标角色放行：即使本地无数据（业务降级），也**不再**是鉴权错误。"""
    if path == "/api/v1/notify/stream":
        pytest.skip("SSE 流式端点：请求会挂起，鉴权由运行时一致性用例覆盖")
    if path == "/api/v1/market/quotes?symbols=600519.SH":
        pytest.skip("放行后会真实调用外部行情源，只验证鉴权不触网")
    r = client.get(path, headers=_auth(client, "rdresearcher"))
    code = r.json()["code"]
    assert code not in (ERR_UNAUTHORIZED, ERR_FORBIDDEN), (path, r.json())


def test_settings_read_rejects_anonymous(client: TestClient) -> None:
    """回归锚点：`GET /api/v1/settings` 匿名可读曾为真实缺口（P2-2）。"""
    assert client.get("/api/v1/settings").json()["code"] == ERR_UNAUTHORIZED
