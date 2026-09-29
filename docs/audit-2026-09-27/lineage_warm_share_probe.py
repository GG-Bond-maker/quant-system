# -*- coding: utf-8 -*-
"""预热与请求共享判别（kou-lineage-40951）。

用 monkeypatch 计数 `_compute_lineage` 的真实调用次数，精确回答：
「启动预热进行中到达的用户请求，是否与预热共享同一次冷扫」。

预期（修复后）：builds == 1。
不含业务改动，仅探测。
"""
from __future__ import annotations

import asyncio
import datetime
import os
import sys
import time
from unittest.mock import patch

ROOT = os.path.dirname(os.path.abspath(__file__))
PROJ = os.path.dirname(os.path.dirname(ROOT))
sys.path.insert(0, os.path.join(PROJ, "backend"))


async def main() -> None:
    from app.api.v1 import ops as ops_mod
    from app.cache.keys import k_ops_lineage
    from app.core.config import get_settings
    from redis import Redis

    s = get_settings()
    r = Redis(host=s.REDIS_HOST, port=s.REDIS_PORT, db=s.REDIS_DB,
              password=s.REDIS_PASSWORD, socket_timeout=3)
    key = k_ops_lineage(datetime.date.today().isoformat())
    r.delete(key, key + ":swr")
    print(f"[share] 已清空 {key}（主键+影子键）")

    calls = {"n": 0}
    real = ops_mod._compute_lineage

    def counting() -> dict:
        calls["n"] += 1
        print(f"  >>> _compute_lineage 第 {calls['n']} 次真实冷扫开始 "
              f"(t+{time.time()-t0:.1f}s)")
        out = real()
        print(f"  <<< 第 {calls['n']} 次冷扫结束 (t+{time.time()-t0:.1f}s)")
        return out

    t0 = time.time()
    with patch.object(ops_mod, "_compute_lineage", counting):
        warm = asyncio.create_task(ops_mod.warm_lineage_cache())
        await asyncio.sleep(3)          # 预热跑到中途
        print(f"[share] t+3.0s 发出用户请求（预热仍在进行）")
        data = await ops_mod.data_lineage(_user={"role": "admin"})
        await warm
    print(f"[share] 请求返回 code={data.code} nodes={len(data.data['nodes'])} "
          f"from_cache={data.data.get('from_cache')}")
    print(f"[share] _compute_lineage 真实调用次数 = {calls['n']}  "
          f"（1 = 预热与请求共享同一次冷扫）")


if __name__ == "__main__":
    asyncio.run(main())
