import sys; sys.path.insert(0,".")
from app.data.etf import build_catalog
cat = build_catalog()
ov = [x for x in cat if x["country"]!="cn"]
us = [x for x in ov if x["country"]=="us"]
print("overseas total:", len(ov), "us:", len(us))
print("us with size_yi truthy:", sum(1 for x in us if x.get("size_yi")))
print("us size_yi values:", [x.get("size_yi") for x in us][:10])
print("sample us:", us[:2])
