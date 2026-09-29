"""审计 P1-32 / R9 防回归：外部数据源不可用必须是 51000，不得逃逸成 50000。

背景（2026-09-21 全栈审计）：
  * `data/realtime._request` 在重试耗尽后 `raise RuntimeError`，该异常穿过数据层
    一路逃逸，最终落到全局兜底 `code=50000`（未分类系统异常）。前端因此无法区分
    「外部行情源暂时不可用」与「平台自身故障」——前者应展示降级/稍后重试，后者
    才应告警。
  * 同一根因导致 `tests/test_read_endpoints_rbac.py` 的 3 条 ETF 读端点用例失败
    （`RuntimeError` 直接从 TestClient 抛出，端点行为不可测）。
  * `ERR_DATA_SOURCE = 51000` 此前**没有任何抛出点**（详见审计 §8.1 R9）。

修复：新增 `core.errors.DataSourceUnavailable(AQPException, RuntimeError)`，
在 `realtime._request` / `_first_source` 抛出。本文件把该契约钉死。
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

BACKEND_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(BACKEND_ROOT))

from app.core.errors import (  # noqa: E402
    ERR_DATA_SOURCE,
    ERR_SYSTEM,
    AQPException,
    DataSourceUnavailable,
    register_error_handlers,
)
from app.data import realtime  # noqa: E402


@pytest.fixture(autouse=True)
def _no_sleep(monkeypatch: pytest.MonkeyPatch) -> None:
    """去掉限速与退避等待，保持用例毫秒级。"""
    monkeypatch.setattr(realtime, "_MIN_INTERVAL", 0.0)
    monkeypatch.setattr(realtime, "_INTERVAL_JITTER", 0.0)
    monkeypatch.setattr(realtime, "_BACKOFF", 0.0)


def test_request_retry_exhausted_raises_typed_error(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """重试耗尽 -> DataSourceUnavailable，且码值/继承关系都符合契约。"""
    import httpx

    def _boom(*_a: object, **_k: object) -> object:
        raise httpx.ConnectError("no route to host")

    monkeypatch.setattr(httpx, "request", _boom)

    with pytest.raises(DataSourceUnavailable) as ei:
        realtime._request("GET", "http://example.invalid/quote", retries=2)

    err = ei.value
    assert isinstance(err, AQPException), "必须能被全局 AQP 处理器接住"
    assert isinstance(err, RuntimeError), "必须兼容历史 except RuntimeError 降级点"
    assert err.code == ERR_DATA_SOURCE == 51000
    assert "外部数据源请求失败" in err.message


def test_first_source_all_failed_raises_typed_error() -> None:
    """多源全部失败 -> 同样是 51000（而不是裸 RuntimeError）。"""
    def _fail_a() -> object:
        raise RuntimeError("source a down")

    def _fail_b() -> object:
        raise RuntimeError("source b down")

    with pytest.raises(DataSourceUnavailable) as ei:
        realtime._first_source([("a", _fail_a), ("b", _fail_b)], "北向资金")
    assert ei.value.code == ERR_DATA_SOURCE
    assert "全部数据源失败" in ei.value.message
    assert "a:RuntimeError" in ei.value.message


def test_envelope_maps_data_source_error_to_51000_not_50000() -> None:
    """端到端：注册了处理器后，该异常返回 code=51000 且 HTTP 恒 200。

    这是本文件的核心断言——回归时会表现为 `code == 50000`，与「平台故障」混淆。
    """
    from fastapi import FastAPI
    from fastapi.testclient import TestClient

    probe = FastAPI()
    register_error_handlers(probe)

    @probe.get("/boom")
    async def _boom_endpoint() -> object:  # pragma: no cover - 仅抛异常
        raise DataSourceUnavailable("外部数据源请求失败: ConnectError")

    with TestClient(probe) as c:
        r = c.get("/boom")

    body = r.json()
    assert r.status_code == 200, "契约：HTTP 恒 200"
    assert body["code"] == ERR_DATA_SOURCE
    assert body["code"] != ERR_SYSTEM, "回归锚点：不得再退化成 50000"
    assert "外部数据源" in body["message"]