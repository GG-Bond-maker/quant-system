#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""AQP 运行时冒烟：读取 openapi，逐个端点打请求，记录 状态/业务code/耗时/错误。
只读，不修改任何业务代码。Token 从 .env 读取，不硬编码。
"""
import json
import os
import re
import sys
import time
import urllib.parse

import requests

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
BASE = "http://127.0.0.1:8000"
OUT = os.path.join(ROOT, "docs", "audit", "_smoke_result.json")


def load_env():
    env = {}
    p = os.path.join(ROOT, ".env")
    if os.path.exists(p):
        with open(p, encoding="utf-8", errors="replace") as f:
            for line in f:
                line = line.strip()
                if not line or line.startswith("#") or "=" not in line:
                    continue
                k, v = line.split("=", 1)
                env[k.strip()] = v.strip().strip('"').strip("'")
    return env


ENV = load_env()

# ---- 取值样例（必填路径/查询参数） ----
PATH_SAMPLE = {
    "symbol": "600519.SH",
    "code": "510300",
    "rule_id": "r-0001",
    "factor_id": "f-0001",
    "task_id": "t-0001",
}
QUERY_SAMPLE = {
    "symbols": "600519.SH,000001.SZ",
    "q": "600519",
    "start": "2026-08-01",
    "end": "2026-09-11",
    "date": "2026-09-11",
    "limit": "20",
}
# 只读打法：需要 body 的 POST 只发空 {}，观察后端是否优雅处理
SAFE_POST_BODY = {}


def fill_path(p):
    return re.sub(r"\{(\w+)\}", lambda m: PATH_SAMPLE.get(m.group(1), "1"), p)


def pick_token():
    """优先 .env 的 ADMIN_TOKEN，其次登录接口。"""
    tok = ENV.get("ADMIN_TOKEN")
    if tok:
        return tok, "env:ADMIN_TOKEN"
    try:
        r = requests.post(
            BASE + "/api/v1/auth/login",
            json={"username": "admin", "password": "admin"},
            timeout=10,
        )
        if r.status_code == 200:
            d = r.json().get("data") or {}
            for k in ("access_token", "token", "accessToken"):
                if d.get(k):
                    return d[k], "login"
    except Exception:
        pass
    return None, "none"


def main():
    spec = json.load(
        open(os.path.join(ROOT, "docs", "audit", "_openapi_raw.json"), encoding="utf-8")
    )
    token, tok_src = pick_token()
    headers = {"Authorization": "Bearer " + token} if token else {}

    ops = []
    for p, item in spec["paths"].items():
        for m, op in item.items():
            if m not in ("get", "post", "put", "delete", "patch"):
                continue
            params = op.get("parameters", [])
            ops.append((m.upper(), p, params))

    # 排序保证可复现
    ops.sort(key=lambda x: (x[1], x[0]))

    results = []
    for m, p, params in ops:
        if m == "DELETE":
            # 删除类不做真实调用，只记录 skip（避免破坏数据）
            results.append(
                dict(method=m, path=p, status=-1, biz=None, ms=0, skipped="DELETE-不执行")
            )
            continue
        url = BASE + fill_path(p)
        qs = {}
        for prm in params:
            if prm.get("in") != "query":
                continue
            name = prm["name"]
            if name in QUERY_SAMPLE:
                qs[name] = QUERY_SAMPLE[name]
        if qs:
            url += "?" + urllib.parse.urlencode(qs)

        entry = dict(method=m, path=p, url=url, status=-1, biz=None, ms=0, err="")
        t0 = time.time()
        try:
            if m == "GET":
                r = requests.get(url, headers=headers, timeout=60)
            else:
                r = requests.post(url, headers=headers, json=SAFE_POST_BODY, timeout=60)
            entry["ms"] = int((time.time() - t0) * 1000)
            entry["status"] = r.status_code
            txt = r.text[:2000]
            try:
                j = r.json()
                if isinstance(j, dict):
                    entry["biz"] = j.get("code")
                    entry["msg"] = str(j.get("message") or j.get("detail") or "")[:300]
            except Exception:
                entry["biz"] = None
                entry["msg"] = txt[:300]
        except requests.exceptions.Timeout:
            entry["ms"] = int((time.time() - t0) * 1000)
            entry["err"] = "TIMEOUT>60s"
        except Exception as e:
            entry["ms"] = int((time.time() - t0) * 1000)
            entry["err"] = f"{type(e).__name__}: {str(e)[:200]}"
        results.append(entry)
        print(
            f"{m:6} {p[:52]:52} {entry['status']:>4} biz={entry.get('biz')} "
            f"{entry['ms']:>6}ms {entry.get('err','')[:40]}",
            flush=True,
        )

    json.dump(
        {"token_source": tok_src, "base": BASE, "results": results},
        open(OUT, "w", encoding="utf-8"),
        ensure_ascii=False,
        indent=1,
    )
    print("\nWROTE", OUT, "n=", len(results))


if __name__ == "__main__":
    main()
