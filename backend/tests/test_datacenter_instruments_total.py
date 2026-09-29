"""§4.8b S12 防回归：`/datacenter/instruments` 的 `total` 必须是**真实总量**。

## 缺陷

`api/v1/datacenter.py::list_instruments` 原实现：

```python
rows = conn.execute("... LIMIT ?", (limit,)).fetchall()
return ok({"items": [...], "total": len(rows)})
```

`total` = **返回条数**（受 LIMIT 约束）⇒ 实测 `limit=1 → total=1`，而真实
`instrument` 表有 5552 行 —— **总数随 limit 一起缩小**，比不返回 `total` 更误导。
前端 `DataCenter/index.tsx:307,346` 直接把它渲染成「可抓取 N 只」「… 等 N 只」，
所以这是**用户可见的错误数字**（报告 §4.8b S12 / §8.2 序 12）。

附带：`limit` 原为裸默认值（**无任何校验**），`limit=-1` 在 SQLite 中等于
**无上限**（全表返回）、`limit=0` 静默空列表。

## 修法

`total` 由独立 `COUNT(*)` 得出（不受 limit 影响），并补齐截断三件套
`returned` / `truncated` / `limit`；`limit` 收紧为 `Query(ge=1, le=500)`。
"""
from __future__ import annotations

import asyncio
import contextlib
import sqlite3
import sys
from pathlib import Path

import pytest

BACKEND_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(BACKEND_ROOT))

from app.api.v1 import datacenter as dc  # noqa: E402

_TOTAL = 7          # stock 行数
_ETF = 2            # etf 行数


def _call(**kw) -> dict:
    """直接调用 async 端点并取出响应体（`.data`）。"""
    out = asyncio.run(dc.list_instruments(_user={}, **kw))
    return out.data if hasattr(out, "data") else out["data"]


@pytest.fixture()
def memory_db(monkeypatch):
    """把 `_sqlite_ro()` 指到内存库（含 7 条 stock + 2 条 etf），不碰生产库。"""
    con = sqlite3.connect(":memory:")
    con.execute("CREATE TABLE instrument (symbol TEXT PRIMARY KEY, name TEXT,"
                " instrument_type TEXT)")
    con.executemany("INSERT INTO instrument VALUES (?,?,?)",
                    [(f"60000{i}.SH", f"股票{i}", "stock") for i in range(_TOTAL)]
                    + [("510300.SH", "沪深300ETF", "etf"),
                       ("159915.SZ", "创业板ETF", "etf")])
    con.commit()

    @contextlib.contextmanager
    def _ro():
        yield con

    monkeypatch.setattr(dc, "_sqlite_ro", _ro)
    yield con
    con.close()


def test_total_is_real_total_not_page_size(memory_db):
    """**缺陷本体**：limit=1 时 total 仍必须是 7（原实现给 1）。"""
    data = _call(asset_type="stock", limit=1)
    assert data["total"] == _TOTAL, f"total 不得随 limit 缩小：{data}"
    assert data["returned"] == 1
    assert data["truncated"] is True and data["limit"] == 1


def test_limit_above_total_is_not_truncated(memory_db):
    """反向断言：limit ≥ 总量时不得谎报 truncated。"""
    data = _call(asset_type="stock", limit=50)
    assert data["total"] == _TOTAL == data["returned"]
    assert data["truncated"] is False


def test_asset_type_filter_has_own_total(memory_db):
    """`asset_type=etf` ⇒ total 是该类型的真实总数（2），不是全表总数。"""
    data = _call(asset_type="etf", limit=1)
    assert data["total"] == _ETF and data["returned"] == 1
    assert data["truncated"] is True
    assert all(i["type"] == "etf" for i in data["items"])


def test_all_type_counts_whole_table(memory_db):
    data = _call(asset_type="all", limit=3)
    assert data["total"] == _TOTAL + _ETF and data["returned"] == 3
    assert data["truncated"] is True


def test_negative_and_zero_limit_are_rejected():
    """原实现下 limit=-1 = 无上限（全表）、limit=0 = 静默空列表；现在必须被参数校验拦下。"""
    from fastapi.testclient import TestClient

    from app.main import app

    with TestClient(app) as client:
        for bad in ("-1", "0", "501"):
            r = client.get(f"/api/v1/datacenter/instruments?limit={bad}",
                           headers={"Authorization": "Bearer aqp-dev-token-change-me"})
            assert r.status_code == 200, "本项目契约：HTTP 恒 200"
            assert r.json()["code"] == 40000, f"limit={bad} 应被拒绝：{r.json()}"


