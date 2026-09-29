# -*- coding: utf-8 -*-
"""冷启动并发探针 · /api/v1/ops/lineage（kou-lineage-40951）。

目的：构造「缓存全空 + 8s 内 4 次并发」场景，如实记录每次响应的
HTTP status / 业务 code / 耗时 / message，用于回答审计附-2 的
「冷启动是否抛 ERR_UNIFIED_TASK_CONFLICT(40951)」。

不含业务改动，仅探测。用法：
    python docs/audit-2026-09-27/lineage_cold_concurrency_probe.py [--clear]
"""
from __future__ import annotations

import argparse
import asyncio
import json
import os
import sys
import time
import urllib.error
import urllib.request

ROOT = os.path.dirname(os.path.abspath(__file__))
PROJ = os.path.dirname(os.path.dirname(ROOT))
BACKEND = os.path.join(PROJ, "backend")
sys.path.insert(0, BACKEND)

BASE = "http://127.0.0.1:8000"
PATH = "/api/v1/ops/lineage"
CONCURRENCY = 4


def _admin_token() -> str:
    try:
        with open(os.path.join(PROJ, ".env"), encoding="utf-8") as f:
            for line in f:
                if line.startswith("ADMIN_TOKEN="):
                    return line.split("=", 1)[1].strip()
    except OSError:
        pass
    return ""


ADMIN_TOKEN = _admin_token()


def clear_lineage_cache() -> None:
    """删除 lineage 主键 + SWR 影子键（Redis 与进程内 LRU 无法从外部清，故只清 Redis）。"""
    import datetime
    from app.cache.keys import k_ops_lineage
    from app.core.config import get_settings
    from redis import Redis

    s = get_settings()
    r = Redis(host=s.REDIS_HOST, port=s.REDIS_PORT, db=s.REDIS_DB,
              password=s.REDIS_PASSWORD, socket_timeout=3)
    key = k_ops_lineage(datetime.date.today().isoformat())
    n = r.delete(key, key + ":swr")
    print(f"[clear] redis 删除 lineage 键 {key} -> {n} 个")
    print(f"[clear] 剩余 exists={r.exists(key)} swr_exists={r.exists(key + ':swr')}")


def _one(i: int, t0: float) -> dict:
    headers = {"Accept": "application/json"}
    if ADMIN_TOKEN:
        headers["Authorization"] = "Bearer " + ADMIN_TOKEN
    req = urllib.request.Request(BASE + PATH, headers=headers, method="GET")
    ts = time.time()
    try:
        with urllib.request.urlopen(req, timeout=120) as resp:
            raw = resp.read().decode("utf-8", "replace")
            return {"i": i, "start": ts - t0, "status": resp.status,
                    "elapsed": time.time() - ts, "raw": raw, "err": ""}
    except urllib.error.HTTPError as e:
        return {"i": i, "start": ts - t0, "status": e.code,
                "elapsed": time.time() - ts,
                "raw": e.read().decode("utf-8", "replace"), "err": ""}
    except Exception as e:  # noqa: BLE001
        return {"i": i, "start": ts - t0, "status": None,
                "elapsed": time.time() - ts, "raw": "",
                "err": f"{type(e).__name__}: {e}"}


async def run() -> None:
    t0 = time.time()
    results = await asyncio.gather(
        *(asyncio.to_thread(_one, i, t0) for i in range(CONCURRENCY)))
    print(f"\n=== 冷启动并发 {CONCURRENCY} 路（同一瞬间发出）===")
    codes: dict[int | str, int] = {}
    for r in results:
        code: int | str = "?"
        msg = ""
        nodes = "-"
        from_cache = "-"
        try:
            j = json.loads(r["raw"])
            code = j.get("code")
            msg = str(j.get("message"))[:60]
            d = j.get("data") or {}
            from_cache = d.get("from_cache")
            nodes = len(d.get("nodes") or [])
        except Exception:  # noqa: BLE001
            msg = (r["raw"] or r["err"])[:80]
        codes[code] = codes.get(code, 0) + 1
        print(f"  #{r['i']}: 发出+{r['start']*1000:6.0f}ms | HTTP={r['status']} "
              f"code={code} | {r['elapsed']*1000:9.1f}ms | from_cache={from_cache} "
              f"nodes={nodes} | {msg}")
    print(f"\n  code 分布: {codes}")
    print(f"  墙钟总耗时: {time.time() - t0:.2f}s")
    print("  ⚠️ 若出现 code=40951 / ERR_UNIFIED_TASK_CONFLICT 即为审计附-2 所述外泄")


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--clear", action="store_true", help="先清 Redis 缓存键")
    args = ap.parse_args()
    if args.clear:
        clear_lineage_cache()
    asyncio.run(run())


if __name__ == "__main__":
    main()
