"""只读：前后端接口契约双向差集核验（2026-10-04）。

- 后端：读 deliverables/gstack/route_scan.json（由 _qa_route_scan.py 从 AST 提取）
- 前端：正则扫描 frontend/src/**/*.{ts,tsx} 里的请求路径字面量
- 归一化后做双向差集：前端有后端无 / 后端有前端无
"""
from __future__ import annotations
import json, os, re, sys

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..", ".."))
FE = os.path.join(ROOT, "frontend", "src")
ROUTE_JSON = os.path.join(ROOT, "deliverables", "gstack", "route_scan.json")

WRAPPERS = ("get", "post", "put", "del", "download", "patch")

def norm(p: str) -> str:
    p = p.split("?")[0]
    p = re.sub(r"\$\{[^}]*\}", "{*}", p)   # TS 模板参数（先整段替换）
    if "${" in p:                          # 残留未闭合模板（嵌套反引号截断）
        head, _ = p.split("${", 1)
        p = head + "{*}" if head.endswith("/") else head
    p = re.sub(r"\{[^}]*\}", "{*}", p)     # FastAPI 路径参数
    p = re.sub(r"/+", "/", p)
    if len(p) > 1 and p.endswith("/"):
        p = p[:-1]
    return p

# ---------- 后端 ----------
data = json.load(open(ROUTE_JSON, encoding="utf-8"))
be = {}
for r in data["routes"]:
    if not r.get("registered") or not r.get("full_path"):
        continue
    key = (r["method"], norm(r["full_path"]))
    be.setdefault(key, []).append(f'{r["file"]}:{r["lineno"]}:{r["func"]}')
be_set = set(be)

# ---------- 前端 ----------
path_re = re.compile(r"""['"`](/api/v1/[^'"`\n]*)['"`]""")
call_re = re.compile(r"""\b(get|post|put|del|download|patch)\s*(?:<[^>]*>)?\s*\(\s*$""")

fe = {}
files = []
for base, _, fns in os.walk(FE):
    for fn in fns:
        if fn.endswith((".ts", ".tsx")) and not fn.endswith(".test.ts"):
            files.append(os.path.join(base, fn))

for fp in files:
    src = open(fp, encoding="utf-8").read()
    lines = src.splitlines()
    rel = os.path.relpath(fp, ROOT).replace("\\", "/")
    for i, line in enumerate(lines, 1):
        for m in path_re.finditer(line):
            raw = m.group(1)
            # 判断方法：向前找最近的 wrapper 调用（同一行或上一行）
            ctx = line[:m.start()]
            method = None
            mm = None
            for w in WRAPPERS:
                # e.g. "post<Foo>("  or "get(" or "download("
                if re.search(rf"\b{w}\s*(?:<[^>]*>)?\s*\([^)]*$", ctx):
                    method = w
            # download() 是 GET/POST 二态，标为 SPECIAL
            if method in ("del",):
                method = "DELETE"
            elif method == "download":
                method = "DOWNLOAD"
            elif method:
                method = method.upper()
            else:
                method = "?"
            key = (method, norm(raw))
            fe.setdefault(key, []).append(f"{rel}:{i}")

# 归并：同一路径若既有 ? 又有具体 method，合并展示
fe_paths = {}
for (m, p), locs in fe.items():
    fe_paths.setdefault(p, set()).add(m)

# 后端按路径聚合方法
be_paths = {}
for (m, p) in be_set:
    be_paths.setdefault(p, set()).add(m)

be_only = sorted(set(be_paths) - set(fe_paths))
fe_only = sorted(set(fe_paths) - set(be_paths))
both = sorted(set(fe_paths) & set(be_paths))

def methods_str(ms):
    return "/".join(sorted(m for m in ms if m != "?"))

print("=" * 70)
print(f"后端已注册路由: {len(be_set)} 条 (唯一路径 {len(be_paths)})")
print(f"前端引用路径 : {len(fe_paths)} 条 (唯一 method+path {len(fe)})")
print(f"交集路径     : {len(both)}")
print("=" * 70)
print("\n### A. 前端引用但后端未注册（潜在 404） ###")
if not fe_only:
    print("  (无)")
for p in fe_only:
    locs = sorted({l for (m, q), ls in fe.items() if q == p for l in ls})
    print(f"  [MISSING-BE] {methods_str(fe_paths[p]):8s} {p}")
    for l in locs:
        print(f"      <- {l}")

print("\n### B. 后端已注册但前端未引用（死接口） ###")
if not be_only:
    print("  (无)")
for p in be_only:
    print(f"  [UNUSED-BE] {methods_str(be_paths[p]):8s} {p}")
    for (m, q), ls in be.items():
        if q == p:
            for l in ls:
                print(f"      -> {l}")

print("\n### C. 方法不一致（路径匹配但 HTTP 方法不同） ###")
mism = []
for p in both:
    fm = {m for m in fe_paths[p] if m not in ("?", "DOWNLOAD")}
    bm = be_paths[p]
    if fm and not (fm & bm):
        mism.append((p, fm, bm))
if not mism:
    print("  (无)")
for p, fm, bm in mism:
    print(f"  [METHOD-MISMATCH] {p}  frontend={sorted(fm)} backend={sorted(bm)}")

print("\n### D. 后端路由清单（供交叉核对） ###")
for (m, p) in sorted(be_set):
    print(f"  {m:7s} {p}")
