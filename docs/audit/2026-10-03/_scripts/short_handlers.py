"""列出后端路由处理器：行数 / 是否有效代码行（去注释空行）/ 是否含 return ok("""
import ast
import csv
import os
import sys

ROOT = r"D:\Python_Project\Alpha Quant Platform"
API = os.path.join(ROOT, "backend", "app", "api", "v1")
METHODS = {"get", "post", "put", "patch", "delete"}


def eff_lines(src_lines, node):
    seg = src_lines[node.lineno - 1: (node.end_lineno or node.lineno)]
    n = 0
    for l in seg:
        s = l.strip()
        if not s or s.startswith("#"):
            continue
        n += 1
    return n


rows = []
for fn in sorted(os.listdir(API)):
    if not fn.endswith(".py"):
        continue
    p = os.path.join(API, fn)
    src = open(p, encoding="utf-8").read()
    lines = src.splitlines()
    tree = ast.parse(src)
    for node in ast.walk(tree):
        if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            continue
        for dec in node.decorator_list:
            if not isinstance(dec, ast.Call) or not isinstance(dec.func, ast.Attribute):
                continue
            if dec.func.attr.lower() not in METHODS:
                continue
            body_src = ast.get_source_segment(src, node) or ""
            rows.append({
                "file": f"backend/app/api/v1/{fn}",
                "line": node.lineno,
                "func": node.name,
                "total": (node.end_lineno or node.lineno) - node.lineno + 1,
                "eff": eff_lines(lines, node),
                "has_ok": "return ok(" in body_src,
                "has_raise": "raise " in body_src,
                "nreturn": body_src.count("return "),
            })

out = sys.argv[1]
with open(out, "w", newline="", encoding="utf-8-sig") as fh:
    w = csv.writer(fh)
    w.writerow(["file", "line", "func", "total_lines", "eff_lines", "has_ok", "has_raise", "n_return"])
    for r in sorted(rows, key=lambda x: x["eff"]):
        w.writerow([r["file"], r["line"], r["func"], r["total"], r["eff"],
                    r["has_ok"], r["has_raise"], r["nreturn"]])
print("handlers:", len(rows))
