import json
import urllib.request
import urllib.error

BASE = "http://127.0.0.1:8000"
TOK = open(r"D:/Python_Project/Alpha Quant Platform/deliverables/gstack/_tmp/tok.txt").read().strip()


def call(path, timeout=120):
    req = urllib.request.Request(BASE + path)
    req.add_header("Authorization", "Bearer " + TOK)
    try:
        r = urllib.request.urlopen(req, timeout=timeout)
        return r.status, json.loads(r.read().decode())
    except urllib.error.HTTPError as e:
        return e.code, json.loads(e.read().decode() or "{}")
    except Exception as e:
        return None, {"err": repr(e)}


paths = [
    "/api/v1/etf/scale?period=1m&top_n=3",
    "/api/v1/etf/detail/511880",
    "/api/v1/etf/overview/series?days=30",
    "/api/v1/market/overview",
    "/api/v1/market/quotes?symbols=600519,000001",
    "/api/v1/market/index/kline?symbol=sh000001",
    "/api/v1/screener?limit=3",
    "/api/v1/screener/watchlist",
    "/api/v1/stock/600519/kline",
    "/api/v1/stock/600519/profile",
    "/api/v1/stock/search?q=茅台",
    "/api/v1/portfolio/search?q=600519",
    "/api/v1/ops/dag",
]
for p in paths:
    st, j = call(p)
    d = j.get("data") if isinstance(j, dict) else None
    print("=" * 90)
    print(f"### GET {p} -> HTTP {st} code={j.get('code') if isinstance(j,dict) else '?'}")
    if isinstance(d, dict):
        print("  keys:", list(d.keys())[:25])
        for k in ("status", "degraded", "reason", "from_cache", "stale"):
            if k in d:
                print(f"  {k} = {json.dumps(d[k], ensure_ascii=False)[:200]}")
        # summarize block statuses
        for k, v in d.items():
            if isinstance(v, dict) and ("status" in v or "reason" in v):
                print(f"  [{k}] {json.dumps({kk:v[kk] for kk in ('status','reason','message') if kk in v}, ensure_ascii=False)[:180]}")
    elif isinstance(d, list):
        print("  list len", len(d), "sample:", json.dumps(d[:1], ensure_ascii=False)[:250])
    else:
        print("  data:", json.dumps(d, ensure_ascii=False)[:300])
