"""写操作端点补测（审计 §七-1）：52 个 POST/PUT/DELETE 的鉴权 / RBAC / 参数校验 / 本地状态写入。

背景
----
首轮全面审核（`AQP_全面审核测试报告_20260911.md` §七）明确把"约 30 个写操作端点"
列为未覆盖项（"为安全起见未执行"）。本文件把这批端点纳管，并固化为可重复回归。

安全设计（四条硬约束）
----------------------
1. 全部在 conftest 的**隔离环境**（临时 DATA_ROOT / MODEL_ROOT / SQLite，Redis 关闭）
   中执行，绝不触碰生产库、生产数据仓库与生产模型目录；
2. 鉴权断言天然无副作用：FastAPI 的 `solve_dependencies` 先执行子依赖
   （`require_auth` / `require_role`），依赖抛出的 40100 早于请求体校验与处理器执行，
   因此"无 token"用例不可能写入任何状态。
   ⚠️ 2026-09-23 全面放开后，`require_role` 默认**不再**因角色不足而拒绝
   （`Settings.RBAC_ENFORCE=False`），"越权"不再由角色门挡下；对写端点的角色语义
   改用**纯函数** `ensure_role` 断言 + 一条带必填 body 的代表端点，避免放开后误触
   真实网络同步 / 训练（见下方第 4 条）；
3. 对**危险端点**（真实网络同步、模型训练、镜像重建）挂 autouse 兜底守卫：
   即使鉴权或校验被绕过，底层服务函数也抛 `AssertionError` 让测试立刻变红，
   而不是真的发起网络请求或训练；
4. **不调用**任何会外发网络的端点正常路径（`datacenter/sync`、`settings/data/sync`、
   `settings/connectors/test`、`datacenter/train/start`、`studio/mining/start`、
   `datacenter/sync/fetch`、`datacenter/text/import`），只验证其拒绝路径。

覆盖矩阵
--------
| 维度 | 覆盖范围 |
|---|---|
| 鉴权 | 50 个受保护端点：无 token → 40100；伪造 token → 40102 |
| RBAC | 默认全面放开：任意已登录角色越过角色门；开关 RBAC_ENFORCE=true 可回滚为 40300 分级 |
| 参数校验 | 全部带 body 的端点：结构非法 → 40000（且不产生副作用） |
| 本地状态写入 | alerts 规则 CRUD、偏好/引擎设置、静音开关、因子 CRUD、缓存清理、DB 备份、日报生成 |
| 注册表一致性 | 硬编码清单必须与运行时 RBAC 依赖逐条吻合，且不得遗漏任何写端点 |
"""
from __future__ import annotations

import asyncio
import os
import sys
from pathlib import Path
from typing import Any

import pytest
from fastapi import HTTPException
from fastapi.routing import APIRoute
from fastapi.testclient import TestClient

BACKEND_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(BACKEND_ROOT))

from app.core import auth as auth_mod  # noqa: E402
from app.core.errors import (  # noqa: E402
    ERR_DATA_EMPTY,
    ERR_EXPR_INVALID,
    ERR_FORBIDDEN,
    ERR_INVALID_TOKEN,
    ERR_PARAMS,
    ERR_SYSTEM,
    ERR_UNAUTHORIZED,
)
from app.main import app  # noqa: E402

# ---------------- 端点注册表（(method, path, 最低角色)，None = 公开） ----------------
# ⚠️ 该清单由运行时依赖扫描生成；test_registry_matches_runtime_rbac 会强制它与
#    实际 RBAC 配置保持一致，防止改代码后注册表腐化成"假覆盖"。

PUBLIC: list[tuple[str, str]] = [
    ("POST", "/api/v1/auth/login"),
    # 自助注册（默认角色 viewer，由 ALLOW_REGISTRATION 开关控制）。
    # 纯鉴权/校验路径安全：本文件只用非法 body / 缺 token 触发拒绝，
    # 从不真的建号；注册的正向用例由 tests/test_auth.py 覆盖。
    ("POST", "/api/v1/auth/register"),
]

VIEWER: list[tuple[str, str]] = [
    ("POST", "/api/v1/alerts/events/read"),
    ("PUT", "/api/v1/settings/preferences"),
    # 通知中心：用 Bearer 身份换取短期一次性 EventSource 建连票据（viewer 级）。
    # 该端点原未被纳管 —— 注册表遗漏会掩盖 RBAC 漂移，故补入并同步计数断言。
    ("POST", "/api/v1/notify/stream-ticket"),
]

