"""/api/v1/datacenter/sync/auto 契约测试（防断链回归）。

背景（2026-09-11 审核发现）：该端点在 09aea21 引入、在 732f9ec（datacenter
同步编排下沉 services）重构中丢失路由注册，前端数据中心页的 autoSync 开关
一直 404，而前端静默 catch 导致用户无感知。本测试固化 GET/POST 契约。

配置写 DATA_ROOT.parent/.auto_sync.json，测试环境下 DATA_ROOT 已被 conftest
重定向到临时目录，不会污染生产配置。
"""
from __future__ import annotations

import os

import pytest
from fastapi.testclient import TestClient

from app.main import app  # noqa: E402

_ADMIN = {"Authorization": f"Bearer {os.environ.get('ADMIN_TOKEN', 'aqp-dev-token-change-me')}"}
pytestmark = pytest.mark.filterwarnings("ignore::DeprecationWarning")


def test_auto_sync_config_roundtrip() -> None:
    """GET 读取配置 → POST 修改 → 非法时间被拒 → 还原。"""
    with TestClient(app) as c:
        r = c.get("/api/v1/datacenter/sync/auto", headers=_ADMIN)
        assert r.status_code == 200
        body = r.json()
        assert body["code"] == 0, f"GET sync/auto 失败：{body}"
        data = body["data"]
        assert {"enabled", "time", "today_done"} <= set(data), data
        origin = {"enabled": data["enabled"], "time": data["time"]}

        try:
            r2 = c.post("/api/v1/datacenter/sync/auto", headers=_ADMIN,
                        json={"enabled": False, "time": "16:30"})
            assert r2.json()["code"] == 0, r2.json()
            assert r2.json()["data"]["time"] == "16:30"
            assert r2.json()["data"]["enabled"] is False

            # 非法时间必须拒绝：调度器按 "HH:MM" 字符串比较触发，
            # 放过错误格式会造成"永不触发"的静默故障
            r3 = c.post("/api/v1/datacenter/sync/auto", headers=_ADMIN,
                        json={"enabled": True, "time": "25:99"})
            assert r3.json()["code"] != 0, "非法时间未被拒绝"

            # 回读确认持久化生效
            r4 = c.get("/api/v1/datacenter/sync/auto", headers=_ADMIN)
            assert r4.json()["data"]["time"] == "16:30"
        finally:
            c.post("/api/v1/datacenter/sync/auto", headers=_ADMIN, json=origin)
