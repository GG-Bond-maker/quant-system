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
2. **行为断言**：GET（无 body）触发——缺 token → 40100（**登录门保留**）；
   已登录（任意角色）→ 放行（code 不属于 {40100, 40300}）。
   ⚠️ 2026-09-23 用户裁决「只要登录即可用全部功能」⇒ 默认 Settings.RBAC_ENFORCE=False，
      角色下限不再拦截；路由上的 require_role 依赖**仍保持挂载**（便于一键回滚），
      只是运行期不再因角色不足而拒绝。回滚路径验证见
      test_route_permission_contract.py::test_rbac_switch_can_restore_grading。

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
    # ---- 2026-09-21 审计：两条"无出处"的无鉴权读端点已补 viewer ----
    "/api/v1/datacenter/train/readiness": "viewer",
    "/api/v1/market/index/kline": "viewer",
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
    # KPI 卡片历史序列（2026-09-28 补登记）。**必须登记**：本文件的
    # ``test_read_endpoint_allows_authorized`` 只遍历本字典，未登记的读端点
    # 等于"零调用"——曾因此让一个 100% 必崩的 ``await <同步函数>``
    # （TypeError: object dict can't be used in 'await' expression）
    # 从 1832 个用例中漏过。登记后该用例会真实发起请求，路径级缺陷必红。
    "/api/v1/etf/overview/series": "viewer",
    "/api/v1/monitor/health": "viewer",
    "/api/v1/notify/recent": "viewer",
    "/api/v1/notify/stream": "viewer",
    "/api/v1/portfolio/search?q=600519": "viewer",
    "/api/v1/report/daily": "viewer",
    "/api/v1/screener": "viewer",
    "/api/v1/screener/stats/series": "viewer",
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


@pytest.fixture(scope="module")
def client_no_raise(client: TestClient):
    """与 ``client`` 同一套隔离环境，但**关闭 TestClient 的异常透传**。

    为什么需要：``TestClient`` 默认 ``raise_server_exceptions=True``，端点里未捕获的
    异常会以 ``ExceptionGroup``（一坨中间件栈）抛给用例。对 **500 类缺陷**（例如
    ``await <同步函数>`` 的 ``TypeError``）而言，用例看到的是"奇怪的异常"，而不是
    一句清楚的「业务码 = 50000」。关闭后 ``app`` 的
    ``@app.exception_handler(Exception)``（app/core/errors.py:233）会把它转成正常信封
    （HTTP 200 + ``code=50000``）⇒ 断言失败信息就是可读的业务码，直指缺陷。

    依赖 ``client`` 以复用其 module 级 DB/角色初始化，不重复那 40 行样板；
    仅新增一个测试客户端，不改动既有 ``client`` 的任何行为。
    """
    with TestClient(app, raise_server_exceptions=False) as c:
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
    from conftest import iter_effective_api_routes

    out: dict[str, str | None] = {}
    for route in iter_effective_api_routes(app):
        if "GET" in (route.methods or ()):
            out[route.path] = _role_of(route)
    return out


# ---------------- 1b. 完备性不变式（2026-09-21 审计 P0-3 / P1-50 的根因修复） ----------------
# 背景：本文件此前**只校验 EXPECTED_ROLES 白名单内的端点**——一条路由只要不在
# 白名单里，就既不要求它有角色、也不要求它被声明为公开，它是"未被分类"而非"通过"。
# 这正是 /datacenter/train/readiness（被三份历史报告登记却三次未修）与
# /market/index/kline（2026-09-21 才发现）能长期无鉴权的原因。
# 下面这条断言把"未被分类"变成失败：任何无角色约束的 GET 路由，
# 必须出现在**显式公开白名单**里。
# 公开依据：frontend/src/App.tsx:86「市场概览是唯一公开业务页」+ 登录/注册开关。
DECLARED_PUBLIC: frozenset[str] = frozenset({
    "/api/v1/auth/login",
    "/api/v1/auth/register",
    "/api/v1/auth/register/status",
    "/api/v1/market/overview",
    "/api/v1/market/overview/daily",
    "/api/v1/market/overview/rt",
})