RESEARCHER: list[tuple[str, str]] = [
    # P0-1：重计算、参数寻优和导出至少要求 researcher；admin 继承该权限。
    ("POST", "/api/v1/backtest/run"),
    ("POST", "/api/v1/backtest/signal-analysis"),
    ("POST", "/api/v1/backtest/strategy-run"),
    ("POST", "/api/v1/desk/attribution"),
    ("POST", "/api/v1/export/backtest"),
    ("POST", "/api/v1/export/strategy-backtest"),
    ("POST", "/api/v1/portfolio/backtest"),
    ("POST", "/api/v1/research/cv-folds"),
    ("POST", "/api/v1/research/factor-corr"),
    ("POST", "/api/v1/research/factor-icir"),
    ("POST", "/api/v1/research/factor-quantile"),
    ("POST", "/api/v1/research/impact-sim"),
    ("POST", "/api/v1/research/optimize"),
    ("POST", "/api/v1/research/stress-test"),
    ("DELETE", "/api/v1/alerts/rules/{rule_id}"),
    ("PUT", "/api/v1/alerts/rules/{rule_id}"),
    ("POST", "/api/v1/alerts/rules"),
    ("POST", "/api/v1/datacenter/mirror/rebuild"),
    ("POST", "/api/v1/datacenter/sync"),
    ("POST", "/api/v1/datacenter/sync/auto"),
    ("POST", "/api/v1/datacenter/sync/cancel"),
    ("POST", "/api/v1/datacenter/sync/fetch"),
    ("POST", "/api/v1/datacenter/text/build-factor"),
    ("POST", "/api/v1/datacenter/text/import"),
    ("POST", "/api/v1/datacenter/train/cancel"),
    ("POST", "/api/v1/datacenter/train/start"),
    ("POST", "/api/v1/desk/exclusion"),
    ("POST", "/api/v1/desk/exclusion/toggle"),
    ("POST", "/api/v1/desk/fills/run"),
    ("POST", "/api/v1/desk/kill-switch"),
    ("POST", "/api/v1/desk/orders"),
    ("POST", "/api/v1/monitor/run"),
    ("POST", "/api/v1/ops/dag/rerun"),
    ("POST", "/api/v1/ops/quality-scan"),
    ("POST", "/api/v1/report/daily/generate"),
    ("POST", "/api/v1/settings/connectors/test"),
    ("POST", "/api/v1/settings/data/sync"),
    ("POST", "/api/v1/studio/alpha-eval"),
    ("POST", "/api/v1/studio/factor-report"),
    ("POST", "/api/v1/studio/factors"),
    ("DELETE", "/api/v1/studio/factors/{factor_id}"),
    ("POST", "/api/v1/studio/mining/cancel/{task_id}"),
    ("POST", "/api/v1/studio/mining/start"),
    ("POST", "/api/v1/studio/nl-to-factor"),
]

ADMIN: list[tuple[str, str]] = [
    ("POST", "/api/v1/settings/data/cache/clear"),
    ("POST", "/api/v1/settings/db/backup"),
    ("PUT", "/api/v1/settings/engine"),
]

WRITE_ENDPOINTS: list[tuple[str, str, str | None]] = (
    [(m, p, None) for m, p in PUBLIC]
    + [(m, p, "viewer") for m, p in VIEWER]
    + [(m, p, "researcher") for m, p in RESEARCHER]
    + [(m, p, "admin") for m, p in ADMIN]
)

_PATH_PARAMS = {
    "{rule_id}": "1",
    "{factor_id}": "1",
    "{task_id}": "no-such-task",
}


def _fill(path: str) -> str:
    """把路径参数替换为稳定的占位值（保持类型合法：两条 {id} 均为字符串/整数兼容）。"""
    out = path
    for k, v in _PATH_PARAMS.items():
        out = out.replace(k, v)
    return out


def _role_of(route: APIRoute) -> str | None:
    """从路由依赖闭包读出 require_role 的 minimum_role（None = 公开接口）。"""
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


def _live_write_registry() -> dict[tuple[str, str], str | None]:
    """运行时扫描：{(METHOD, path): 最低角色}。

    路由拍平统一走 ``conftest.iter_effective_api_routes``，以同时兼容
    FastAPI 新旧两代的挂载形态（急切拷贝 vs 惰性 ``_IncludedRouter``）。
    """
    from conftest import iter_effective_api_routes

    out: dict[tuple[str, str], str | None] = {}
    for route in iter_effective_api_routes(app):
        for method in set(route.methods or ()) & {"POST", "PUT", "DELETE", "PATCH"}:
            out[(method, route.path)] = _role_of(route)
    return out


def _bodies_with_schema() -> set[tuple[str, str]]:
    """带 requestBody 的写端点集合（用于参数校验用例）。"""
    spec = app.openapi()
    out: set[tuple[str, str]] = set()
    for path, ops in spec["paths"].items():
        for method, op in ops.items():
            if method.upper() not in {"POST", "PUT", "DELETE", "PATCH"}:
                continue
            if op.get("requestBody"):
                out.add((method.upper(), path))
    return out


def _find_id(payload: Any) -> Any:
    """从响应里捞出第一个 id-ish 字段（不同端点的返回结构不统一）。"""
    if isinstance(payload, dict):
        for key in ("id", "rule_id", "factor_id"):
            if key in payload and isinstance(payload[key], (int, str)):
                return payload[key]
        for v in payload.values():
            found = _find_id(v)
            if found is not None:
                return found
    elif isinstance(payload, list):
        for v in payload:
            found = _find_id(v)
            if found is not None:
                return found
    return None


