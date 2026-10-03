"""扫描 frontend/src 中所有后端端点调用（字符串字面量形式），导出 CSV。

匹配两类：
 1. 直接字面量 '/api/v1/xxx' 或 `/api/v1/${...}`
 2. API 常量文件中定义的 ENDPOINTS/BASE 等拼接（尽力匹配 'xxx/yyy' 形态）
"""
import csv
import os
import re
import sys

ROOT = r"D:\Python_Project\Alpha Quant Platform"
SRC = os.path.join(ROOT, "frontend", "src")

LIT = re.compile(r"""['"`](/?api/v1[^'"`$]*)['"`]""")
TMPL = re.compile(r"""['"`](/?api/v1[^'"`]*)['"`]""")
# 相对路径调用: get('screener', ...) 之类 —— 先在 api/*.ts 找 BASE/前缀
BASE = re.compile(r"""(?:const\s+(\w+)\s*=\s*['"`](/api/v1[^'"`]*)['"`])""")
SEG = re.compile(r"""['"`]([a-z][a-zA-Z0-9_\-/{}]*)['"`]""")

rows = []


def scan_file(path):
    try:
        src = open(path, encoding="utf-8").read()
    except Exception:
        return
    rel = os.path.relpath(path, ROOT).replace("\\", "/")
    lines = src.splitlines()
    for i, line in enumerate(lines, 1):
        for m in TMPL.finditer(line):
            p = m.group(1)
            rows.append({"file": rel, "line": i, "path": p, "kind": "literal",
                         "ctx": line.strip()[:200]})


for dirpath, dirnames, filenames in os.walk(SRC):
    dirnames[:] = [d for d in dirnames if d not in ("node_modules", "dist")]
    for fn in filenames:
        if fn.endswith((".ts", ".tsx")):
            scan_file(os.path.join(dirpath, fn))

out = sys.argv[1]
with open(out, "w", newline="", encoding="utf-8-sig") as fh:
    w = csv.writer(fh)
    w.writerow(["file", "line", "path", "kind", "ctx"])
    for r in rows:
        w.writerow([r["file"], r["line"], r["path"], r["kind"], r["ctx"]])
print("fe literal api calls:", len(rows))