def test_no_undeclared_unauthenticated_get_route() -> None:
    """任何无鉴权的 **业务面** GET 路由都必须显式登记在 DECLARED_PUBLIC 中。

    失败时说明**新增了未挂 require_role/require_auth 的读端点**，或既有端点的
    角色依赖被误删 —— 两种情况都必须由人显式裁决（加鉴权，或加入公开白名单）。

    范围说明：只覆盖 ``/api/v1`` 业务面。基础设施端点有意排除：
      * ``/``、``/health``、``/health/live``、``/health/ready`` —— 探针必须匿名可达；
      * ``/metrics`` —— 由 config 决定 dev 免认证（见审计 DEP-19，属另一条待办）。
    "已挂鉴权" 的判据包含 ``require_role`` 的 ``checker`` 闭包与 ``require_auth``
    （后者无角色、只要求登录，如 ``/api/v1/auth/me``）。
    """
    from conftest import iter_effective_api_routes

    undeclared: list[str] = []
    all_v1_paths: set[str] = set()
    for route in iter_effective_api_routes(app):
        path = route.path
        if path.startswith("/api/v1"):
            all_v1_paths.add(path)
        if "GET" not in (route.methods or ()):
            continue
        if not path.startswith("/api/v1"):
            continue
        if path in DECLARED_PUBLIC:
            continue
        if _role_of(route) is not None:
            continue
        if any(getattr(getattr(d, "call", None), "__name__", "") == "require_auth"
               for d in route.dependant.dependencies):
            continue
        undeclared.append(path)

    assert not sorted(undeclared), (
        "以下 /api/v1 GET 路由没有任何角色/登录约束，也未登记为公开端点："
        f"{sorted(undeclared)}；请为其挂载 require_role(...)/require_auth()，"
        "或加入 DECLARED_PUBLIC 并说明公开理由")
    # 反向：白名单里的端点必须真的存在（防止路由改名后白名单变成空保护）
    missing = sorted(DECLARED_PUBLIC - all_v1_paths)
    assert not missing, f"DECLARED_PUBLIC 中的端点已不存在（路由改名？）：{missing}"


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
def test_researcher_read_allows_viewer_when_rbac_open(client: TestClient, path: str) -> None:
    """全面放开（默认）：viewer 访问 researcher 级读端点**不再**被角色拦截（新语义）。

    非恒真断言：缺 token 仍必须 40100（见 ``test_read_endpoint_requires_auth``），
    此处只断言"已登录的 viewer 越过了角色门"。回滚后仍会 40300（见
    ``test_route_permission_contract.py::test_rbac_switch_can_restore_grading``）。
    """
    r = client.get(path, headers=_auth(client, "rdviewer"))
    assert r.json()["code"] not in (ERR_UNAUTHORIZED, ERR_FORBIDDEN), (path, r.json())


def test_settings_read_allows_viewer(client: TestClient) -> None:
    """viewer 级读端点：viewer 放行，返回结构正常。"""
    body = client.get("/api/v1/settings", headers=_auth(client, "rdviewer")).json()
    assert body["code"] == 0, body
    assert "settings" in body["data"]


