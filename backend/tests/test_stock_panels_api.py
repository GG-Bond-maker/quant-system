"""
个股详情面板接口测试（GET /api/v1/stock/{symbol}/panels）。

测试策略：
- 网络数据源全部用 monkeypatch 替换为假实现（测试不依赖外网、不依赖东财限流）；
- 本地计算块（筹码 / 风险）写入合成 Parquet 后真实计算；
- 断言重点是【分块降级契约】：任一数据源抛异常只让对应块 unavailable，
  接口仍返回 code=0，其他块不受影响。
"""
from __future__ import annotations

import sys
from datetime import date, timedelta
from pathlib import Path

import numpy as np
import polars as pl
import pytest
from fastapi.testclient import TestClient

BACKEND_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(BACKEND_ROOT))

from app.cache.redis_client import RedisClient  # noqa: E402
from app.core.config import get_settings  # noqa: E402
from app.data import panels as panels_mod  # noqa: E402
from app.data import realtime as realtime_mod  # noqa: E402
from app.data.parquet_store import write_year_batch  # noqa: E402
from app.main import app  # noqa: E402

SYMBOL = "000001.SZ"
BLOCKS = (
    "quote", "money_flow", "north", "fundamentals",
    "events", "holders", "chip", "risk",
)
# T-03 B 组：/stock/{symbol}/panels 收紧为 require_role("viewer")，
# 测试客户端统一注入 admin 静态 token（与 test_monitor 同一 conftest 环境）。
_AUTH_HEADERS = {"Authorization": "Bearer aqp-dev-token-change-me"}


# ---------------- 合成数据 ----------------
def _seed_bars(days: int = 400) -> None:
    """写入不复权 / 前复权日线，供筹码与风险块计算。"""
    s = get_settings()
    rng = np.random.default_rng(42)
    start = date(2024, 1, 1)
    dates = [start + timedelta(days=i) for i in range(days)]
    close = 10.0 * np.exp(np.cumsum(rng.normal(0, 0.01, days)))
    df = pl.DataFrame({
        "date": dates,
        "open": close,
        "high": close * 1.01,
        "low": close * 0.99,
        "close": close,
        "volume": np.full(days, 1_000_000.0),
        "amount": close * 1_000_000.0,
        "turnover": np.full(days, 0.01),
        "code": ["000001"] * days,
        "symbol": [SYMBOL] * days,
        "source": ["test"] * days,
    })
    years = sorted({d.year for d in dates})
    for ds in ("daily_bar", "daily_bar_qfq"):
        for year in years:
            write_year_batch(
                ds, SYMBOL, year,
                df.filter(pl.col("date").dt.year() == year),
            )
    assert (s.DATA_ROOT / "daily_bar").exists()


# ---------------- 假数据源 ----------------
def _fake_quote(symbol: str) -> dict:
    return {
        "price": 10.5, "prev_close": 10.4, "pct": 0.96, "turnover": 1.23,
        "outer_vol": 600.0, "inner_vol": 400.0,
        "float_cap_yi": 100.0, "total_cap_yi": 120.0,
        "pe_ttm": 10.5, "pb": 2.5,
        "quote_time": "20260101150000", "source": "fake",
    }


def _fake_flow(symbol: str) -> dict:
    return {"date": "2026-01-01", "main_net": 1.5e8, "super_large_net": 8e7,
            "large_net": 7e7, "medium_net": -3e7, "small_net": -2e7,
            "main_net_ratio": 5.5, "close": 10.5, "source": "fake"}


def _fake_north(symbol: str) -> dict:
    return {"date": "2025-12-31", "hold_shares": 1e8, "hold_market_cap": 1e10,
            "pct_of_float": 3.21, "close": 10.4, "source": "fake"}


def _fake_financials(symbol: str) -> dict:
    """构造一期财报：净利率由后端按 归母净利/营收 推导 = 25/100 = 25%。"""
    return {
        "report_date": "2025-12-31", "roe": 12.34, "gross_margin": 40.5,
        "net_margin": 25.0, "eps": 1.5, "bps": 12.0,
        "revenue_yi": 100.0, "net_profit_yi": 25.0, "source": "fake",
    }


def _fake_events(symbol: str, limit: int = 3) -> list[dict]:
    return [
        {"title": "关于回购股份的公告", "date": "2026-01-02", "url": "http://x/1", "source": "fake"},
        {"title": "2025 年年度报告", "date": "2026-01-01", "url": "http://x/2", "source": "fake"},
    ]


def _fake_holder_num(symbol: str) -> dict:
    return {"end_date": "2025-09-30", "holder_num": 100000.0, "prev_holder_num": 95000.0,
            "change_ratio": 5.26, "avg_market_cap": 120000.0, "avg_hold_num": 11000.0}


def _fake_top10(symbol: str) -> list[dict]:
    return [{"rank": 1.0, "name": "大股东A", "hold_num": 1e9, "pct_of_float": 30.0,
             "change": "不变", "holder_type": "机构"}]


