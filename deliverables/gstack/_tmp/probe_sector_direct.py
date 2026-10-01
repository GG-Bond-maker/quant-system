import sys; sys.path.insert(0,".")
from app.data.realtime import _request
for host in ["https://push2test.eastmoney.com","https://push2.eastmoney.com","https://push2his.eastmoney.com"]:
    try:
        d = _request("GET", f"{host}/api/qt/clist/get",
            params={"pn":"1","pz":"8","po":"1","np":"1","ut":"b2884a393a59ad64002292a3e90d46a5",
                    "fltt":"2","invt":"2","fid0":"f62","fs":"m:90+t:2","stat":"1",
                    "fields":"f12,f14,f2,f3,f62,f184,f66,f69,f72,f75,f78,f81,f84,f87,f204,f205,f124"},
            retries=1, timeout=8)
        diff = ((d or {}).get("data") or {}).get("diff") or []
        print(f"{host:42s} -> OK n={len(diff)} top={diff[0] if diff else None}")
    except Exception as e:
        print(f"{host:42s} -> FAIL {type(e).__name__}: {str(e)[:80]}")
