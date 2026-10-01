import json
import urllib.request
import urllib.error

BASE = "http://127.0.0.1:8000"
TOK = open(r"D:/Python_Project/Alpha Quant Platform/deliverables/gstack/_tmp/tok.txt").read().strip()


def call(path, timeout=90):
    req = urllib.request.Request(BASE + path)
    req.add_header("Authorization", "Bearer " + TOK)
    try:
        r = urllib.request.urlopen(req, timeout=timeout)
        return r.status, json.loads(r.read().decode())
    except urllib.error.HTTPError as e:
        return e.code, json.loads(e.read().decode() or "{}")


for path in [
    "/api/v1/market/overview/daily",
    "/api/v1/etf/overview",
    "/api/v1/etf/flow?period=1d&limit=10",
    "/api/v1/etf/list?limit=3",
    "/api/v1/etf/hot?limit=3",
    "/api/v1/etf/scale?period=1d&top_n=3",
    "/api/v1/watchlist/dashboard?symbols=600519,000001",
    "/api/v1/stock/600519/panels",
    "/api/v1/screener/stocks?limit=3",
    "/api/v1/research/overview",
    "/api/v1/datacenter/overview",
    "/api/v1/etf/detail/510300",
]:
    try:
        st, j = call(path)
    except Exception as e:
        print(f"### {path} -> ERR {e!r}")
        continue
    print("=" * 100)
    print(f"### GET {path} -> HTTP {st} code={j.get('code')}")
    d = j.get("data")
    txt = json.dumps(d, ensure_ascii=False)
    print(txt[:2500])
    print(f"... [len={len(txt)}]")
