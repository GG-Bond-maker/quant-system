import requests, time
HDR = {"User-Agent":"Mozilla/5.0","Referer":"https://data.eastmoney.com/"}
def try_url(tag, url, host=None):
    s = requests.Session(); s.trust_env = False
    try:
        r = s.get(url, headers=HDR, timeout=12)
        print(f"[{tag}] {r.status_code} len={len(r.text)} :: {r.text[:200]}")
    except Exception as e:
        print(f"[{tag}] FAIL {type(e).__name__}: {str(e)[:130]}")

base_kline = "/api/qt/stock/fflow/daykline/get?lmt=5&klt=101&secid=1.000001&secid2=0.399001&fields1=f1,f2,f3,f7&fields2=f51,f52,f53,f54,f55,f56,f57,f58,f59,f60,f61,f62,f63,f64,f65&ut=b2884a393a59ad64002292a3e90d46a5"
base_clist = "/api/qt/clist/get?pn=1&pz=8&po=1&np=1&ut=b2884a393a59ad64002292a3e90d46a5&fltt=2&invt=2&fid0=f62&fs=m:90+t:2&stat=1&fields=f12,f14,f62,f184,f66,f69"

for host in ["https://push2his.eastmoney.com","https://push2test.eastmoney.com","http://push2his.eastmoney.com","https://push2.eastmoney.com"]:
    try_url("kline@"+host, host+base_kline)
for host in ["https://push2.eastmoney.com","https://push2test.eastmoney.com","https://push2his.eastmoney.com"]:
    try_url("clist@"+host, host+base_clist)
# datacenter-web 备用：板块资金流
try_url("dcweb-sector", "https://datacenter-web.eastmoney.com/api/data/v1/get?reportName=RPT_VALUEANALYSIS_DET&columns=ALL&pageSize=5")
