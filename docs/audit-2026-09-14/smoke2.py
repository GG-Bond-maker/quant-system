"""
第二轮冒烟：用 OpenAPI 契约里的**正确参数**复测第一轮因参数错误失败的端点。
（第一轮 40000 全是我方参数填错，不是后端缺陷；本轮给出真实可用性结论）

用法：backend/.venv/Scripts/python.exe docs/audit-2026-09-14/smoke2.py <base_url> <admin_token>
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
                "ms": ms, "data": j.get("data"), "srv": srv, "raw": txt[:1200]}
    except Exception:  # noqa: BLE001
        return {"http": status, "code": None, "msg": "NON_JSON", "ms": ms,
                "data": None, "srv": srv, "raw": txt[:200]}


SYM = "600519.SH"
F = "macd_dif"
CASES = [
    ("GET", f"/api/v1/stock/{SYM}/kline", {"start": "20240101", "end": "20240930"}, None),
    ("GET", "/api/v1/screener/watchlist", {"symbols": f"{SYM},000001.SZ"}, None),
    ("GET", "/api/v1/watchlist/dashboard", {"symbols": f"{SYM},000001.SZ"}, None),
    ("GET", "/api/v1/watchlist/correlation", {"symbols": f"{SYM},000001.SZ"}, None),
    ("GET", "/api/v1/portfolio/search", {"q": "600519"}, None),
    ("POST", "/api/v1/backtest/signal-analysis", None,
     {"start": "2024-01-01", "end": "2024-06-30"}),
    ("POST", "/api/v1/backtest/strategy-run", None,
     {"start": "2024-01-01", "end": "2024-06-30", "symbols": [SYM]}),
    ("POST", "/api/v1/portfolio/backtest", None,
     {"assets": [SYM, "000001.SZ"], "start_date": "2024-01-01", "end_date": "2024-06-30"}),
    ("POST", "/api/v1/research/factor-icir", None, {"factors": [F]}),
    ("POST", "/api/v1/research/factor-quantile", None, {"factor": F}),
    ("POST", "/api/v1/research/factor-corr", None, {"factors": [F, "hl_range"]}),
    ("POST", "/api/v1/research/stress-test", None, {"assets": [SYM, "000001.SZ"]}),
    ("POST", "/api/v1/studio/factor-report", None, {"expr": F}),
    ("POST", "/api/v1/studio/alpha-eval", None, {"expr": F}),
    ("POST", "/api/v1/settings/connectors/test", None, {"connector": "akshare"}),
    ("POST", "/api/v1/desk/attribution", None, {"assets": [SYM, "000001.SZ"]}),
]

for method, path, params, body in CASES:
    r = call(method, path, TOKEN, params, body)
    flag = "OK " if r["code"] == 0 else "ERR"
    print(f"[{flag}] {method:6} {path:46} http={r['http']} code={r['code']} "
          f"{r['ms']:>7}ms srv={r.get('srv')} {r['msg']}")
    sample = r.get("data")
    s = json.dumps(sample, ensure_ascii=False, default=str)[:400] if sample is not None else ""
    print(f"        data: {s}")
    OUT.append({"method": method, "path": path, "http": r["http"], "code": r["code"],
                "msg": r["msg"], "ms": r["ms"], "srv": r.get("srv"), "sample": s})

with open(r"D:\Python_Project\Alpha Quant Platform\docs\audit-2026-09-14\smoke2-results.json",
          "w", encoding="utf-8") as f:
    json.dump(OUT, f, ensure_ascii=False, indent=2)
print("done", len(OUT))