def _boom(symbol: str, *a, **k):
    raise RuntimeError("数据源炸了")


@pytest.fixture()
def client(monkeypatch):
    """替换全部网络数据源为假实现，关闭缓存，并写入合成日线。

    ⚠️ 必须 patch ``panels_mod`` 上的名字：panels 用
    ``from .realtime import fetch_xxx`` 直接绑定了函数对象，
    只 patch realtime 模块的同名属性对 panels 无效（仍会打真实网络）。
    """
    monkeypatch.setattr(panels_mod, "fetch_quote", _fake_quote)
    monkeypatch.setattr(panels_mod, "fetch_main_fund_flow", _fake_flow)
    monkeypatch.setattr(panels_mod, "fetch_north_holding", _fake_north)
    monkeypatch.setattr(panels_mod, "fetch_financial_indicators", _fake_financials)
    monkeypatch.setattr(panels_mod, "fetch_events", _fake_events)
    # 公告读口径解耦：本仓 `test_p1_data.py::test_announcements_pit_and_dedup` 会向
    # **同一会话隔离 DATA_ROOT** 写入 `announcements` parquet（含 SYMBOL="000001.SZ"）。
    # `build_events` 现为「parquet → SQLite → fetch_events」优先读 parquet，若不隔离
    # 会命中那份 ambient 分区、掩盖本文件"本地无公告、走 fetch_events 假源"的用例意图，
    # 形成对执行顺序敏感的 flake。这里显式桩空公告读取器，使本地源稳定为空。
    monkeypatch.setattr(panels_mod, "read_symbol_announcements", lambda *a, **k: [])
    monkeypatch.setattr(panels_mod, "fetch_holder_num", _fake_holder_num)
    monkeypatch.setattr(panels_mod, "fetch_top10_float_holders", _fake_top10)
    # _benchmark_frame 内部是惰性导入，需 patch 源模块
    monkeypatch.setattr(realtime_mod, "fetch_benchmark_daily", _boom)
    # 关闭缓存：否则用例之间会互相读到上一次的块结果，掩盖降级行为
    async def _no_get(cls, key: str):  # noqa: ANN001
        return None

    async def _no_set(cls, *a, **k):  # noqa: ANN001
        return None

    monkeypatch.setattr(RedisClient, "get", classmethod(_no_get))
    monkeypatch.setattr(RedisClient, "set", classmethod(_no_set))

    _seed_bars()
    with TestClient(app) as c:
        c.headers.update(_AUTH_HEADERS)
        yield c


def _panels(c: TestClient) -> dict:
    r = c.get(f"/api/v1/stock/{SYMBOL}/panels")
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["code"] == 0, body
    return body["data"]


# ---------------- 用例 ----------------
def test_panels_returns_all_blocks(client):
    d = _panels(client)
    assert d["symbol"] == SYMBOL
    for b in BLOCKS:
        assert b in d, f"缺少数据块 {b}"
        assert d[b]["status"] in ("ok", "degraded", "unavailable")


def test_panels_quote_and_money_flow_values(client):
    d = _panels(client)
    q, m = d["quote"], d["money_flow"]
    assert q["status"] == "ok"
    assert q["total_cap_yi"] == 120.0 and q["float_cap_yi"] == 100.0
    assert q["turnover"] == 1.23
    # 外盘 600 / 内盘 400
    assert q["outer_ratio"] == pytest.approx(60.0)
    assert q["inner_ratio"] == pytest.approx(40.0)

    assert m["status"] == "ok"
    assert m["main_net_yi"] == pytest.approx(1.5)
    assert m["main_net_ratio"] == pytest.approx(5.5)


def test_panels_events_padded_to_requested_slots(client):
    """公告只有 2 条时原样返回（前端负责占位补齐）。"""
    d = _panels(client)
    ev = d["events"]
    assert ev["status"] == "ok"
    assert len(ev["items"]) == 2
    assert ev["items"][0]["title"].startswith("关于回购")
    # 关键词规则打标：回购 -> positive
    assert ev["items"][0]["sentiment"] == "positive"


def test_panels_fundamentals_values(client):
    """PE / PB 来自快照，ROE / 毛利率 / 净利率来自财报。"""
    d = _panels(client)
    f = d["fundamentals"]
    assert f["status"] == "ok"
    assert f["pe_ttm"] == 10.5          # 与 _fake_quote 的 pe_ttm 对齐
    assert f["pb"] == 2.5
    assert f["roe"] == pytest.approx(12.34)
    assert f["gross_margin"] == pytest.approx(40.5)
    assert f["net_margin"] == pytest.approx(25.0)
    assert f["report_date"] == "2025-12-31"


def test_panels_fundamentals_degrades_to_pe_pb_when_report_down(client, monkeypatch):
    """财报源挂了：仍返回 PE / PB，但标记为 degraded，不让整块消失。"""
    monkeypatch.setattr(panels_mod, "fetch_financial_indicators", _boom)
    d = _panels(client)
    f = d["fundamentals"]
    assert f["status"] == "degraded"
    assert f["pe_ttm"] == 10.5 and f["pb"] == 2.5
    assert f["roe"] is None and f["net_margin"] is None


