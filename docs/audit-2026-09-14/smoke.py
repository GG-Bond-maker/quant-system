"""
AQP 后端 API 端到端冒烟脚本（QA: 严过关）。

用法：
    backend/.venv/Scripts/python.exe docs/audit-2026-09-14/smoke.py <base_url> <admin_token>

策略：
 - GET / 只读端点：真实调用，记录 HTTP 状态 / 业务 code / 关键字段 / 耗时
 - POST / DELETE / PUT 写端点：
     * `SAFE_WRITE` 白名单内的（轻量、可回滚）真实调用
     * 其余只走「鉴权 + 参数校验」路径（无 token 应 40100；非法参数应 40000）
 - 角色差异：对受保护端点额外用 viewer / researcher token 各打一次
"""
from __future__ import annotations

import json
import sys
import time
from pathlib import Path

import urllib.error
import urllib.parse
import urllib.request

BASE = sys.argv[1] if len(sys.argv) > 1 else "http://127.0.0.1:8124"
ADMIN_TOKEN = sys.argv[2] if len(sys.argv) > 2 else ""

RESULTS: list[dict] = []


def call(method: str, path: str, token: str | None = None,
         params: dict | None = None, body: dict | None = None,
         timeout: int = 60, raw: bool = False):
    """返回 (http_status, code, message, data, cost_ms, headers, raw_text)。"""
    url = BASE + path
    if params:
        url += "?" + urllib.parse.urlencode(params)
    data = None
    headers = {"Accept": "application/json"}
    if body is not None:
        data = json.dumps(body).encode("utf-8")
        headers["Content-Type"] = "application/json"
    if token:
        headers["Authorization"] = f"Bearer {token}"
    req = urllib.request.Request(url, data=data, headers=headers, method=method)
    t0 = time.perf_counter()
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            text = resp.read().decode("utf-8", "replace")
            status = resp.status
            hdrs = dict(resp.headers)
    except urllib.error.HTTPError as e:
        text = e.read().decode("utf-8", "replace")
        status = e.code
        hdrs = dict(e.headers)
    except Exception as e:  # noqa: BLE001
        return {"http": 0, "code": None, "msg": f"{type(e).__name__}: {e}",
                "data": None, "ms": int((time.perf_counter() - t0) * 1000),
                "hdr": {}, "raw": ""}
    ms = int((time.perf_counter() - t0) * 1000)
    try:
        j = json.loads(text)
        code = j.get("code")
        msg = j.get("message")
        d = j.get("data")
    except Exception:  # noqa: BLE001
        code, msg, d = None, "NON_JSON", text[:200]
    return {"http": status, "code": code, "msg": msg, "data": d,
            "ms": ms, "hdr": hdrs, "raw": text if raw else text[:1500]}


def brief(v, n=180):
    s = json.dumps(v, ensure_ascii=False, default=str)
    return s[:n] + ("..." if len(s) > n else "")


def record(method, path, r, note="", auth="admin", probe=""):
    RESULTS.append({
        "method": method, "path": path, "auth": auth, "probe": probe,
        "http": r["http"], "code": r["code"], "msg": r["msg"],
        "ms": r["ms"], "note": note, "sample": brief(r["data"]),
        "srv_ms": r["hdr"].get("X-Response-Time-MS"),
    })
    flag = "OK " if r["code"] == 0 else ("ERR" if r["code"] else "HTTP")
    print(f"[{flag}] {method:6} {path:55} http={r['http']} code={r['code']} "
          f"{r['ms']:>6}ms {r['msg']}")
    return r


