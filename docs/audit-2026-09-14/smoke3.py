"""
第三轮：修正 assets=[{code,weight}] 结构 + 合法日期区间，复测剩余端点。
"""
from __future__ import annotations

import json
import sys
import time
import urllib.error
import urllib.parse
import urllib.request

BASE = sys.argv[1] if len(sys.argv) > 1 else "http://127.0.0.1:8124"
TOKEN = sys.argv[2] if len(sys.argv) > 2 else ""
OUT = []


def call(method, path, token=None, params=None, body=None, timeout=180):
    url = BASE + path
    if params:
        url += "?" + urllib.parse.urlencode(params)
    data = json.dumps(body).encode() if body is not None else None
    headers = {"Accept": "application/json"}
    if data:
        headers["Content-Type"] = "application/json"
    if token:
        headers["Authorization"] = f"Bearer {token}"
    req = urllib.request.Request(url, data=data, headers=headers, method=method)
    t0 = time.perf_counter()
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            txt, status, srv = (r.read().decode("utf-8", "replace"), r.status,
                                r.headers.get("X-Response-Time-MS"))
    except urllib.error.HTTPError as e:
        txt, status, srv = (e.read().decode("utf-8", "replace"), e.code,
                            e.headers.get("X-Response-Time-MS"))
    except Exception as e:  # noqa: BLE001
        return {"http": 0, "code": None, "msg": f"{type(e).__name__}: {e}",
                "ms": int((time.perf_counter() - t0) * 1000), "data": None, "srv": None}
    ms = int((time.perf_counter() - t0) * 1000)
    try:
        j = json.loads(txt)
        return {"http": status, "code": j.get("code"), "msg": j.get("message"),
                "ms": ms, "data": j.get("data"), "srv": srv}
    except Exception:  # noqa: BLE001
        return {"http": status, "code": None, "msg": "NON_JSON", "ms": ms,
                "data": txt[:200], "srv": srv}


A = [{"code": "600519.SH", "weight": 0.5}, {"code": "000001.SZ", "weight": 0.5}]
CASES = [
    ("POST", "/api/v1/portfolio/backtest", None,
     {"assets": A, "start_date": "2024-01-02", "end_date": "2024-06-28"}),
    ("POST", "/api/v1/research/stress-test", None, {"assets": A}),
    ("POST", "/api/v1/backtest/strategy-run", None,
     {"start": "2024-01-02", "end": "2024-06-28", "symbols": ["600519.SH"]}),
    ("POST", "/api/v1/desk/attribution", None, {"assets": A}),
    ("POST", "/api/v1/export/backtest", None,
     {"start": "2024-01-02", "end": "2024-06-28", "top_k": 5}),
]

for method, path, params, body in CASES:
    r = call(method, path, TOKEN, params, body)
    flag = "OK " if r["code"] == 0 else "ERR"
    print(f"[{flag}] {method:6} {path:42} http={r['http']} code={r['code']} "
          f"{r['ms']:>7}ms srv={r.get('srv')} {r['msg']}")
    s = json.dumps(r.get("data"), ensure_ascii=False, default=str)[:500]
    print(f"        data: {s}")
    OUT.append({"method": method, "path": path, "http": r["http"], "code": r["code"],
                "msg": r["msg"], "ms": r["ms"], "srv": r.get("srv"), "sample": s})

with open(r"D:\Python_Project\Alpha Quant Platform\docs\audit-2026-09-14\smoke3-results.json",
          "w", encoding="utf-8") as f:
    json.dump(OUT, f, ensure_ascii=False, indent=2)
print("done", len(OUT))
