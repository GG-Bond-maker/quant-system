import json, urllib.request
BASE="http://127.0.0.1:8000/api/v1"
def get(path, timeout=120):
    try:
        with urllib.request.urlopen(BASE+path, timeout=timeout) as r:
            return r.status, json.loads(r.read().decode())
    except Exception as e:
        return None, {"ERR": f"{type(e).__name__}: {e}"}
for p in ["/datacenter/overview", "/datacenter/datasets", "/datacenter/quality"]:
    st, body = get(p)
    print("="*60); print(f"GET {p} -> {st}")
    if isinstance(body, dict):
        d = body.get("data", body)
        if p.endswith("overview"):
            print(json.dumps(d, ensure_ascii=False)[:1500])
        elif p.endswith("datasets"):
            items = d if isinstance(d, list) else d.get("items", d)
            if isinstance(items, list):
                for it in items[:20]:
                    print(f"  {it.get('key') or it.get('dataset')}: rows={it.get('rows')} syms={it.get('symbols')} range={it.get('start')}~{it.get('end')} skipped={it.get('skipped_files')}/{it.get('total_files')}")
            else:
                print(json.dumps(d, ensure_ascii=False)[:800])
        else:
            print(json.dumps(d, ensure_ascii=False)[:1200])
    else:
        print(body)
