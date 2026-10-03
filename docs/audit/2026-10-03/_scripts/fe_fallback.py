"""扫描前端静默兜底：catch 后返回空/默认、?? [] / ?? 0 / ?? {}、mock 数据。"""
import os
import re
import sys

ROOT = r"D:\Python_Project\Alpha Quant Platform"
SRC = os.path.join(ROOT, "frontend", "src")

PATTERNS = [
    ("catch 返回默认值", re.compile(r"catch[^{]{0,80}\{[^}]{0,200}?return\s+(\[\]|\{\}|null|0|''|\"\")", re.S)),
    ("catch 静默", re.compile(r"catch\s*(?:\([^)]*\))?\s*\{\s*\}")),
    ("catch 仅 console", re.compile(r"catch\s*(?:\([^)]*\))?\s*\{\s*console\.")),
    ("?? []", re.compile(r"\?\?\s*\[\s*\]")),
    ("?? {}", re.compile(r"\?\?\s*\{\s*\}")),
    ("?? 0", re.compile(r"\?\?\s*0\b")),
    ("?? ''", re.compile(r"\?\?\s*(''|\"\")")),
    ("|| []", re.compile(r"\|\|\s*\[\s*\]")),
    ("|| {}", re.compile(r"\|\|\s*\{\s*\}")),
    ("常量 0 兜底", re.compile(r"\?\?\s*0\.\d+|\|\|\s*0\b")),
    ("mock/MOCK", re.compile(r"\b(mock|MOCK|Mock|fake|FAKE)\w*")),
    ("DEMO/示例数据", re.compile(r"\b(DEMO|demo_data|示例数据|SAMPLE_)\w*")),
]

rows = []
for dirpath, dirnames, filenames in os.walk(SRC):
    dirnames[:] = [d for d in dirnames if d not in ("node_modules", "dist")]
    for fn in filenames:
        if not fn.endswith((".ts", ".tsx")):
            continue
        p = os.path.join(dirpath, fn)
        rel = os.path.relpath(p, ROOT).replace("\\", "/")
        src = open(p, encoding="utf-8", errors="ignore").read()
        for name, pat in PATTERNS:
            for m in pat.finditer(src):
                line = src[:m.start()].count("\n") + 1
                snippet = src.splitlines()[line - 1].strip()[:160]
                rows.append((rel, line, name, snippet))

out = sys.argv[1]
import csv
with open(out, "w", newline="", encoding="utf-8-sig") as fh:
    w = csv.writer(fh)
    w.writerow(["file", "line", "pattern", "snippet"])
    for r in rows:
        w.writerow(r)
print("fallback hits:", len(rows))
