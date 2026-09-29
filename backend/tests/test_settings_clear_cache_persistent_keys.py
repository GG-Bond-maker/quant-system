"""`POST /api/v1/settings/data/cache/clear` 不得删除持久状态键（ETF 快照存档）。

## 缺陷（2026-09-28 事故）

`clear_cache` 原实现按命名空间无差别删除：

    keys = [k async for k in r.scan_iter(match="aqp:*", count=500)]
    if keys:
        await r.delete(*keys)

而 `aqp:etf:snap:history` **不是缓存**，是 ETF 每日概览快照序列的**唯一副本**
（只写 Redis，磁盘与数据库都没有第二份，见
``app/api/v1/etf.py::append_etf_snapshot``）。一次「清除缓存」即把 8 条归档
删成 1 条 —— 7 天永久丢失，ETF 中心 KPI 序列随之退化为
``status=unavailable``（存档不足 6 天）。

## 修法与验证面

1. 持久键白名单落在 ``app/cache/keys.py``（``PERSISTENT_KEYS`` /
   :func:`is_persistent`），``etf.py`` 与 ``kpi_series.py`` 都从
   ``k_etf_snap_history()`` 取键名 —— 单一事实源；
2. ``clear_cache`` 先过滤再删，并把**真实删除键数** ``deleted`` 与被保留的
   ``protected`` 写进响应体（``deleted`` 来自 Redis ``DEL`` 返回值，不猜）。

本文件**双向**断言：
- 正向：存档键在清理后仍在、值未被破坏，普通缓存键确实被删；
- 反向：清理时根本没有把存档键交给 ``DEL``；且实现里不存在「无过滤的
  ``delete(*keys)``」这个回归形态（AST 静态锁）。

## 隔离

套件强制 ``REDIS_ENABLED=0``，且本文件**不打真实 Redis**：用进程内替身
``_FakeRedis`` 顶替 ``RedisClient._ensure``，只覆盖被测端点用到的
``info / scan_iter / delete``。既验证真实代码路径，又不会误删生产缓存。
"""
from __future__ import annotations

import ast
import inspect
import os
import sys
from pathlib import Path
from typing import Any, AsyncIterator

import orjson
import pytest
from fastapi.testclient import TestClient

BACKEND_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(BACKEND_ROOT))

from app.cache.keys import PERSISTENT_KEYS, is_persistent, k_etf_snap_history  # noqa: E402
from app.cache.redis_client import RedisClient  # noqa: E402
from app.main import app  # noqa: E402

_ADMIN_H = {"Authorization": f"Bearer {os.environ.get('ADMIN_TOKEN', 'aqp-dev-token-change-me')}"}

SNAP_KEY = "aqp:etf:snap:history"
SNAP_BYTES = SNAP_KEY.encode()
# 私有测试键前缀：只落在 _FakeRedis 的内存字典里，不进任何真实存储。
_TEST_CACHE_PREFIX = "aqp:test:clearcache:"


def _archive(n: int = 8) -> bytes:
    """构造一份「n 天归档」的存档字节（形状与 etf.append_etf_snapshot 一致）。"""
    snap = [{"date": f"2026-09-{d:02d}", "total": 1000 + d, "size_yi": 100.0 + d}
            for d in range(11, 11 + n)]
    return orjson.dumps(snap)


