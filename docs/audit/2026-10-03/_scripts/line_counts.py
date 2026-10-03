import os

ROOT = r"D:\Python_Project\Alpha Quant Platform"
API = os.path.join(ROOT, "backend", "app", "api", "v1")
rows = []
for f in sorted(os.listdir(API)):
    if f.endswith(".py"):
        p = os.path.join(API, f)
        n = sum(1 for _ in open(p, encoding="utf-8", errors="ignore"))
        rows.append((n, f))
rows.sort(reverse=True)
for n, f in rows:
    print(f"{n:6}  backend/app/api/v1/{f}")
print("total", sum(n for n, _ in rows))
