"""B-4：HTTP 安全响应头（2026-10-02 上线前全检）。

**缺陷**：全仓 grep ``x-content-type-options|x-frame-options|strict-transport|
content-security-policy`` **0 命中** ⇒ 浏览器侧完全依赖默认行为（无 MIME 嗅探防护、
无点击劫持防护、无 Referrer 收敛）。

**修复**：``app/main.py`` 的 ``_apply_security_headers()``，挂在既有
``add_response_time_header`` 中间件里（不新增中间件层，避免每个响应多一次栈穿越）。

**为什么单独成文件**：本项是**全站横切**行为（每个响应都必须带），与任何业务模块无关。
塞进 ``test_etf.py`` / ``test_main.py`` 会让"新增业务测试"看起来顺带覆盖了安全头，
掩盖它其实是一条独立契约。
"""
from __future__ import annotations

from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient
from starlette.responses import Response

from app.main import _SECURITY_HEADERS, _apply_security_headers, app

_EXPECTED = {
    "X-Content-Type-Options": "nosniff",
    "X-Frame-Options": "DENY",
    "Referrer-Policy": "no-referrer",
    "Permissions-Policy": "geolocation=(), microphone=(), camera=()",
}


@pytest.fixture()
def client():
    with TestClient(app) as c:
        yield c


def test_headers_present_on_real_responses(client):
    """端到端：真实响应（含错误响应）都必须带全部 4 个头。

    只测 ``_apply_security_headers()`` 的直接调用**不够** —— 那样无法证明它真的
    接在中间件链上（本项目反复出现"函数写好了但没接线"）。
    """
    for path in ("/health", "/health/live", "/api/v1/etf/overview"):
        r = client.get(path)
        for name, value in _EXPECTED.items():
            assert r.headers.get(name) == value, f"{path} 缺 {name}（实为 {r.headers.get(name)!r}）"

    # 404 也要带：安全头的作用面是"所有响应"，不是"成功的业务响应"
    r404 = client.get("/definitely-not-a-route")
    for name, value in _EXPECTED.items():
        assert r404.headers.get(name) == value, f"404 响应缺 {name}"


def test_header_set_matches_constant():
    """``_SECURITY_HEADERS`` 与断言表必须逐字一致（防单边改动导致测试恒真）。"""
    assert _SECURITY_HEADERS == _EXPECTED


def test_hsts_absent_outside_prod():
    """非 prod 不下发 HSTS。

    理由：非 prod 多为 ``http://localhost``，下发 ``max-age`` 会让浏览器把 localhost
    也强制升级到 https ⇒ **本地开发直接不可用**（且浏览器会缓存该策略，清起来很麻烦）。
    """
    r = Response()
    _apply_security_headers(r, SimpleNamespace(ENV="dev"))
    assert "Strict-Transport-Security" not in r.headers


def test_hsts_present_in_prod():
    """prod 必须下发 HSTS（防降级劫持）。"""
    r = Response()
    _apply_security_headers(r, SimpleNamespace(ENV="prod"))
    assert r.headers["Strict-Transport-Security"] == (
        "max-age=31536000; includeSubDomains")


def test_no_csp_by_design():
    """刻意不下发 CSP —— 断言"不在"以防后来者顺手加上。

    非 prod 的 ``/docs`` 用 Swagger UI（依赖内联脚本），任何可用 CSP 都会打坏它；
    而本服务是纯 JSON API，CSP 的收益远低于误伤风险。需要 CSP 应由前置网关按
    站点维度统一注入。
    """
    r = Response()
    _apply_security_headers(r, SimpleNamespace(ENV="prod"))
    assert "Content-Security-Policy" not in r.headers


def test_existing_header_not_overwritten():
    """幂等：端点/网关已显式设过的同名头不得被覆盖（否则无法按路由收紧）。"""
    r = Response()
    r.headers["X-Frame-Options"] = "SAMEORIGIN"
    _apply_security_headers(r, SimpleNamespace(ENV="prod"))
    assert r.headers["X-Frame-Options"] == "SAMEORIGIN"
    # 其余头仍应补齐
    assert r.headers["X-Content-Type-Options"] == "nosniff"
