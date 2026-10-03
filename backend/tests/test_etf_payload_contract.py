"""``/etf/*`` 响应字段集合契约测试（A-1 / A-5 修复，2026-10-02）。

## 守的是什么

审计 A-1：全站 **67 处** ``response_model=APIResponse[dict]``（含 ``/etf/*`` 全部 8 个端点），
``dict`` 意味着**字段契约完全由前端 TS 单方面声明** —— 后端删/改/重命名字段不会有任何
编译期或运行期报错，前端只会静默拿到 ``undefined``。这是"前后端接口一一对应"最薄弱的环节。

审计 A-5：成对方法共用同一路径（GET 读 / POST 写）时，两侧都返回 ``APIResponse[dict]``
且字段名相近，前端极易把"读"的返回类型套到"写"上。

本文件采取审计给出的**"至少"方案**：为 ``/etf/*`` 高频域冻结**字段集合快照**。
任何字段的增删改都会让测试 FAIL ⇒ 强制走一次"有意为之"的评审，堵住静默失效通道。

## 测试口径（对齐本仓既有契约测试纪律）

被测对象是**载荷装配**，不是内部真实 IO 聚合 ⇒ 对数据源用 stub 属**合法依赖注入**
（与 ``tests/test_rt_payload_status_contract.py`` 对 ``_build_rt`` 打桩同一口径）。

🔴 **必须同时锁「正常路径」与「降级路径」两张形状** —— 实测 ``/etf/flow`` 两者**字段不同**
（正常路径有 ``truncation_basis`` 无 ``reason``；降级路径反之）。只锁一张会漏掉
"降级时前端读不到字段"这一类真实事故。

⚠️ 这些断言是**快照**，不是"正确性证明"：它保证的是"形状变化被看见"，
不是"形状本身合理"。改契约时请**同时**改前端 TS 类型并在此显式更新期望集合。
"""
from __future__ import annotations

from typing import Any

import pytest

from app.api.v1 import etf as etf_api

# --------------------------------------------------------------------------- #
# 期望的字段集合（快照 —— 改契约必须显式改这里）
# --------------------------------------------------------------------------- #

# /overview 的**降级**形状：today 是"结构完整但全 null"的诚实占位，
# 前端据此渲染黄条而不是把 null 当 0。
_OVERVIEW_DEGRADED_TOP = {"today", "prev", "data_freshness"}
_OVERVIEW_DEGRADED_TODAY = {
    "etf_count", "total_size_yi", "avg_pct", "net_inflow_yi", "amount_yi",
    "overseas", "date", "status", "reason",
}

_LIST_KEYS = {
    "total", "page", "page_size", "items", "options", "sort_applied", "dir_applied",
}
_LIST_OPTIONS_KEYS = {"boards", "types", "indexes", "managers"}

# /scale：正常与降级**共用** period/sample_size/points/note/scope/scope_note，
# 降级另加 status/reason/data_freshness。
_SCALE_CORE = {"period", "sample_size", "points", "note", "scope", "scope_note"}
_SCALE_DEGRADED_EXTRA = {"status", "reason", "data_freshness"}

# /flow：两张形状**不同**（见文件头说明）
_FLOW_OK_KEYS = {
    "period", "items", "status", "returned", "limit", "total", "truncated",
    "scope", "scope_note", "truncation_basis",
}
_FLOW_DEGRADED_KEYS = {
    "period", "items", "status", "reason", "returned", "limit", "total",
    "truncated", "scope", "scope_note", "data_freshness",
}


def _data(resp: Any) -> dict:
    """取出统一信封里的 data 载荷。"""
    return resp.data


# --------------------------------------------------------------------------- #
# /overview
# --------------------------------------------------------------------------- #
@pytest.mark.asyncio
async def test_overview_degraded_field_set(monkeypatch):
    """数据源不可用时，/overview 的降级载荷必须结构完整（不是空对象）。"""

    async def _force_fallback(_key, _build, fallback):
        return fallback("stub: 数据源不可用")

    monkeypatch.setattr(etf_api, "_cached_etf_payload", _force_fallback)

    d = _data(await etf_api.etf_overview(_user={"role": "viewer"}))

    assert set(d) == _OVERVIEW_DEGRADED_TOP
    assert set(d["today"]) == _OVERVIEW_DEGRADED_TODAY
    # 降级时数值必须是 None（不可得），绝不用 0 冒充
    assert d["today"]["etf_count"] is None
    assert d["today"]["status"] == "unavailable"
    assert d["prev"] is None


# --------------------------------------------------------------------------- #
# /list
# --------------------------------------------------------------------------- #
@pytest.mark.asyncio
async def test_list_field_set(monkeypatch):
    """列表载荷的字段集合 + options 子结构。"""
    fake_cat = [{
        "code": "510300", "name": "沪深300ETF", "country": "cn", "board": "宽基ETF",
        "type": "股票型", "tracking_index": "沪深300", "manager": "华泰柏瑞",
        "size_yi": 100.0, "amount": 1e8, "pct": 0.5, "price": 4.0,
    }]
    monkeypatch.setattr(etf_api.E, "build_catalog", lambda: fake_cat)
    monkeypatch.setattr(etf_api, "_filter_catalog", lambda **kw: list(fake_cat))

    d = _data(await etf_api.etf_list(
        country="all", board="all", etype="all", index=None, manager=None, q=None,
        min_size=None, max_size=None, inception_from=None, inception_to=None,
        sort="size", sort_dir="desc", page=1, page_size=20, _user={"role": "viewer"},
    ))

    assert set(d) == _LIST_KEYS
    assert set(d["options"]) == _LIST_OPTIONS_KEYS
    # 排序白名单外的值必须回落并**回显**（否则前端无法知道实际口径）
    assert d["sort_applied"] and d["dir_applied"]


