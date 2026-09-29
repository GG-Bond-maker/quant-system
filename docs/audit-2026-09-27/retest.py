# -*- coding: utf-8 -*-
"""第二轮：超时端点长超时复测 + 关键端点正确参数复测。"""
import json
import os
import time
import urllib.error
import urllib.parse
import urllib.request

BASE = "http://127.0.0.1:8000"
ROOT = os.path.dirname(os.path.abspath(__file__))
env_path = os.path.join(os.path.dirname(os.path.dirname(ROOT)), ".env")
TOKEN = ""
with open(env_path, encoding="utf-8") as f:
    for line in f:
        if line.startswith("ADMIN_TOKEN="):
            TOKEN = line.split("=", 1)[1].strip()
            break
H = {"Authorization": "Bearer " + TOKEN, "Accept": "application/json"}


def call(method, path, body=None, timeout=90, params=None):
    url = BASE + path
    if params:
        url += "?" + urllib.parse.urlencode(params)
    data = json.dumps(body).encode() if body is not None else None
    h = dict(H)
    if data:
        h["Content-Type"] = "application/json"
    req = urllib.request.Request(url, data=data, headers=h, method=method)
    t0 = time.time()
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            raw = r.read(3000).decode("utf-8", "replace")
            return r.status, time.time() - t0, raw
    except urllib.error.HTTPError as e:
        return e.code, time.time() - t0, e.read(3000).decode("utf-8", "replace")
    except Exception as e:
        return None, time.time() - t0, f"{type(e).__name__}: {e}"


ASSETS = [{"code": "600519.SH", "weight": 0.5}, {"code": "000001.SZ", "weight": 0.5}]

print("===== A. 超时端点长超时复测（timeout=90s）=====")
for p in ["/api/v1/datacenter/datasets", "/api/v1/datacenter/mirror/status",
          "/api/v1/datacenter/quality", "/api/v1/ops/lineage"]:
    st, el, raw = call("GET", p, timeout=90)
    print(f"GET {p:42} {st} {el:6.2f}s {raw[:150]}")

print("\n===== B. 正确参数复测 =====")
cases = [
    ("GET", "/api/v1/stock/600519.SH/profile", None, None),
    ("GET", "/api/v1/stock/600519.SH/kline", None, {"start": "20240101", "end": "20240630"}),
    ("GET", "/api/v1/market/quotes", None, {"symbols": "600519.SH,000001.SZ"}),
    ("GET", "/api/v1/watchlist/dashboard", None, {"symbols": "600519.SH,000001.SZ"}),
    ("GET", "/api/v1/watchlist/correlation", None, {"symbols": "600519.SH,000001.SZ"}),
    ("GET", "/api/v1/screener/watchlist", None, {"symbols": "600519.SH,000001.SZ"}),
    ("POST", "/api/v1/desk/attribution", {"assets": ASSETS}, None),
    ("POST", "/api/v1/portfolio/backtest", {"assets": ASSETS, "start_date": "2024-01-01", "end_date": "2024-06-30"}, None),
    ("POST", "/api/v1/research/optimize", {"assets": ASSETS}, None),
    ("POST", "/api/v1/research/stress-test", {"assets": ASSETS}, None),
]
for m, p, b, q in cases:
    st, el, raw = call(m, p, body=b, params=q, timeout=60)
    print(f"{m:5} {p:42} {st} {el:6.2f}s {raw[:150]}")
