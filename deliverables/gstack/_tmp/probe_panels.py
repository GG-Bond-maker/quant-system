import sys; sys.path.insert(0,".")
from app.data.panels import build_money_flow, build_north
import json
print("=== build_money_flow(600519.SH) ===")
print(json.dumps(build_money_flow("600519.SH"), ensure_ascii=False, indent=1))
print("\n=== build_north(600519.SH) ===")
print(json.dumps(build_north("600519.SH"), ensure_ascii=False, indent=1))
