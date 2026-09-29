#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""AQP 冒烟 v3：
1) 用正确参数复测 v2 中因样例值不当而 40000 的 GET 端点（区分"参数未确认"与"功能不可用"）
2) 验证 OpenAPI 声明 PUT 但服务端报 Method Not Allowed 的端点
3) 冷/热（第 1 次 vs 第 2 次）耗时对比，观察缓存效果
只读。
"""
import json
import os
import time

import requests

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
BASE = "http://127.0.0.1:8000"
OUT = os.path.join(ROOT, "docs", "audit", "_smoke3_result.json")


def load_env():
    env = {}
    p = os.path.join(ROOT, ".env")
    if os.path.exists(p):
        with open(p, encoding="utf-8", errors="replace") as f:
            for line in f:
                line = line.strip()
                if not line or line.startswith("#") or "=" not in line:
                    continue
                k, v = line.split("=", 1)
                env[k.strip()] = v.strip().strip('"').strip("'")
    return env


ENV = load_env()
HEAD = {"Authorization": "Bearer " + ENV.get("ADMIN_TOKEN", "")}
sess = requests.Session()
sess.trust_env = False

# (method, path, params) —— 正确样例值
CASES = [
    ("GET", "/api/v1/etf/detail/510300", {"kline_period": "day"}),
    ("GET", "/api/v1/etf/flow", {"period": "1d", "limit": 10}),
    ("GET", "/api/v1/etf/hot", {"limit": 5, "sort": "amount"}),
    ("GET", "/api/v1/etf/performance",
     {"symbols": "510300,510500,159915", "metric": "pct", "period": "1y"}),
    ("GET", "/api/v1/etf/scale", {"period": "1m", "top_n": 10}),
    ("GET", "/api/v1/etf/list", {"q": "", "limit": 30}),
    ("GET", "/api/v1/market/index/kline", {"code": "sh000001", "limit": 400}),
    ("GET", "/api/v1/market/overview", {"date": "20260911"}),
    ("GET", "/api/v1/market/overview/daily", {"date": "20260911"}),
    ("GET", "/api/v1/screener",
     {"date": "20260911", "strategy": "alpha_basic_v1", "top_k": 50, "board": "all"}),
    ("GET", "/api/v1/screener/stocks",
     {"board": "all", "industry": "all", "page": 1, "page_size": 20}),
    ("GET", "/api/v1/stock/600519.SH/kline",
     {"start": "2026-08-01", "end": "2026-09-11", "adjust": "none"}),
    ("GET", "/api/v1/export/screener", {"date": "20260911"}),
    ("GET", "/api/v1/settings", {}),
]

# PUT 方法验证
PUT_CASES = [
    ("PUT", "/api/v1/alerts/rules/r-0001", None),
    ("PUT", "/api/v1/settings/engine", None),
    ("PUT", "/api/v1/settings/preferences", None),
]

# 冷/热对比目标（v2 中慢的端点）
WARM_TARGETS = [
    ("GET", "/api/v1/datacenter/datasets", {}),
    ("GET", "/api/v1/datacenter/quality", {}),
    ("GET", "/api/v1/datacenter/overview", {}),
    ("GET", "/api/v1/market/overview", {"date": "20260911"}),
    ("GET", "/api/v1/market/overview/daily", {"date": "20260911"}),
    ("GET", "/api/v1/market/overview/rt", {}),
    ("GET", "/api/v1/research/lab/yearly", {}),
    ("GET", "/api/v1/screener/stocks", {"board": "all", "page": 1, "page_size": 20}),
    ("GET", "/api/v1/etf/list", {"limit": 30}),
    ("GET", "/api/v1/stock/600519.SH/panels", {}),
]


def call(m, p, params=None, timeout=200):
    url = BASE + p
    t0 = time.time()
    try:
        if m == "GET":
            r = sess.get(url, headers=HEAD, params=params or {}, timeout=timeout)
        else:
            r = sess.request(m, url, headers=HEAD, json={}, timeout=timeout)
        ms = int((time.time() - t0) * 1000)
        try:
            j = r.json()
            biz = j.get("code") if isinstance(j, dict) else None
            msg = str(j.get("message") or j.get("detail") or "")[:200] if isinstance(j, dict) else ""
            n = len(j.get("data")) if isinstance(j, dict) and isinstance(j.get("data"), (list, dict)) else None
        except Exception:
            biz, msg, n = None, r.text[:200], None
        return dict(method=m, path=p, status=r.status_code, biz=biz, msg=msg, ms=ms, dataLen=n)
    except Exception as e:
        return dict(method=m, path=p, status=-1, biz=None,
                    msg=f"{type(e).__name__}: {str(e)[:150]}",
                    ms=int((time.time() - t0) * 1000), dataLen=None)


def main():
    out = {"retry": [], "put": [], "warm": []}

    print("=== A. 正确参数复测 ===")
    for m, p, prm in CASES:
        r = call(m, p, prm)
        r["params"] = prm
        out["retry"].append(r)
        print(f"{m:5} {p[:46]:46} {r['status']:>4} biz={str(r['biz']):>6} "
              f"{r['ms']:>7}ms len={r['dataLen']} {r['msg'][:70]}", flush=True)

    print("\n=== B. PUT 方法验证 ===")
    for m, p, _ in PUT_CASES:
        r = call(m, p)
        # 同时试 POST 看是否路由只认 POST
        r2 = call("POST", p)
        r["post_result"] = r2
        out["put"].append(r)
        print(f"PUT  {p[:44]:44} {r['status']:>4} biz={str(r['biz']):>6} {r['msg'][:60]}")
        print(f"POST {p[:44]:44} {r2['status']:>4} biz={str(r2['biz']):>6} {r2['msg'][:60]}",
              flush=True)

    print("\n=== C. 冷/热耗时对比（同一端点连打 2 次） ===")
    for m, p, prm in WARM_TARGETS:
        a = call(m, p, prm)
        b = call(m, p, prm)
        rec = dict(path=p, first_ms=a["ms"], second_ms=b["ms"],
                   first_biz=a["biz"], second_biz=b["biz"],
                   first_status=a["status"], second_status=b["status"])
        out["warm"].append(rec)
        print(f"{p[:46]:46} 1st={a['ms']:>7}ms(biz={a['biz']})  "
              f"2nd={b['ms']:>7}ms(biz={b['biz']})", flush=True)

    json.dump(out, open(OUT, "w", encoding="utf-8"), ensure_ascii=False, indent=1)
    print("\nWROTE", OUT)


if __name__ == "__main__":
    main()
