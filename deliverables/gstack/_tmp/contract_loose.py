"""只读：宽松交叉核对 —— 抓取 frontend 中所有 'api/v1/...' token，与后端路由比对。"""
from __future__ import annotations
import json, os, re

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..", ".."))
FE = os.path.join(ROOT, "frontend", "src")
ROUTE_JSON = os.path.join(ROOT, "deliverables", "gstack", "route_scan.json")

def norm(p: str) -> str:
    p = p.split("?")[0].rstrip("/")
    p = re.sub(r"\$\{[^}]*\}", "{*}", p)
    p = re.sub(r"\{[^}]*\}", "{*}", p)
    p = re.sub(r"/+", "/", p)
    if len(p) > 1 and p.endswith("/"):
        p = p[:-1]
    return p

data = json.load(open(ROUTE_JSON, encoding="utf-8"))
be = {}
for r in data["routes"]:
    if r.get("registered") and r.get("full_path"):
        be.setdefault(norm(r["full_path"]), set()).add(r["method"])
be_set = set(be)

tok_re = re.compile(r"/api/v1/[A-Za-z0-9_./{}$\-]*")
hits = {}
for base, _, fns in os.walk(FE):
    for fn in fns:
        if not fn.endswith((".ts", ".tsx")):
            continue
        fp = os.path.join(base, fn)
        rel = os.path.relpath(fp, ROOT).replace("\\", "/")
        for i, line in enumerate(open(fp, encoding="utf-8").read().splitlines(), 1):
            for m in tok_re.finditer(line):
                raw = m.group(0)
                if raw.endswith((".py", ".ts", ".tsx", ".md")):
                    continue
                n = norm(raw)
                if not n.startswith("/api/v1/"):
                    continue
                hits.setdefault(n, []).append(f"{rel}:{i}")

fe_set = set(hits)
missing = sorted(p for p in fe_set if p not in be_set)
print(f"后端路径 {len(be_set)} / 前端宽松 token {len(fe_set)}")
print("\n### 宽松口径下：前端出现但后端无此路径 ###")
for p in missing:
    print(f"  [CHECK] {p}")
    for l in sorted(set(hits[p]))[:6]:
        print(f"      <- {l}")

print("\n### 后端有但前端宽松口径也未出现 ###")
for p in sorted(be_set - fe_set):
    print(f"  [UNUSED] {'/'.join(sorted(be[p])):12s} {p}")
