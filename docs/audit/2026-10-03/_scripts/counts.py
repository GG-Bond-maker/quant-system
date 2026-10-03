import os
import collections

ROOT = r"D:\Python_Project\Alpha Quant Platform"


def count(base, exts, skip=("node_modules", "dist", "__pycache__")):
    c = collections.Counter()
    for dp, dns, fns in os.walk(base):
        dns[:] = [d for d in dns if d not in skip]
        for f in fns:
            if f.endswith(exts):
                c[os.path.relpath(dp, base).replace("\\", "/")] += 1
    return c


print("== frontend/src ==")
for k, v in sorted(count(os.path.join(ROOT, "frontend", "src"), (".ts", ".tsx")).items()):
    print(f"  {k or '.':40} {v}")

print("== backend/app ==")
for k, v in sorted(count(os.path.join(ROOT, "backend", "app"), (".py",)).items()):
    print(f"  {k or '.':40} {v}")

# 页面数
pages = os.path.join(ROOT, "frontend", "src", "pages")
print("pages:", sorted(os.listdir(pages)))
