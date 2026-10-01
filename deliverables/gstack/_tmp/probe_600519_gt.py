import requests, json
s = requests.Session(); s.trust_env=False
HDR={"User-Agent":"Mozilla/5.0","Referer":"https://data.eastmoney.com/"}
# 600519 secid = 1.600519
url=("https://push2test.eastmoney.com/api/qt/stock/fflow/daykline/get"
     "?lmt=3&klt=101&secid=1.600519&fields1=f1,f2,f3,f7"
     "&fields2=f51,f52,f53,f54,f55,f56,f57,f58,f59,f60,f61,f62,f63,f64,f65"
     "&ut=b2884a393a59ad64002292a3e90d46a5")
r=s.get(url,headers=HDR,timeout=12); j=r.json()
kl=(j.get("data") or {}).get("klines") or []
print("600519 ground truth last 3:")
for k in kl: print("  ", k[:70])
print("\n=> 主力净额(元) last:", kl[-1].split(",")[1], "=", round(float(kl[-1].split(",")[1])/1e8,2), "亿")

# watchlist endpoint check
import sys; sys.path.insert(0,".")