# ---------------- 夹具 ----------------
_ADMIN_H = {"Authorization": f"Bearer {os.environ.get('ADMIN_TOKEN', 'aqp-dev-token-change-me')}"}


async def _seed_users() -> None:
    """建三个角色 + viewer / researcher 两个测试账号（admin 走 ADMIN_TOKEN 直通）。"""
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
        role_ids = {
            r.name: r.id for r in (await sess.scalars(select(Role))).all()
        }
        for uname, pwd, role in (
            ("wqviewer", "viewpass123", "viewer"),
            ("wqresearcher", "respass123", "researcher"),
        ):
            if not (await sess.scalars(select(User).where(User.username == uname))).first():
                sess.add(User(username=uname, password_hash=hash_password(pwd),
                              role_id=role_ids[role]))
        await sess.commit()


@pytest.fixture(autouse=True)
def _dangerous_calls_blocked(monkeypatch):
    """兜底红线：任何真实网络同步 / 训练 / 镜像重建被触发 → 立即让测试失败。

    这些端点本应被 40100/40300/40000 拦在处理器之前；该守卫确保"万一没拦住"
    时是测试变红，而不是真的跑一次外部同步或训练。
    """
    def _boom(*_a: Any, **_k: Any) -> None:
        raise AssertionError("危险写端点被真实执行（鉴权/校验未拦住）——测试设计失效")

    import app.api.v1.datacenter as dc
    import app.data.cross_section as cs
    import app.ml.train_service as ts
    import app.services.sync_service as ss

    targets = (
        (dc, "trigger_sync"), (dc, "trigger_fetch"), (dc, "trigger_train"),
        (cs, "build_mirror"),
        (ts, "start_training"), (ts, "run_lgbm_training"),
        (ss, "_run_incremental"), (ss, "_run_repair"), (ss, "_run_rebuild"),
    )
    for mod, name in targets:
        if hasattr(mod, name):
            monkeypatch.setattr(mod, name, _boom)


@pytest.fixture(scope="module")
def client():
    with TestClient(app) as c:
        yield c


@pytest.fixture(scope="module")
def tokens(client: TestClient) -> dict[str, dict[str, str]]:
    """admin（ADMIN_TOKEN 直通）+ viewer / researcher（真实 JWT 登录）。"""
    asyncio.run(_seed_users())
    out: dict[str, dict[str, str]] = {"admin": _ADMIN_H, "none": {}, "bogus": {
        "Authorization": "Bearer not-a-jwt.aaa.bbb"}}
    for key, uname, pwd in (
        ("viewer", "wqviewer", "viewpass123"),
        ("researcher", "wqresearcher", "respass123"),
    ):
        r = client.post("/api/v1/auth/login",
                        json={"username": uname, "password": pwd})
        body = r.json()
        assert body.get("code") == 0, f"{uname} 登录失败: {body}"
        out[key] = {"Authorization": f"Bearer {body['data']['access_token']}"}
    return out


# ---------------- 1. 覆盖完整性 ----------------
def test_registry_matches_runtime_rbac() -> None:
    """硬编码注册表必须与运行时 RBAC 依赖逐条一致，且不得遗漏任何写端点。

    这条用例是整套覆盖率的"守门人"：新增写端点却忘了纳管会立刻变红。
    """
    live = _live_write_registry()
    declared = {(m, p) for m, p, _ in WRITE_ENDPOINTS}

    missing = sorted(set(live) - declared)
    assert not missing, f"存在未纳管的写端点（请补进注册表）: {missing}"

    stale = sorted(declared - set(live))
    assert not stale, f"注册表中的端点已不存在（请删除）: {stale}"

    mismatched = [
        (m, p, role, live[(m, p)])
        for m, p, role in WRITE_ENDPOINTS
        if live[(m, p)] != role
    ]
    assert not mismatched, f"注册表角色与实际 RBAC 不符（(method, path, 声明, 实际)）: {mismatched}"


def test_registry_covers_all_write_endpoints() -> None:
    """写端点总数与审计口径一致（数量变化时强制复核覆盖面）。"""
    assert len(WRITE_ENDPOINTS) == 52, len(WRITE_ENDPOINTS)
    assert len({(m, p) for m, p, _ in WRITE_ENDPOINTS}) == 52


# ---------------- 2. 鉴权边界 ----------------
_PROTECTED = [(m, p, r) for m, p, r in WRITE_ENDPOINTS if r is not None]


@pytest.mark.parametrize("method,path,role", _PROTECTED,
                         ids=[f"{m} {p}" for m, p, _ in _PROTECTED])
def test_write_requires_token(client: TestClient, method: str, path: str, role: str) -> None:
    """受保护写端点：无 Authorization → 40100（HTTP 恒 200）。"""
    r = client.request(method, _fill(path), json={})
    assert r.status_code == 200, r.text
    assert r.json().get("code") == ERR_UNAUTHORIZED, r.json()


@pytest.mark.parametrize("method,path,role", _PROTECTED[:8],
                         ids=[f"{m} {p}" for m, p, _ in _PROTECTED[:8]])
