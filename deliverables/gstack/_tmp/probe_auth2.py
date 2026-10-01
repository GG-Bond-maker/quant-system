import json, urllib.request, time
BASE="http://127.0.0.1:8000/api/v1"
TOK = open("deliverables/gstack/_tmp/token.txt").read().strip()
def get(path, timeout=150):
    t=time.time()
    req = urllib.request.Request(BASE+path, headers={"Authorization": "Bearer "+TOK})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return r.status, json.loads(r.read().decode()), round(time.time()-t,2)
    except Exception as e:
        return None, {"ERR": f"{type(e).__name__}: {str(e)[:150]}"}, round(time.time()-t,2)
for p in ["/etf/overview", "/market/index/kline?code=sh000001&limit=5"]:
    st, body, dt = get(p)
    print("="*60); print(f"GET {p} -> {st} ({dt}s)")
    print(json.dumps(body, ensure_ascii=False)[:1600])