# --------------------------------------------------------------------------- #
# /scale
# --------------------------------------------------------------------------- #
def _fake_scale_catalog() -> list[dict]:
    return [
        {"code": "510300", "country": "cn", "size_yi": 300.0, "price": 4.0},
        {"code": "510500", "country": "cn", "size_yi": 200.0, "price": 6.0},
        {"code": "159915", "country": "cn", "size_yi": 100.0, "price": 2.0},
        {"code": "SPY", "country": "us", "size_yi": 9999.0, "price": 500.0},  # 非 A 股，必须被排除
    ]


def _fake_kline(_market: str, code: str, limit: int = 0) -> list[dict]:
    return [{"date": "2026-09-29", "close": 1.0, "open": 1.0, "high": 1.0,
             "low": 1.0, "volume": 1.0, "amount": 1.0},
            {"date": "2026-09-30", "close": 1.1, "open": 1.0, "high": 1.1,
             "low": 1.0, "volume": 1.0, "amount": 1.0}]


@pytest.mark.asyncio
async def test_scale_ok_field_set_and_scope_echo(monkeypatch):
    """正常路径字段集合；并断言「作用域回显」存在（E-3 契约）。"""
    monkeypatch.setattr(etf_api.E, "build_catalog", _fake_scale_catalog)
    monkeypatch.setattr(etf_api.E, "fetch_kline", _fake_kline)

    async def _no_cache(_key, build, _fallback):
        return await build()

    monkeypatch.setattr(etf_api, "_cached_etf_payload", _no_cache)

    d = _data(await etf_api.etf_scale(period="1m", top_n=3, _user={"role": "viewer"}))

    assert set(d) == _SCALE_CORE
    # 🔴 E-3：作用域必须由**后端回显**，前端不得写死
    assert d["scope"] == etf_api._SCALE_SCOPE
    assert d["scope_note"] == etf_api._SCALE_SCOPE_NOTE
    assert "不随" in d["scope_note"]
    # 非 A 股标的必须被排除（否则口径谎报成"全市场"）
    assert d["sample_size"] == 3


@pytest.mark.asyncio
async def test_scale_degraded_field_set(monkeypatch):
    async def _force_fallback(_key, _build, fallback):
        return fallback("stub: 数据源不可用")

    monkeypatch.setattr(etf_api, "_cached_etf_payload", _force_fallback)

    d = _data(await etf_api.etf_scale(period="1m", top_n=3, _user={"role": "viewer"}))

    assert set(d) == _SCALE_CORE | _SCALE_DEGRADED_EXTRA
    assert d["points"] == []
    assert d["sample_size"] == 0
    # 降级也必须带作用域回显（否则前端那张卡的"口径"行会消失）
    assert d["scope_note"] == etf_api._SCALE_SCOPE_NOTE


# --------------------------------------------------------------------------- #
# /flow
# --------------------------------------------------------------------------- #
@pytest.mark.asyncio
async def test_flow_ok_field_set(monkeypatch):
    # 返回**满 limit 条**，才能覆盖"达到上限即视为可能被截断"的披露分支
    monkeypatch.setattr(etf_api.E, "fetch_flow", lambda _f, limit: [
        {"code": f"51030{i}", "name": f"ETF{i}", "net_inflow": 1.23e8}
        for i in range(limit)
    ])

    d = _data(await etf_api.etf_flow(period="1d", limit=10, _user={"role": "viewer"}))

    assert set(d) == _FLOW_OK_KEYS
    assert d["status"] == "ok"
    assert d["returned"] == 10
    # 截断必须披露：达到 limit 即视为"可能仍有更多"，绝不声称"这就是全部"
    assert d["truncated"] is True
    # 数据源不回传总量 ⇒ total 如实置 None（不用 0 冒充"没有更多"）
    assert d["total"] is None
    assert d["scope_note"] == etf_api._FLOW_SCOPE_NOTE


@pytest.mark.asyncio
async def test_flow_degraded_field_set_differs_from_ok(monkeypatch):
    """降级路径是**另一张形状** —— 必须与正常路径一起锁。"""
    def _boom(_f, _limit):
        raise RuntimeError("stub: 东财不可达")

    monkeypatch.setattr(etf_api.E, "fetch_flow", _boom)

    d = _data(await etf_api.etf_flow(period="1d", limit=10, _user={"role": "viewer"}))

    assert set(d) == _FLOW_DEGRADED_KEYS
    assert d["status"] == "unavailable"
    assert d["items"] == []
    assert d["reason"] == etf_api._FLOW_UNAVAILABLE_REASON
    # 两张形状确实不同（A-5：同一路径下读写/正常降级易串型）
    assert set(d) != _FLOW_OK_KEYS
    assert "truncation_basis" not in d
