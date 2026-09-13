"""§3.3 /market/quotes + §3.4 SSE quotes 频道测试（Sprint2）。

全部离线：fetch_quotes_batch 一律 monkeypatch，不触网。
覆盖：批量解析（真实腾讯响应样本）、契约、上限 ERR_PARAMS(40000)、缓存命中、
降级不造数、SSE 事件格式（event: quotes + data: JSON + 心跳）。
"""
from __future__ import annotations

import asyncio
import sys
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

BACKEND_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(BACKEND_ROOT))

from app.core.errors import ERR_PARAMS  # noqa: E402
from app.data import quotes_hub  # noqa: E402
from app.main import app  # noqa: E402

# /market/quotes 等读端点自 T-03 B 组起要求 viewer 及以上角色
# （require_role("viewer")），匿名调用会先吃 40100 而非被测的业务错误码。
# 本文件只验证正常读取与参数校验路径，统一带默认 ADMIN_TOKEN；
# conftest.py 已把 ADMIN_TOKEN 钉回该默认值。
_AUTH_HEADERS = {"Authorization": "Bearer aqp-dev-token-change-me"}

# 2026-09-05 实测抓取的真实响应样本（600519 截取自线上，000001 为同构合成）
# 2026-09-05 实测抓取的腾讯响应结构：600519 为线上原文；
# 000001 由同一 88 字段骨架程序化改写（保证字段数一致，仅覆写关键索引）。
_MT_FIELDS = ("1~贵州茅台~600519~1330.00~1298.88~1295.88~45416~27411~18005"
              "~1329.82~1~1329.59~22~1329.58~1~1329.50~2~1329.49~3~1330.00~46~1330.01~2"
              "~1330.02~29~1330.03~23~1330.06~1~~20260904161433~31.12~2.40~1338.86"
              "~1295.60~1330.00/45416/6022594729~45416~602259~0.36~20.42~~1338.86"
              "~1295.60~3.33~16626.09~16626.09~6.62~1428.77~1168.99~2.06").split("~")


def _make_line(fields: list[str], overrides: dict[int, str], prefix: str) -> str:
    out = list(fields)
    for i, v in overrides.items():
        out[i] = v
    return f'v_{prefix}="{"~".join(out)}"'


_TENCENT_SAMPLE = (
    _make_line(_MT_FIELDS, {}, "sh600519") + ";\n"
    + _make_line(_MT_FIELDS, {1: "平安银行", 2: "000001", 3: "11.89", 4: "11.88",
                              5: "11.86", 6: "814373", 30: "20260904150000",
                              31: "0.01", 32: "0.08", 33: "12.00", 34: "11.85",
                              36: "814373", 37: "96995", 38: "0.42",
                              44: "2307.34", 45: "2307.36"}, "sz000001") + ";\n"
)


@pytest.fixture(scope="module")
def quotes_client():
    """轻量 client（不训练模型；quotes/alerts 端点无重依赖）。"""
    with TestClient(app) as c:
        c.headers.update(_AUTH_HEADERS)
        yield c


@pytest.fixture(autouse=True)
def _isolate_hub_state():
    """每个用例前后清空 hub 状态与进程缓存（跨用例隔离）。"""
    quotes_hub._quotes_cache.clear()
    yield
    quotes_hub.stop_quotes_task()
    quotes_hub._quotes_task = None  # 跨 loop 的死任务 done() 恒 False，直接清引用
    quotes_hub._quotes_cache.clear()
    for q in list(quotes_hub._quotes_subs):
        quotes_hub.unsubscribe_quotes(q)
    for q in list(quotes_hub._alerts_subs):
        quotes_hub.unsubscribe_alerts(q)


def _patch_fetch(monkeypatch, quotes=None, source="tencent", calls=None):
    """替换批量抓取：返回固定快照并计数（验证缓存命中与调用次数）。"""
    from app.data import realtime

    def _fake(symbols):
        if calls is not None:
            calls.append(list(symbols))
        out = quotes if quotes is not None else []
        return [dict(q) for q in out if q["symbol"] in symbols], source

    monkeypatch.setattr(realtime, "fetch_quotes_batch", _fake)