# ---------------------------------------------------------------------------
# F10（审计 §4.6/§8.2 序 7）：同文件的 /quality 与 /logs 也曾无上界
# ---------------------------------------------------------------------------
_ADMIN_H = {"Authorization": "Bearer aqp-dev-token-change-me"}
_UNBOUNDED_LIMITS = ("-1", "0", "1000000000")


@pytest.fixture(scope="module")
def http():
    from fastapi.testclient import TestClient

    from app.main import app

    with TestClient(app) as c:
        yield c


def test_limit_bounds_are_declared() -> None:
    """两处 limit 的边界必须落在 OpenAPI 契约上（ge=1 + 有限上界），而非仅靠运行时拦。"""
    from app.main import app

    def _limit_schema(path: str) -> dict:
        params = app.openapi()["paths"][path]["get"]["parameters"]
        return next(p["schema"] for p in params if p["name"] == "limit")

    q = _limit_schema("/api/v1/datacenter/quality")
    g = _limit_schema("/api/v1/datacenter/logs")
    assert (q["minimum"], q["maximum"]) == (1, 10000)
    # /logs 对齐同文件 /instruments 的 500；/quality 上界须容纳前端的"展开全部"
    # （DataCenter/index.tsx:403 用 limit=10000），故不能收到 500。
    assert (g["minimum"], g["maximum"]) == (1, 500)


@pytest.mark.parametrize("bad", _UNBOUNDED_LIMITS)
def test_quality_limit_must_be_bounded(http, bad: str) -> None:
    """缺陷本体：`/quality?limit=10**9` 原样接受并序列化全量列表；`limit=-1` 丢最后一条。"""
    r = http.get(f"/api/v1/datacenter/quality?limit={bad}", headers=_ADMIN_H)
    assert r.status_code == 200, "本项目契约：HTTP 恒 200"
    assert r.json()["code"] == 40000, f"limit={bad} 应被拒绝：{r.json()}"


@pytest.mark.parametrize("bad", _UNBOUNDED_LIMITS)
def test_logs_limit_must_be_bounded(http, bad: str) -> None:
    """缺陷本体：`/logs?limit=0` 原为 `out[-0:]` = 返回**全部**日志。"""
    r = http.get(f"/api/v1/datacenter/logs?limit={bad}", headers=_ADMIN_H)
    assert r.status_code == 200, "本项目契约：HTTP 恒 200"
    assert r.json()["code"] == 40000, f"limit={bad} 应被拒绝：{r.json()}"


def test_quality_and_logs_default_limits_still_accepted(http) -> None:
    """反向断言：既有默认值（50 / 60）与前端实际会发的上界值仍必须放行。"""
    q = http.get("/api/v1/datacenter/quality", headers=_ADMIN_H).json()
    g = http.get("/api/v1/datacenter/logs", headers=_ADMIN_H).json()
    q_all = http.get("/api/v1/datacenter/quality?limit=10000", headers=_ADMIN_H).json()
    assert q["code"] == 0 and g["code"] == 0, (q, g)
    assert q_all["code"] == 0, q_all


def test_logs_discloses_tail_truncation(http) -> None:
    """**R10 / P5-S12**：`/logs` 的 `out[-limit:]` 尾部截断必须如实披露。

    原实现只回 `{items}`：`limit` 之外被丢弃的日志**毫无痕迹**，前端把返回条数
    当"全部日志"。修法是在**不改形状**（前端 `api/datacenter.ts:21` 消费
    `{items}`）的前提下补齐 `total`/`returned`/`limit`/`truncated`。
    """
    d = http.get("/api/v1/datacenter/logs?limit=1", headers=_ADMIN_H).json()["data"]
    for k in ("items", "total", "returned", "limit", "truncated"):
        assert k in d, f"/logs 缺披露字段 {k}: {sorted(d)}"
    assert d["limit"] == 1
    assert d["returned"] == len(d["items"]) <= 1
    # 隔离环境无日志文件 ⇒ total=0 不算截断；有日志时必须与 returned 一致
    assert d["truncated"] is (d["returned"] < d["total"])

    d2 = http.get("/api/v1/datacenter/logs?limit=500", headers=_ADMIN_H).json()["data"]
    assert d2["returned"] == d2["total"], "上界内必须返回全部（不得谎报截断）"
    assert d2["truncated"] is False