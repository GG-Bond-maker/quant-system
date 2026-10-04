"""端到端 HTTP 探针（in-process ASGI，不绑端口、不跑 lifespan 副作用）。

用 httpx.ASGITransport 直接调用 FastAPI app 的真实路由，验证
`GET /api/v1/market/overview/global` 与 `/overview/rt` 的完整返回形状。
不触发 lifespan（跳过 DB/日历/预热/后台调度），避免副作用。
"""
from __future__ import annotations

import asyncio
import json
import sys
import time
from pathlib import Path

BACKEND_ROOT = Path(__file__).resolve().parents[3] / "backend"
if not str(BACKEND_ROOT) in sys.path:
    sys.path.insert(0, str(BACKEND_ROOT))

import httpx  # noqa: E402


async def call(app, path: str) -> None:
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://probe") as c:
        t0 = time.time()
        try:
            r = await c.get(path, timeout=90.0)
            dt = time.time() - t0
            print(f"\nGET {path} -> HTTP {r.status_code} ({dt:.2f}s)", flush=True)
            try:
                body = r.json()
            except Exception:  # noqa: BLE001
                print(f"  body(非JSON, 前500字): {r.text[:500]}", flush=True)
                return
            data = body.get("data", body)
            print(f"  code={body.get('code')} msg={body.get('message')}", flush=True)
            if isinstance(data, dict) and "items" in data:
                print(f"  status={data.get('status')} failed={data.get('failed')}",
                      flush=True)
                for it in data.get("items", []):
                    print(f"    - {it.get('name')} close={it.get('close')} "
                          f"pct={it.get('pct')} data_date={it.get('data_date')} "
                          f"source={it.get('source')} as_of={it.get('as_of')}",
                          flush=True)
            else:
                print(f"  data keys={list(data.keys()) if isinstance(data, dict) else type(data)}",
                      flush=True)
                print(f"  raw={json.dumps(body, ensure_ascii=False, default=str)[:800]}",
                      flush=True)
        except Exception as e:  # noqa: BLE001
            dt = time.time() - t0
            print(f"\nGET {path} -> FAIL {type(e).__name__}: {e} ({dt:.2f}s)",
                  flush=True)


async def main() -> None:
    import app.main as m
    print("app imported", flush=True)
    for p in ["/api/v1/market/overview/global",
              "/api/v1/market/overview/rt"]:
        await call(m.app, p)


if __name__ == "__main__":
    asyncio.run(main())
