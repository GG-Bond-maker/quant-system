"""§8.2 第 20 项（F10 同族）· 列表/扫描类查询参数的边界门禁。

缺陷本体（本轮实测）：`limit`/`top_k` 一类"取多少条"的参数此前**无上下界**，
`limit=-1`、`limit=0`、`limit=1e9` 全部 `code=0`（其中 `/datacenter/logs?limit=0`
真会返回**全部**日志）；`top_k=-1` 走 `[:−1]`（"除最后一个"的怪异语义）。
修法是给这些站点加上下界，但**必须防止后续新增端点再次漏加** ⇒ 本文件做两件事：

1. **OpenAPI 层的不变量**：所有名为 `limit` / `top_k` / `page_size` 的 query 参数
   必须在 schema 里同时暴露 `minimum` 与 `maximum`。
   - `page`（页码/偏移）**有意不要求上界**：它只是对已加载列表的切片偏移，
     越界只会返回空页，不构成放大攻击面；但必须 ≥1 或由处理函数显式校验
     （`/screener/stocks` 就是"Query 层无界 + 函数内 `ERR_PARAMS`"的既有范式，
     见 `screener.py:671-674`）⇒ 本文件把这类站点列为**显式豁免名单**并要求
     豁免项在源码里确有校验，避免"豁免"变成永久后门。
2. **行为层**：`/desk/orders`（F10 第 4 处）非法 limit ⇒ HTTP 200 + `code=40000`，
   合法 limit ⇒ `code=0`。
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

BACKEND_ROOT = Path(__file__).resolve().parents[1]
if str(BACKEND_ROOT) not in sys.path:
    sys.path.insert(0, str(BACKEND_ROOT))

from app.core.errors import ERR_PARAMS, ERR_UNAUTHORIZED  # noqa: E402
from app.main import app  # noqa: E402

# 需"取多少条"上界的参数名（放大攻击面 / 静默截断语义）
_BOUNDED_PARAMS = ("limit", "top_k", "page_size")

# 页码/偏移：只要求下界（上界无意义——越界只返回空页）
_MIN_ONLY_PARAMS = ("page",)


def _source_guard(module: str, param: str, root: Path) -> bool:
    """该端点的模块里是否有"同行的 `ERR_PARAMS` + 参数名"显式校验。

    等价判据：**要么**在 Query 层声明上下界，**要么**在函数里显式校验并返回
    `ERR_PARAMS`。后者是本仓既有范式（如 `screener.py` 的 `page 必须 ≥ 1`
    带中文文案，比 FastAPI 的英文校验消息对用户友好），因此不能为了"统一"
    把中文守卫换成框架默认消息 —— 实测这么干会打破 3 条既有断言（消息文案）。
    **这是自动判定，不是手写豁免名单**（手写名单会变成永久后门）。
    """
    import importlib
    import re as _re

    try:
        path = importlib.import_module(module).__file__
    except Exception:  # pragma: no cover - 模块必定可导入
        return False
    src = Path(path or "").read_text(encoding="utf-8")
    return _re.search(rf"ERR_PARAMS[^\n]*{_re.escape(param)}", src) is not None


def _query_params() -> list[tuple[str, str, dict, str]]:
    """(path, 参数名, schema, 端点模块) 四元组；schema 取自 OpenAPI（= 运行时真实约束）。"""
    from conftest import iter_effective_api_routes

    spec = app.openapi()
    by_path: dict[tuple[str, str], str] = {}
    for r in iter_effective_api_routes(app):
        for p in getattr(r.dependant, "query_params", []):
            by_path[(r.path, p.name)] = r.endpoint.__module__
    out: list[tuple[str, str, dict, str]] = []
    for path, ops in spec["paths"].items():
        for op in ops.values():
            for p in op.get("parameters", []):
                if p.get("in") == "query":
                    out.append((path, p["name"], p.get("schema", {}),
                                by_path.get((path, p["name"]), "")))
    return out


@pytest.mark.parametrize("name", _BOUNDED_PARAMS)
def test_list_size_params_are_bounded(name: str) -> None:
    """`limit`/`top_k`/`page_size` 必须有**上下界**，或模块内有显式 `ERR_PARAMS` 校验。"""
    offenders = []
    for path, pname, schema, module in _query_params():
        if pname != name:
            continue
        if schema.get("minimum") is not None and schema.get("maximum") is not None:
            continue
        if module and _source_guard(module, pname, BACKEND_ROOT):
            continue
        offenders.append(f"{path} ({pname}: min={schema.get('minimum')}, "
                         f"max={schema.get('maximum')}, module={module})")
    assert not offenders, (
        f"{name} 既无上下界也无显式校验 ⇒ 可被无限放大或静默截断：\n  "
        + "\n  ".join(sorted(offenders)))


@pytest.mark.parametrize("name", _MIN_ONLY_PARAMS)
def test_page_params_have_a_lower_bound(name: str) -> None:
    """`page` 不要求上界（越界只返回空页），但必须 ≥1 或有显式校验。

    实测教训：`/etf/list` 已有 `Query(1, ge=1)`；`/screener/stocks` 走函数内
    `page 必须 ≥ 1`（中文文案，比框架英文消息友好）——两者都算满足。
    """
    offenders = []
    for path, pname, schema, module in _query_params():
        if pname != name:
            continue
        if schema.get("minimum") is not None:
            continue
        if module and _source_guard(module, pname, BACKEND_ROOT):
            continue
        offenders.append(f"{path} ({pname}: minimum=None, module={module})")
    assert not offenders, (
        f"{name} 缺下界且无显式校验 ⇒ 负页码可能被当作反向切片：\n  "
        + "\n  ".join(sorted(offenders)))


# ------------------------------------------------------------------ 行为层

def _auth_headers(client: TestClient) -> dict[str, str]:
    from sqlalchemy import select

    from app.core.auth import hash_password
    from app.db.init_db import init_database
    from app.db.models_auth import Role, User
    from app.db.session import get_session_factory, reset_engine

    import asyncio

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
            user = (await sess.scalars(
                select(User).where(User.username == "qb_probe"))).first()
            if user is None:
                sess.add(User(username="qb_probe", password_hash=hash_password("qbpass12345"),
                              role_id=role.id))
                await sess.commit()

    asyncio.run(_ensure())
    body = client.post("/api/v1/auth/login",
                       json={"username": "qb_probe", "password": "qbpass12345"}).json()
    assert body["code"] == 0, body
    return {"Authorization": f"Bearer {body['data']['access_token']}"}


@pytest.fixture(scope="module")
def client():
    with TestClient(app) as c:
        yield c


@pytest.mark.parametrize("bad", [-1, 0, 10**9])
def test_desk_orders_rejects_out_of_range_limit(client: TestClient, bad: int) -> None:
    """F10 第 4 处：`/desk/orders` 的 limit 越界必须是 40000（契约内错误，HTTP 200）。"""
    headers = _auth_headers(client)
    r = client.get(f"/api/v1/desk/orders?limit={bad}", headers=headers)
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["code"] == ERR_PARAMS, (
        f"limit={bad} 未被拒 ⇒ 仍是「无界取数」：{body}")
    assert body["code"] != ERR_UNAUTHORIZED


def test_desk_orders_accepts_valid_limit(client: TestClient) -> None:
    """反向锁：合法 limit 不得被新加的边界误伤。"""
    headers = _auth_headers(client)
    r = client.get("/api/v1/desk/orders?limit=5", headers=headers)
    assert r.status_code == 200, r.text
    assert r.json()["code"] == 0, r.text


def test_desk_orders_returns_envelope_with_total_and_truncated(client: TestClient) -> None:
    """P2-8：`/desk/orders` 响应改为信封 `{items,total,returned,limit,truncated}`。

    `total` 必须是独立 COUNT 的真实总数（不受 limit 影响），`truncated` 披露本次
    是否被截断；否则调用方只能把窗口条数当全量（C-3 的根因）。
    """
    headers = _auth_headers(client)
    body = client.get("/api/v1/desk/orders?limit=5", headers=headers).json()
    assert body["code"] == 0, body
    data = body["data"]
    assert isinstance(data, dict), f"仍是裸数组，未改为信封：{type(data).__name__}"
    assert set(data) >= {"items", "total", "returned", "limit", "truncated"}, data.keys()
    assert isinstance(data["items"], list), data
    assert data["limit"] == 5
    assert data["returned"] == len(data["items"])
    assert data["total"] >= data["returned"]
    assert data["truncated"] == (data["total"] > data["returned"])


def test_research_feature_importance_rejects_out_of_range_top_k(client: TestClient) -> None:
    """`/research/feature-importance?top_k=-1` 此前走 `[:−1]`，现在必须 40000。"""
    headers = _auth_headers(client)
    for bad in (-1, 0, 100000):
        r = client.get(f"/api/v1/research/feature-importance?top_k={bad}", headers=headers)
        assert r.status_code == 200, r.text
        assert r.json()["code"] == ERR_PARAMS, f"top_k={bad} 未被拒：{r.text}"