"""P1-35（B7a-05）防回归：**日历非法**的日期参数必须是 40000，不是 50000。

## 缺陷

日期参数普遍只用 `Query(pattern=r"^\\d{8}$")` / `^\\d{4}-\\d{2}-\\d{2}$` 校验**形状**，
于是 `20269999`（年=2026、月=99）、`2026-02-30` 这类"形状对、日历错"的输入穿到
`date(...)` / `date.fromisoformat(...)` 才抛 `ValueError`，被全局异常处理器归成
`code=50000`（未分类**系统**异常）——纯参数错误被报成平台故障，前端无法区分
"我传错了"与"服务挂了"（实测三处端点全为 50000）。

另有一个更糟的变体：`market/overview?date=20269999` 反而 `code=0` ——
`_build_overview_hist` 把历史推荐切换的 `ValueError` 吞进 `except` 后**保留最新榜**，
调用方再把 `trade_date` 改成请求日 ⇒ **最新推荐榜被错标成请求日期**。

## 修法

1. 新增 `core/params.py`：`parse_iso_date` / `parse_yyyymmdd` 严格解析，
   非法一律 `AQPException(ERR_PARAMS=40000)`；四处调用点统一改用它；
2. 历史路径不再错标：`trade_date` 恒为实时块真实所属交易日 `td`，请求日放
   `requested_date`，推荐榜回退时在块内标 `fallback="latest"` + `fallback_reason`。
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

BACKEND_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(BACKEND_ROOT))

from app.core.errors import ERR_PARAMS, AQPException  # noqa: E402
from app.core.params import parse_iso_date, parse_yyyymmdd  # noqa: E402

_ADMIN = {"Authorization": "Bearer aqp-dev-token-change-me"}


# ---------------- 1. 解析器本身 ----------------

@pytest.mark.parametrize("bad", ["20269999", "20260230", "20261301", "00000000",
                                 "2026021", "202602181", "abcdefgh"])
def test_parse_yyyymmdd_rejects_calendar_illegal(bad):
    """含**长度不足**的 `"2026021"`：切片会把它当 `date(2026, 2, 1)` 静默接受。"""
    with pytest.raises(AQPException) as ei:
        parse_yyyymmdd(bad, field="start")
    assert ei.value.code == ERR_PARAMS
    assert "start" in ei.value.message and bad in ei.value.message


@pytest.mark.parametrize("bad", ["2026-02-30", "2026-99-99", "2026-13-01",
                                 "20260218", "2026-2-1"])
def test_parse_iso_date_rejects_calendar_illegal(bad):
    with pytest.raises(AQPException) as ei:
        parse_iso_date(bad, field="date")
    assert ei.value.code == ERR_PARAMS


def test_parse_accepts_real_dates():
    assert parse_yyyymmdd("20260228").isoformat() == "2026-02-28"
    assert parse_iso_date("2024-02-29").isoformat() == "2024-02-29"   # 闰年
    with pytest.raises(AQPException):        # 非闰年 2-29 必须拒
        parse_iso_date("2026-02-29")


# ---------------- 2. 端点层：必须是 40000 ----------------

@pytest.fixture(scope="module")
def client():
    from fastapi.testclient import TestClient

    from app.main import app

    with TestClient(app) as c:
        yield c


def _code(r) -> int:
    assert r.status_code == 200, "业务端点契约：HTTP 恒 200（探针除外）"
    return r.json()["code"]


def test_kline_illegal_start_is_params_error(client):
    """`kline?start=20269999` 原为 50000（date() 抛 ValueError）。"""
    r = client.get("/api/v1/stock/600519.SH/kline",
                   params={"start": "20269999", "end": "20260101"}, headers=_ADMIN)
    assert _code(r) == ERR_PARAMS, r.json()


def test_kline_illegal_end_is_params_error(client):
    r = client.get("/api/v1/stock/600519.SH/kline",
                   params={"start": "20260101", "end": "20260230"}, headers=_ADMIN)
    assert _code(r) == ERR_PARAMS, r.json()


def test_screener_illegal_date_is_params_error(client):
    """`screener?date=2026-02-30` 原为 50000（fromisoformat 抛 ValueError）。"""
    r = client.get("/api/v1/screener", params={"date": "2026-02-30"}, headers=_ADMIN)
    assert _code(r) == ERR_PARAMS, r.json()


@pytest.mark.parametrize("path", ["/api/v1/market/overview",
                                  "/api/v1/market/overview/daily"])
def test_overview_illegal_date_is_params_error(client, path):
    """`overview?date=20269999` 原为 **code=0 + 错标日期**；daily 版原为 50000。"""
    r = client.get(path, params={"date": "20269999"}, headers=_ADMIN)
    assert _code(r) == ERR_PARAMS, r.json()


# ---------------- 3. 历史路径不得错标 ----------------

def test_hist_path_does_not_mislabel_trade_date(client):
    """合法历史日：`trade_date` 必须是**实时块真实所属交易日**，请求日另放字段。"""
    from app.data.parquet_store import today_trade_date_or_last

    r = client.get("/api/v1/market/overview",
                   params={"date": "20260101"}, headers=_ADMIN)
    body = r.json()
    assert body["code"] == 0, body
    data = body["data"]
    assert data["trade_date"] == today_trade_date_or_last().strftime("%Y%m%d"), (
        "trade_date 是实时块（指数/涨跌分布/资金）的日期，不得回显请求日")
    assert data["requested_date"] == "20260101"
    assert data["from_cache"] is False


def test_hist_recommend_fallback_is_disclosed():
    """推荐快照不可用时：保留最新榜**但必须标注** fallback（原来只写 debug 日志）。"""
    from app.api.v1 import market as market_api

    called: dict = {}

    def _boom(k, target=None):
        # 最新榜（target=None）成功；请求日的那次失败 —— 复现真实降级路径
        # （`_build_overview(td, ...)` 内部也会调它，所以不能无差别抛）。
        if target is None:
            return {"status": "ok", "items": [], "as_of": None, "kind": "platform"}
        called["target"] = target
        raise RuntimeError("no snapshot for that day")

    orig = market_api._build_recommend
    market_api._build_recommend = _boom          # type: ignore[assignment]
    try:
        data = market_api._build_overview_hist(
            __import__("datetime").date(2026, 9, 18), 50,
            __import__("datetime").date(2026, 1, 1))
    finally:
        market_api._build_recommend = orig        # type: ignore[assignment]
    rec = data.get("recommend") or {}
    assert called["target"].isoformat() == "2026-01-01", "必须按请求日尝试，而非静默用最新"
    assert rec.get("fallback") == "latest", rec
    assert rec.get("requested_date") == "20260101"
    assert rec.get("fallback_reason") == "RuntimeError"