"""回归护栏：`/api/v1/etf/flow` 的 `limit` 上限必须与数据层缓存深度 `_FLOW_MAX` 对齐。

## 缺陷（2026-10-03）

数据层 `fetch_flow` 已改为「缓存**全量** `_FLOW_MAX`(=100) 条、调用侧切片」，但接口
的 Query 上限仍是 `le=50`：

```
GET /api/v1/etf/flow?period=1d&limit=100
→ 200 {"code":40000,"message":"请求参数错误",
       "data":[{"type":"less_than_equal","loc":["query","limit"],
                "msg":"Input should be less than or equal to 50"}]}
```

即"数据层明明能给出 100 条，接口却声称最多 50 条"。上限必须与 `_FLOW_MAX`
**单一来源**（本文件据此锁死：`api/v1/etf.py` 不得另写一个 100 字面量）。

## 判据

- `limit=100` / `limit=51`：**不再** 40000，`code==0` 且 `len(items) == limit`。
- `limit=101`：**仍**被拒（40000）——超出 `_FLOW_MAX` 的请求必须被明确拒绝，
  而不是被静默少给（那是"要 101 条只给 100 条"却谎报未截断）。
- `limit=1`：边界仍正常。

## 隔离

不连真 Redis / 不连外网：`fetch_flow` 换成本地假源（按调用侧 `limit` 切片，与真实
实现的"全量缓存 + 切片"同形）；`RedisClient.get/set` 置为 no-op（沿用
`tests/test_etf.py` 的手法）。测试自身不写任何共享状态。
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

BACKEND_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(BACKEND_ROOT))

from app.cache.redis_client import RedisClient  # noqa: E402
from app.data import etf as etf_mod  # noqa: E402
from app.main import app  # noqa: E402

# 与 tests/test_etf.py 同一鉴权口径（conftest 已把 ADMIN_TOKEN 钉回默认值）。
_AUTH_HEADERS = {"Authorization": "Bearer aqp-dev-token-change-me"}

#: 假"全量榜单"：深度恰为数据层缓存深度 `_FLOW_MAX`，使"切片后条数"可精确断言。
_RANKED = [
    {"code": f"51{i:04d}", "name": f"ETF{i:03d}", "pct": round(0.1 * i, 2),
     "amount": float(1e8 + i), "net_inflow": float(1e7 + i), "inflow_ratio": 1.0}
    for i in range(etf_mod._FLOW_MAX)
]


@pytest.fixture()
def client(monkeypatch):
    """TestClient + 本地假数据源 + 关闭 Redis（不依赖外部环境）。"""
    monkeypatch.setattr(
        etf_mod, "fetch_flow",
        lambda period="1d", limit=20: _RANKED[: int(limit)])

    async def _no_get(cls, key):  # noqa: ANN001
        return None

    async def _no_set(cls, *a, **k):  # noqa: ANN002, ANN003
        return None

    monkeypatch.setattr(RedisClient, "get", classmethod(_no_get))
    monkeypatch.setattr(RedisClient, "set", classmethod(_no_set))
    with TestClient(app) as c:
        c.headers.update(_AUTH_HEADERS)
        yield c


# ---------------- 1. 上限与 _FLOW_MAX 对齐：100 / 51 不再被拒 ----------------

def test_limit_100_is_accepted(client):
    """`limit=100`（= `_FLOW_MAX`）必须被接受，且返回恰好 100 条。"""
    r = client.get("/api/v1/etf/flow?period=1d&limit=100")
    assert r.status_code == 200, r.text
    b = r.json()
    assert b["code"] == 0, b
    d = b["data"]
    assert d["limit"] == 100
    assert d["returned"] == len(d["items"]) == 100


def test_limit_51_is_accepted(client):
    """`limit=51`（> 旧上限 50、≤ `_FLOW_MAX`）必须被接受，返回恰好 51 条。"""
    r = client.get("/api/v1/etf/flow?period=1d&limit=51")
    assert r.status_code == 200, r.text
    b = r.json()
    assert b["code"] == 0, b
    assert len(b["data"]["items"]) == 51


@pytest.mark.parametrize("limit", [1, 10, 50, 99, 100])
def test_in_range_limits_are_ok(client, limit):
    """区间内任意 limit 均正常，且返回数恰为请求数（≤ _FLOW_MAX）。"""
    r = client.get(f"/api/v1/etf/flow?period=1d&limit={limit}")
    assert r.status_code == 200, r.text
    b = r.json()
    assert b["code"] == 0, b
    assert len(b["data"]["items"]) == limit <= etf_mod._FLOW_MAX


# ---------------- 2. 超上限仍被拒（明确拒绝，而非静默少给） ----------------

def test_limit_101_is_rejected(client):
    """`limit=101`（> `_FLOW_MAX`）必须被拒：HTTP 200 + 业务码 40000。"""
    r = client.get("/api/v1/etf/flow?period=1d&limit=101")
    # 本仓"HTTP 恒 200 + 业务码"契约：参数错误走 40000 信封（errors.py:244）
    assert r.status_code == 200, r.text
    b = r.json()
    assert b["code"] == 40000, f"超上限未被拒绝：{b}"
    assert "limit" in str(b.get("data")), "拒绝原因应指向 limit 越界"


def test_limit_zero_is_rejected(client):
    """`limit=0`（< ge=1）仍被拒（下限未被动到）。"""
    r = client.get("/api/v1/etf/flow?period=1d&limit=0")
    assert r.json()["code"] == 40000, r.text


# ---------------- 3. 单一来源：上限不得在本文件另写 100 字面量 ----------------

def test_flow_limit_cap_is_single_source(client):
    """结构锁：`/flow` 端点的 Query 上限引用 `_FLOW_LIMIT_MAX`，且等于数据层 `_FLOW_MAX`。"""
    import inspect

    from app.api.v1 import etf as etf_api

    assert etf_api._FLOW_LIMIT_MAX == etf_mod._FLOW_MAX, (
        "接口上限与数据层缓存深度漂移 ⇒ 会出现'要 100 条却回 40000'或静默少给")
    # 只锁 `/flow` 端点自身的源码（本文件其它端点合法使用 le=50，不能全局断言）。
    src = inspect.getsource(etf_api.etf_flow)
    assert "le=_FLOW_LIMIT_MAX" in src, "limit 上限必须引用单一来源常量"
    assert "le=50" not in src, "旧上限 le=50 不得在 /flow 端点回流"
