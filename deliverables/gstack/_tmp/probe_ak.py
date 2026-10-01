import akshare as ak, pandas as pd
print("akshare", ak.__version__)
def t(name, fn):
    try:
        df = fn()
        print(f"\n=== {name} === OK shape={getattr(df,'shape',None)}")
        if hasattr(df, "columns"):
            print("cols:", list(df.columns)[:20])
            print(df.head(10).to_string())
    except Exception as e:
        print(f"\n=== {name} === FAIL {type(e).__name__}: {e}")

t("stock_hsgt_fund_flow_summary_em", lambda: ak.stock_hsgt_fund_flow_summary_em())
t("stock_market_fund_flow", lambda: ak.stock_market_fund_flow())
t("stock_sector_fund_flow_rank(行业)", lambda: ak.stock_sector_fund_flow_rank(indicator="今日", sector_type="行业资金流"))
