"""P1-36（B7a-07）+ B7a-06 + P1-43 防回归：零数据不得造结论、异常串不外泄、探针必须能失败。"""

from __future__ import annotations

import sys
from pathlib import Path

import pandas as pd
import polars as pl
import pytest

BACKEND_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(BACKEND_ROOT))

from app.api.v1 import market as market_api  # noqa: E402

_ADMIN = {"Authorization": "Bearer aqp-dev-token-change-me"}


# ---------------- P1-36 / B7a-07：空涨跌分布不得报 ok ----------------

@pytest.mark.parametrize("pct, label", [
    (pd.Series([], dtype="float64"), "空集"),
    (pd.Series([float("nan")] * 5), "全 NaN"),
    (pl.Series("pct", [], dtype=pl.Float64), "polars 空集"),
    (pl.Series("pct", [None, None], dtype=pl.Float64), "polars 全 None"),
])
def test_heat_payload_without_samples_is_unavailable(pct, label):
    """**缺陷本体**：原实现返回 `status:"ok"` + 九个全 0 桶（"看似正常实则为空"）。"""
    out = market_api._heat_payload("local", pct)
    assert out["status"] == "unavailable", f"{label} 竟然报 ok：{out}"
    assert "reason" in out and out["source"] == "local"
    assert "up" not in out, "空样本不得给出 up/down/flat 这些会被误读为真值的计数"


def test_heat_payload_keeps_working_with_samples():
    """反向断言：有样本时行为不变（含桶/涨跌停近似）。"""
    out = market_api._heat_payload("em", pd.Series([1.0, -2.0, 0.0, 10.0, -10.0]))
    assert out["status"] == "ok"
    assert (out["up"], out["down"], out["flat"]) == (2, 2, 1)
    assert out["limit_up"] == 1 and out["limit_down"] == 1
    assert sum(out["buckets"].values()) == 5, out["buckets"]


def test_heat_extra_is_preserved_even_when_unavailable():
    """降级时仍保留有意义的口径字段（成交额/note），便于前端显示"空但成因清楚"。"""
    out = market_api._heat_payload("local", pd.Series([float("nan")]),
                                   {"total_amount_yi": 2.0, "note": "当年分区仅 1 行"})
    assert out["status"] == "unavailable"
    assert out["total_amount_yi"] == 2.0 and out["note"] == "当年分区仅 1 行"


def _zero_heat() -> dict:
    return {"status": "ok", "source": "local", "up": 0, "down": 0, "flat": 0,
            "limit_up": 0, "limit_down": 0,
            "buckets": {f"b{i}": 0 for i in range(1, 10)}}


def test_sentiment_never_invents_neutral_from_zero_samples():
    """**缺陷本体**：up=down=0 时原实现算出 50/中性（零数据造结论）。"""
    out = market_api._build_sentiment(_zero_heat())
    assert out["status"] == "unavailable", out
    assert "score" not in out and "label" not in out, "不得给出 50/中性"
    assert "样本" in out["reason"]


def test_sentiment_still_works_with_real_breadth():
    """反向断言：正常广度仍给出 score/label 与平台口径披露。"""
    heat = _zero_heat() | {"up": 60, "down": 20, "flat": 10}
    out = market_api._build_sentiment(heat)
    assert out["status"] == "ok" and 60 <= out["score"] <= 100
    assert out["label"] in ("乐观", "贪婪")
    assert out["kind"] == "platform" and "basis" in out


def test_sentiment_unavailable_when_heat_unavailable():
    out = market_api._build_sentiment({"status": "unavailable", "reason": "无样本"})
    assert out["status"] == "unavailable"


# ---------------- B7a-06：降级 reason 不得外泄内部异常串 ----------------

class _Boom(Exception):
    pass


def test_heat_degrade_reason_does_not_leak_exception(monkeypatch):
    """实测形态：`reason` 里出现 `_Boom: ConnectionError: ... RemoteDisconnected(...)`。"""
    def _raise(*a, **k):  # noqa: ANN002, ANN003
        raise _Boom("ConnectionError: ('Connection aborted.', "
                    "RemoteDisconnected('Remote end closed connection'))")

    monkeypatch.setattr(market_api, "_safe_call", _raise)
    monkeypatch.setattr(market_api, "_heat_from_local",
                        lambda *a, **k: {"status": "unavailable",
                                         "reason": "本地无日线数据"})
    out = market_api._build_heat()
    assert out["status"] == "unavailable"
    blob = str(out)
    assert "RemoteDisconnected" not in blob and "_Boom" not in blob, (
        f"内部异常串外泄：{out}")
    assert out["degraded_from"] == "em_snapshot"


def test_ai_stats_degrade_reason_does_not_leak_exception(monkeypatch):
    """同型点 `_build_ai_stats`：不得把异常串放进响应。"""
    monkeypatch.setattr(market_api, "_safe_call",
                        lambda *a, **k: (_ for _ in ()).throw(_Boom("RemoteDisconnected('x')")))
    out = market_api._build_ai_stats()
    assert out["status"] == "unavailable"
    assert "RemoteDisconnected" not in str(out) and "_Boom" not in str(out), out


# ---------------- P1-43：readiness 探针必须能"失败" ----------------

def test_readiness_returns_503_when_not_ready(monkeypatch):
    """**缺陷本体**：原实现经 `ok()` 包装 ⇒ 无论 checks 怎样都是 HTTP 200。"""
    from fastapi.testclient import TestClient

    from app.core.config import get_settings
    from app.main import app

    # DATA_ROOT 是**实例**属性（既有多数用例同样 setattr 到 get_settings() 实例上），
    # 打到类上会 AttributeError。
    monkeypatch.setattr(get_settings(), "DATA_ROOT",
                        Path("Z:/definitely-not-a-dir"))
    with TestClient(app) as c:
        r = c.get("/health/ready")
    assert r.status_code == 503, f"未就绪必须回 503，实为 {r.status_code}"
    body = r.json()
    assert body["code"] == 50300, body
    assert body["data"]["status"] == "not_ready"
    assert body["data"]["checks"]["data_root"] == "failed"
    assert r.headers.get("Retry-After") == "5"


def test_readiness_returns_200_and_liveness_stays_200():
    """就绪态 200；/health 与 /health/live 仍按各自语义（不误伤前端与既有测试）。"""
    from fastapi.testclient import TestClient

    from app.main import app

    with TestClient(app) as c:
        r = c.get("/health/ready")
        assert r.status_code == 200 and r.json()["code"] == 0, r.json()
        assert r.json()["data"]["status"] == "ready"
        assert c.get("/health").status_code == 200
        assert c.get("/health/live").status_code == 200


def test_readiness_anonymous_and_not_wrapped_by_business_envelope_rule():
    """探针必须匿名可达（RBAC 契约），且它是**唯一**允许非 200 的端点类别。"""
    from fastapi.testclient import TestClient

    from app.main import app

    with TestClient(app) as c:
        assert c.get("/health/ready").status_code in (200, 503)