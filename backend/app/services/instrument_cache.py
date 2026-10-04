"""``instrument`` 表的进程级 TTL 快照缓存（watchlist / datacenter **共用**）。

背景（2026-10-04 上线前全检 P2）
--------------------------------
``instrument`` 表（证券基础信息：symbol / name / instrument_type）此前在两处被
**各自**无 LIMIT、无缓存地整表读进内存：

* ``api/v1/watchlist.py::_load_instrument_names`` → ``SELECT symbol, name``（dict）；
* ``api/v1/datacenter.py::_instrument_symbols_by_type`` → ``SELECT symbol ... ORDER BY``（list）。

自选看板每次刷新（默认 60s/页面）都会重读一次全表，且两份实现会**各自漂移**
（本项目红线：禁止重复实现漂移）。这里收敛成**唯一**的快照加载函数，返回
**不可变**的 ``tuple``，调用方无法就地修改污染缓存。

为什么用单一快照、不需要额外缓存 key 维度
----------------------------------------
快照取的是**超集**（``symbol, name, instrument_type`` 三列，按 symbol 排序）——
watchlist 需要的 ``symbol -> name`` 与 datacenter 需要的 ``instrument_type`` 过滤
都能由同一份快照派生，因此不存在"是否需要 name 列"这类 key 差异：一个快照即覆盖
全部消费方，也天然杜绝两份实现分叉。

TTL 取值理由
------------
``instrument`` 表仅在数据同步（每日/手动）时 upsert，变动频率极低；600s（10 分钟）
在"名字/类型不过期太久"与"避免每次看板请求都全表读盘"之间取平衡。
"""
from __future__ import annotations

import sqlite3
import threading
import time
from typing import NamedTuple

from ..core.config import get_settings

# 全表仅在同步时 upsert；600s 足以避免高频读盘，又不会让改名/新上市长期不可见。
_TTL_SECONDS = 600.0


class InstrumentRow(NamedTuple):
    """``instrument`` 表的一行（不可变）。"""

    symbol: str
    name: str
    instrument_type: str


_lock = threading.Lock()
# (写入时刻 monotonic, 快照)；None 表示尚无有效快照。
_snapshot: tuple[float, tuple[InstrumentRow, ...]] | None = None


def _load_from_db() -> tuple[InstrumentRow, ...]:
    """只读 SQLite 读全表并按 symbol 排序（读失败时**不**吞异常，交调用方决定降级口径）。"""
    db = get_settings().SQLITE_PATH
    with sqlite3.connect(f"file:{db}?mode=ro", uri=True, timeout=30) as conn:
        rows = conn.execute(
            "SELECT symbol, name, instrument_type FROM instrument ORDER BY symbol"
        ).fetchall()
    return tuple(InstrumentRow(r[0], r[1], r[2]) for r in rows)


def load_instrument_snapshot() -> tuple[InstrumentRow, ...]:
    """返回 ``instrument`` 全表的**不可变快照**（按 symbol 排序），带进程级 TTL。

    * 缓存的是 ``tuple[InstrumentRow, ...]``（不可变），调用方无法就地修改污染缓存；
    * 命中未过期快照直接返回，否则重读；TTL 到期后自然重读；
    * 读取失败**不写缓存**（下次调用重试），并把异常原样上抛 —— 由调用方
      （watchlist 返回空字典 / datacenter 记 warning 后返回空列表）决定降级口径。
    """
    global _snapshot
    now = time.monotonic()
    with _lock:
        hit = _snapshot
        if hit is not None and now - hit[0] < _TTL_SECONDS:
            return hit[1]
    rows = _load_from_db()
    with _lock:
        _snapshot = (time.monotonic(), rows)
    return rows
