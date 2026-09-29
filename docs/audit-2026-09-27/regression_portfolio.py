# -*- coding: utf-8 -*-
"""回归验证 · /portfolio/backtest 错误码语义（yan-regression）。"""
import json
import os
import time
import urllib.error
import urllib.request

ROOT = os.path.dirname(os.path.abspath(__file__))
PROJ = os.path.dirname(os.path.dirname(ROOT))
BASE = "http://127.0.0.1:8000"

ADMIN_TOKEN = ""
try:
    with open(os.path.join(PROJ, ".env"), encoding="utf-8") as f:
        for line in f:
            if line.startswith("ADMIN_TOKEN="):
                ADMIN_TOKEN = line.split("=", 1)[1].strip()
                break
except OSError:
    pass


def post(path, body, timeout=90):
    headers = {"Accept": "application/json", "Content-Type": "application/json"}
    if ADMIN_TOKEN:
        headers["Authorization"] = "Bearer " + ADMIN_TOKEN
    r = urllib.request.Request(BASE + path, data=json.dumps(body).encode(),
                               headers=headers, method="POST")
    t0 = time.time()
    try:
        with urllib.request.urlopen(r, timeout=timeout) as resp:
            return resp.status, time.time() - t0, json.loads(resp.read().decode("utf-8"))
    except urllib.error.HTTPError as e:
        return e.code, time.time() - t0, json.loads(e.read().decode("utf-8"))
    except Exception as e:
        return None, time.time() - t0, {"_err": f"{type(e).__name__}: {e}"}


def body(code, weight=1.0):
    return {"assets": [{"code": code, "type": "stock", "weight": weight}],
            "start_date": "2024-01-01", "end_date": "2024-06-30"}


CASES = [
    ("600519.SH.XX", 1.0, "格式非法(多段后缀) → 期望 40000"),
    ("abc", 1.0, "格式非法(非数字) → 期望 40000"),
    ("600519.SH", 1.0, "合法带后缀 → 期望 code=0 成功"),
    ("600519", 1.0, "合法裸码 → 期望 code=0 成功"),
    ("999999", 1.0, "格式合法但无数据 → 期望 51001"),
]

print("=== /api/v1/portfolio/backtest 错误码语义 ===")
for code, w, desc in CASES:
    st, el, j = post("/api/v1/portfolio/backtest", body(code, w))
    bc = j.get("code")
    msg = (j.get("message") or j.get("_err") or "")[:90]
    print(f"  code={code:14} status={st} {el:6.2f}s bizcode={bc!s:6} | {desc}")
    print(f"      message={msg}")
