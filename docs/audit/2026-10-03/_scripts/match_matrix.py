"""把后端路由与前端调用做匹配，输出 A 类（后端有前端无）清单。"""
import csv
import os
import re
import sys

ROOT = r"D:\Python_Project\Alpha Quant Platform"
SRC = os.path.join(ROOT, "frontend", "src")

# 读取前端全部源码文本（按文件）
cache = {}
for dirpath, dirnames, filenames in os.walk(SRC):
    dirnames[:] = [d for d in dirnames if d not in ("node_modules", "dist")]
    for fn in filenames:
        if fn.endswith((".ts", ".tsx")):
            p = os.path.join(dirpath, fn)
            cache[os.path.relpath(p, ROOT).replace("\\", "/")] = open(
                p, encoding="utf-8", errors="ignore").read()

routes = list(csv.DictReader(open(sys.argv[1], encoding="utf-8-sig")))


def norm(p):
    """把 /api/v1/stock/{symbol}/profile -> 正则"""
    parts = p.split("/")
    out = []
    for seg in parts:
        if seg.startswith("{") and seg.endswith("}"):
            out.append(r"[^/]+")
        else:
            out.append(re.escape(seg))
    return "/".join(out) + r"(?=$|['\"`?\s])"


unmatched = []
matched = []
for r in routes:
    path = r["path"]
    # 去掉 /api/v1 前缀后的"特征串"用于粗匹配
    tail = path[len("/api/v1"):]
    pat = re.compile(norm(path))
    hits = [f for f, t in cache.items() if pat.search(t)]
    if hits:
        matched.append((r, hits))
    else:
        # 再试去掉 /api/v1 前缀
        pat2 = re.compile(norm(tail))
        hits2 = [f for f, t in cache.items() if pat2.search(t)]
        if hits2:
            matched.append((r, hits2))
        else:
            unmatched.append(r)

print("=== A 类候选：后端有、前端未出现（%d） ===" % len(unmatched))
for r in unmatched:
    print(f"{r['method']:6} {r['path']:60} {r['file']}:{r['line']} {r['func']}")

print()
print("=== 已匹配（%d） ===" % len(matched))
for r, h in matched:
    print(f"{r['method']:6} {r['path']:60} -> {len(h)} files: {h[:3]}")
