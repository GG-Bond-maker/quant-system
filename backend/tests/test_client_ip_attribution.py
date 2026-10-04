"""来源 IP 归因 与 登录「IP 维度」限速（2026-10-04 上线前全检残留项）。

背景
----
``X-Forwarded-For`` / ``X-Real-IP`` 都是**客户端可写**的请求头。旧实现无条件取 XFF
**首跳**当来源 IP，而 nginx 用的是 ``$proxy_add_x_forwarded_for``（语义 =
``"$http_x_forwarded_for, $remote_addr"``，**追加**）⇒ 首跳恰好就是**客户端自己塞
进去的值**。两个后果：

* 审计日志（OWASP A09 留痕）可被**投毒**成任意 IP；
* 若拿它做限速键，**一个请求头即可绕过**（"已防护"的错觉比不做更糟）。

本文件钉住两条契约：
  A. :func:`app.core.auth.client_ip_info` 只在**直连对端是已声明的受信代理**时
     才采信代理头，且把值校验为 IP 字面量（防日志注入）；
  B. 登录的 IP 维度限速是**双重门槛**：IP 必须可归因 **且** 运维显式打开
     ``LOGIN_IP_RATE_LIMIT``（默认关闭 —— 拓扑未知时按 IP 限速会把多个用户并进
     同一个桶，属**自伤**，比不做更糟）。
"""
from __future__ import annotations

import sys
from pathlib import Path
from types import SimpleNamespace

import pytest
from starlette.requests import Request

BACKEND_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(BACKEND_ROOT))

from app.api.v1 import auth as auth_api  # noqa: E402
from app.core import auth as auth_core  # noqa: E402
from app.core.auth import client_ip, client_ip_info  # noqa: E402
from app.core.errors import ERR_RATE_LIMITED, AQPException  # noqa: E402


def _req(headers: dict[str, str], peer: str | None) -> Request:
    """构造最小 ASGI scope 的 Request（不需要 TestClient / 数据库）。"""
    return Request({
        "type": "http", "method": "POST", "path": "/api/v1/auth/login",
        "query_string": b"",
        "headers": [(k.lower().encode("latin-1"), v.encode("latin-1"))
                    for k, v in headers.items()],
        "client": (peer, 12345) if peer else None,
    })


def _patch_cfg(monkeypatch: pytest.MonkeyPatch, *, trusted: str,
               ip_limit: bool) -> None:
    """把两处 ``get_settings`` 都换成可控假配置。

    必须**两处都换**：``core/auth`` 用它判定受信代理，``api/v1/auth`` 用它判定是否
    启用 IP 桶 —— 只换一处会让用例验证到错误的模块（判据 65 的同族陷阱）。
    """
    def _fake() -> SimpleNamespace:
        return SimpleNamespace(TRUSTED_PROXY_IPS=trusted,
                               LOGIN_IP_RATE_LIMIT=ip_limit)

    monkeypatch.setattr(auth_core, "get_settings", _fake)
    monkeypatch.setattr(auth_api, "get_settings", _fake)


@pytest.fixture(autouse=True)
def _clean_buckets():
    """限速桶是模块级可变状态，用例之间必须隔离。"""
    auth_api._login_attempts.clear()
    auth_api._login_attempts_by_ip.clear()
    yield
    auth_api._login_attempts.clear()
    auth_api._login_attempts_by_ip.clear()


# ---------------- A. 归因契约 ----------------
@pytest.mark.parametrize("headers", [
    {"x-forwarded-for": "1.2.3.4"},
    {"x-real-ip": "1.2.3.4"},
    {"x-forwarded-for": "1.2.3.4, 5.6.7.8"},
])
def test_untrusted_peer_ignores_proxy_headers(
    monkeypatch: pytest.MonkeyPatch, headers: dict[str, str],
) -> None:
    """未声明任何受信代理 ⇒ 代理头**一律不采信**，只用 socket 对端。

    这是本项修复的核心断言：修复前 ``client_ip`` 会返回 ``"1.2.3.4"``（客户端自选）。
    """
    _patch_cfg(monkeypatch, trusted="", ip_limit=False)
    req = _req(headers, "9.9.9.9")
    assert client_ip_info(req) == ("9.9.9.9", True)
    assert client_ip(req) == "9.9.9.9"


def test_trusted_proxy_prefers_x_real_ip(monkeypatch: pytest.MonkeyPatch) -> None:
    """对端是受信代理 ⇒ 优先 ``X-Real-IP``（nginx 用 ``$remote_addr`` **覆写**）。"""
    _patch_cfg(monkeypatch, trusted="127.0.0.1", ip_limit=True)
    req = _req({"x-forwarded-for": "1.2.3.4", "x-real-ip": "203.0.113.7"}, "127.0.0.1")
    assert client_ip_info(req) == ("203.0.113.7", True)