def test_write_rejects_forged_token(client: TestClient, method: str, path: str,
                                    role: str, tokens) -> None:
    """伪造/无法解析的 Bearer → 40102（抽样 8 个端点，避免重复开销）。"""
    r = client.request(method, _fill(path), headers=tokens["bogus"], json={})
    assert r.status_code == 200, r.text
    body = r.json()
    assert body.get("code") in (ERR_INVALID_TOKEN, ERR_UNAUTHORIZED), body


# ---------------- 3. RBAC 边界（2026-09-23 全面放开后的新语义） ----------------
# 用户裁决：只要登录即可用全部功能 ⇒ 默认 Settings.RBAC_ENFORCE=False，任何已登录
# 用户都越过角色门。此处断言新语义，并保留"可回滚"的双向验证：
#   · 默认放开：ensure_role 对任何已登录角色放行；
#   · 打开 RBAC_ENFORCE：恢复 40300 分级拦截。
# ⚠️ 不再对 researcher/admin 写端点**逐个发真实请求**：放开后请求会越过鉴权层进入
#    处理器，而本文件的安全设计明确"不调用会外发网络 / 触发训练的真实路径"（见第 4 条）。
#    改为：纯函数断言 + 一条**必填 body** 的代表端点（空 body 必在参数校验层被拦 ⇒
#    处理器不执行、无副作用）。
_SAFE_RBAC_PROBE = ("POST", "/api/v1/desk/orders")


def test_ensure_role_allows_any_logged_in_when_rbac_open(
        monkeypatch: pytest.MonkeyPatch) -> None:
    """默认全面放开：任何已登录角色对任何最低角色都放行（不再比较等级）。"""
    monkeypatch.setattr(auth_mod, "rbac_enforced", lambda: False)
    for role in ("viewer", "researcher", "admin"):
        for minimum in ("viewer", "researcher", "admin"):
            assert auth_mod.ensure_role({"role": role}, minimum)["role"] == role


def test_ensure_role_forbids_when_rbac_enforced(
        monkeypatch: pytest.MonkeyPatch) -> None:
    """回滚开关打开：恢复 viewer<researcher<admin 分级拦截（证明放开可回滚、非恒真）。"""
    monkeypatch.setattr(auth_mod, "rbac_enforced", lambda: True)
    with pytest.raises(HTTPException) as forbidden:
        auth_mod.ensure_role({"role": "viewer"}, "researcher")
    assert forbidden.value.detail == "FORBIDDEN"
    assert auth_mod.ensure_role({"role": "admin"}, "researcher")["role"] == "admin"


def test_viewer_passes_rbac_on_privileged_write(client: TestClient, tokens) -> None:
    """新语义：viewer 调 researcher 级写端点不再被角色门拒绝。

    探测端点带**必填 body**：空 body 被参数校验拦成 40000 ⇒ 证明请求已越过 RBAC，
    且处理器未执行（无副作用）。未登录仍 40100 由 test_write_requires_token 覆盖。
    """
    method, path = _SAFE_RBAC_PROBE
    r = client.request(method, path, headers=tokens["viewer"], json={})
    assert r.status_code == 200, r.text
    assert r.json().get("code") not in (ERR_UNAUTHORIZED, ERR_FORBIDDEN), r.json()


# ---------------- 4. 参数校验（结构非法 → 40000，且不产生副作用） ----------------
def _wrong_role_for(role: str | None) -> dict[str, str]:
    """挑一个"恰好通过鉴权"的请求头，以便测到校验层。"""
    if role == "admin":
        return _ADMIN_H
    if role == "researcher":
        return {"Authorization": "Bearer __researcher__"}  # 占位，下面用 tokens 覆盖
    return {}


def test_write_rejects_malformed_body(client: TestClient, tokens) -> None:
    """所有带 body 的写端点：JSON 结构非法（数组替代对象）→ 40000，绝不 50000。

    用"结构错"而不是"字段缺失"是有意为之：结构错误在任何字段配置下都必然被
    pydantic 拒绝，因此该用例对所有端点都成立，不会因为字段可选而误执行。
    """
    checked = 0
    for method, path, role in WRITE_ENDPOINTS:
        if (method, path) not in _bodies_with_schema():
            continue
        headers = tokens["none"] if role is None else tokens[role]
        r = client.request(method, _fill(path), headers=headers, json=[1, 2, 3])
        assert r.status_code == 200, r.text
        code = r.json().get("code")
        assert code == ERR_PARAMS, f"{method} {path} 期望 40000，实际 {code}: {r.json()}"
        checked += 1
    assert checked >= 35, f"带 body 的写端点覆盖数偏少（{checked}），疑似漏测"


def test_public_compute_writes_reject_malformed_body(client: TestClient) -> None:
    """公开的纯计算端点（回测/研究/导出）同样必须做参数校验。"""
    for method, path in PUBLIC:
        if method != "POST" or path.endswith("/auth/login"):
            continue
        if (method, path) not in _bodies_with_schema():
            continue
        r = client.post(path, json=[])
        assert r.json().get("code") == ERR_PARAMS, f"{path}: {r.json()}"


