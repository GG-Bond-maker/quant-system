# 用 trust_env=False 的 session 直接打东财接口，拿到 main/sector 的 ground truth
import requests, json
s = requests.Session(); s.trust_env = False
HDR = {"User-Agent":"Mozilla/5.0","Referer":"https://data.eastmoney.com/"}

# 1) 大盘主力净流入日线（stock_market_fund_flow 等价）
url = ("https://push2his.eastmoney.com/api/qt/stock/fflow/daykline/get"
       "?lmt=5&klt=101&secid=1.000001&secid2=0.399001"
       "&fields1=f1,f2,f3,f7&fields2=f51,f52,f53,f54,f55,f56,f57,f58,f59,f60,f61,f62,f63,f64,f65"
       "&ut=b2884a393a59ad64002292a3e90d46a5")
r = s.get(url, headers=HDR, timeout=15)
print("== market_fund_flow daykline ==", r.status_code)
j = r.json()
kl = (j.get("data") or {}).get("klines") or []
print("n_klines:", len(kl))
for row in kl[-3:]:
    print(row)

# 2) 行业板块资金流榜（stock_sector_fund_flow_rank 等价）
url2 = ("https://push2.eastmoney.com/api/qt/clist/get?pn=1&pz=8&po=1&np=1"
        "&ut=b2884a393a59ad64002292a3e90d46a5&fltt=2&invt=2&fid0=f62"
        "&fs=m:90+t:2&stat=1"
        "&fields=f12,f14,f62,f184,f66,f69,f72,f75,f78,f81,f84,f87")
r2 = s.get(url2, headers=HDR, timeout=15)
print("\n== sector fund flow ==", r2.status_code)
j2 = r2.json()
diff = (j2.get("data") or {}).get("diff") or []
print("n_rows:", len(diff))
for d in diff[:8]:
    print(d)
