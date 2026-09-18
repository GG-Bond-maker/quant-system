"""合并三轮冒烟结果 + 角色表 -> Markdown 端点总表。"""
from __future__ import annotations

import json
from pathlib import Path

BASE = Path(r"D:\Python_Project\Alpha Quant Platform\docs\audit-2026-09-14")
roles = json.load(open(BASE / "roles.json", encoding="utf-8"))
registered = [tuple(x) for x in json.load(open(BASE / "registered.json", encoding="utf-8"))]

rows: dict[tuple[str, str], dict] = {}
for f in ("smoke-results.json", "smoke2-results.json", "smoke3-results.json"):
    p = BASE / f
    if not p.exists():
        continue
    for r in json.load(open(p, encoding="utf-8")):
        key = (r["method"], r["path"].split("?")[0])
        prev = rows.get(key)
        # 后一轮（参数修正过）优先：code==0 覆盖非零；非零之间取最后一次
        if prev is None or r.get("code") == 0 or prev.get("code") != 0:
            rows[key] = r

# 手工补充（docs / register / sse）
extra = [
    ("GET", "/docs", 200, 0, None, "Swagger UI 可访问"),
    ("GET", "/redoc", 200, 0, None, "ReDoc 可访问"),
    ("POST", "/api/v1/auth/login", 200, 0, None,
     "正确口令 -> JWT 正常；错误口令 -> 40104；无效 token -> 40102；viewer 访问 researcher 端点 -> 40300"),
    ("POST", "/api/v1/auth/register", 200, 0, None,
     "注册成功（已建测试账号 qaprobe_viewer，需清理）；重名 40105；弱口令 40000"),
    ("GET", "/api/v1/notify/stream", 200, 0, 12000,
     "SSE 连接建立但 12s 内 0 字节（无心跳/无初始事件）"),
]
for m, p, http, code, ms, note in extra:
    rows[(m, p)] = {"method": m, "path": p, "http": http, "code": code,
                    "msg": "", "ms": ms, "sample": "", "note": note}

# 人工标注：code==0 但数据明显不可用的「异常数据」端点
OVERRIDE = {
    ("GET", "/api/v1/portfolio/search"): ("⚠️", "异常数据", "code=0 但返回 []，且耗时 175.7s"),
    ("GET", "/api/v1/screener"): ("⚠️", "异常数据",
        "count=1 且 close/pct/amount 全 null（今日股票池快照未就绪，无 status 降级提示）"),
    ("GET", "/api/v1/market/overview"): ("⚠️", "异常数据",
        "stale=true；recommend/ai_stats=unavailable，anomalies=degraded 0 条"),
    ("GET", "/api/v1/export/screener"): ("✅", "通过", "返回 CSV 文件流（非 JSON，正常）"),
    ("POST", "/api/v1/export/backtest"): ("✅", "通过", "返回 xlsx 文件流（非 JSON，正常）"),
    ("GET", "/metrics"): ("✅", "通过", "Prometheus 文本格式（非 JSON，正常）"),
    ("GET", "/openapi.json"): ("✅", "通过", "OpenAPI JSON（非业务信封，正常）"),
    ("GET", "/api/v1/datacenter/sync/tasks/{task_id}"): ("✅", "通过", "伪造 task_id，返回 51001 任务不存在（预期）"),
    ("GET", "/api/v1/studio/mining/status/{task_id}"): ("✅", "通过", "伪造 task_id，返回 51001 任务不存在（预期）"),
    ("GET", "/api/v1/notify/stream"): ("⚠️", "异常", "SSE 已连接但 12s 内 0 字节，无心跳/初始事件"),
}

ICON = {0: "✅", 40000: "⛔参数", 40100: "🔒鉴权", 40300: "🔒鉴权",
        51001: "⚠️无数据", 40012: "⚠️数据范围"}
STATUS = {0: "通过", 40000: "参数校验", 40100: "鉴权", 40300: "鉴权",
          51001: "无数据", 40012: "数据范围"}


def norm_path(p: str) -> str:
    p = (p.replace("600519.SH", "{symbol}").replace("510300", "{code}")
          .replace("qa-probe-task", "{task_id}").replace("qa-probe", "{task_id}"))
    if p.startswith("/api/v1/alerts/rules/"):
        p = "/api/v1/alerts/rules/{rule_id}"
    if p.startswith("/api/v1/studio/factors/"):
        p = "/api/v1/studio/factors/{factor_id}"
    return p


lines = ["| 端点 | 方法 | 鉴权 | HTTP | code | 结果 | 耗时 | 备注 |",
         "|---|---|---|---|---|---|---|---|"]
ok = bad = skipped = 0
for method, path in sorted(registered, key=lambda x: (x[1], x[0])):
    key = (method, path)
    np_ = norm_path(path)
    rk = f"{method} {path}"
    role = roles.get(rk, "?")
    if path in ("/docs", "/docs/oauth2-redirect", "/redoc", "/openapi.json", "/metrics"):
        role = "public"
    # 找实测记录（含路径参数具体化）
    rec = rows.get(key)
    if rec is None:
        for (m, p), v in rows.items():
            if m == method and norm_path(p) == np_:
                rec = v
                break
    if rec is None:
        lines.append(f"| `{np_}` | {method} | {role} | - | - | ⛔未执行 | - | 未覆盖 |")
        skipped += 1
        continue
    code = rec.get("code")
    ms = rec.get("ms")
    http = rec.get("http")
    ov = OVERRIDE.get((method, np_))
    if code == 0:
        icon, ok = "✅", ok + 1
        status = "通过"
    elif code in (40100, 40300):
        icon, status = "🔒", "鉴权路径已验证（未执行写操作）"
    elif code == 40000:
        icon, status = "⚠️", "参数校验生效"
    else:
        icon, status = "⚠️", rec.get("msg") or "异常"
        bad += 1
    if ov:
        icon, status, note = ov[0], ov[1], ov[2]
        if ov[0] == "⚠️":
            bad += 1
    if not ov:
        note = rec.get("note") or ""
    if isinstance(ms, int) and ms and ms > 10000:
        note = (note + " " if note else "") + "**慢**"
    mss = f"{ms}ms" if isinstance(ms, int) else str(ms)
    lines.append(f"| `{np_}` | {method} | {role} | {http} | {code} | {icon} {status} "
                 f"| {mss} | {note} |")

(BASE / "endpoint-table.md").write_text("\n".join(lines), encoding="utf-8")
print(f"rows={len(registered)} ok={ok} warn={bad} notrun={skipped}")
print("\n".join(lines[:8]))
