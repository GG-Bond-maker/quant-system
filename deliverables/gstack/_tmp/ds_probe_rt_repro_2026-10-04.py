"""复现 /overview/rt 降级（连续 2 次，含 refresh=1）。"""
from __future__ import annotations

import asyncio
import sys
import time
from pathlib import Path

BACKEND_ROOT = Path(__file__).resolve().parents[3] / "backend"
if not str(BACKEND_ROOT) in sys.path:
    sys.path.insert(0, str(BACKEND_ROOT))

import httpx  # noqa: E402


async def main() -> None:
    import app.main as m
    transport = httpx.ASGITransport(app=m.app)
    async with httpx.AsyncClient(transport=transport, base_url="http://probe") as c:
        for i, q in enumerate(["", "?refresh=1"]):
            t0 = time.time()
            r = await c.get(f"/api/v1/market/overview/rt{q}", timeout=90.0)
            d = r.json().get("data", {})
            print(f"[{i}] rt{q} -> {r.status_code} status={d.get('status')} "
                  f"indices={d.get('indices', {}).get('status')} "
                  f"money_flow={d.get('money_flow', {}).get('status')} "
                  f"anomalies={d.get('anomalies', {}).get('status')} "
                  f"({time.time()-t0:.2f}s)", flush=True)


if __name__ == "__main__":
    asyncio.run(main())
