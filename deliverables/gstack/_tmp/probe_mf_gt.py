import os
# 关键：让 akshare 内的 requests 不走沙箱代理
for k in ("HTTP_PROXY","HTTPS_PROXY","http_proxy","https_proxy","ALL_PROXY","all_proxy"):
    os.environ.pop(k, None)
import akshare as ak, pandas as pd
pd.set_option("display.width", 200)

def t(name, fn):
    try:
        df = fn()
        print(f"\n=== {name} === OK shape={getattr(df,'shape',None)}")
        if hasattr(df, "columns"):
            print("cols:", list(df.columns)[:22])
            print(df.head(6).to_string())
            print("...tail:")
            print(df.tail(4).to_string())
    except Exception as e:
        print(f"\n=== {name} === FAIL {type(e).__name__}: {str(e)[:200]}")

t("stock_market_fund_flow", lambda: ak.stock_market_fund_flow())
t("stock_sector_fund_flow_rank(行业)", lambda: ak.stock_sector_fund_flow_rank(indicator="今日", sector_type="行业资金流"))
