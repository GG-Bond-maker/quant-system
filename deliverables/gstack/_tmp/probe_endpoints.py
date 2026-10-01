import json, urllib.request, time
BASE="http://127.0.0.1:8000/api/v1"
def get(path, timeout=120):
    t=time.time()
    try:
        with urllib.request.urlopen(BASE+path, timeout=timeout) as r:
            return r.status, json.loads(r.read().decode()), round(time.time()-t,2)
    except Exception as e:
        return None, {"ERR": f"{type(e).__name__}: {e}"}, round(time.time()-t,2)

for p in ["/market/overview/rt", "/market/overview/daily"]:
    st, body, dt = get(p)
    print("="*70)
    print(f"GET {p} -> HTTP {st} ({dt}s)")
    print(json.dumps(body, ensure_ascii=False, indent=1)[:4000])
