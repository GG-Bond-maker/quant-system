import sys, os
sys.path.insert(0, ".")
from app.data.realtime import fetch_main_fund_flow, _fetch_em_fflow
for sym in ["600519.SH", "000001.SZ", "300750.SZ"]:
    try:
        d = fetch_main_fund_flow(sym)
        print(f"{sym}: main_net={d['main_net']:.2f} 元 = {d['main_net']/1e8:.2f} 亿 | date={d['date']} ratio={d['main_net_ratio']}% src={d.get('source')}")
    except Exception as e:
        print(f"{sym}: FAIL {type(e).__name__}: {str(e)[:150]}")