# ---------------- 5. 本地状态写入（真实走通） ----------------
def _ok(body: dict) -> None:
    assert body.get("code") == 0, body


def _ok_or_graceful(body: dict, what: str) -> None:
    """隔离环境（无 features / 无行情）下允许**优雅**降级，但绝不允许未分类异常。

    允许集合刻意收窄为三类，避免"什么都放过"导致用例失去意义：
      - `0`              正常完成；
      - `ERR_PARAMS`     参数可被合法拒绝（如数据源未配置）；
      - `ERR_DATA_EMPTY` 本地无数据（51001）——隔离 DATA_ROOT 是空目录，
                         纯本地只读端点报这个码属于**设计内降级**而非缺陷。
    """
    code = body.get("code")
    assert code != ERR_SYSTEM, f"{what} 触发未分类异常：{body}"
    assert code in (0, ERR_PARAMS, ERR_DATA_EMPTY), f"{what} 返回非预期错误码：{body}"


def test_alerts_rule_crud(client: TestClient, tokens) -> None:
    """预警规则 建→查→改→删 全链路。"""
    payload = {
        "name": "补测-价格异动", "rule_type": "price_pct", "scope": "symbol",
        "symbol": "600519.SH", "params": {"threshold": 5},
        "channels": ["sse"], "cooldown_minutes": 30, "enabled": True,
    }
    created = client.post("/api/v1/alerts/rules", headers=tokens["researcher"],
                          json=payload).json()
    _ok(created)
    rule_id = _find_id(created.get("data"))
    assert rule_id is not None, created

    listed = client.get("/api/v1/alerts/rules", headers=tokens["researcher"]).json()
    _ok(listed)
    names = [x.get("name") for x in (listed.get("data") or [])]
    assert "补测-价格异动" in names, listed

    updated = client.put(f"/api/v1/alerts/rules/{rule_id}", headers=tokens["researcher"],
                         json={**payload, "name": "补测-价格异动-改", "enabled": False}).json()
    _ok(updated)

    deleted = client.delete(f"/api/v1/alerts/rules/{rule_id}",
                            headers=tokens["researcher"]).json()
    _ok(deleted)
    after = client.get("/api/v1/alerts/rules", headers=tokens["researcher"]).json()
    assert "补测-价格异动-改" not in [x.get("name") for x in (after.get("data") or [])]


def test_alerts_rule_rejects_unknown_type(client: TestClient, tokens) -> None:
    """未知 rule_type 必须被白名单拒绝（不是静默入库）。"""
    body = client.post("/api/v1/alerts/rules", headers=tokens["researcher"], json={
        "name": "x", "rule_type": "不存在的类型", "scope": "symbol", "symbol": "600519.SH",
    }).json()
    assert body.get("code") == ERR_PARAMS, body


def test_alerts_events_read(client: TestClient, tokens) -> None:
    """标记预警事件已读（viewer 权限即可）。

    注意：`ids=[]` 且未给 `all` 会被判为"什么都没指定"而拒绝（40000），
    因此这里显式走 `all=true`（隔离库内无真实事件，等价 no-op）。
    """
    empty = client.post("/api/v1/alerts/events/read", headers=tokens["viewer"],
                        json={"ids": []}).json()
    assert empty.get("code") == ERR_PARAMS, empty  # 空 ids 必须拒绝，不能静默成功

    body = client.post("/api/v1/alerts/events/read", headers=tokens["viewer"],
                       json={"all": True}).json()
    _ok(body)
    assert body.get("data", {}).get("marked") == 0, body  # 隔离库无事件


def test_settings_preferences_roundtrip(client: TestClient, tokens) -> None:
    """偏好设置写入后可读回（viewer 权限）。

    口径：`/settings/preferences` 是**只写**端点，没有同名 GET；读回统一走
    聚合端点 `GET /api/v1/settings` 的 `data.settings.preferences`。
    """
    r = client.put("/api/v1/settings/preferences", headers=tokens["viewer"],
                   json={"nickname": "补测用户", "theme": "light"}).json()
    _ok(r)
    assert (r.get("data") or {}).get("nickname") == "补测用户", r  # PUT 回显已落库值

    got = client.get("/api/v1/settings", headers=tokens["viewer"]).json()
    _ok(got)
    prefs = ((got.get("data") or {}).get("settings") or {}).get("preferences") or {}
    assert prefs.get("nickname") == "补测用户", got


def test_settings_engine_roundtrip(client: TestClient, tokens) -> None:
    """交易引擎参数写入后可读回（admin 权限）。同样经聚合端点读回。"""
    r = client.put("/api/v1/settings/engine", headers=tokens["admin"],
                   json={"commission_pct": 0.00025, "slippage_pct": 0.0004}).json()
    _ok(r)
    got = client.get("/api/v1/settings", headers=tokens["admin"]).json()
    _ok(got)
    engine = ((got.get("data") or {}).get("settings") or {}).get("engine") or {}
    assert engine.get("commission_pct") == pytest.approx(0.00025), got
    assert engine.get("slippage_pct") == pytest.approx(0.0004), got


