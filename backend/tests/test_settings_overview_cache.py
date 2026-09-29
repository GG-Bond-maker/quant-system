"""§4.8 序 16 防回归：`/settings` 不得清空数据中心统计缓存（`Query` 当默认值的坑）。

## 缺陷

`datacenter.overview` 原签名 `refresh: int = Query(0, ge=0, le=1, ...)`。FastAPI
路由调用时 `refresh` 是解析后的 `int`，但 **Python 直接调用**（`app_settings.py:157`
就是 `await overview()`）拿到的是 **`Query` 对象本身**，而 `bool(Query(0)) is True`
⇒ `datacenter.py:466 if refresh:` 恒真 ⇒ **每次 `GET /settings` 都
`invalidate_stats_cache()`**，把 120s 的进程级统计缓存清掉、逼出数百个 parquet 的
全量重扫（前端注释里"冷算 27~30s"的根因）。

## 修法

1. `overview` 改为 `Annotated[int, Query(...)] = 0`：FastAPI 仍按查询参数校验
   （`ge=0, le=1` 的 422 行为不变），而内部无参调用拿到**真正的 `0`**；
2. `app_settings.py:157` 再**显式**写死 `refresh=0`（双保险，且自文档）。

## 本文件验证

- 签名默认值必须是普通 `0`，不能是 `Query` 实例（直接锁死回归面）；
- `GET /settings` 不触发失效；`GET /datacenter/overview?refresh=1` **必须**触发
  （证明修复没有把 `refresh=1` 的能力一起废掉）；
- `refresh=5` 仍是 422（证明 `Annotated` 改法没有破坏参数校验）。
"""
from __future__ import annotations

import asyncio
import inspect
import os
import sys
from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient

BACKEND_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(BACKEND_ROOT))

from app.main import app  # noqa: E402

_ADMIN_H = {"Authorization": f"Bearer {os.environ.get('ADMIN_TOKEN', 'aqp-dev-token-change-me')}"}


async def _seed_users() -> None:
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
        role_ids = {r.name: r.id for r in (await sess.scalars(select(Role))).all()}
        if not (await sess.scalars(
                select(User).where(User.username == "ovviewer"))).first():
            sess.add(User(username="ovviewer", password_hash=hash_password("ovpass1234"),
                          role_id=role_ids["viewer"]))
        await sess.commit()


@pytest.fixture(scope="module")
def client():
    with TestClient(app) as c:
        yield c


@pytest.fixture(scope="module")
def viewer_h(client: TestClient) -> dict[str, str]:
    asyncio.run(_seed_users())
    r = client.post("/api/v1/auth/login",
                    json={"username": "ovviewer", "password": "ovpass1234"})
    body = r.json()
    assert body.get("code") == 0, f"登录失败: {body}"
    return {"Authorization": f"Bearer {body['data']['access_token']}"}


@pytest.fixture()
def invalidations(monkeypatch) -> list[int]:
    """把 `invalidate_stats_cache` 换成计数器（统计缓存失效次数）。"""
    import app.api.v1.datacenter as dc

    calls: list[int] = []

    def _spy() -> None:
        calls.append(1)

    monkeypatch.setattr(dc, "invalidate_stats_cache", _spy)
    return calls


def test_overview_refresh_default_is_plain_zero() -> None:
    """**回归面**：默认值必须是 `0` 本身，而不是真值的 `Query` 对象。"""
    import app.api.v1.datacenter as dc

    default = inspect.signature(dc.overview).parameters["refresh"].default
    assert default == 0, f"refresh 默认值应为 0，实为 {default!r}"
    assert bool(default) is False, (
        "默认值必须为假值——真值会让任何直接调用都清缓存（本缺陷的根因）")


def test_settings_does_not_invalidate_stats_cache(client: TestClient,
                                                  viewer_h: dict[str, str],
                                                  invalidations: list[int]) -> None:
    """`GET /settings` 不得清空 DC 统计缓存（原实现每次调用都清）。"""
    r = client.get("/api/v1/settings", headers=viewer_h)
    assert r.status_code == 200, r.text
    assert r.json()["code"] == 0
    assert invalidations == [], (
        f"/settings 触发了 {len(invalidations)} 次统计缓存失效 ⇒ 27~30s 重扫复发")


def test_overview_refresh_one_still_invalidates(client: TestClient,
                                                viewer_h: dict[str, str],
                                                invalidations: list[int]) -> None:
    """反向断言：`refresh=1` 的能力必须保留（防"为修一处而废掉功能"）。"""
    r = client.get("/api/v1/datacenter/overview", params={"refresh": 1},
                   headers=viewer_h)
    assert r.status_code == 200, r.text
    assert r.json()["code"] == 0
    assert invalidations, "refresh=1 必须触发统计缓存失效"


def test_overview_default_does_not_invalidate(client: TestClient,
                                              viewer_h: dict[str, str],
                                              invalidations: list[int]) -> None:
    """HTTP 侧默认（不带 refresh）也不得清缓存。"""
    r = client.get("/api/v1/datacenter/overview", headers=viewer_h)
    assert r.status_code == 200, r.text
    assert invalidations == [], "默认请求不应清缓存"


@pytest.mark.parametrize("bad", [5, -1])
def test_overview_refresh_bounds_still_enforced(client: TestClient,
                                                viewer_h: dict[str, str],
                                                bad: int) -> None:
    """`Annotated[int, Query(ge=0, le=1)]` 改法必须保留参数校验（否则是修复引入的回归）。

    注意本项目契约是 **HTTP 恒 200 + 信封业务码**：`RequestValidationError` 由
    `app/core/errors.py:168-173` 统一转成 `code=40000`（ERR_PARAMS），
    故这里断言的是**信封码**而不是 HTTP 状态码。
    """
    from app.core.errors import ERR_PARAMS

    r = client.get("/api/v1/datacenter/overview", params={"refresh": bad},
                   headers=viewer_h)
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["code"] == ERR_PARAMS, (
        f"refresh={bad} 应以业务码 {ERR_PARAMS} 拒绝，实为 {body.get('code')}")


def test_overview_refresh_is_typed_int_in_openapi(client: TestClient) -> None:
    """OpenAPI 里该参数仍是 integer 查询参数（`Annotated` 改法的契约面）。"""
    spec = client.get("/openapi.json").json()
    params: list[dict[str, Any]] = spec["paths"]["/api/v1/datacenter/overview"]["get"].get(
        "parameters", [])
    ref = [p for p in params if p["name"] == "refresh"]
    assert ref, f"OpenAPI 应仍暴露 refresh 参数：{[p['name'] for p in params]}"
    assert ref[0]["in"] == "query"
    assert ref[0]["schema"].get("type") == "integer", ref[0]
    assert ref[0]["schema"].get("maximum") == 1