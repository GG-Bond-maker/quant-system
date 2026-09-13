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
    {"CORS_ORIGINS": "http://localhost:5173"},
])
def test_prod_security_policy_rejects_weak_values(monkeypatch, overrides):
    """生产弱配置必须在启动前失败，而不是只记录 warning。"""
    monkeypatch.setenv("ENV", "prod")
    monkeypatch.setenv("ADMIN_TOKEN", overrides.get("ADMIN_TOKEN", "A" * 48))
    monkeypatch.setenv("JWT_SECRET", overrides.get("JWT_SECRET", "J" * 48) or "")
    monkeypatch.setenv("ALLOW_ADMIN_TOKEN_LOGIN",
                       str(overrides.get("ALLOW_ADMIN_TOKEN_LOGIN", False)).lower())
    monkeypatch.setenv("CORS_ORIGINS", overrides.get("CORS_ORIGINS", "https://app.example.com"))
    get_settings.cache_clear()
    with pytest.raises(ValueError, match="生产安全配置不合格"):
        get_settings()
    get_settings.cache_clear()


def test_prod_security_policy_accepts_strong_values(monkeypatch):
    monkeypatch.setenv("ENV", "prod")
    monkeypatch.setenv("ADMIN_TOKEN", "A" * 48)
    monkeypatch.setenv("JWT_SECRET", "J" * 48)
    monkeypatch.setenv("ALLOW_ADMIN_TOKEN_LOGIN", "false")
    monkeypatch.setenv("CORS_ORIGINS", "https://app.example.com")
    get_settings.cache_clear()
    assert get_settings().ENV == "prod"
    get_settings.cache_clear()