def test_parse_tencent_batch_sample():
    """真实响应样本解析：字段索引与单位口径（volume=手，amount=元）。"""
    from app.data.realtime import _parse_tencent_batch

    out = _parse_tencent_batch(_TENCENT_SAMPLE, ["600519.SH", "000001.SZ"])
    assert set(out) == {"600519.SH", "000001.SZ"}
    mt = out["600519.SH"]
    assert mt["name"] == "贵州茅台"
    assert mt["price"] == 1330.00 and mt["prev_close"] == 1298.88
    assert mt["pct"] == 2.40
    assert mt["open"] == 1295.88 and mt["high"] == 1338.86 and mt["low"] == 1295.60
    assert mt["volume"] == 45416            # 手（与 daily_bar 对齐）
    assert mt["amount"] == 602259 * 1e4     # 万元 -> 元
    assert mt["as_of"] == "2026-09-04 16:14:33"
    assert mt["source"] == "tencent"
    pa = out["000001.SZ"]
    assert pa["name"] == "平安银行" and pa["price"] == 11.89


def test_quotes_contract_and_cache(quotes_client: TestClient, monkeypatch):
    """契约 + 缓存命中：TTL 内二次请求不再触发外部抓取。"""
    calls: list[list[str]] = []
    fake_quote = {"symbol": "600519.SH", "name": "贵州茅台", "price": 1330.0,
                  "pct": 2.4, "open": 1295.88, "high": 1338.86, "low": 1295.6,
                  "prev_close": 1298.88, "volume": 45416, "amount": 6.02e9,
                  "as_of": "2026-09-04 16:14:33", "source": "tencent"}
    _patch_fetch(monkeypatch, quotes=[fake_quote], source="tencent", calls=calls)

    r1 = quotes_client.get("/api/v1/market/quotes",
                           params={"symbols": "600519.SH"})
    assert r1.status_code == 200
    b1 = r1.json()
    assert b1["code"] == 0, b1["message"]
    data = b1["data"]
    assert data["source"] == "tencent"
    assert data["as_of"] == "2026-09-04 16:14:33"
    assert len(data["quotes"]) == 1
    q = data["quotes"][0]
    assert {"symbol", "name", "price", "pct", "open", "high", "low",
            "prev_close", "volume", "amount"} <= set(q)

    r2 = quotes_client.get("/api/v1/market/quotes",
                           params={"symbols": "600519.SH"})
    assert r2.json()["code"] == 0
    # 相同 symbols 集合共享进程缓存：外部抓取只发生 1 次（乱序传入也命中）
    r3 = quotes_client.get("/api/v1/market/quotes",
                           params={"symbols": "600519.SH,600519.SH"})
    assert r3.json()["code"] == 0
    assert len(calls) == 1, f"缓存未生效，抓取了 {len(calls)} 次"


def test_quotes_limit_exceeded(quotes_client: TestClient):
    """symbols 上限 200：超限业务码 ERR_PARAMS(40000)，不触达数据源。"""
    syms = ",".join(f"6{i:05d}.SH" for i in range(201))
    r = quotes_client.get("/api/v1/market/quotes", params={"symbols": syms})
    assert r.json()["code"] == ERR_PARAMS


def test_quotes_empty_rejected(quotes_client: TestClient):
    r = quotes_client.get("/api/v1/market/quotes", params={"symbols": " , , , , , "})
    assert r.json()["code"] == ERR_PARAMS


def test_quotes_degraded_no_fabrication(quotes_client: TestClient, monkeypatch):
    """降级链走完仍失败：返回空数组 + source=degraded，绝不造数。"""
    _patch_fetch(monkeypatch, quotes=[], source="degraded")
    r = quotes_client.get("/api/v1/market/quotes", params={"symbols": "600519.SH"})
    body = r.json()
    assert body["code"] == 0
    assert body["data"]["source"] == "degraded"
    assert body["data"]["quotes"] == []