class _FakeRedis:
    """``clear_cache`` 用到的 Redis 子集替身（进程内，无网络、无副作用）。

    只实现 ``info`` / ``scan_iter`` / ``delete`` / ``get`` / ``set``；
    ``scan_iter`` 默认产出 **bytes**（真实 redis-py 在
    ``decode_responses=False`` 下就是 bytes），以保证过滤逻辑的 bytes 分支
    被真正覆盖。
    """

    def __init__(self, store: dict[bytes, bytes] | None = None) -> None:
        self.store: dict[bytes, bytes] = dict(store or {})
        self.delete_calls: list[list[bytes]] = []
        self._used_memory = 64 * 1024 * 1024

    async def info(self, section: str = "memory") -> dict[str, Any]:
        """内存采样：删除后水位按被删值长度下降（够 clear_cache 取差值）。"""
        return {"used_memory": self._used_memory}

    async def scan_iter(self, match: str = "*", count: int = 100) -> AsyncIterator[bytes]:
        import fnmatch

        for key in list(self.store):
            if fnmatch.fnmatch(key.decode("utf-8", errors="replace"), match):
                yield key

    async def delete(self, *keys: bytes) -> int:
        """删除并返回**真实**删除条数（语义与 Redis DEL 一致）。"""
        self.delete_calls.append(list(keys))
        removed = 0
        for key in keys:
            value = self.store.pop(key, None)
            if value is not None:
                removed += 1
                self._used_memory -= max(1, len(value))
        return removed

    async def get(self, key: bytes) -> bytes | None:
        return self.store.get(key)

    async def set(self, key: bytes, value: bytes, ex: int | None = None) -> bool:
        self.store[key] = value
        return True


@pytest.fixture(scope="module")
def client() -> Any:
    with TestClient(app) as c:
        yield c


@pytest.fixture()
def fake_redis(monkeypatch: pytest.MonkeyPatch) -> _FakeRedis:
    """把 ``RedisClient._ensure`` 顶成进程内替身，并交给用例使用。"""
    fake = _FakeRedis()
    monkeypatch.setattr(RedisClient, "_ensure", lambda: fake)
    return fake


def _post_clear(client: TestClient) -> dict[str, Any]:
    """调用被测端点并返回信封里的 ``data``（先断言业务码为 0）。"""
    r = client.post("/api/v1/settings/data/cache/clear", headers=_ADMIN_H)
    assert r.status_code == 200, r.text
    body = r.json()
    assert body.get("code") == 0, f"清理缓存失败: {body}"
    return body["data"]


# ---------------- 正向：存档留下，缓存删掉 ----------------
def test_clear_cache_keeps_snapshot_archive_and_deletes_cache_keys(
        client: TestClient, fake_redis: _FakeRedis) -> None:
    """存档键必须**原值保留**，普通缓存键必须被删，且 delete 是真实计数。"""
    archive = _archive(8)
    fake_redis.store[SNAP_BYTES] = archive
    cache_keys = [f"{_TEST_CACHE_PREFIX}{i}".encode() for i in range(3)]
    for k in cache_keys:
        fake_redis.store[k] = b"cache-payload"

    data = _post_clear(client)

    # 存档：键还在，值一个字节都没变（不是「重建出一条」）
    assert fake_redis.store.get(SNAP_BYTES) == archive, (
        "ETF 快照存档被清掉了 —— 2026-09-28 事故复发（7 天归档不可再生）")
    # 缓存：确实删掉了（防「为避免误删而什么都不删」的反向退化）
    assert not [k for k in fake_redis.store if k.startswith(_TEST_CACHE_PREFIX.encode())], (
        f"普通缓存键未被清理: {sorted(k.decode() for k in fake_redis.store)}")
    # 响应体如实披露
    assert data["redis_cleared"] is True, data
    assert data["deleted"] == 3, f"deleted 必须等于真实删除键数: {data}"
    assert data["protected"] == [SNAP_KEY], data
    assert isinstance(data["freed_mb"], (int, float)), data
    # DEL 从未收到存档键
    assert all(SNAP_BYTES not in call for call in fake_redis.delete_calls), (
        f"存档键被送进 DEL: {fake_redis.delete_calls}")
    assert "保留" in data["message"], f"message 应如实说明保留了持久键: {data['message']}"


def test_clear_cache_reports_deleted_zero_when_no_cache_keys(
        client: TestClient, fake_redis: _FakeRedis) -> None:
    """只有持久键时：一个都不删（DEL 都不该发），deleted=0 且不得凑数。"""
    archive = _archive(8)
    fake_redis.store[SNAP_BYTES] = archive

    data = _post_clear(client)

    assert fake_redis.store.get(SNAP_BYTES) == archive
    assert data["deleted"] == 0, f"无缓存键时 deleted 必须是 0（真实计数）: {data}"
    assert data["protected"] == [SNAP_KEY], data
    assert fake_redis.delete_calls == [], (
        f"没有可删的缓存键时不应发起 DEL: {fake_redis.delete_calls}")


