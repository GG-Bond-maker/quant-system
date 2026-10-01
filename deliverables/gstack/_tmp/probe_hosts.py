import requests, json, os
print("proxy env:", {k:v for k,v in os.environ.items() if 'proxy' in k.lower()})
s = requests.Session(); s.trust_env = False
hosts = ["https://push2test.eastmoney.com", "https://push2.eastmoney.com", "https://push2his.eastmoney.com", "https://datacenter-web.eastmoney.com"]
for h in hosts:
    try:
        r = s.get(h+"/api/qt/ulist.np/get?fltt=2&secids=1.000001&fields=f2,f3,f12,f14", timeout=8)
        print(f"{h:45s} -> {r.status_code} len={len(r.text)} {r.text[:120]}")
    except Exception as e:
        print(f"{h:45s} -> FAIL {type(e).__name__}: {str(e)[:100]}")