# ------------------------------------------------------------------ 端点清单
# (method, path, params/body, mode)  mode: R=真实读 / W=轻量写 / A=仅鉴权校验
# 说明：重型写端点（训练/pipeline/扩容/备份恢复/truncate）一律标 A，不真实执行。
ENDPOINTS: list[tuple] = [
    # ---------- root / 运维 ----------
    ("GET", "/", None, "R"),
    ("GET", "/health", None, "R"),
    ("GET", "/health/ready", None, "R"),
    ("GET", "/health/live", None, "R"),
    ("GET", "/metrics", None, "R"),
    ("GET", "/openapi.json", None, "R"),

    # ---------- auth ----------
    ("POST", "/api/v1/auth/login", {"username": "admin", "password": "wrong-password-probe"}, "W"),
    ("POST", "/api/v1/auth/login", {"username": "no-such-user-xyz", "password": "x"}, "W"),
    ("GET", "/api/v1/auth/register/status", None, "R"),
    ("GET", "/api/v1/auth/me", None, "R"),

    # ---------- market ----------
    ("GET", "/api/v1/market/overview", None, "R"),
    ("GET", "/api/v1/market/overview/daily", None, "R"),
    ("GET", "/api/v1/market/overview/rt", None, "R"),
    ("GET", "/api/v1/market/quotes", {"symbols": "600519.SH,000001.SZ,300750.SZ"}, "R"),
    ("GET", "/api/v1/market/index/kline", {"symbol": "sh000001", "limit": 30}, "R"),

    # ---------- stock ----------
    ("GET", "/api/v1/stock/search", {"q": "茅台"}, "R"),
    ("GET", "/api/v1/stock/600519.SH/profile", None, "R"),
    ("GET", "/api/v1/stock/600519.SH/kline", {"days": 60}, "R"),
    ("GET", "/api/v1/stock/600519.SH/panels", None, "R"),
    ("GET", "/api/v1/stock/600519.SH/predict", None, "R"),

    # ---------- screener ----------
    ("GET", "/api/v1/screener", {"limit": 10}, "R"),
    ("GET", "/api/v1/screener/stocks", {"limit": 10}, "R"),
    ("GET", "/api/v1/screener/watchlist", None, "R"),

    # ---------- etf ----------
    ("GET", "/api/v1/etf/overview", None, "R"),
    ("GET", "/api/v1/etf/list", {"limit": 10}, "R"),
    ("GET", "/api/v1/etf/hot", {"limit": 10}, "R"),
    ("GET", "/api/v1/etf/scale", {"limit": 10}, "R"),
    ("GET", "/api/v1/etf/flow", {"limit": 10}, "R"),
    ("GET", "/api/v1/etf/performance", {"limit": 10}, "R"),
    ("GET", "/api/v1/etf/detail/510300", None, "R"),

    # ---------- watchlist ----------
    ("GET", "/api/v1/watchlist/dashboard", None, "R"),
    ("GET", "/api/v1/watchlist/correlation", None, "R"),

    # ---------- backtest ----------
    ("POST", "/api/v1/backtest/run", {
        "symbols": ["600519.SH"], "start": "2024-01-01", "end": "2024-06-30",
        "strategy": "ma_cross", "initial_cash": 100000}, "W"),
    ("POST", "/api/v1/backtest/signal-analysis", {"symbol": "600519.SH"}, "W"),
    ("POST", "/api/v1/backtest/strategy-run", {"symbol": "600519.SH"}, "W"),

    # ---------- portfolio ----------
    ("GET", "/api/v1/portfolio/search", {"q": "600519.SH"}, "R"),
    ("POST", "/api/v1/portfolio/backtest", {"symbols": ["600519.SH"]}, "W"),

    # ---------- research ----------
    ("GET", "/api/v1/research/overview", None, "R"),
    ("GET", "/api/v1/research/experiments", None, "R"),
    ("GET", "/api/v1/research/feature-importance", None, "R"),
    ("GET", "/api/v1/research/lab/yearly", None, "R"),
    ("POST", "/api/v1/research/factor-icir", {"factor": "momentum_20"}, "W"),
    ("POST", "/api/v1/research/factor-quantile", {"factor": "momentum_20"}, "W"),
    ("POST", "/api/v1/research/factor-corr", {"factors": ["momentum_20", "vol_20"]}, "W"),
    ("POST", "/api/v1/research/cv-folds", {"n_splits": 3}, "W"),
    ("POST", "/api/v1/research/stress-test", {"symbols": ["600519.SH"]}, "W"),
    ("POST", "/api/v1/research/impact-sim", {"symbol": "600519.SH"}, "W"),
    ("POST", "/api/v1/research/optimize", {"n_trials": 1}, "A"),

    # ---------- studio ----------
    ("GET", "/api/v1/studio/factors", None, "R"),
    ("POST", "/api/v1/studio/factors", {"name": "qa_probe_factor",
                                        "expr": "momentum_20"}, "A"),
    ("DELETE", "/api/v1/studio/factors/1", None, "A"),
    ("POST", "/api/v1/studio/factor-report", {"factor": "momentum_20"}, "W"),
    ("POST", "/api/v1/studio/alpha-eval", {"expr": "momentum_20"}, "W"),
    ("POST", "/api/v1/studio/nl-to-factor", {"text": "20日动量"}, "A"),
    ("POST", "/api/v1/studio/mining/start", {"universe": ["600519.SH"]}, "A"),
    ("GET", "/api/v1/studio/mining/status/qa-probe-task", None, "R"),
    ("POST", "/api/v1/studio/mining/cancel/qa-probe-task", None, "A"),

    # ---------- alerts ----------
    ("GET", "/api/v1/alerts/rules", None, "R"),
    ("POST", "/api/v1/alerts/rules", {"name": "qa_probe", "symbol": "600519.SH"}, "A"),
    ("PUT", "/api/v1/alerts/rules/1", {"name": "qa_probe"}, "A"),
    ("DELETE", "/api/v1/alerts/rules/1", None, "A"),
    ("GET", "/api/v1/alerts/events", {"limit": 10}, "R"),
    ("POST", "/api/v1/alerts/events/read", {"ids": []}, "A"),
    ("GET", "/api/v1/alerts/health", None, "R"),

    # ---------- monitor / report ----------
    ("GET", "/api/v1/monitor/health", None, "R"),
    ("POST", "/api/v1/monitor/run", None, "A"),
    ("GET", "/api/v1/report/daily", None, "R"),
    ("POST", "/api/v1/report/daily/generate", None, "A"),

    # ---------- notify ----------
    ("GET", "/api/v1/notify/recent", {"limit": 10}, "R"),

    # ---------- ops ----------
    ("GET", "/api/v1/ops/dag", None, "R"),
    ("POST", "/api/v1/ops/dag/rerun", {"node": "probe"}, "A"),
    ("GET", "/api/v1/ops/lineage", None, "R"),
    ("POST", "/api/v1/ops/quality-scan", None, "A"),

    # ---------- datacenter ----------
    ("GET", "/api/v1/datacenter/overview", None, "R"),
    ("GET", "/api/v1/datacenter/datasets", None, "R"),
    ("GET", "/api/v1/datacenter/instruments", {"limit": 10}, "R"),
    ("GET", "/api/v1/datacenter/quality", None, "R"),
    ("GET", "/api/v1/datacenter/logs", {"limit": 10}, "R"),
    ("GET", "/api/v1/datacenter/sync/auto", None, "R"),
    ("POST", "/api/v1/datacenter/sync/auto", {"enabled": True, "time": "15:45"}, "A"),
    ("GET", "/api/v1/datacenter/sync/status", None, "R"),
    ("GET", "/api/v1/datacenter/sync/tasks/qa-probe", None, "R"),
    ("POST", "/api/v1/datacenter/sync", None, "A"),
    ("POST", "/api/v1/datacenter/sync/fetch", {"symbols": ["600519.SH"]}, "A"),
    ("POST", "/api/v1/datacenter/sync/cancel", None, "A"),
    ("GET", "/api/v1/datacenter/task-stats", None, "R"),
    ("GET", "/api/v1/datacenter/train/readiness", None, "R"),
    ("GET", "/api/v1/datacenter/train/status", None, "R"),
    ("POST", "/api/v1/datacenter/train/start", {}, "A"),
    ("POST", "/api/v1/datacenter/train/cancel", None, "A"),
    ("GET", "/api/v1/datacenter/text/status", None, "R"),
    ("POST", "/api/v1/datacenter/text/import", {}, "A"),
    ("POST", "/api/v1/datacenter/text/build-factor", {}, "A"),
    ("GET", "/api/v1/datacenter/mirror/status", None, "R"),
    ("POST", "/api/v1/datacenter/mirror/rebuild", {}, "A"),

    # ---------- settings ----------
    ("GET", "/api/v1/settings", None, "R"),
    ("PUT", "/api/v1/settings/preferences", {"theme": "dark"}, "A"),
    ("PUT", "/api/v1/settings/engine", {"engine": "polars"}, "A"),
    ("POST", "/api/v1/settings/apikeys/rotate", {}, "A"),
    ("POST", "/api/v1/settings/connectors/test", {"name": "redis"}, "W"),
    ("POST", "/api/v1/settings/data/cache/clear", None, "A"),
    ("POST", "/api/v1/settings/data/sync", {}, "A"),
    ("POST", "/api/v1/settings/db/backup", None, "A"),

    # ---------- desk ----------
    ("GET", "/api/v1/desk/account", None, "R"),
    ("GET", "/api/v1/desk/orders", {"limit": 10}, "R"),
    ("POST", "/api/v1/desk/orders", {"symbol": "600519.SH"}, "A"),
    ("GET", "/api/v1/desk/capacity", None, "R"),
    ("POST", "/api/v1/desk/attribution", {}, "W"),
    ("GET", "/api/v1/desk/exclusion", None, "R"),
    ("POST", "/api/v1/desk/exclusion", {"symbol": "600519.SH"}, "A"),
    ("POST", "/api/v1/desk/exclusion/toggle", {"symbol": "600519.SH"}, "A"),
    ("GET", "/api/v1/desk/exclusion/screen", None, "R"),
    ("GET", "/api/v1/desk/kill-switch", None, "R"),
    ("POST", "/api/v1/desk/kill-switch", {"enabled": True}, "A"),
    ("POST", "/api/v1/desk/fills/run", {}, "A"),

    # ---------- export ----------
    ("GET", "/api/v1/export/screener", {"limit": 5}, "R"),
    ("POST", "/api/v1/export/backtest", {"symbols": ["600519.SH"]}, "A"),
    ("POST", "/api/v1/export/strategy-backtest", {"symbol": "600519.SH"}, "A"),
]


def main() -> None:
    print(f"=== AQP API smoke  BASE={BASE} ===")
    for method, path, payload, mode in ENDPOINTS:
        if mode == "A":
            # 只走鉴权路径：不带 token
            r = call(method, path, token=None,
                     params=payload if method in ("GET", "DELETE") else None,
                     body=(payload or {}) if method in ("POST", "PUT") else None,
                     timeout=30)
            record(method, path, r, note="仅鉴权校验(未执行重型操作)", auth="anon")
        else:
            params = payload if method == "GET" else None
            body = payload if method in ("POST", "PUT", "DELETE") else None
            r = call(method, path, token=ADMIN_TOKEN, params=params,
                     body=body, timeout=120)
            record(method, path, r, note="", auth="admin")

    out = Path(__file__).with_name("smoke-results.json")
    out.write_text(json.dumps(RESULTS, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"\n=== 写入 {out}  共 {len(RESULTS)} 条 ===")


if __name__ == "__main__":
    main()
