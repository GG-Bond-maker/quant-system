# -*- coding: utf-8 -*-
"""AQP 后端端点运行时实证探测脚本（audit-2026-09-27）。

只做只读/计算型探测；写操作与长任务显式跳过。
输出：smoke-results.csv（UTF-8-SIG）
"""
import csv
import json
import os
import sys
import time
import urllib.error
import urllib.parse
import urllib.request

BASE = "http://127.0.0.1:8000"
ROOT = os.path.dirname(os.path.abspath(__file__))
OPENAPI = os.path.join(ROOT, "openapi.json")
OUT_CSV = os.path.join(ROOT, "smoke-results.csv")

# 从项目根 .env 读 ADMIN_TOKEN
ADMIN_TOKEN = ""
env_path = os.path.join(os.path.dirname(os.path.dirname(ROOT)), ".env")
try:
    with open(env_path, encoding="utf-8") as f:
        for line in f:
            if line.startswith("ADMIN_TOKEN="):
                ADMIN_TOKEN = line.split("=", 1)[1].strip()
                break
except OSError:
    pass

# 危险写操作 / 长任务：跳过
SKIP = {
    ("POST", "/api/v1/alerts/events/read"),
    ("POST", "/api/v1/alerts/rules"),
    ("PUT", "/api/v1/alerts/rules/{rule_id}"),
    ("DELETE", "/api/v1/alerts/rules/{rule_id}"),
    ("POST", "/api/v1/datacenter/mirror/rebuild"),
    ("POST", "/api/v1/datacenter/sync"),
    ("POST", "/api/v1/datacenter/sync/auto"),
    ("POST", "/api/v1/datacenter/sync/cancel"),
    ("POST", "/api/v1/datacenter/sync/fetch"),
    ("POST", "/api/v1/datacenter/text/import"),
    ("POST", "/api/v1/datacenter/train/cancel"),
    ("POST", "/api/v1/datacenter/train/start"),
    ("POST", "/api/v1/desk/exclusion"),
    ("POST", "/api/v1/desk/exclusion/toggle"),
    ("POST", "/api/v1/desk/fills/run"),
    ("POST", "/api/v1/desk/kill-switch"),
    ("POST", "/api/v1/desk/orders"),
    ("POST", "/api/v1/export/backtest"),
    ("POST", "/api/v1/export/strategy-backtest"),
    ("POST", "/api/v1/monitor/run"),
    ("POST", "/api/v1/ops/dag/rerun"),
    ("POST", "/api/v1/ops/quality-scan"),
    ("POST", "/api/v1/report/daily/generate"),
    ("POST", "/api/v1/settings/apikeys/rotate"),
    ("POST", "/api/v1/settings/data/cache/clear"),
    ("POST", "/api/v1/settings/data/sync"),
    ("POST", "/api/v1/settings/db/backup"),
    ("PUT", "/api/v1/settings/engine"),
    ("PUT", "/api/v1/settings/preferences"),
    ("POST", "/api/v1/studio/factors"),
    ("DELETE", "/api/v1/studio/factors/{factor_id}"),
    ("POST", "/api/v1/studio/mining/start"),
    ("POST", "/api/v1/studio/mining/cancel/{task_id}"),
}

# 路径参数取值
PATH_VALS = {
    "symbol": "600519",
    "code": "510300",
    "rule_id": "1",
    "task_id": "probe-nonexistent",
    "factor_id": "1",
}

# 显式请求体（计算型 POST）
BODIES = {
    ("POST", "/api/v1/backtest/run"): {"start": "2024-01-01", "end": "2024-06-30", "top_k": 20},
    ("POST", "/api/v1/backtest/signal-analysis"): {"start": "2024-01-01", "end": "2024-06-30", "horizons": [1, 5]},
    ("POST", "/api/v1/backtest/strategy-run"): {"start": "2024-01-01", "end": "2024-06-30", "symbols": ["600519"], "strategy_type": "ma_cross", "strategy_name": "ma"},
    ("POST", "/api/v1/portfolio/backtest"): {"assets": ["600519", "000001"], "start_date": "2024-01-01", "end_date": "2024-06-30"},
    ("POST", "/api/v1/research/factor-corr"): {"factors": ["close", "volume"]},
    ("POST", "/api/v1/research/factor-icir"): {"factors": ["close"]},
    ("POST", "/api/v1/research/factor-quantile"): {"factor": "close"},
    ("POST", "/api/v1/research/optimize"): {"assets": ["600519", "000001"]},
    ("POST", "/api/v1/research/stress-test"): {"assets": ["600519"]},
    ("POST", "/api/v1/research/impact-sim"): {"symbol": "600519", "order_amount": 100000},
    ("POST", "/api/v1/desk/attribution"): {"assets": ["600519"]},
    ("POST", "/api/v1/studio/alpha-eval"): {"expr": "close"},
    ("POST", "/api/v1/studio/factor-report"): {"expr": "close"},
    ("POST", "/api/v1/studio/nl-to-factor"): {"text": "5日均线"},
    ("POST", "/api/v1/datacenter/text/build-factor"): {},
    ("POST", "/api/v1/notify/stream-ticket"): {},
    ("POST", "/api/v1/research/cv-folds"): {},
    ("POST", "/api/v1/settings/connectors/test"): {},
    ("POST", "/api/v1/auth/login"): {"username": "probe_nonexistent_user", "password": "wrongpass123"},
    ("POST", "/api/v1/auth/register"): {"username": "probe_user_xyz", "password": "probe123456"},
}

