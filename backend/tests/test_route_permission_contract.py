"""关键 API 的错误码与角色权限契约回归测试。

该文件只固化跨端契约，不验证各领域的计算结果：
- 匿名访问受保护端点必须返回 40100；
- viewer 访问 researcher/admin 端点必须返回 40300；
- 达到最低角色后必须越过鉴权层；
- 路由声明必须与预期最低角色一致。
"""
from __future__ import annotations

from collections.abc import Iterator
from typing import Any

import pandas as pd
import pytest
from fastapi import HTTPException
from fastapi.routing import APIRoute
from fastapi.testclient import TestClient

from app.api.v1 import app_settings as settings_module
from app.api.v1 import backtest as backtest_module
from app.api.v1 import research as research_module
from app.api.v1 import stock as stock_module
from app.core.auth import ROLE_RANK, create_jwt_token, ensure_role, require_role
from app.core.errors import (
    ERR_CREDENTIALS,
    ERR_FORBIDDEN,
    ERR_PIPELINE_BUSY,
    ERR_UNAUTHORIZED,
)
from app.data import quotes_hub
from app.main import app


_RESEARCHER_ENDPOINTS: tuple[tuple[str, str, dict[str, Any] | None], ...] = (
    ("GET", "/api/v1/stock/600519.SH/predict", None),
    ("POST", "/api/v1/backtest/run", {
        "start": "2024-01-02",
        "end": "2024-01-31",
    }),
    ("GET", "/api/v1/research/overview", None),
)

_ROUTE_CONTRACT: dict[tuple[str, str], str] = {
    ("GET", "/api/v1/stock/{symbol}/predict"): "researcher",
    ("POST", "/api/v1/backtest/run"): "researcher",
    ("GET", "/api/v1/research/overview"): "researcher",
    ("PUT", "/api/v1/settings/engine"): "admin",
    ("GET", "/api/v1/market/quotes"): "viewer",
}


def _headers(role: str) -> dict[str, str]:
    token, _ = create_jwt_token(f"contract-{role}", role)
    return {"Authorization": f"Bearer {token}"}


def _request(
    client: TestClient,
    method: str,
    path: str,
    payload: dict[str, Any] | None,
    role: str | None = None,
):
    headers = _headers(role) if role else None
    return client.request(method, path, json=payload, headers=headers)


def _declared_role(route: APIRoute) -> str | None:
    """读取 ``require_role`` 生成依赖所捕获的最低角色。"""
    for dependency in route.dependant.dependencies:
        checker = dependency.call
        if getattr(checker, "__name__", "") != "checker":
            continue
        for cell in getattr(checker, "__closure__", None) or ():
            value = cell.cell_contents
            if isinstance(value, str) and value in ROLE_RANK:
                return value
    return None


@pytest.fixture(scope="module")
def client() -> Iterator[TestClient]:
    with TestClient(app) as test_client:
        yield test_client


@pytest.fixture(autouse=True)
def isolate_endpoint_work(monkeypatch: pytest.MonkeyPatch) -> None:
    """替换领域计算/外部行情，只测试鉴权成功后是否放行。"""
    monkeypatch.setattr(stock_module, "predict_symbol", lambda _symbol: {"status": "ok"})
    monkeypatch.setattr(backtest_module, "_run", lambda _request: {"status": "ok"})
    monkeypatch.setattr(
        research_module,
        "_load_features",
        lambda: pd.DataFrame({
            "symbol": ["600519.SH"],
            "date": [pd.Timestamp("2024-01-02")],
            "close": [100.0],
            "factor_contract": [0.1],
        }),
    )

    async def fake_quotes_snapshot(symbols: list[str]) -> dict[str, Any]:
        return {"items": [], "symbols": symbols, "source": "contract-test"}

    monkeypatch.setattr(quotes_hub, "quotes_snapshot", fake_quotes_snapshot)
    monkeypatch.setattr(settings_module, "_load_settings", lambda: {"engine": {}})
    monkeypatch.setattr(settings_module, "_save_settings", lambda _data: None)


def test_business_error_codes_are_unambiguous() -> None:
    """登录凭证错误保持兼容码，管道繁忙使用独立的 409 冲突码。"""
    assert ERR_CREDENTIALS == 40104
    assert ERR_PIPELINE_BUSY == 40900
    assert ERR_PIPELINE_BUSY != ERR_CREDENTIALS


def test_role_hierarchy_is_the_authorization_source() -> None:
    """层级授权覆盖三种角色，并在装载时拒绝拼错的最低角色。"""
    assert ROLE_RANK == {"viewer": 0, "researcher": 1, "admin": 2}
    assert ensure_role({"role": "admin"}, "researcher")["role"] == "admin"
    with pytest.raises(HTTPException) as forbidden:
        ensure_role({"role": "viewer"}, "researcher")
    assert forbidden.value.detail == "FORBIDDEN"
    with pytest.raises(ValueError, match="未知最低角色"):
        require_role("reseacher")


def test_key_routes_declare_expected_minimum_roles() -> None:
    """关键路由声明与产品角色契约一致，防止角色依赖被误删或降级。"""
    actual: dict[tuple[str, str], str | None] = {}
    for route in app.routes:
        if not isinstance(route, APIRoute):
            continue
        for method in route.methods or ():
            key = (method, route.path)
            if key in _ROUTE_CONTRACT:
                actual[key] = _declared_role(route)
    assert actual == _ROUTE_CONTRACT


@pytest.mark.parametrize("method,path,payload", _RESEARCHER_ENDPOINTS)
def test_researcher_routes_reject_anonymous_and_viewer(
    client: TestClient,
    method: str,
    path: str,
    payload: dict[str, Any] | None,
) -> None:
    """predict、backtest、research 均要求 researcher。"""
    anonymous = _request(client, method, path, payload)
    viewer = _request(client, method, path, payload, "viewer")
    assert anonymous.json()["code"] == ERR_UNAUTHORIZED
    assert viewer.json()["code"] == ERR_FORBIDDEN


@pytest.mark.parametrize("method,path,payload", _RESEARCHER_ENDPOINTS)
def test_researcher_routes_allow_researcher(
    client: TestClient,
    method: str,
    path: str,
    payload: dict[str, Any] | None,
) -> None:
    """researcher 必须越过关键研究端点的鉴权层。"""
    response = _request(client, method, path, payload, "researcher")
    assert response.json()["code"] not in (ERR_UNAUTHORIZED, ERR_FORBIDDEN)


def test_admin_settings_contract(client: TestClient) -> None:
    """引擎设置匿名 401、researcher 403、admin 放行。"""
    path = "/api/v1/settings/engine"
    assert _request(client, "PUT", path, {}).json()["code"] == ERR_UNAUTHORIZED
    assert _request(client, "PUT", path, {}, "researcher").json()["code"] == ERR_FORBIDDEN
    assert _request(client, "PUT", path, {}, "admin").json()["code"] == 0


def test_read_only_quote_contract(client: TestClient) -> None:
    """只读行情匿名 401；viewer 及以上可读。"""
    path = "/api/v1/market/quotes?symbols=600519.SH"
    assert _request(client, "GET", path, None).json()["code"] == ERR_UNAUTHORIZED
    assert _request(client, "GET", path, None, "viewer").json()["code"] == 0
    assert _request(client, "GET", path, None, "researcher").json()["code"] == 0
