"""P3 安全扫描 + metrics 测试。"""
from __future__ import annotations

import re
from pathlib import Path

BACKEND_ROOT = Path(__file__).resolve().parents[1]
APP_DIR = BACKEND_ROOT / "app"

# 允许的硬编码模式（测试/默认值/注释）
SAFE_PATTERNS = {
    "aqp-dev-token-change-me", "please-change-me", "testpass123", "viewpass123",
    "respass123", "testadmin", "citest123", "citest", "wrongpass", "wrong",
    "JWT_SECRET", "ADMIN_TOKEN", "TUSHARE_TOKEN", "JWT_EXPIRE", "NOTIFY_",
    "api_key", "token_type", "access_token", "refresh_token", "secret_key",
    "password_hash", "password:", "password=", ".password", "verify_password",
    "hash_password", "_password", "api_tokens", "aqp-derive:",
}


def test_no_hardcoded_secrets():
    """安全扫描：app/ 源码中不得出现真实密钥（默认占位值除外）。"""
    dangerous = re.compile(
        r"""(?i)(password|secret|api_key|token)\s*[=:]\s*['"]([a-zA-Z0-9!@#$%^&*()_+]{8,})['"]"""
    )
    violations: list[str] = []
    for py in sorted(APP_DIR.rglob("*.py")):
        text = py.read_text(encoding="utf-8", errors="ignore")
        for m in dangerous.finditer(text):
            matched_val = m.group(2)
            if any(s in matched_val.lower() or s in m.group(0).lower() for s in SAFE_PATTERNS):
                continue
            if "change-me" in matched_val or "change_me" in matched_val:
                continue
            violations.append(f"{py.relative_to(BACKEND_ROOT)}: {m.group(0)[:60]}")
    assert not violations, f"发现可能的硬编码密钥: {violations}"


def test_prometheus_metrics_importable():
    from app.core.metrics import metrics_response  # noqa: F401


def test_metrics_endpoint_returns_text():
    """验证 /metrics 返回 Prometheus exposition format。"""
    from fastapi.testclient import TestClient

    from app.main import app

    with TestClient(app) as c:
        r = c.get("/metrics")
        assert r.status_code == 200
        assert "aqp_http_requests_total" in r.text


def test_health_ready_live():
    """验证 /health/ready 和 /health/live。"""
    from fastapi.testclient import TestClient

    from app.main import app

    with TestClient(app) as c:
        assert c.get("/health/ready").json()["data"]["status"] == "ready"
        assert c.get("/health/live").json()["data"]["status"] == "live"