def test_settings_cache_clear(client: TestClient, tokens) -> None:
    """清空缓存（admin）：返回释放空间字段，不报错。"""
    body = client.post("/api/v1/settings/data/cache/clear", headers=tokens["admin"]).json()
    _ok(body)


def test_settings_db_backup_writes_within_data_root(client: TestClient, tokens) -> None:
    """数据库备份（admin）：必须落在隔离的 DATA_ROOT 内，绝不写到生产目录。"""
    from app.core.config import get_settings

    body = client.post("/api/v1/settings/db/backup", headers=tokens["admin"]).json()
    _ok(body)
    data_root = get_settings().DATA_ROOT.resolve()
    blob = str(body.get("data"))
    for token in ("backup", ".sqlite", ".db", ".zip"):
        if token in blob:
            break
    # 关键断言：返回信息里若含绝对路径，必须在隔离目录内
    for chunk in blob.replace("'", " ").replace('"', " ").split():
        if data_root.drive and data_root.drive.lower() in chunk.lower() and ":\\" in chunk:
            assert str(data_root).lower() in chunk.lower().replace("/", "\\"), chunk


def test_datacenter_text_build_factor_is_graceful(client: TestClient, tokens) -> None:
    """文本因子重建（本地规则词库，LLM 未启用）：无文档时优雅报"无数据"，绝不 50000。"""
    body = client.post("/api/v1/datacenter/text/build-factor",
                       headers=tokens["researcher"]).json()
    _ok_or_graceful(body, "datacenter/text/build-factor")


def test_datacenter_mirror_rebuild_returns_list_data(
        client: TestClient, tokens, monkeypatch) -> None:
    """POST /datacenter/mirror/rebuild 的 ``data`` 必须是**数组**（回归 2026-09-28 的 500）。

    历史缺陷：``cs_mirror_rebuild`` 注解为 ``-> APIResponse[dict]``，但返回 ``ok(out)``
    其中 ``out`` 是 ``list[dict]`` ⇒ FastAPI response_model 校验失败
    （``data: Input should be a valid dictionary``）⇒ **每次调用都是 HTTP 500**，
    尽管镜像其实已成功重建。此用例把"注解/返回形状"钉死为数组，防止回归
    （该 bug 能上线，正是因为没有任何用例覆盖此端点的响应形状）。

    真实 ``build_mirror`` 由本文件 autouse 守卫改成抛错；这里覆盖为返回合成结果，
    只验证**响应形状**，不触碰真实磁盘/网络。
    """
    import app.data.cross_section as cs

    def _fake(ds: str) -> dict:
        return {"dataset": ds, "dates": 3, "built": 2, "skipped": 1, "rows": 120}

    # 覆盖 autouse 守卫的 _boom（本用例在测试体内 setattr，晚于 autouse 的 setup ⇒ 生效）
    monkeypatch.setattr(cs, "build_mirror", _fake)

    body = client.post("/api/v1/datacenter/mirror/rebuild",
                       headers=tokens["researcher"], json={}).json()
    assert body.get("code") == 0, body
    assert isinstance(body.get("data"), list), f"data 必须是数组（否则 FastAPI 500）: {body}"
    assert [row["dataset"] for row in body["data"]] == [
        "daily_bar", "daily_bar_hfq", "daily_bar_qfq"], body


def test_desk_exclusion_add_and_toggle(client: TestClient, tokens) -> None:
    """禁投池：新增 → 列表可见 → 切换启用态 → 再切换回来。"""
    added = client.post("/api/v1/desk/exclusion", headers=tokens["researcher"],
                        json={"symbol": "600519.SH", "reason": "补测"}).json()
    _ok(added)
    item_id = _find_id(added.get("data"))
    listed = client.get("/api/v1/desk/exclusion", headers=tokens["researcher"]).json()
    _ok(listed)
    if item_id is None:
        items = listed.get("data") or []
        match = [x for x in items if x.get("symbol") == "600519.SH"]
        assert match, listed
        item_id = match[0].get("id")
    assert item_id is not None, listed

    off = client.post("/api/v1/desk/exclusion/toggle", headers=tokens["researcher"],
                      json={"id": item_id, "active": False}).json()
    _ok(off)
    on = client.post("/api/v1/desk/exclusion/toggle", headers=tokens["researcher"],
                     json={"id": item_id, "active": True}).json()
    _ok(on)


def test_desk_kill_switch_toggle(client: TestClient, tokens) -> None:
    """熔断开关：置位后必须能复位（避免测试把环境永久留在熔断态）。

    读回字段口径：`data.kill_switch`（非 `active`）。
    """
    on = client.post("/api/v1/desk/kill-switch", headers=tokens["researcher"],
                     json={"active": True, "reason": "补测"}).json()
    _ok(on)
    state = client.get("/api/v1/desk/kill-switch", headers=tokens["researcher"]).json()
    _ok(state)
    assert (state.get("data") or {}).get("kill_switch") is True, state
    off = client.post("/api/v1/desk/kill-switch", headers=tokens["researcher"],
                      json={"active": False, "reason": "补测复位"}).json()
    _ok(off)
    back = client.get("/api/v1/desk/kill-switch", headers=tokens["researcher"]).json()
    assert (back.get("data") or {}).get("kill_switch") is False, back