# SSE 长连接：给短超时，预期挂起
SSE = {("GET", "/api/v1/notify/stream")}


def qval(name: str, schema: dict):
    n = name.lower()
    if n in ("start", "start_date"):
        return "2024-01-01"
    if n in ("end", "end_date"):
        return "2024-06-30"
    if n in ("q", "keyword", "query", "kw", "text"):
        return "600519"
    if n in ("symbol", "code", "ts_code"):
        return "600519"
    if n in ("limit", "size", "page_size", "top", "top_k", "n"):
        return 10
    if n in ("page", "offset"):
        return 1
    if n in ("horizon", "window", "window_days", "lookback_days"):
        return 5
    if n in ("days",):
        return 30
    t = schema.get("type")
    if t == "integer":
        return 1
    if t == "number":
        return 1
    if t == "boolean":
        return True
    if t == "array":
        return []
    return ""


def build_url(path: str, op: dict) -> str:
    p = path
    for name in list(PATH_VALS):
        p = p.replace("{" + name + "}", PATH_VALS[name])
    qs = []
    for prm in op.get("parameters", []):
        if prm.get("in") != "query":
            continue
        if prm.get("required"):
            qs.append((prm["name"], qval(prm["name"], prm.get("schema", {}))))
    if qs:
        return BASE + p + "?" + urllib.parse.urlencode(qs)
    return BASE + p


def probe(method, url, body, timeout):
    headers = {"Accept": "application/json"}
    if ADMIN_TOKEN:
        headers["Authorization"] = "Bearer " + ADMIN_TOKEN
    data = None
    if body is not None:
        data = json.dumps(body).encode("utf-8")
        headers["Content-Type"] = "application/json"
    req = urllib.request.Request(url, data=data, headers=headers, method=method)
    t0 = time.time()
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            raw = resp.read(4000).decode("utf-8", "replace")
            return resp.status, time.time() - t0, raw, ""
    except urllib.error.HTTPError as e:
        raw = e.read(4000).decode("utf-8", "replace")
        return e.code, time.time() - t0, raw, ""
    except Exception as e:  # 超时/连接错误
        return None, time.time() - t0, "", f"{type(e).__name__}: {e}"


def classify(status, raw, err):
    flags = []
    if err:
        flags.append("TIMEOUT/ERR")
    if status is not None and status >= 500:
        flags.append("5xx")
    if status is not None and 400 <= status < 500:
        flags.append("4xx")
    if raw:
        try:
            j = json.loads(raw)
            if isinstance(j, dict):
                code = j.get("code")
                if code not in (0, None):
                    flags.append(f"bizcode={code}")
                d = j.get("data")
                if d == [] or d == {} or d is None:
                    flags.append("empty")
                s = json.dumps(d, ensure_ascii=False)[:2000].lower()
                for kw in ("degraded", "fallback", "mock", "stub", "placeholder", "not implemented", "未实现"):
                    if kw in s:
                        flags.append(f"degraded:{kw}")
                        break
        except Exception:
            flags.append("non-json")
    return ",".join(flags)


def main():
    with open(OPENAPI, encoding="utf-8") as f:
        spec = json.load(f)
    rows = []
    targets = []
    for path, methods in spec["paths"].items():
        for method, op in methods.items():
            m = method.upper()
            if m not in ("GET", "POST", "PUT", "DELETE"):
                continue
            targets.append((m, path, op))
    targets.sort(key=lambda x: (x[1], x[0]))

    for m, path, op in targets:
        key = (m, path)
        url = build_url(path, op)
        if key in SKIP:
            rows.append([m, path, "", "跳过-写操作", "", "", ""])
            continue
        if key in SSE:
            st, el, raw, err = probe(m, url, None, 3)
            rows.append([m, path, "SSE", st or "", f"{el:.2f}", (raw or err)[:200], "SSE流(3s超时预期)"])
            continue
        body = BODIES.get(key)
        to = 45 if m == "POST" else 20
        st, el, raw, err = probe(m, url, body, to)
        flags = classify(st, raw, err)
        rows.append([m, path, st or "", "OK" if st else "ERR", f"{el:.2f}",
                     (raw or err).replace("\n", " ")[:200], flags])
        print(f"{m:6} {path:52} {st} {el:5.2f}s {flags}")

    with open(OUT_CSV, "w", newline="", encoding="utf-8-sig") as f:
        w = csv.writer(f)
        w.writerow(["method", "path", "status", "result", "elapsed_s", "body_prefix", "flags"])
        w.writerows(rows)
    print(f"\n写入 {OUT_CSV}，共 {len(rows)} 行")


if __name__ == "__main__":
    main()
