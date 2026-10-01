import json, urllib.request, time
BASE="http://127.0.0.1:8000/api/v1"
def get(path, timeout=180):
    t=time.time()
    try:
        with urllib.request.urlopen(BASE+path, timeout=timeout) as r:
            return r.status, json.loads(r.read().decode()), round(time.time()-t,2)
    except Exception as e:
        return None, {"ERR": f"{type(e).__name__}: {e}"}, round(time.time()-t,2)

st, body, dt = get("/market/overview/rt")
print(f"GET /market/overview/rt -> HTTP {st} ({dt}s)")
d = body.get("data", body)
mf = d.get("money_flow")
print("\n=== money_flow BLOCK ===")
print(json.dumps(mf, ensure_ascii=False, indent=2))
print("\n=== indices (top keys) ===")
idx = d.get("indices", {})
print("indices.status =", idx.get("status"), "| keys:", list(idx.keys())[:12])
print("\n=== data_freshness ===")
print(json.dumps(d.get("data_freshness"), ensure_ascii=False))
print("\n=== top-level status ===", d.get("status"))
print("=== anomalies status ===", (d.get("anomalies") or {}).get("status"))