@pytest.mark.parametrize("path,minimum", sorted(EXPECTED_ROLES.items()))
def test_read_endpoint_allows_authorized(
    client: TestClient, path: str, minimum: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    """达标角色放行：即使本地无数据（业务降级），也**不再**是鉴权错误。"""
    if path == "/api/v1/notify/stream":
        pytest.skip("SSE 流式端点：请求会挂起，鉴权由运行时一致性用例覆盖")
    if path == "/api/v1/market/quotes?symbols=600519.SH":
        pytest.skip("放行后会真实调用外部行情源，只验证鉴权不触网")
    if path == "/api/v1/etf/hot":
        # 既存 flake（2026-09-23）：该端点**无预算**直接 `to_thread(_filter_catalog)`
        # → `E.build_catalog()` 真打外部 ETF 目录（东财/新浪/腾讯），在 `not network`
        # 套件里是随外源可达性漂移的假失败（首次红、重跑绿）。此处**只把外源换成假
        # 目录**（`build_catalog`），保留「达标角色放行」正向断言且零外网——鉴权的
        # 匿名拒绝契约由 test_read_endpoint_requires_auth（缺 token ⇒ 40100，依赖先于
        # 处理器执行、不触外源）独立覆盖。**不移出 not network 套件**：移出会减少
        # passed 计数（本用例本就应通过），且失去放行正向断言。
        from app.data import etf as _etf_data

        monkeypatch.setattr(
            _etf_data, "build_catalog",
            lambda: [{"code": "510300", "name": "沪深300ETF", "country": "cn",
                      "board": "宽基ETF", "type": "股票型", "tracking_index": None,
                      "manager": None, "inception": None, "price": 1.0, "pct": 0.5,
                      "amount": 1.0e8, "size_yi": 10.0, "quote_status": "ok",
                      "overseas": None}],
        )
    r = client.get(path, headers=_auth(client, "rdresearcher"))
    ctype = r.headers.get("content-type", "")
    if "json" not in ctype:
        # 二进制导出（xlsx/ZIP）：放行后**没有 JSON 信封可解析**。此处只断言
        # 鉴权未被拒（HTTP 200 + 非空二进制），细节由
        # `test_read_endpoint_binary_export_is_not_json_parsed` 固化。
        assert r.status_code == 200, (path, r.status_code, r.text[:200])
        assert r.content, path
        return
    code = r.json()["code"]
    assert code not in (ERR_UNAUTHORIZED, ERR_FORBIDDEN), (path, r.json())


def test_read_endpoint_binary_export_is_not_json_parsed(
    client: TestClient,
) -> None:
    """二进制导出端点（xlsx）成功时**不是** JSON，不得无条件 `r.json()`。

    [AQP 2026-09-21 测试加固] `test_read_endpoint_allows_authorized` 对
    ``/api/v1/export/screener`` 直接 `r.json()`：当共享 DATA_ROOT 里恰好有
    选股数据时，该端点返回 **xlsx（ZIP）二进制** ⇒ `json.loads` 抛
    ``UnicodeDecodeError: ... byte 0xc7``，用例失败；而 DATA_ROOT 为空时它走
    ``ERR_DATA_EMPTY`` 的 JSON 降级路径 ⇒ 用例"碰巧"通过。**这是随测试顺序/
    数据状态漂移的假失败**（与产品代码无关），故单独固化正确断言：鉴权通过
    时要么是 JSON 信封（降级），要么是 xlsx 二进制（成功），两者都必须 **HTTP 200**。
    """
    r = client.get("/api/v1/export/screener", headers=_auth(client, "rdresearcher"))
    assert r.status_code == 200, r.text
    ctype = r.headers.get("content-type", "")
    if "json" in ctype:
        assert r.json()["code"] not in (ERR_UNAUTHORIZED, ERR_FORBIDDEN), r.text
    else:
        assert "spreadsheet" in ctype or "octet-stream" in ctype, ctype
        assert r.content[:2] == b"PK", "xlsx 必须是 ZIP 容器（PK 魔数）"
        assert len(r.content) > 1000, "成功导出的工作簿不应是空壳"


def test_settings_read_rejects_anonymous(client: TestClient) -> None:
    """回归锚点：`GET /api/v1/settings` 匿名可读曾为真实缺口（P2-2）。"""
    assert client.get("/api/v1/settings").json()["code"] == ERR_UNAUTHORIZED


# ---------------- 3. KPI 历史序列端点：断言「真的能跑」（必须 code == 0） ----------------
# 背景（2026-09-28）：`/api/v1/etf/overview/series` 与 `/api/v1/screener/stats/series`
# 的 `_build` 曾写成**同步** `def`，而 `app/cache/swr.py:cached_or_build` 内部是
# `await build()` ⇒ `TypeError: object dict can't be used in 'await' expression`
# ⇒ HTTP 200 但业务 `code=50000`（真机 100% 必崩，缓存永远填不上）。
#
# 为什么逃过 1832 个用例：把路径登记进 `EXPECTED_ROLES` 只解决了「零调用」，
# 而 `test_read_endpoint_allows_authorized` 的断言是
# `code not in (ERR_UNAUTHORIZED, ERR_FORBIDDEN)` —— `50000` 照样通过。
# 下面单独固化**严格断言 `code == 0`** + 契约结构，把这条缝隙焊死。
#
# 键集来源（不得照抄文档，须与实现一致）：
#   * screener → `app/data/kpi_series.py::screener_stats_series` 的 6 个 MetricSeries
#   * etf      → `app/data/kpi_series.py::etf_overview_series` 的 5 个 MetricSeries
SCREENER_SERIES_KEYS: frozenset[str] = frozenset({
    "pool_size", "win_rate", "avg_pct", "avg_score", "strong_signal", "industry_count",
})
ETF_OVERVIEW_SERIES_KEYS: frozenset[str] = frozenset({
    "etf_count", "total_size_yi", "avg_pct", "net_inflow_yi", "amount_yi",
})


def _assert_metric_contract(key: str, m: object) -> None:
    """单个 metric 的结构契约（`_build` 崩掉时这里根本到不了）。"""
    assert isinstance(m, dict), (key, type(m))
    assert isinstance(m["basis"], str) and m["basis"].strip(), (key, "basis 不得为空")
    assert m["kind"] == "platform", (key, m["kind"])
    assert isinstance(m["unit"], str) and m["unit"].strip(), (key, m["unit"])
    assert isinstance(m["enough"], bool), (key, m["enough"])
    assert isinstance(m["comparable"], bool), (key, m["comparable"])
    assert isinstance(m["count"], int) and not isinstance(m["count"], bool), (key, m["count"])
    assert isinstance(m["points"], list), (key, type(m["points"]))
    assert isinstance(m["dropped"], list), (key, type(m["dropped"]))
    # 点数与 count 必须自洽：绝不允许「count 说有 N 个点、points 却是空」这种谎报
    assert len(m["points"]) == m["count"], (key, len(m["points"]), m["count"])


@pytest.mark.parametrize(
    "path,expected_keys",
    [
        ("/api/v1/screener/stats/series?days=30&top_k=50", SCREENER_SERIES_KEYS),
        ("/api/v1/etf/overview/series?days=30", ETF_OVERVIEW_SERIES_KEYS),
    ],
)
def test_kpi_series_endpoint_actually_runs(
    client_no_raise: TestClient, path: str, expected_keys: frozenset[str]
) -> None:
    """KPI 历史序列端点必须真的能跑通：`code == 0` 且契约完整。

    隔离环境 DATA_ROOT 为空、Redis 关闭 ⇒ 两个端点都走「无有效数据」分支。
    这**正是**要覆盖的路径：必须返回 `code=0` + 各指标 `enough=False`
    （如实说「数据不够，不画」），而**不是**崩成 `code=50000`。
    若哪天在无数据时返回非 0 code，那是产品缺陷，应修产品代码而非放宽本断言。

    用 ``client_no_raise``：端点抛异常时得到的是可读的 `code=50000` 断言失败，
    而不是一坨 ExceptionGroup 中间件栈（见该 fixture 的 docstring）。
    """
    r = client_no_raise.get(path, headers=_auth(client_no_raise, "rdviewer"))
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["code"] == 0, (
        f"{path} 返回业务码 {body['code']}（期望 0）。"
        f"若为 50000（ERR_SYSTEM）：典型原因是 `cached_or_build` 的 build 传了"
        f"**同步函数**，内部 `await build()` 抛 TypeError —— 请检查对应端点"
        f"是否为 `async def _build()`。响应={body}")
    data = body["data"]

    for k in ("as_of", "window_days", "min_points", "drawable", "metrics"):
        assert k in data, (path, k, sorted(data))
    assert isinstance(data["window_days"], int) and data["window_days"] == 30, data["window_days"]
    assert isinstance(data["min_points"], int) and data["min_points"] > 0, data["min_points"]
    assert isinstance(data["drawable"], list), type(data["drawable"])
    assert data["as_of"] is None or isinstance(data["as_of"], str), data["as_of"]

    metrics = data["metrics"]
    assert set(metrics) == set(expected_keys), (path, sorted(metrics), sorted(expected_keys))
    for key, m in metrics.items():
        _assert_metric_contract(key, m)

    # 无有效数据时：drawable 必为空，且不得有任何一个指标自称 enough
    if not data["as_of"]:
        assert data["drawable"] == [], (path, data["drawable"])
        assert not any(m["enough"] for m in metrics.values()), path
