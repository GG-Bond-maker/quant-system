import json, urllib.request
BASE="http://127.0.0.1:8000/api/v1"
TOK = open("deliverables/gstack/_tmp/token.txt").read().strip()
def get(path, timeout=150):
    req = urllib.request.Request(BASE+path, headers={"Authorization":"Bearer "+TOK})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return r.status, json.loads(r.read().decode())
    except Exception as e:
        return None, {"ERR": f"{type(e).__name__}: {str(e)[:150]}"}
for p in ["/etf/flow?period=1d&limit=10"]:
    st, body = get(p)
    print(f"GET {p} -> {st}")
    print(json.dumps(body, ensure_ascii=False)[:2500])
