"""校验前端出现的 /api/v1 路径是否都能在后端路由表中找到（B 类：前端调用无后端支撑）。"""
import csv
import re
import sys

routes = list(csv.DictReader(open(sys.argv[1], encoding="utf-8-sig")))
fe = list(csv.DictReader(open(sys.argv[2], encoding="utf-8-sig")))


def to_regex(p):
    # /api/v1/stock/${encodeURIComponent(symbol)}/profile -> 通配
    parts = re.split(r"(\$\{[^}]*\}|`|\?)", p)
    out = []
    for seg in parts:
        if not seg:
            continue
        if seg.startswith("${") or seg == "`":
            out.append(r"[^/]*")
        elif seg == "?":
            break
        else:
            out.append(re.escape(seg))
    return "^" + "".join(out) + "$"


backends = [r["path"] for r in routes]


def norm_backend(p):
    return re.sub(r"\{[^}]+\}", "{}", p)


bset = {norm_backend(p) for p in backends}

bad = []
for r in fe:
    p = r["path"]
    if p.startswith("/api/v1/nope"):
        continue
    pat = re.compile(to_regex(p))
    # 去掉模板变量后的骨架
    skeleton = re.sub(r"\$\{[^}]*\}", "{}", p)
    skeleton = skeleton.replace("`", "")
    if "?" in skeleton:
        skeleton = skeleton.split("?")[0]
    if skeleton in bset:
        continue
    # 再试通配匹配
    hit = any(pat.match(b) for b in backends)
    if not hit:
        bad.append((r["file"], r["line"], p, r["ctx"][:80]))

print("=== 前端调用但后端无匹配路由（%d） ===" % len(bad))
for f, l, p, c in bad:
    print(f"  {f}:{l}  {p}\n      {c}")