def test_sse_quotes_stream_format(monkeypatch):
    """SSE quotes 频道协议格式：event: quotes + data: JSON（首推即到）。

    注：不走 TestClient.stream —— httpx ASGITransport 会等 ASGI app 运行结束
    才返回响应，无限 SSE 流在 transport 层即死锁；改为直接调用端点并迭代
    body_iterator（同样经过 hub 订阅/广播/pump 全链路）。
    """
    fake_quote = {"symbol": "600519.SH", "name": "贵州茅台", "price": 1330.0,
                  "pct": 2.4, "prev_close": 1298.88, "as_of": "2026-09-04 16:14:33",
                  "source": "tencent"}
    _patch_fetch(monkeypatch, quotes=[fake_quote])
    # 预热进程缓存：确保 hub 首次抓取零延迟（缓存命中）
    asyncio.run(quotes_hub.quotes_snapshot(["600519.SH"]))

    from app.api.v1 import notify as notify_mod

    async def _consume() -> list[str]:
        resp = await notify_mod.stream(channels="quotes", symbols="600519.SH", ttl=15)
        chunks: list[str] = []
        async for chunk in resp.body_iterator:
            chunks.append(chunk)
            if "event: quotes" in chunk:
                break
            if len(chunks) > 40:  # 硬上限：心跳空转不再无限等
                break
        return chunks

    chunks = asyncio.run(asyncio.wait_for(_consume(), timeout=15))
    joined = "".join(chunks)
    assert "event: quotes" in joined, f"未收到 quotes 事件: {joined[:200]}"
    # SSE 帧结构：event: 行与 data: 行同帧（chunk 级别按行拆分再断言）
    lines = [ln for c in chunks for ln in c.split("\n")]
    data_lines = [ln for ln in lines if ln.startswith("data: ")]
    assert data_lines, f"缺少 data 行: {lines[:10]}"
    import json

    payload = json.loads(data_lines[0][6:])
    assert "quotes" in payload and "as_of" in payload
    assert payload["quotes"][0]["symbol"] == "600519.SH"


def test_sse_unknown_channel_rejected(quotes_client: TestClient):
    """未知频道：立即返回 event: error，不建立长连接。"""
    with quotes_client.stream(
        "GET", "/api/v1/notify/stream", params={"channels": "bogus"},
    ) as resp:
        text = "".join(resp.iter_text())
    assert "event: error" in text


def test_quotes_hub_alerts_fanout():
    """alerts 频道单元：publish_alert 广播到全部订阅者；退订后不再收到。"""
    payload = {"rule_id": 1, "rule": "测试规则", "symbol": "600519.SH"}

    async def _run():
        q1 = await quotes_hub.subscribe_alerts()
        q2 = await quotes_hub.subscribe_alerts()
        quotes_hub.publish_alert(payload)
        got1 = await asyncio.wait_for(q1.get(), timeout=2)
        got2 = await asyncio.wait_for(q2.get(), timeout=2)
        quotes_hub.unsubscribe_alerts(q2)
        quotes_hub.publish_alert({"rule_id": 2})
        got1b = await asyncio.wait_for(q1.get(), timeout=2)
        return got1, got2, got1b

    got1, got2, got1b = asyncio.run(_run())
    assert got1["rule_id"] == 1 and got2["rule_id"] == 1
    assert got1b["rule_id"] == 2


# ---------------- L2-2 overview 拆分端点（rt/daily） ----------------
def test_overview_split_endpoints_contract(quotes_client: TestClient):
    """rt/daily 拆分端点：结构契约 + 字段互斥（外部源离线时各块优雅降级）。

    纪律：外部源在本环境不可达 -> rt 各块返回 unavailable/degraded 属预期，
    断言只针对信封与块结构，不断言具体行情数字（不造数）。
    """
    r_rt = quotes_client.get("/api/v1/market/overview/rt")
    assert r_rt.status_code == 200
    b_rt = r_rt.json()
    assert b_rt["code"] == 0, b_rt["message"]
    d_rt = b_rt["data"]
    assert {"indices", "money_flow", "anomalies", "trade_date"} <= set(d_rt)
    assert d_rt["from_cache"] is False  # 首次构建

    r_rt2 = quotes_client.get("/api/v1/market/overview/rt")
    assert r_rt2.json()["data"]["from_cache"] is True  # 命中缓存

    r_daily = quotes_client.get("/api/v1/market/overview/daily",
                                params={"recommend_k": 5})
    b_daily = r_daily.json()
    assert b_daily["code"] == 0, b_daily["message"]
    d_daily = b_daily["data"]
    assert {"heat", "sectors", "recommend", "ai_stats", "sentiment",
            "pred_dates", "trade_date"} <= set(d_daily)
    # rt/daily 互斥：daily 块不含实时块字段
    assert "indices" not in d_daily and "money_flow" not in d_daily


def test_overview_compat_includes_both_blocks(quotes_client: TestClient):
    """/overview 兼容端点：rt + daily 字段并集（旧消费者不受影响）。"""
    r = quotes_client.get("/api/v1/market/overview", params={"recommend_k": 5})
    body = r.json()
    assert body["code"] == 0, body["message"]
    d = body["data"]
    assert {"indices", "money_flow", "heat", "sectors", "recommend",
            "sentiment"} <= set(d)