def test_panels_fundamentals_degrades_to_report_when_quote_down(client, monkeypatch):
    """快照源挂了：仍返回财报口径的 ROE / 毛利率 / 净利率。"""
    monkeypatch.setattr(panels_mod, "fetch_quote", _boom)
    d = _panels(client)
    f = d["fundamentals"]
    assert f["status"] == "degraded"
    assert f["pe_ttm"] is None and f["pb"] is None
    assert f["roe"] == pytest.approx(12.34)


def test_panels_holders_and_north(client):
    d = _panels(client)
    assert d["north"]["status"] == "ok"
    assert d["north"]["pct_of_float"] == pytest.approx(3.21)
    assert d["holders"]["status"] == "ok"
    assert d["holders"]["holder_num"]["holder_num"] == 100000.0
    assert len(d["holders"]["top10"]) == 1


def test_panels_chip_and_risk_computed_from_parquet(client):
    d = _panels(client)
    chip, risk = d["chip"], d["risk"]
    assert chip["status"] == "ok"
    assert 0.0 <= chip["profit_ratio"] <= 1.0
    assert chip["profit_ratio"] + chip["trapped_ratio"] == pytest.approx(1.0, abs=1e-6)
    assert chip["p5"] <= chip["avg_cost"] <= chip["p95"]

    assert risk["status"] == "degraded"      # 基准被 _boom 打断 -> Beta 缺失
    assert risk["beta"] is None
    assert risk["annual_vol"] is not None and risk["annual_vol"] > 0
    assert 0.0 <= risk["vol_percentile"] <= 100.0


def test_panels_degrades_single_source_without_breaking_others(client, monkeypatch):
    """资金流数据源炸了：只有 money_flow 降级，其他块照常 ok。"""
    monkeypatch.setattr(panels_mod, "fetch_main_fund_flow", _boom)
    d = _panels(client)
    assert d["money_flow"]["status"] == "unavailable"
    assert "数据源" in (d["money_flow"].get("reason") or "")
    assert d["quote"]["status"] == "ok"
    assert d["chip"]["status"] == "ok"


def test_panels_all_sources_down_keeps_endpoint_alive(client, monkeypatch):
    """全部网络源失败：接口恒可用，所有网络块统一降级为 unavailable。"""
    for name in ("fetch_quote", "fetch_main_fund_flow", "fetch_north_holding",
                 "fetch_financial_indicators", "fetch_events",
                 "fetch_holder_num", "fetch_top10_float_holders"):
        monkeypatch.setattr(panels_mod, name, _boom)
    d = _panels(client)
    for b in ("quote", "money_flow", "north", "fundamentals", "events", "holders"):
        assert d[b]["status"] == "unavailable", b
    # 本地计算块不受网络影响
    assert d["chip"]["status"] == "ok"
    assert d["risk"]["status"] in ("ok", "degraded")


def test_panels_unavailable_without_local_data(client, monkeypatch, tmp_path):
    """本地无日线时，筹码 / 风险块降级而非让接口 500。"""
    monkeypatch.setattr(panels_mod, "read_symbol_dataset", lambda *a, **k: pl.DataFrame())
    d = _panels(client)
    assert d["chip"]["status"] == "unavailable"
    assert d["risk"]["status"] == "unavailable"


def test_panels_slow_block_times_out_while_others_ok(client, monkeypatch):
    """P1-2 回归：数据源「挂起」（非抛异常）时，该块按超时降级为 unavailable，
    其余块照常 ok，且整个 /panels 响应时间被 `_PANEL_BLOCK_TIMEOUT` 约束。

    区别于点上的 ``_boom``（立即抛错）：挂起不会触发 ``_cached_block`` 的
    except 分支，只能靠 ``asyncio.wait_for`` 兜底——没有这层守卫时接口会一直
    等到 requests 的 read 超时（数十秒），这正是审计发现的 P1-2。
    """
    import time as _time

    from app.api.v1 import stock as stock_mod

    def _slow_quote(symbol: str) -> dict:
        _time.sleep(3.0)          # 模拟外部源挂起
        return _fake_quote(symbol)

    monkeypatch.setattr(panels_mod, "fetch_quote", _slow_quote)
    # 调用时读取全局，故 monkeypatch 生效（见 stock_panels._with_timeout）
    monkeypatch.setattr(stock_mod, "_PANEL_BLOCK_TIMEOUT", 1.0)

    t0 = _time.perf_counter()
    d = _panels(client)
    elapsed = _time.perf_counter() - t0

    assert elapsed < 2.0, f"响应应被超时约束（<2s），实测 {elapsed:.2f}s"
    assert d["quote"]["status"] == "unavailable"
    assert "超时" in (d["quote"].get("reason") or "")
    assert d["quote"]["from_cache"] is False
    assert d["money_flow"]["status"] == "ok"      # 其他块不受影响
    assert d["chip"]["status"] == "ok"
