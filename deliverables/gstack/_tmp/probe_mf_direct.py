# 验证：同样参数，用 push2test 是否能拿到 market-level 主力（若能 => 是代码可修复缺陷）
import sys; sys.path.insert(0,".")
from app.data.realtime import _request
for host in ["https://push2test.eastmoney.com","https://push2his.eastmoney.com","https://push2delay.eastmoney.com"]:
    try:
        d = _request("GET", f"{host}/api/qt/stock/fflow/daykline/get",
                     params={"lmt":"3","klt":"101","secid":"1.000001","secid2":"0.399001",
                             "fields1":"f1,f2,f3,f7",
                             "fields2":"f51,f52,f53,f54,f55,f56,f57,f58,f59,f60,f61,f62,f63,f64,f65",
                             "ut":"b2884a393a59ad64002292a3e90d46a5"}, retries=1, timeout=8)
        kl = ((d or {}).get("data") or {}).get("klines") or []
        print(f"{host:42s} -> OK n={len(kl)} last={kl[-1].split(',')[0] if kl else None} 主力净额={kl[-1].split(',')[1] if kl else None}")
    except Exception as e:
        print(f"{host:42s} -> FAIL {type(e).__name__}: {str(e)[:80]}")