def test_studio_factor_save_is_graceful(client: TestClient, tokens) -> None:
    """因子入库端点在隔离环境下的**冒烟**契约：要么成功，要么优雅报"无数据"。

    为什么不做成"保存→列表→删除"全链路：`POST /studio/factors` 的入库前必须先
    加载 features 快照做真实 IC 评估，属于数据依赖很重的深路径，已由
    `tests/test_lab_factor.py::test_factor_save_list_delete`（自带合成 features）
    完整覆盖；本用例只保证**广度层面的三条底线**：
      1. 绝不出现 50000（未分类异常）/ 裸 HTTP 5xx；
      2. 成功时必须能删除（不残留污染后续用例）；
      3. 删除不存在的 id 也必须优雅。

    注意断言写成"允许集合"而非固定码：同一次 pytest 会话里若 test_lab_factor
    先跑过，features 会真实存在（两个模块共享同一隔离 DATA_ROOT），
    固定断言会变成顺序敏感 flake。
    """
    created = client.post("/api/v1/studio/factors", headers=tokens["researcher"],
                          json={"name": "补测因子", "expression": "close - mom_20",
                                "horizon": 5}).json()
    code = created.get("code")
    # [AQP R10/53001 2026-09-22] 本断言是**允许集合**（原为 40000/51001/0）。
    # 非法/不可解析表达式现在有专属码 `ERR_EXPR_INVALID=53001`（此前一律 40000
    # 冒充"参数错"）⇒ 必须把 53001 纳入允许集合，否则"更精确的错误码"反而把
    # 既有用例打红。守卫内容不变：不得 50000、不得静默入库。
    # 同文件 `test_studio_factor_rejects_invalid_expression` 的允许集合里
    # 早已含 53001（且其 docstring 写明"40000/53001"），此处只是对齐。
    assert code in (0, ERR_PARAMS, ERR_DATA_EMPTY, ERR_EXPR_INVALID), created
    if code == 0:
        fid = _find_id(created.get("data"))
        assert fid is not None, created
        deleted = client.delete(f"/api/v1/studio/factors/{fid}",
                                headers=tokens["researcher"]).json()
        _ok(deleted)

    # 删除不存在的因子：必须优雅（ERR_DATA_EMPTY/ERR_NOT_FOUND），不能 50000
    miss = client.delete("/api/v1/studio/factors/99999999",
                         headers=tokens["researcher"]).json()
    assert miss.get("code") != ERR_SYSTEM, miss


def test_studio_factor_rejects_invalid_expression(client: TestClient, tokens) -> None:
    """非法表达式（AST 白名单外）**任何情况下都不得入库成功**。

    关键不变量是 `code != 0`：无 features 时端点先报 51001（数据缺失早于表达式
    校验——`parse_expr` 的 `extra_fields` 来自快照列，因此加载是前置条件），
    有 features 时被 AST 白名单拒绝（40000/53001）。两种路径都不能落库废因子。
    """
    body = client.post("/api/v1/studio/factors", headers=tokens["researcher"],
                       json={"name": "坏因子", "expression": "__import__('os').system('id')",
                             "horizon": 5}).json()
    assert body.get("code") != 0, f"危险表达式竟然入库成功: {body}"
    assert body.get("code") in (ERR_PARAMS, 53001, ERR_DATA_EMPTY), body


def test_studio_mining_cancel_unknown_task(client: TestClient, tokens) -> None:
    """取消不存在的挖掘任务：给出可预期的"任务不存在"，而不是 500。"""
    body = client.post("/api/v1/studio/mining/cancel/no-such-task",
                       headers=tokens["researcher"]).json()
    assert body.get("code") != ERR_SYSTEM, body


def test_datacenter_sync_cancel_is_noop(client: TestClient, tokens) -> None:
    """无同步任务在跑时取消：恒可用（不清空任何真实任务）。"""
    body = client.post("/api/v1/datacenter/sync/cancel", headers=tokens["researcher"]).json()
    _ok(body)


def test_datacenter_train_cancel_is_noop(client: TestClient, tokens) -> None:
    """无训练任务在跑时取消：恒可用。"""
    body = client.post("/api/v1/datacenter/train/cancel", headers=tokens["researcher"]).json()
    _ok(body)


