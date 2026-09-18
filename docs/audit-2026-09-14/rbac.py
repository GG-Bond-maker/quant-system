"""
RBAC 冒烟：用工程自身的 create_jwt_token 签发 viewer / researcher JWT，
对比同一端点在不同角色下的行为（期望：越权 -> code 40300）。

用法：
    backend/.venv/Scripts/python.exe docs/audit-2026-09-14/rbac.py <base_url>
"""
from __future__ import annotations

import json
import sys
import time
import urllib.error
import urllib.parse
import urllib.request

sys.path.insert(0, r"D:\Python_Project\Alpha Quant Platform\backend")

from app.core.auth import create_jwt_token  # noqa: E402

BASE = sys.argv[1] if len(sys.argv) > 1 else "http://127.0.0.1:8124"

VIEWER = create_jwt_token("qa_viewer", "viewer")[0]
RESEARCHER = create_jwt_token("qa_researcher", "researcher")[0]
ADMIN = create_jwt_token("qa_admin", "admin")[0]


def call(method, path, token=None, params=None, body=None, timeout=60):
    url = BASE + path
    if params:
        url += "?" + urllib.parse.urlencode(params)
    data = json.dumps(body).encode() if body is not None else None
    headers = {"Accept": "application/json"}
    if data:
        headers["Content-Type"] = "application/json"
    if token:
        headers["Authorization"] = f"Bearer {token}"
    req = urllib.request.Request(url, data=data, headers=headers, method=method)
    t0 = time.perf_counter()
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            txt, status = r.read().decode("utf-8", "replace"), r.status
    except urllib.error.HTTPError as e:
        txt, status = e.read().decode("utf-8", "replace"), e.code
    except Exception as e:  # noqa: BLE001
        return 0, None, f"{type(e).__name__}", int((time.perf_counter() - t0) * 1000)
    ms = int((time.perf_counter() - t0) * 1000)
    try:
        j = json.loads(txt)
        return status, j.get("code"), j.get("message"), ms
    except Exception:  # noqa: BLE001
        return status, None, "NON_JSON", ms


# (method, path, params, body, 期望最小可访问角色)
CASES = [
    ("GET", "/api/v1/market/overview", None, None, "anon"),
    ("GET", "/api/v1/stock/600519.SH/predict", None, None, "researcher"),
    ("GET", "/api/v1/screener", {"top_k": 5}, None, "viewer"),
    ("GET", "/api/v1/etf/list", {"page_size": 5}, None, "viewer"),
    ("GET", "/api/v1/alerts/rules", None, None, "researcher"),
    ("GET", "/api/v1/datacenter/overview", None, None, "viewer"),
    ("GET", "/api/v1/datacenter/train/status", None, None, "researcher"),
    ("POST", "/api/v1/backtest/run", None,
     {"symbols": ["600519.SH"], "start": "2024-01-01", "end": "2024-06-30"},
     "researcher"),
    ("GET", "/api/v1/settings", None, None, "viewer"),
    ("PUT", "/api/v1/settings/engine", None, {"engine": "polars"}, "admin"),
    ("GET", "/api/v1/ops/dag", None, None, "viewer"),
    ("GET", "/api/v1/desk/account", None, None, "researcher"),
    ("GET", "/api/v1/auth/me", None, None, "any"),
]

ROLES = [("anon", None), ("viewer", VIEWER), ("researcher", RESEARCHER), ("admin", ADMIN)]
RANK = {"anon": -1, "viewer": 0, "researcher": 1, "admin": 2}


def main() -> None:
    print(f"{'endpoint':50} {'anon':>10} {'viewer':>10} {'researcher':>12} {'admin':>10}  期望")
    rows = []
    for method, path, params, body, expect in CASES:
        cells = []
        for name, tk in ROLES:
            st, code, msg, ms = call(method, path, tk, params, body)
            cells.append(f"{code}/{ms}ms" if code is not None else f"http{st}")
        # 判定：期望最小角色 Rank 以下的角色应被拒（40300），以上的应放行
        exp_rank = {"any": -2}.get(expect, RANK[expect])
        verdict = []
        for (name, _), cell in zip(ROLES, cells):
            got = cell.split("/")[0]
            should_pass = RANK[name] >= exp_rank
            ok = (got != "40300") if should_pass else (got == "40300")
            verdict.append("" if ok else f"✗{name}")
        bad = [v for v in verdict if v]
        print(f"{method + ' ' + path:50} " + " ".join(f"{c:>12}" for c in cells)
              + f"  >={expect}" + ("  ⚠ " + ",".join(bad) if bad else "  ✅"))
        rows.append({"method": method, "path": path, "expect": expect,
                     "cells": cells, "issues": bad})
    out = __file__.replace("rbac.py", "rbac-results.json")
    with open(out, "w", encoding="utf-8") as f:
        json.dump(rows, f, ensure_ascii=False, indent=2)
    print(f"\n写入 {out}")


if __name__ == "__main__":
    main()