def test_trusted_proxy_takes_last_hop_of_xff(monkeypatch: pytest.MonkeyPatch) -> None:
    """只有 XFF 时取**尾跳** —— nginx 的 ``$proxy_add_x_forwarded_for`` 是**追加**，
    尾跳才是真实对端；取首跳等于取客户端伪造值。"""
    _patch_cfg(monkeypatch, trusted="172.18.0.0/16", ip_limit=True)
    req = _req({"x-forwarded-for": "1.2.3.4, 203.0.113.7"}, "172.18.0.5")
    assert client_ip_info(req) == ("203.0.113.7", True)


def test_trusted_proxy_without_usable_header_is_not_attributable(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """受信代理但没带可解析的头 ⇒ 只剩代理自身地址，**不可归因**（不得当限速键）。"""
    _patch_cfg(monkeypatch, trusted="127.0.0.1", ip_limit=True)
    assert client_ip_info(_req({}, "127.0.0.1")) == ("127.0.0.1", False)


@pytest.mark.parametrize("bad", [
    # 日志注入：HTTP 头只能是 latin-1，攻击者用 ASCII 即可伪造出整行日志
    "1.2.3.4\n[FAKE] INFO login ok user='admin'",
    "not-an-ip",
    "1.2.3.4,",                                     # 尾跳为空
])
def test_header_value_must_be_ip_literal(
    monkeypatch: pytest.MonkeyPatch, bad: str,
) -> None:
    """头里的值会进审计日志 ⇒ 必须校验为 IP 字面量，否则可**伪造整行日志**。"""
    _patch_cfg(monkeypatch, trusted="127.0.0.1", ip_limit=True)
    ip, ok = client_ip_info(_req({"x-real-ip": bad}, "127.0.0.1"))
    assert (ip, ok) == ("127.0.0.1", False), f"非法值 {bad!r} 不得被采信"


def test_missing_client_info(monkeypatch: pytest.MonkeyPatch) -> None:
    _patch_cfg(monkeypatch, trusted="", ip_limit=True)
    assert client_ip_info(None) == ("-", False)
    assert client_ip_info(_req({}, None)) == ("-", False)


# ---------------- B. IP 维度限速的双重门槛 ----------------
@pytest.mark.parametrize("ip,attributable,ip_limit,expected", [
    ("203.0.113.7", True, True, "203.0.113.7"),   # 两道门都过
    ("203.0.113.7", True, False, None),           # 拓扑未声明 ⇒ 不启用
    ("203.0.113.7", False, True, None),           # 不可归因 ⇒ 不能当键
    ("-", True, True, None),
    ("", True, True, None),
])
def test_ip_limit_key_requires_both_gates(
    monkeypatch: pytest.MonkeyPatch, ip: str, attributable: bool,
    ip_limit: bool, expected: str | None,
) -> None:
    _patch_cfg(monkeypatch, trusted="", ip_limit=ip_limit)
    assert auth_api._ip_limit_key(ip, attributable) == expected


def test_ip_bucket_blocks_username_spraying(monkeypatch: pytest.MonkeyPatch) -> None:
    """IP 桶存在的**唯一理由**：单账号桶拦不住"换用户名喷洒"。

    每个用户名只失败 1 次 ⇒ 账号桶永远到不了阈值；同一 IP 累计到阈值必须被拦。
    """
    _patch_cfg(monkeypatch, trusted="", ip_limit=True)
    key = "203.0.113.7"
    for i in range(auth_api._MAX_IP_ATTEMPTS):
        auth_api._check_rate_limit(f"user{i}", key, ip_key=key)
        auth_api._record_attempt(f"user{i}", False, ip_key=key)
    # 前置条件：账号桶确实都没到阈值（否则本用例证明不了 IP 桶的作用）
    assert all(len(v) == 1 for v in auth_api._login_attempts.values())

    with pytest.raises(AQPException) as ei:
        auth_api._check_rate_limit("user-final", key, ip_key=key)
    assert ei.value.code == ERR_RATE_LIMITED


def test_ip_bucket_disabled_by_default(monkeypatch: pytest.MonkeyPatch) -> None:
    """开关关闭（默认）时 IP 桶不生效 —— 换用户名喷洒**不会**被拦（现状保持）。"""
    _patch_cfg(monkeypatch, trusted="", ip_limit=False)
    key = "203.0.113.7"
    for i in range(auth_api._MAX_IP_ATTEMPTS * 2):
        # ip_key 由 _ip_limit_key 决定，此处即"开关关闭"下的取值
        k = auth_api._ip_limit_key(key, True)
        assert k is None
        auth_api._check_rate_limit(f"user{i}", key, ip_key=k)
        auth_api._record_attempt(f"user{i}", False, ip_key=k)
    assert auth_api._login_attempts_by_ip == {}


def test_successful_login_does_not_clear_ip_bucket(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """成功登录只清**账号**桶；IP 桶保留 —— 否则攻击者用一个合法账号就能洗白自己的 IP。"""
    _patch_cfg(monkeypatch, trusted="", ip_limit=True)
    key = "198.51.100.9"
    auth_api._record_attempt("victim", False, ip_key=key)
    auth_api._record_attempt("victim", True)
    assert auth_api._login_attempts == {}
    assert len(auth_api._login_attempts_by_ip.get(key, [])) == 1