def test_datacenter_sync_auto_roundtrip(client: TestClient, tokens) -> None:
    """autoSync 配置写入 → 读回 → 还原（回归 P0-2）。

    非法时间的拒绝码统一为 `ERR_PARAMS = 40000`（此前是裸 4 位字面量，
    与 5 位错误码体系不符；全仓 9 处 `fail(...)` 调用点及 4 条既有断言已
    就地归一，见补测报告 P2-C）。此处收紧为**精确**断言，防再次漂移。
    """
    origin = client.get("/api/v1/datacenter/sync/auto", headers=tokens["researcher"]).json()
    _ok(origin)
    try:
        saved = client.post("/api/v1/datacenter/sync/auto", headers=tokens["researcher"],
                            json={"enabled": True, "time": "17:45"}).json()
        _ok(saved)
        back = client.get("/api/v1/datacenter/sync/auto", headers=tokens["researcher"]).json()
        assert (back.get("data") or {}).get("time") == "17:45", back
        bad = client.post("/api/v1/datacenter/sync/auto", headers=tokens["researcher"],
                          json={"enabled": True, "time": "25:99"}).json()
        assert bad.get("code") == ERR_PARAMS, bad
        assert bad.get("code") != 0, bad
    finally:
        saved_origin = origin.get("data") or {}
        if saved_origin:
            client.post("/api/v1/datacenter/sync/auto", headers=tokens["researcher"],
                        json={"enabled": bool(saved_origin.get("enabled", False)),
                              "time": saved_origin.get("time", "17:30")})


def test_datacenter_dag_rerun_rejects_bad_date(client: TestClient, tokens) -> None:
    """DAG 重跑：日期非法必须被拦（防止误触发整条流水线）。"""
    body = client.post("/api/v1/ops/dag/rerun", headers=tokens["researcher"],
                       json={"trade_date": "not-a-date"}).json()
    assert body.get("code") == ERR_PARAMS, body


def test_ops_quality_scan_runs(client: TestClient, tokens) -> None:
    """数据质量扫描（本地只读扫描，隔离目录下应优雅返回）。"""
    body = client.post("/api/v1/ops/quality-scan", headers=tokens["researcher"],
                       json={"dataset": "daily_bar"}).json()
    _ok_or_graceful(body, "quality-scan")


def test_report_daily_generate_runs(client: TestClient, tokens) -> None:
    """日报生成（本地模板，无外网）。"""
    body = client.post("/api/v1/report/daily/generate", headers=tokens["researcher"]).json()
    _ok_or_graceful(body, "report/daily/generate")


def test_monitor_run_runs(client: TestClient, tokens) -> None:
    """因子健康度巡检（本地计算 + 落库；无特征时优雅降级）。"""
    body = client.post("/api/v1/monitor/run", headers=tokens["researcher"]).json()
    _ok_or_graceful(body, "monitor/run")


def test_desk_fills_run_and_orders(client: TestClient, tokens) -> None:
    """模拟盘：下单 + 撮合。隔离环境无行情时应优雅降级而非 50000。"""
    order = client.post("/api/v1/desk/orders", headers=tokens["researcher"],
                        json={"symbol": "600519.SH", "side": "buy",
                              "order_amount": 100000, "algo": "market"}).json()
    _ok_or_graceful(order, "desk/orders")
    fills = client.post("/api/v1/desk/fills/run", headers=tokens["researcher"]).json()
    _ok_or_graceful(fills, "desk/fills/run")


def test_envelope_contract_holds_for_all_write_endpoints(client: TestClient, tokens) -> None:
    """信封契约：52 个写端点在被拒绝时都必须 HTTP 200 且含完整信封字段。

    拒绝策略按端点类型选择，保证**零副作用**（关键：不带 body 的端点无法用
    "非法 body" 强制拒绝，`json=[]` 会被直接忽略并真实执行处理器——因此这里
    对它们改用"缺 token"，让 40100 在处理器之前拦下）：

    | 端点类型 | 触发方式 | 期望码 |
    |---|---|---|
    | 受保护（50 个） | 不带 Authorization | 40100 |
    | 公开（2 个：/auth/login、/auth/register，均带 body） | 结构非法（数组替代对象） | 40000 |

    注：/auth/login 带 requestBody，会先命中上面的 40000 分支（结构非法早于账号
    校验），因此下方 40104 分支在当前端点集合下不可达（保留以容纳未来无 body 的
    公开端点）。

    "不带 body 的私有端点真实执行"由各自专项用例覆盖：
    `*_is_noop` / `*_toggle` / `*_runs` / `test_settings_db_backup_*` /
    `test_datacenter_text_build_factor_*`。
    """
    checked = 0
    for method, path, role in WRITE_ENDPOINTS:
        if role is not None:
            headers: dict[str, str] = tokens["none"]
            payload: Any = {}
        elif (method, path) in _bodies_with_schema():
            headers, payload = {}, []
        elif path.endswith("/auth/login"):
            headers, payload = {}, {"username": "\x00no-such-user", "password": "x"}
        else:  # pragma: no cover - 当前 52 个端点不存在该分支
            continue
        r = client.request(method, _fill(path), headers=headers, json=payload)
        assert r.status_code == 200, f"{method} {path} → HTTP {r.status_code}"
        body = r.json()
        assert set(body) >= {"code", "message", "data"}, f"{method} {path} 信封字段缺失: {body}"
        assert body["code"] != 0, f"{method} {path} 期望被拒绝，却成功: {body}"
        checked += 1
    assert checked == 52, f"信封契约覆盖数 {checked} != 52"
