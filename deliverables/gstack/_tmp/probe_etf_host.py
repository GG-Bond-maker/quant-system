import sys; sys.path.insert(0,"backend")
import os; os.chdir("backend")
from app.data.realtime import _request
import sys; sys.path.insert(0,".")
for host in ["https://push2test.eastmoney.com","https://push2.eastmoney.com","https://push2his.eastmoney.com"]:
    try:
        d=_request("GET", f"{host}/api/qt/clist/get", params={
            "pn":"1","pz":"5","po":"1","np":"1","fltt":"2","invt":"2","fid":"f62",
            "fs":"b:MK0021,b:MK0022","fields":"f12,f14,f2,f3,f6,f62,f164,f174",
            "ut":"b2884a393a59ad64002292a3e90d46a5"}, retries=1, timeout=8)
        diff=((d or {}).get("data") or {}).get("diff") or []
        print(f"{host:40s} OK n={len(diff)} first={diff[0] if diff else None}")
    except Exception as e:
        print(f"{host:40s} FAIL {type(e).__name__}: {str(e)[:70]}")
