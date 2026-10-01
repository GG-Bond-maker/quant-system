import sys; sys.path.insert(0,".")
from app.data.realtime import _request
d=_request("GET","https://push2test.eastmoney.com/api/qt/clist/get", params={
  "pn":"1","pz":"3","po":"1","np":"1","fltt":"2","invt":"2","fid":"f62",
  "fs":"b:MK0021,b:MK0022","fields":"f12,f14,f2,f3,f6,f62,f124",
  "ut":"b2884a393a59ad64002292a3e90d46a5"}, retries=1, timeout=8)
diff=((d or {}).get("data") or {}).get("diff") or []
import datetime as dt
for x in diff:
    ts = x.get("f124")
    print(x.get("f12"), x.get("f14"), "f62=", x.get("f62"), "f124=", ts, "->", dt.datetime.fromtimestamp(ts) if ts else None)
