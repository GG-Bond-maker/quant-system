#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""AQP 运行时冒烟 v2：
- 从 openapi schema 自动生成合法样例参数（尊重 pattern / minimum / enum）
- trust_env=False 绕过本机 HTTP 代理，避免把"代理 502"误判成后端 502
- SSE 端点单独短读处理
只读，不改业务代码。
"""
import json
import os
import re
import time
import urllib.parse

import requests

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
BASE = "http://127.0.0.1:8000"
OUT = os.path.join(ROOT, "docs", "audit", "_smoke2_result.json")

PATH_SAMPLE = {
    "symbol": "600519.SH",
    "code": "510300",
    "rule_id": "r-0001",
    "factor_id": "f-0001",
    "task_id": "t-0001",
}
# 手工兜底样例（schema 推断不出语义时）
MANUAL = {
    "symbols": "600519.SH,000001.SZ",
    "q": "600519",
    "keyword": "600519",
    "start": "2026-08-01",
    "end": "2026-09-11",
    "start_date": "2026-08-01",
    "end_date": "2026-09-11",
    "date": "20260911",
    "trade_date": "20260911",
    "limit": "30",
    "page": "1",
    "page_size": "30",
    "days": "30",
    "top": "20",
    "period": "20",
    "window": "20",
}
SSE_PATHS = {"/api/v1/notify/stream"}


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


def schema_sample(sch, name):
    """按 schema 推断一个合法样例值（字符串形式）。"""
    if not isinstance(sch, dict):
        return None
    if "enum" in sch and sch["enum"]:
        return str(sch["enum"][0])
    t = sch.get("type")
    if sch.get("anyOf"):
        for sub in sch["anyOf"]:
            v = schema_sample(sub, name)
            if v is not None:
                return v
    if t == "integer" or t == "number":
        lo = sch.get("minimum", sch.get("exclusiveMinimum"))
        hi = sch.get("maximum")
        if lo is not None:
            return str(int(lo) if t == "integer" else lo)
        if hi is not None:
            return str(int(hi) if t == "integer" else hi)
        return "30"
    if t == "boolean":
        return "true"
    if t == "array":
        return MANUAL.get(name, "600519.SH")
    if t == "string":
        pat = sch.get("pattern")
        if pat:
            if r"\d{8}" in pat:
                return "20260911"
            if r"\d{4}-\d{2}-\d{2}" in pat:
                return "2026-09-11"
        if name in MANUAL:
            return MANUAL[name]
        if sch.get("format") == "date":
            return "2026-09-11"
        return "600519"
    return None


def main():
    spec = json.load(
        open(os.path.join(ROOT, "docs", "audit", "_openapi_raw.json"), encoding="utf-8")
    )
    token = ENV.get("ADMIN_TOKEN")
    tok_src = "env:ADMIN_TOKEN" if token else "none"
    headers = {"Authorization": "Bearer " + token} if token else {}
    if token:
        headers["Authorization"] = "Bearer " + token

    sess = requests.Session()
    sess.trust_env = False  # 绕开 HTTP_PROXY，直连后端

    ops = []
    for p, item in spec["paths"].items():
        for m, op in item.items():
            if m not in ("get", "post", "put", "delete", "patch"):
                continue
            ops.append((m.upper(), p, op.get("parameters", [])))
    ops.sort(key=lambda x: (x[1], x[0]))

    results = []
    for m, p, params in ops:
        if m == "DELETE":
            results.append(dict(method=m, path=p, status=-1, biz=None, ms=0,
                                skipped="DELETE-避免破坏数据，未执行"))
            continue
        url = BASE + re.sub(r"\{(\w+)\}",
                            lambda mm: PATH_SAMPLE.get(mm.group(1), "1"), p)
        qs = {}
        for prm in params:
            if prm.get("in") != "query":
                continue
            name = prm["name"]
            sch = (prm.get("schema") or {})
            v = schema_sample(sch, name)
            if v is None and name in MANUAL:
                v = MANUAL[name]
            if v is not None:
                qs[name] = v
        if qs:
            url += "?" + urllib.parse.urlencode(qs)
        query_used = dict(qs)

        entry = dict(method=m, path=p, url=url, query=query_used,
                     status=-1, biz=None, ms=0, err="", msg="")
        t0 = time.time()
        try:
            if p in SSE_PATHS:
                # SSE：只读取前若干字节后断开，避免长连接挂死
                with sess.get(url, headers=headers, timeout=20, stream=True) as r:
                    entry["status"] = r.status_code
                    chunk = b""
                    for c in r.iter_content(256):
                        chunk += c
                        break
                    entry["msg"] = chunk[:200].decode("utf-8", "replace")
                    entry["note"] = "SSE-短读"
                entry["ms"] = int((time.time() - t0) * 1000)
            elif m == "GET":
                r = sess.get(url, headers=headers, timeout=180)
                entry["ms"] = int((time.time() - t0) * 1000)
                entry["status"] = r.status_code
                try:
                    j = r.json()
                    if isinstance(j, dict):
                        entry["biz"] = j.get("code")
                        entry["msg"] = str(j.get("message") or j.get("detail") or "")[:300]
                except Exception:
                    entry["msg"] = r.text[:300]
            else:
                r = sess.post(url, headers=headers, json={}, timeout=180)
                entry["ms"] = int((time.time() - t0) * 1000)
                entry["status"] = r.status_code
                try:
                    j = r.json()
                    if isinstance(j, dict):
                        entry["biz"] = j.get("code")
                        entry["msg"] = str(j.get("message") or j.get("detail") or "")[:300]
                except Exception:
                    entry["msg"] = r.text[:300]
        except requests.exceptions.Timeout:
            entry["ms"] = int((time.time() - t0) * 1000)
            entry["err"] = "TIMEOUT>180s"
        except Exception as e:
            entry["ms"] = int((time.time() - t0) * 1000)
            entry["err"] = f"{type(e).__name__}: {str(e)[:200]}"
        results.append(entry)
        print(f"{m:6} {p[:50]:50} {entry['status']:>4} biz={str(entry.get('biz')):>6} "
              f"{entry['ms']:>7}ms {entry.get('err','')[:30]}", flush=True)

    json.dump({"token_source": tok_src, "results": results},
              open(OUT, "w", encoding="utf-8"), ensure_ascii=False, indent=1)
    print("\nWROTE", OUT, "n=", len(results))


if __name__ == "__main__":
    main()
