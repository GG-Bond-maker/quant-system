"""生成审查报告的 ① 后端端点全景表 与 ② 前端模块全景表（Markdown）。"""
import csv
import os
import re

ROOT = r"D:\Python_Project\Alpha Quant Platform"
SRC = os.path.join(ROOT, "frontend", "src")
API_DIR = os.path.join(SRC, "api")

files = {}
for dp, dns, fns in os.walk(SRC):
    dns[:] = [d for d in dns if d not in ("node_modules", "dist")]
    for f in fns:
        if f.endswith((".ts", ".tsx")):
            p = os.path.join(dp, f)
            files[os.path.relpath(p, ROOT).replace("\\", "/")] = open(
                p, encoding="utf-8", errors="ignore").read()

routes = list(csv.DictReader(
    open(r"D:\Python_Project\Alpha Quant Platform\docs\audit\2026-10-03\_scripts\routes.csv",
         encoding="utf-8-sig")))

# api 模块 -> 被哪些非 api 文件 import
consumers = {}
for rel, t in files.items():
    if rel.startswith("frontend/src/api/"):
        continue
    for m in re.finditer(r"from\s+'([^']+)'", t):
        consumers.setdefault(m.group(1), set()).add(rel)


def resolve_api_module(literal: str) -> str | None:
    for rel, t in files.items():
        if not rel.startswith("frontend/src/api/"):
            continue
        if literal in t:
            return rel
    return None


def norm(p):
    parts = p.split("/")
    out = []
    for s in parts:
        out.append(r"[^/]+" if s.startswith("{") and s.endswith("}") else re.escape(s))
    return "/" .join(out) + r"(?=$|['\"`?\s])"


print("| # | Method | 路径 | 后端位置 | 前端封装 | 消费页面/组件 |")
print("|---|---|---|---|---|---|")
for i, r in enumerate(routes, 1):
    path = r["path"]
    lit = path[len("/api/v1"):] or "/"
    api_file = resolve_api_module(path) or resolve_api_module(lit)
    if api_file is None:
        pat = re.compile(norm(path))
        cand = [rel for rel, t in files.items() if pat.search(t)]
        api_file = cand[0] if cand else ""
    cons = ""
    if api_file:
        mod = api_file[len("frontend/src/"):-3]
        for spec, sets in consumers.items():
            if spec.endswith(mod) or spec == mod or spec == "@/" + mod:
                cons = "、".join(sorted(x.replace("frontend/src/", "")
                                       for x in sets))
                break
    loc = f"{r['file'].split('/')[-1]}:{r['line']}"
    print(f"| {i} | {r['method']} | `{path}` | {loc} | "
          f"{api_file.replace('frontend/src/','') or '—'} | {cons or '—'} |")
