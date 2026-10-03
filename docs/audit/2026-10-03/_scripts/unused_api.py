"""找出 frontend/src/api/*.ts 中定义但从未被 pages/components/hooks/stores 使用的导出符号（A 类僵尸端点候选）。"""
import os
import re
import sys

ROOT = r"D:\Python_Project\Alpha Quant Platform"
SRC = os.path.join(ROOT, "frontend", "src")
API_DIR = os.path.join(SRC, "api")

files = {}
for dirpath, dirnames, filenames in os.walk(SRC):
    dirnames[:] = [d for d in dirnames if d not in ("node_modules", "dist")]
    for fn in filenames:
        if fn.endswith((".ts", ".tsx")):
            p = os.path.join(dirpath, fn)
            rel = os.path.relpath(p, ROOT).replace("\\", "/")
            files[rel] = open(p, encoding="utf-8", errors="ignore").read()

# ---- 1. 收集 api 目录下的导出符号名 ----
# 形式：export const xxx = ...  / export function xxx( / export interface/class/enum
EXPORT_RE = re.compile(
    r"^export\s+(?:const|let|function|async function|class|interface|type|enum)\s+(\w+)",
    re.M)
# api 对象里的 key（如 alertsApi = { rules: ..., health: ... }）
OBJKEY_RE = re.compile(r"^\s{2,}(\w+)\s*[:(]", re.M)

symbols = {}   # symbol -> (file, line, snippet)
for fn in sorted(os.listdir(API_DIR)):
    if not fn.endswith(".ts") or fn.endswith(".test.ts"):
        continue
    rel = f"frontend/src/api/{fn}"
    src = files[rel]
    lines = src.splitlines()
    for m in EXPORT_RE.finditer(src):
        line = src[:m.start()].count("\n") + 1
        symbols.setdefault(m.group(1), []).append((rel, line, lines[line - 1].strip()[:120]))
    # 对象键
    for m in OBJKEY_RE.finditer(src):
        k = m.group(1)
        if k in ("if", "for", "return", "const", "export"):
            continue
        line = src[:m.start()].count("\n") + 1
        symbols.setdefault(k, []).append((rel, line, lines[line - 1].strip()[:120]))

# ---- 2. 在非 api 文件中查找引用 ----
consumers = {rel: t for rel, t in files.items() if not rel.startswith("frontend/src/api/")}
blob = "\n".join(consumers.values())

unused = []
used = []
for sym, defs in sorted(symbols.items()):
    # 在 consumers 中查找 `sym` 作为标识符出现（排除定义行所在文件本身）
    pat = re.compile(r"(?<![\w$])" + re.escape(sym) + r"(?![\w$])")
    hit_files = [rel for rel, t in consumers.items() if pat.search(t)]
    if hit_files:
        used.append((sym, defs, hit_files))
    else:
        unused.append((sym, defs))

print("=== api/*.ts 导出但前端其它模块未引用的符号（%d） ===" % len(unused))
for sym, defs in unused:
    for f, l, s in defs:
        print(f"  {sym:32} {f}:{l}  {s}")
print()
print("=== 已引用（%d） ===" % len(used))
for sym, defs, hf in used:
    print(f"  {sym:32} <- {hf[:4]}")
