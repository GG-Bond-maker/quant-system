import json
import sys
import time
import urllib.request
import urllib.error

BASE = "http://127.0.0.1:8000"
TOK = open(r"D:/Python_Project/Alpha Quant Platform/deliverables/gstack/_tmp/tok.txt").read().strip()


def call(path, timeout=90):
    url = BASE + path
    req = urllib.request.Request(url)
    req.add_header("Authorization", "Bearer " + TOK)
    t0 = time.time()
    try:
        r = urllib.request.urlopen(req, timeout=timeout)
        body = r.read().decode()
        dt = time.time() - t0
        return r.status, body, dt, None
    except urllib.error.HTTPError as e:
        dt = time.time() - t0
        try:
            body = e.read().decode()
        except Exception:
            body = ""
        return e.code, body, dt, "HTTPError"
    except Exception as e:
        dt = time.time() - t0
        return None, "", dt, repr(e)


def summarize(path, timeout=90):
    status, body, dt, err = call(path, timeout)
    print("=" * 100)
    print(f"GET {path}  ->  HTTP {status}  ({dt:.2f}s)  err={err}")
    if not body:
        print("  <empty body>")
        return status, None
    try:
        j = json.loads(body)
    except Exception:
        print("  NON-JSON:", body[:400])
        return status, None
    print("  code=", j.get("code"), "message=", str(j.get("message"))[:120])
    data = j.get("data")
    if isinstance(data, dict):
        # print top-level keys and status-ish fields
        keys = list(data.keys())
        print("  data.keys =", keys)
        for k in keys:
            v = data[k]
            if k in ("status", "degraded", "reason", "data_freshness", "trade_date",
                     "as_of", "from_cache", "stale"):
                print(f"    {k} = {json.dumps(v, ensure_ascii=False)[:300]}")
        # nested block statuses
        for k in keys:
            v = data[k]
            if isinstance(v, dict):
                sub = {kk: v[kk] for kk in ("status", "reason", "n_ok", "unavailable", "degraded") if kk in v}
                if sub:
                    print(f"    [{k}] {json.dumps(sub, ensure_ascii=False)[:250]}")
    elif isinstance(data, list):
        print("  data = list len", len(data), "first:", json.dumps(data[:1], ensure_ascii=False)[:300])
    else:
        print("  data =", json.dumps(data, ensure_ascii=False)[:300])
    return status, j


ENDPOINTS = [
    "/health/ready",
    "/api/v1/market/overview/rt",
    "/api/v1/market/overview/daily",
    "/api/v1/etf/overview",
    "/api/v1/etf/flow?period=1d&limit=10",
    "/api/v1/etf/list?limit=5",
    "/api/v1/etf/hot?limit=5",
    "/api/v1/etf/scale?period=1d&top_n=5",
    "/api/v1/etf/detail/510300",
    "/api/v1/watchlist/dashboard?symbols=600519,000001",
    "/api/v1/stock/600519/panels",
    "/api/v1/stock/600519/profile",
    "/api/v1/stock/600519/kline",
    "/api/v1/screener/stocks?limit=3",
    "/api/v1/research/overview",
    "/api/v1/datacenter/overview",
]

if __name__ == "__main__":
    only = sys.argv[1] if len(sys.argv) > 1 else None
    for e in ENDPOINTS:
        if only and only not in e:
            continue
        try:
            summarize(e)
        except Exception as ex:
            print("PROBE ERROR", e, repr(ex))
