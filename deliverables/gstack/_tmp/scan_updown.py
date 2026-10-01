# -*- coding: utf-8 -*-
import os, re

ROOT = r"D:/Python_Project/Alpha Quant Platform/frontend/src"

PATTERNS = [
    ("t-up/t-down", r"\bt-(up|down)\b"),
    ("text-up/down", r"\btext-(up|down)\b"),
    ("bg-up/down", r"\bbg-(up|down)\b"),
    ("border-up/down", r"\bborder-(up|down)\b"),
    ("upDownColor", r"upDownColor\s*\("),
    ("palette.UP/DOWN", r"\.(UP|DOWN)\b"),
    ("pctClass", r"pctClass\s*\("),
    ("text-red-*", r"\btext-red-\d{2,3}\b"),
    ("text-emerald-*", r"\btext-emerald-\d{2,3}\b"),
    ("text-green-*", r"\btext-green-\d{2,3}\b"),
    ("text-rose-*", r"\btext-rose-\d{2,3}\b"),
    ("bg-red-*", r"\bbg-red-\d{2,3}\b"),
    ("bg-emerald-*", r"\bbg-emerald-\d{2,3}\b"),
    ("bg-green-*", r"\bbg-green-\d{2,3}\b"),
    ("text-blue-*", r"\btext-blue-\d{2,3}\b"),
    ("text-amber/orange-*", r"\btext-(amber|orange)-\d{2,3}\b"),
    ("hardcode hex", r"#(DC2626|16A34A|EF4444|22C55E|D92B2B|12995B|3B82F6|F59E0B|10B981|DC2626)"),
    ("color={", r"color\s*=\s*\{"),
    ("itemStyle", r"itemStyle"),
    ("lineStyle", r"lineStyle"),
    ("areaStyle", r"areaStyle"),
]

files = []
for dirpath, dirnames, filenames in os.walk(ROOT):
    dirnames[:] = [d for d in dirnames if d not in ('node_modules', 'dist', '__pycache__')]
    for fn in filenames:
        if fn.endswith(('.tsx', '.ts')):
            files.append(os.path.join(dirpath, fn))

total = 0
out = []
for f in sorted(files):
    rel = os.path.relpath(f, ROOT).replace('\\', '/')
    try:
        lines = open(f, encoding='utf-8').readlines()
    except Exception:
        continue
    hits = []
    for i, line in enumerate(lines, 1):
        for name, pat in PATTERNS:
            if re.search(pat, line):
                hits.append((i, name, line.strip()[:180]))
                break
    if hits:
        total += len(hits)
        out.append(f"### {rel} ({len(hits)})")
        for i, name, line in hits:
            out.append(f"  {i:5d} [{name}] {line}")
        out.append("")

print("=== 命中文件数:", len(out)//1, " 命中行数:", total, "===\n")
print("\n".join(out))
