"""P0-2 生产认证配置安全门禁。"""
from __future__ import annotations

import pytest

from app.core.config import DEFAULT_ADMIN_TOKEN, get_settings


@pytest.mark.parametrize("overrides", [
    {"ADMIN_TOKEN": DEFAULT_ADMIN_TOKEN},
    {"ADMIN_TOKEN": "short"},
    {"JWT_SECRET": None},
    {"JWT_SECRET": "short"},
    {"ALLOW_ADMIN_TOKEN_LOGIN": True},
    {"ALLOW_REGISTRATION": True},
    {"CORS_ORIGINS": "http://localhost:5173"},
    # 2026-09-30 全检 P1-d：RBAC_ENFORCE=false 此前完全未纳入 prod 校验，
    # 意味着生产可带 False 启动 ⇒ 任何已登录用户等同于管理员。现纳入 fail-fast。
    {"RBAC_ENFORCE": False},
])
def test_prod_security_policy_rejects_weak_values(monkeypatch, overrides):
    """生产弱配置必须在启动前失败，而不是只记录 warning。"""
    monkeypatch.setenv("ENV", "prod")
    monkeypatch.setenv("ADMIN_TOKEN", overrides.get("ADMIN_TOKEN", "A" * 48))
    monkeypatch.setenv("JWT_SECRET", overrides.get("JWT_SECRET", "J" * 48) or "")
    monkeypatch.setenv("ALLOW_ADMIN_TOKEN_LOGIN",
                       str(overrides.get("ALLOW_ADMIN_TOKEN_LOGIN", False)).lower())
    monkeypatch.setenv("ALLOW_REGISTRATION",
                       str(overrides.get("ALLOW_REGISTRATION", False)).lower())
    monkeypatch.setenv("CORS_ORIGINS", overrides.get("CORS_ORIGINS", "https://app.example.com"))
    # 默认给 true，使 RBAC 成为**被单独测试**的那一项（其余用例不受其影响）
    monkeypatch.setenv("RBAC_ENFORCE",
                       str(overrides.get("RBAC_ENFORCE", True)).lower())
    get_settings.cache_clear()
    with pytest.raises(ValueError, match="生产安全配置不合格"):
        get_settings()
    get_settings.cache_clear()


def test_prod_security_policy_accepts_strong_values(monkeypatch):
    monkeypatch.setenv("ENV", "prod")
    monkeypatch.setenv("ADMIN_TOKEN", "A" * 48)
    monkeypatch.setenv("JWT_SECRET", "J" * 48)
    monkeypatch.setenv("ALLOW_ADMIN_TOKEN_LOGIN", "false")
    monkeypatch.setenv("ALLOW_REGISTRATION", "false")
    monkeypatch.setenv("CORS_ORIGINS", "https://app.example.com")
    # 2026-09-30 全检 P1-d：prod 现要求 RBAC_ENFORCE=true
    monkeypatch.setenv("RBAC_ENFORCE", "true")
    get_settings.cache_clear()
    assert get_settings().ENV == "prod"
    get_settings.cache_clear()


def test_prod_rbac_unenforced_requires_explicit_optin(monkeypatch):
    """2026-09-30 全检 P1-d：放开 RBAC 必须**显式声明**，不能靠默认值静默通过。

    两条断言：
    ① 未设 AQP_ALLOW_UNENFORCED_RBAC 时，prod + RBAC_ENFORCE=false ⇒ fail-fast；
    ② 显式设该变量后 ⇒ 放行（留下审计痕迹的逃生舱）。
    """
    monkeypatch.setenv("ENV", "prod")
    monkeypatch.setenv("ADMIN_TOKEN", "A" * 48)
    monkeypatch.setenv("JWT_SECRET", "J" * 48)
    monkeypatch.setenv("ALLOW_ADMIN_TOKEN_LOGIN", "false")
    monkeypatch.setenv("ALLOW_REGISTRATION", "false")
    monkeypatch.setenv("CORS_ORIGINS", "https://app.example.com")
    monkeypatch.setenv("RBAC_ENFORCE", "false")

    monkeypatch.delenv("AQP_ALLOW_UNENFORCED_RBAC", raising=False)
    get_settings.cache_clear()
    with pytest.raises(ValueError, match="RBAC_ENFORCE 必须为 true"):
        get_settings()
    get_settings.cache_clear()

    monkeypatch.setenv("AQP_ALLOW_UNENFORCED_RBAC", "1")
    get_settings.cache_clear()
    assert get_settings().RBAC_ENFORCE is False  # 显式知悉风险 ⇒ 放行
    get_settings.cache_clear()
