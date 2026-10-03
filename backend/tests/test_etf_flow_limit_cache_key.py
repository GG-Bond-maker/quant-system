"""回归护栏：ETF 资金榜（`data/etf.py::fetch_flow`）缓存**不得**按 `limit` 分叉。

## 缺陷（2026-09-30 实测钉死）

`fetch_flow` 把 `limit` 编进了**缓存内容**（`"pz": max(20, limit)` + `diff[:limit]`），
却把缓存键写成 `flow:{period}`（**不含 limit**）。于是两个调用侧争抢同一槽位：

- `/etf/flow`（前端 ETF 页资金流表，`limit=10`）
- `/etf/overview`（概览聚合，`app/api/v1/etf.py:385` 固定 `limit=100`）

谁先构建谁就把行数**写死**在共享缓存里（TTL=300）。实测：`/etf/flow?limit=10`
返回 `items=100 / returned=100`，前端 10 行的表格被撑到 100 行（×~25px ≈ 2500px），
表现为「切走再切回布局错乱」——因为概览与会话外的请求先后抢到了缓存槽位。

## 修复契约

「**缓存全量、调用侧切片**」：外部请求固定取 `_FLOW_MAX`(=100) 条并整段缓存
（键仍为 `flow:{period}`，不含 limit），`fetch_flow` 末尾按调用侧 `limit` 切片返回。

## 本文件的判据

在**干净缓存**下，先 `fetch_flow("1d", 100)` 再 `fetch_flow("1d", 10)`：
第二次必须**恰好 10 条**，且与第一次的**前 10 条同源同序**。
反向断言（缺陷本体）：若把 `limit` 重新编回缓存内容，第二次会拿到 100 条 ⇒ 本用例转红。
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

BACKEND_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(BACKEND_ROOT))

from app.data import etf as etf_mod  # noqa: E402


@pytest.fixture(autouse=True)
def _clear_cache():
    """每个用例前清进程级缓存，避免用例间互相污染（同 test_us_kline_symbol_resolution）。"""
    with etf_mod._CACHE_LOCK:
        etf_mod._cache.clear()
    yield
    with etf_mod._CACHE_LOCK:
        etf_mod._cache.clear()


def _row(i: int) -> dict:
    """构造可辨识的一行原始东财 clist 记录（f12/f14/f3/f6/f62/f164/f174）。"""
    return {
        "f12": f"51{i:04d}", "f14": f"ETF{i:03d}", "f2": 1.0,
        "f3": round(0.1 * i, 2), "f6": float(1_000_000_000 + i),
        "f62": float(1e8 + i), "f164": float(2e8 + i), "f174": float(3e8 + i),
    }


#: 后端目录的"全量"：100 行，足以覆盖 `_FLOW_MAX`(=100)。
_FULL = [_row(i) for i in range(100)]


def _patch_request(monkeypatch) -> dict:
    """把 `_request` 换成**遵守 pz** 的假源，并回传捕获到的请求参数。

    假源按请求的 ``pz`` 截断返回（模拟真实 clist 行为）——这正是缺陷得以复现的前提：
    旧实现按调用侧 limit 请求，于是第一次 limit=100 会把 100 行写进共享缓存。
    """
    captured: dict = {}

    def _fake_request(method, url, *args, params=None, **kwargs):
        captured["method"] = method
        captured["url"] = url
        captured["params"] = params or {}
        pz = int((params or {}).get("pz", 20))
        return {"data": {"diff": _FULL[:pz]}}

    monkeypatch.setattr(etf_mod, "_request", _fake_request)
    return captured


# ---------------- 1. 主判据：limit=100 先建缓存，limit=10 仍恰好 10 条 ----------------

def test_limit_after_wider_caller_still_respected(monkeypatch):
    """**缺陷本体回归**：宽 limit(100) 先建缓存后，窄 limit(10) 必须仍返回 10 条。

    修复前：第二次命中 `flow:1d` 缓存（内容 100 行）⇒ 返回 100 条 ⇒ 本断言转红。
    """
    _patch_request(monkeypatch)

    wide = etf_mod.fetch_flow("1d", 100)
    assert len(wide) == 100, "全量调用应拿到 100 条"

    narrow = etf_mod.fetch_flow("1d", 10)
    assert len(narrow) == 10, (
        f"limit=10 却拿到 {len(narrow)} 条 —— limit 又被编回了缓存内容（缓存键污染回归）"
    )
    # 同源同序：窄切片恰为宽切片的前 10 条
    assert narrow == wide[:10], "limit 切片结果必须与全量前 N 条同源同序"


def test_first_ten_identical_across_call_orders(monkeypatch):
    """两种调用顺序（宽→窄 / 窄→宽）下，前 10 条必须一致（缓存不再与顺序相关）。"""
    _patch_request(monkeypatch)
    wide_first = etf_mod.fetch_flow("1d", 100)
    narrow_second = etf_mod.fetch_flow("1d", 10)

    # 重新清缓存，反向顺序再来一次
    with etf_mod._CACHE_LOCK:
        etf_mod._cache.clear()
    narrow_first = etf_mod.fetch_flow("1d", 10)
    wide_second = etf_mod.fetch_flow("1d", 100)

    assert narrow_second == narrow_first == wide_first[:10] == wide_second[:10], (
        "前 10 条随调用顺序漂移 —— 缓存内容仍与 limit 耦合"
    )


# ---------------- 2. 结构判据：缓存键不含 limit + 只发一次外部请求 ----------------

def test_cache_key_has_no_limit_dimension(monkeypatch):
    """缓存只应存在 `flow:{period}` 一个键（不因 limit 分叉成多个）。"""
    captured = _patch_request(monkeypatch)
    etf_mod.fetch_flow("1d", 10)
    etf_mod.fetch_flow("1d", 100)

    with etf_mod._CACHE_LOCK:
        flow_keys = [k for k in etf_mod._cache if k.startswith("flow:")]
    assert flow_keys == ["flow:1d"], (
        f"资金流缓存键应恒为 flow:1d（不含 limit），实为 {flow_keys}"
    )
    # 且 pz 请求深度固定为 _FLOW_MAX，与调用侧 limit 无关
    assert captured["params"]["pz"] == etf_mod._FLOW_MAX


def test_single_external_fetch_shared_by_all_limits(monkeypatch):
    """不同 limit 共享同一缓存条目 ⇒ 外部源只被请求一次（不因 limit 重复打源）。"""
    calls = {"n": 0}

    def _counting_request(method, url, *args, params=None, **kwargs):
        calls["n"] += 1
        pz = int((params or {}).get("pz", 20))
        return {"data": {"diff": _FULL[:pz]}}

    monkeypatch.setattr(etf_mod, "_request", _counting_request)
    etf_mod.fetch_flow("1d", 10)
    etf_mod.fetch_flow("1d", 10)
    etf_mod.fetch_flow("1d", 100)

    assert calls["n"] == 1, f"外部源被请求 {calls['n']} 次 —— 缓存未按全量共享"


# ---------------- 3. 边界：切片下限与超量请求 ----------------

@pytest.mark.parametrize("limit", [1, 5, 20, 50])
def test_arbitrary_limit_sliced_exactly(monkeypatch, limit):
    """任意合法 limit 都精确切片（1/5/20/50）。"""
    _patch_request(monkeypatch)
    etf_mod.fetch_flow("1d", 100)  # 先建全量缓存
    assert len(etf_mod.fetch_flow("1d", limit)) == limit


def test_limit_zero_clamped_to_one(monkeypatch):
    """`limit<=0` 由 `max(1, int(limit))` 夹到 1（API 层 ge=1，数据层仍自守）。"""
    _patch_request(monkeypatch)
    assert len(etf_mod.fetch_flow("1d", 0)) == 1


def test_flow_max_constant_is_100(monkeypatch):
    """结构锁：`_FLOW_MAX` 固定深度为 100（覆盖 overview 的 limit=100 与 flow 的 ≤50）。"""
    assert etf_mod._FLOW_MAX == 100


def test_source_has_no_limit_in_cache_content(monkeypatch):
    """静态锁：`_build` 内不得再出现按调用侧 `limit` 的请求/截断。"""
    import inspect

    src = inspect.getsource(etf_mod.fetch_flow)
    assert '"pz": _FLOW_MAX' in src, "请求深度必须固定为 _FLOW_MAX"
    assert "max(20, limit)" not in src, "`max(20, limit)` 请求深度写法不得回流"
    assert "diff[:limit]" not in src, "按调用侧 limit 的缓存内容截断不得回流"
    assert "diff[:_FLOW_MAX]" in src, "全量构建应在 _FLOW_MAX 处封顶"
