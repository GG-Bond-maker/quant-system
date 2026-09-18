"""可观测性与模型治理 P1 回归测试。"""
from __future__ import annotations

import asyncio
import sqlite3
from pathlib import Path
from types import SimpleNamespace

import pytest
from fastapi import HTTPException
from fastapi.testclient import TestClient

from app.api.v1 import research
from app.core.errors import AQPException
from app.main import _metric_endpoint_template, app


def _request_for_metric(route_path: str | None) -> SimpleNamespace:
    """构造仅含 route scope 的请求替身，模拟 Starlette 匹配路由后的状态。"""
    route = SimpleNamespace(path=route_path) if route_path is not None else None
    return SimpleNamespace(scope={"route": route})


def test_metrics_endpoint_template_collapses_symbol_values() -> None:
    """不同证券代码必须归到同一模板标签，404 则使用固定低基数值。"""
    with TestClient(app) as client:
        client.headers.update({"Authorization": "Bearer aqp-dev-token-change-me"})
        client.get("/api/v1/stock/600519.SH/profile")
        client.get("/api/v1/stock/000001.SZ/profile")
        client.get("/not-a-real-route")
        metrics = client.get("/metrics").text

    template = '/api/v1/stock/{symbol}/profile'
    assert template in metrics
    assert 'endpoint="/api/v1/stock/600519.SH/profile"' not in metrics
    assert 'endpoint="/api/v1/stock/000001.SZ/profile"' not in metrics
    assert 'endpoint="/unmatched"' in metrics

    # helper 的无路由降级也单独固定，防后续维护中回退为 request.url.path。
    assert _metric_endpoint_template(_request_for_metric(None)) == "/unmatched"


def test_metrics_prod_requires_existing_bearer_auth(monkeypatch: pytest.MonkeyPatch) -> None:
    """prod 默认保护 /metrics，仍复用现有 Bearer 认证而非自造认证体系。"""
    from app import main

    monkeypatch.setattr(main, "get_settings", lambda: SimpleNamespace(metrics_require_auth=True))
    with pytest.raises(HTTPException) as exc_info:
        asyncio.run(main.prometheus_metrics(None))
    assert exc_info.value.detail == "UNAUTHORIZED"


def test_api_key_rotation_explicitly_disabled() -> None:
    """历史假 API Key 接口必须明确拒绝，不能返回伪成功凭证。"""
    from app.api.v1.app_settings import rotate_api_key

    response = asyncio.run(rotate_api_key({"role": "admin"}))

    assert response.code != 0
    assert "未启用" in response.message


def _registry_database(tmp_path: Path, version: str, model_path: Path) -> Path:
    """写入仅包含 registry 查询所需字段的临时数据库。"""
    sqlite_path = tmp_path / "aqp.db"
    with sqlite3.connect(sqlite_path) as connection:
        connection.execute(
            "CREATE TABLE model_registry (id INTEGER, version TEXT, model_path TEXT, is_production INTEGER)"
        )
        connection.execute(
            "INSERT INTO model_registry VALUES (1, ?, ?, 1)",
            (version, str(model_path)),
        )
    return sqlite_path


def test_production_feature_importance_uses_registry_model_path(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path,
) -> None:
    """研究接口只能使用 registry 的生产模型路径，不能拼接 models/exp。"""
    model_file = tmp_path / "prod" / "registered" / "model.lgbm"
    model_file.parent.mkdir(parents=True)
    model_file.write_text("model", encoding="utf-8")
    sqlite_path = _registry_database(tmp_path, "governed-v1", model_file)
    monkeypatch.setattr(research, "get_settings", lambda: SimpleNamespace(SQLITE_PATH=sqlite_path))

    version, path = research._resolve_production_lgbm()

    assert version == "governed-v1"
    assert path == model_file


def test_production_feature_importance_rejects_missing_registry_artifact(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path,
) -> None:
    """生产登记缺产物时返回明确业务错误，不应回退实验副本。"""
    missing_model = tmp_path / "exp" / "model.lgbm"
    sqlite_path = _registry_database(tmp_path, "broken-v1", missing_model)
    monkeypatch.setattr(research, "get_settings", lambda: SimpleNamespace(SQLITE_PATH=sqlite_path))

    with pytest.raises(AQPException, match="生产模型文件缺失"):
        research._resolve_production_lgbm()
