"""第3轮：core/errors.py 通用层改动 —— 多端点参数校验回归探针。

目的：
  1. 覆盖 5+ 模块的端点，各传一个明显非法参数，确认仍返回 40000（非 50000）；
  2. 响应体 details/errors 结构未变形（list[dict]，含 type/loc/msg，ctx 无异常对象）；
  3. 目标场景 /portfolio/backtest start>end → 40000；
  4. 记录 app.log 行数，供随后核对无 PydanticSerializationError / unhandled error。

运行：backend/.venv/Scripts/python.exe docs/audit-2026-09-27/_r3_endpoint_probe.py
"""
from __future__ import annotations

import json
import urllib.error
import urllib.request
from pathlib import Path

BASE = "http://127.0.0.1:8000"
TOKEN = "cTwPZSdPU6WT_4DEcc02Z4KlCo4IgiN5HFoQQRMso2I"
HDR = {"Authorization": f"Bearer {TOKEN}", "Content-Type": "application/json"}
LOG = Path(__file__).resolve().parents[2] / "backend" / "logs" / "app.log"

# (模块, method, path, 非法参数说明, body_or_None)
CASES = [
    ("portfolio", "POST", "/api/v1/portfolio/backtest", "start_date>end_date",
     {"assets": [{"code": "600519", "type": "stock", "weight": 1.0}],
      "start_date": "2024-06-30", "end_date": "2024-01-01"}),
    ("portfolio", "POST", "/api/v1/portfolio/backtest", "weight=1.5(>le)",
     {"assets": [{"code": "600519", "type": "stock", "weight": 1.5}],
      "start_date": "2024-01-01", "end_date": "2024-06-30"}),
    ("portfolio", "GET", "/api/v1/portfolio/search?q=x&limit=0", "limit=0(<ge)", None),
    ("alerts", "GET", "/api/v1/alerts/events?unread=0&limit=0", "limit=0(<ge)", None),
    ("alerts", "GET", "/api/v1/alerts/events?unread=9&limit=50", "unread=9(>le=1)", None),
    ("etf", "GET", "/api/v1/etf/hot?limit=0", "limit=0(<ge)", None),
    ("etf", "GET", "/api/v1/etf/hot?sort=bad", "sort 不在 pattern", None),
    ("etf", "GET", "/api/v1/etf/list?page=0&page_size=9999", "page=0/page_size 超界", None),
    ("market", "GET", "/api/v1/market/index/kline?symbol=000001.SH&limit=1", "limit=1(<ge=30)", None),
    ("screener", "GET", "/api/v1/screener?top_k=0", "top_k=0(<ge=1)", None),
    ("datacenter", "GET", "/api/v1/datacenter/logs?limit=0", "limit=0(<ge=1)", None),
    ("notify", "GET", "/api/v1/notify/stream?ttl=1", "ttl=1(<ge=5)", None),
    ("research", "GET", "/api/v1/research/feature-importance?top_k=0", "top_k=0(<ge=1)", None),
    ("desk", "GET", "/api/v1/desk/orders?limit=0", "limit=0(<ge=1)", None),
]


def call(method: str, path: str, body):
    url = BASE + path
    data = json.dumps(body).encode() if body is not None else None
    req = urllib.request.Request(url, data=data, headers=HDR, method=method)
    try:
        with urllib.request.urlopen(req, timeout=40) as r:
            return r.status, json.loads(r.read().decode())
    except urllib.error.HTTPError as e:
        try:
            return e.code, json.loads(e.read().decode())
        except Exception:
            return e.code, {"_raw": "<non-json>"}
    except Exception as e:  # noqa: BLE001
        return -1, {"_error": f"{type(e).__name__}: {e}"}


def structure_ok(payload) -> tuple[bool, str]:
    if not isinstance(payload, dict):
        return False, "响应非 dict"
    if "code" not in payload:
        return False, "缺 code"
    data = payload.get("data")
    if data is None:
        return True, "data=None"
    if not isinstance(data, list):
        return False, f"data 非 list（{type(data).__name__}）"
    for it in data:
        if not isinstance(it, dict):
            return False, "error item 非 dict"
        for key in ("type", "loc", "msg"):
            if key not in it:
                return False, f"error item 缺字段 {key}: {it}"
        ctx = it.get("ctx")
        if isinstance(ctx, dict):
            for v in ctx.values():
                if not isinstance(v, (str, int, float, bool, type(None), list, dict)):
                    return False, f"ctx 值仍为不可序列化类型 {type(v).__name__}"
    return True, f"{len(data)} 条错误项"


def main() -> None:
    before = LOG.stat().st_size if LOG.exists() else 0
    print(f"app.log size before = {before}")
    print(f"{'模块':<11}{'code':<7}{'期望':<7}{'结构':<6}说明")
    all_ok = True
    for mod, method, path, why, body in CASES:
        st, payload = call(method, path, body)
        code = payload.get("code")
        ok_struct, note = structure_ok(payload)
        expect = 40000
        verdict = "OK" if code == expect and ok_struct else "FAIL"
        if verdict == "FAIL":
            all_ok = False
        print(f"{mod:<11}{str(code):<7}{expect:<7}{'好' if ok_struct else '坏':<6}{verdict} | {why} | {note}")
        if code not in (40000, 0):
            print(f"           msg={payload.get('message')!r} trace={payload.get('trace_id')}")
    print(f"\nALL_OK={all_ok}")
    print(f"app.log size after = {LOG.stat().st_size if LOG.exists() else 0}")


if __name__ == "__main__":
    main()