# ---------------- 反向：实现里不存在无差别批量删除 ----------------
def test_clear_cache_has_no_unfiltered_bulk_delete() -> None:
    """**回归面**：``clear_cache`` 里任何 ``delete(...)`` 都不得直接吃掉扫描全集。

    缺陷形态是 ``await r.delete(*keys)``（``keys`` 直接来自 ``scan_iter``）。
    这里用 AST 判定：若出现 ``*name`` 形式的删除实参，``name`` 必须是**经过
    ``is_persistent`` 过滤**的列表推导结果 —— 裸 ``*keys`` 一律判红。
    """
    from app.api.v1 import app_settings as mod

    src = inspect.getsource(mod.clear_cache)
    tree = ast.parse(src)

    # 1) 收集「由 is_persistent 过滤得到的」变量名
    def _calls_is_persistent(node: ast.AST) -> bool:
        return any(isinstance(n, ast.Call)
                   and isinstance(n.func, ast.Name)
                   and n.func.id == "is_persistent"
                   for n in ast.walk(node))

    filtered_names: set[str] = set()
    for node in ast.walk(tree):
        value = None
        targets: list[ast.expr] = []
        if isinstance(node, ast.Assign):
            value, targets = node.value, list(node.targets)
        elif isinstance(node, ast.AnnAssign) and node.value is not None:
            value, targets = node.value, [node.target]
        if isinstance(value, ast.ListComp) and _calls_is_persistent(value):
            for t in targets:
                if isinstance(t, ast.Name):
                    filtered_names.add(t.id)
    assert filtered_names, (
        "clear_cache 里找不到经 is_persistent 过滤的列表推导 —— 保护被移除？")

    # 2) 所有 delete(...) 的 *name 实参必须来自上面那份过滤结果
    starred: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute) \
                and node.func.attr == "delete":
            for arg in node.args:
                if isinstance(arg, ast.Starred) and isinstance(arg.value, ast.Name):
                    starred.add(arg.value.id)
    assert starred, "clear_cache 里找不到批量删除调用（实现已变，请复核本锁）"
    assert starred <= filtered_names, (
        f"存在未经持久键过滤的批量删除（裸 *keys 形态回归）: "
        f"{sorted(starred - filtered_names)}")


# ---------------- 单一事实源 ----------------
def test_persistent_key_has_single_source_of_truth() -> None:
    """键名只能有一处定义：三方（keys / etf / kpi_series）必须完全一致。"""
    from app.api.v1 import etf as etf_mod
    from app.data import kpi_series as kpi_mod

    expected = k_etf_snap_history()
    assert expected == SNAP_KEY, expected
    assert etf_mod._SNAP_KEY == expected, "etf.py 仍在自己拼键名"
    assert kpi_mod._ETF_SNAP_KEY == expected, "kpi_series.py 仍在自己拼键名"
    assert PERSISTENT_KEYS == frozenset({expected}), PERSISTENT_KEYS
    assert is_persistent(expected) is True
    assert is_persistent(expected.encode()) is True, "必须兼容 scan_iter 的 bytes"
    assert is_persistent("aqp:market:overview:20260928") is False


def test_no_third_hardcoded_snap_key_literal() -> None:
    """AST 扫描：``app/`` 下不得再出现该键名的硬编码字符串常量。"""
    app_dir = BACKEND_ROOT / "app"
    offenders: list[str] = []
    for path in app_dir.rglob("*.py"):
        try:
            tree = ast.parse(path.read_text(encoding="utf-8"))
        except (SyntaxError, UnicodeDecodeError):  # pragma: no cover - 不应发生
            continue
        for node in ast.walk(tree):
            if isinstance(node, ast.Constant) and node.value == SNAP_KEY:
                rel = path.relative_to(BACKEND_ROOT).as_posix()
                offenders.append(f"{rel}:{getattr(node, 'lineno', '?')}")
    assert not offenders, (
        f"发现第三处硬编码存档键名（应改用 k_etf_snap_history()）: {offenders}")
