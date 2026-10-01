import json, urllib.request, time
BASE="http://127.0.0.1:8000/api/v1"
def get(path, timeout=150):
    t=time.time()
    try:
        with urllib.request.urlopen(BASE+path, timeout=timeout) as r:
            return r.status, json.loads(r.read().decode()), round(time.time()-t,2)
    except Exception as e:
        return None, {"ERR": f"{type(e).__name__}: {str(e)[:120]}"}, round(time.time()-t,2)
for p in ["/etf/overview", "/market/index/kline?code=sh000001&limit=5", "/stock/600519.SH/panels"]:
    st, body, dt = get(p)
    print("="*60); print(f"GET {p} -> {st} ({dt}s)")
    print(json.dumps(body, ensure_ascii=False)[:1800])
