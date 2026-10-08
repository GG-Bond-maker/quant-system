"""系统设置 P0/P1/P2 演进重构功能端点测试。

覆盖：
1. A 股精细摩擦力（印花税/过户费/最低佣金/Almgren-Chriss 滑点）与基准参数落库；
2. Webhook 通道配置与联调测试端点；
3. 数据源主备路由与流控配置；
4. 开发者 API Key (PAT) 生成与撤销；
5. 运行环境算力探针与操作审计日志记录。
"""
from __future__ import annotations

import os
from fastapi.testclient import TestClient
from app.main import app

ADMIN_H = {"Authorization": f"Bearer {os.environ.get('ADMIN_TOKEN', 'aqp-dev-token-change-me')}"}


def test_settings_bundle_contains_evolution_fields():
    with TestClient(app) as client:
        r = client.get("/api/v1/settings", headers=ADMIN_H)
        assert r.status_code == 200
        body = r.json()
        assert body["code"] == 0
        data = body["data"]

        # 检查结构完整性
        assert "settings" in data
        assert "telemetry" in data
        assert "audit_logs" in data

        settings = data["settings"]
        engine = settings["engine"]
        assert "stamp_duty_pct" in engine
        assert "transfer_fee_pct" in engine
        assert "min_commission" in engine
        assert "slippage_model" in engine
        assert "default_benchmark" in engine
        assert "risk_free_rate" in engine

        assert "webhook" in settings
        assert "datasources" in settings
        assert "api_keys" in settings


def test_settings_engine_frictions_update_and_audit():
    with TestClient(app) as client:
        payload = {
            "name": "vectorbt",
            "commission_pct": 0.02,
            "commission_sell_pct": 0.02,
            "stamp_duty_pct": 0.05,
            "transfer_fee_pct": 0.001,
            "min_commission": 5.0,
            "slippage_model": "almgren_chriss",
            "slippage_pct": 0.08,
            "default_benchmark": "000852.SH",
            "risk_free_rate": 1.8,
            "t_plus_one": True,
            "limit_up_down_guard": True,
        }
        r = client.put("/api/v1/settings/engine", headers=ADMIN_H, json=payload)
        assert r.status_code == 200
        body = r.json()
        assert body["code"] == 0
        assert body["data"]["stamp_duty_pct"] == 0.05
        assert body["data"]["slippage_model"] == "almgren_chriss"
        assert body["data"]["default_benchmark"] == "000852.SH"

        # 检查审计日志是否记录
        r2 = client.get("/api/v1/settings", headers=ADMIN_H)
        logs = r2.json()["data"]["audit_logs"]
        assert len(logs) > 0
        assert any("摩擦力配置" in log["action"] for log in logs)


def test_settings_webhook_config_and_test_ping():
    with TestClient(app) as client:
        # 1. 保存 Webhook 配置
        wh_payload = {
            "enabled": True,
            "channel_type": "feishu",
            "webhook_url": "https://open.feishu.cn/open-apis/bot/v2/hook/mock-test-key",
            "events": ["backtest_done", "risk_alert"],
        }
        r = client.put("/api/v1/settings/webhook", headers=ADMIN_H, json=wh_payload)
        assert r.status_code == 200
        assert r.json()["code"] == 0
        assert r.json()["data"]["enabled"] is True

        # 2. 发起测试心跳
        test_r = client.post(
            "/api/v1/settings/webhook/test",
            headers=ADMIN_H,
            json={"channel_type": "feishu", "webhook_url": "https://open.feishu.cn/open-apis/bot/v2/hook/mock-test-key"},
        )
        assert test_r.status_code == 200
        assert test_r.json()["code"] == 0


def test_settings_datasources_update():
    with TestClient(app) as client:
        ds_payload = {
            "primary_source": "eastmoney",
            "fallback_source": "akshare",
            "request_timeout_sec": 20,
            "rate_limit_qps": 8,
        }
        r = client.put("/api/v1/settings/datasources", headers=ADMIN_H, json=ds_payload)
        assert r.status_code == 200
        assert r.json()["code"] == 0
        assert r.json()["data"]["rate_limit_qps"] == 8


def test_settings_api_keys_lifecycle():
    with TestClient(app) as client:
        # 1. 创建 API Key
        create_payload = {
            "name": "Test SDK Key",
            "permission": "read_only",
            "expires_days": 15,
        }
        r = client.post("/api/v1/settings/api-keys", headers=ADMIN_H, json=create_payload)
        assert r.status_code == 200
        body = r.json()
        assert body["code"] == 0
        key_id = body["data"]["key_id"]
        raw_token = body["data"]["raw_token"]
        assert raw_token.startswith("aqp_live_")
        assert key_id.startswith("key_")

        # 2. 查询 settings 包含掩码 key
        r_all = client.get("/api/v1/settings", headers=ADMIN_H)
        keys = r_all.json()["data"]["settings"]["api_keys"]
        target = next((k for k in keys if k["key_id"] == key_id), None)
        assert target is not None
        assert "raw_token" not in target  # 明文不存入配置表
        assert target["prefix"].endswith("****")

        # 3. 撤销 API Key
        del_r = client.delete(f"/api/v1/settings/api-keys/{key_id}", headers=ADMIN_H)
        assert del_r.status_code == 200
        assert del_r.json()["code"] == 0

        # 4. 再次查询已不存在
        r_after = client.get("/api/v1/settings", headers=ADMIN_H)
        keys_after = r_after.json()["data"]["settings"]["api_keys"]
        assert not any(k["key_id"] == key_id for k in keys_after)


def test_settings_telemetry_probe():
    with TestClient(app) as client:
        r = client.get("/api/v1/settings/telemetry", headers=ADMIN_H)
        assert r.status_code == 200
        body = r.json()
        assert body["code"] == 0
        data = body["data"]
        assert "python_version" in data
        assert "polars_version" in data
        assert "cpu_percent" in data
        assert "memory_used_gb" in data
