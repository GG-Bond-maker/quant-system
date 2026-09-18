"""独立验证 /datacenter/sync/fetch 在其他 pipeline slot 忙时直接拒绝。"""
from __future__ import annotations

import asyncio
import sys

sys.path.insert(0, r"D:\Python_Project\Alpha Quant Platform\backend")

from app.api.v1.datacenter import FetchRequest, trigger_fetch
from app.core.errors import ERR_PIPELINE_BUSY
from app.core.pipeline_lock import pipeline_slot

request = FetchRequest(
    asset_type="stock", start="2026-01-02", end="2026-01-02",
    symbols=["000001.SZ"],
)
with pipeline_slot("sync"):
    response = asyncio.run(trigger_fetch(request, {"username": "qa", "role": "researcher"}))

assert response.code == ERR_PIPELINE_BUSY, response
assert "sync" in response.message, response
print({"code": response.code, "message": response.message, "data": response.data})
