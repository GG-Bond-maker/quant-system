# -*- coding: utf-8 -*-
"""写操作端点补测（audit-2026-09-27 遗留缺口）。

安全策略：
  - 实测        ：无害最小参数 / 不存在的 id，验证真实路径且不改数据；
  - 参数校验型探测：故意非法参数触发校验分支（40000/51001），不执行真实任务；
  - 跳过        ：高危且无请求体可供校验型探测，无法安全触碰。

输出：smoke-write-endpoints.csv（UTF-8-SIG）
"""
import csv
import json
import os
import time
import urllib.error
import urllib.request

ROOT = os.path.dirname(os.path.abspath(__file__))
PROJ = os.path.dirname(os.path.dirname(ROOT))
BASE = "http://127.0.0.1:8000"
OUT = os.path.join(ROOT, "smoke-write-endpoints.csv")

ADMIN_TOKEN = ""
try:
    with open(os.path.join(PROJ, ".env"), encoding="utf-8") as f:
        for line in f:
            if line.startswith("ADMIN_TOKEN="):
                ADMIN_TOKEN = line.split("=", 1)[1].strip()
                break
except OSError:
    pass


def call(method, path, body=None, timeout=150):
    headers = {"Accept": "application/json"}
    if ADMIN_TOKEN:
        headers["Authorization"] = "Bearer " + ADMIN_TOKEN
    data = None
    if body is not None:
        data = json.dumps(body).encode("utf-8")
        headers["Content-Type"] = "application/json"
    r = urllib.request.Request(BASE + path, data=data, headers=headers, method=method)
    t0 = time.time()
    try:
        with urllib.request.urlopen(r, timeout=timeout) as resp:
            raw = resp.read(20000)
            ctype = resp.headers.get("Content-Type", "")
            return resp.status, time.time() - t0, raw, ctype, ""
    except urllib.error.HTTPError as e:
        return e.code, time.time() - t0, e.read(20000), "", ""
    except Exception as e:
        return None, time.time() - t0, b"", "", f"{type(e).__name__}: {e}"


def jget(path):
    st, el, raw, ct, err = call("GET", path)
    try:
        return json.loads(raw.decode("utf-8"))
    except Exception:
        return {"_raw": raw[:200].decode("utf-8", "replace"), "_err": err}


def bizcode(raw):
    try:
        j = json.loads(raw.decode("utf-8"))
        return j.get("code")
    except Exception:
        return "binary" if raw else ""


def prefix(raw, n=110):
    return raw[:n].decode("utf-8", "replace").replace("\n", " ")


# (method, path, category, body, note)
PROBES = [
    ("POST", "/api/v1/alerts/events/read", "已实测",
     {"ids": [999999999]}, "标记不存在的事件 id（marked=0，无数据变更）"),
    ("POST", "/api/v1/alerts/rules", "参数校验型探测",
     {"name": "qa-probe", "rule_type": "__invalid__"}, "非法 rule_type → 40000，不落库"),
    ("PUT", "/api/v1/alerts/rules/999999", "已实测",
     {"name": "qa-probe", "rule_type": "price_pct", "scope": "symbol", "symbol": "600519.SH",
      "params": {"threshold": 5}, "channels": ["sse"]}, "不存在的 rule_id → 40400"),
    ("DELETE", "/api/v1/alerts/rules/999999", "已实测", None, "不存在的 rule_id → 40400"),
    ("POST", "/api/v1/datacenter/mirror/rebuild", "参数校验型探测",
     {"dataset": 123}, "非法类型 → 40000，不触发重建"),
    ("POST", "/api/v1/datacenter/sync", "参数校验型探测",
     {"mode": "__invalid__"}, "非法 mode → 40000，不启动同步"),
    ("POST", "/api/v1/datacenter/sync/auto", "参数校验型探测",
     {"enabled": True, "time": "99:99"}, "非法时间格式 → 40000，不写配置"),
    ("POST", "/api/v1/datacenter/sync/cancel", "已实测", None, "无运行任务 → cancelled=false"),
    ("POST", "/api/v1/datacenter/sync/fetch", "参数校验型探测",
     {"start": "bad", "end": "bad"}, "非法日期 → 40000，不抓取"),
    ("POST", "/api/v1/datacenter/text/import", "参数校验型探测",
     {}, "缺 docs → 40000，不导入"),
    ("POST", "/api/v1/datacenter/train/cancel", "已实测", None, "无训练任务 → 幂等取消"),
    ("POST", "/api/v1/datacenter/train/start", "参数校验型探测",
     {}, "缺 model → 40000，不启动训练"),
    ("POST", "/api/v1/desk/exclusion", "参数校验型探测",
     {"symbol": "600519.SH", "category": "__invalid__"}, "非法 category → 40000，不入池"),
    ("POST", "/api/v1/desk/exclusion/toggle", "已实测",
     {"id": 999999, "active": False}, "不存在的条目 → 51001，不写库"),
    ("POST", "/api/v1/desk/fills/run", "跳过（原因）", None,
     "高危：真实撮合改动持仓/现金；无请求体，无法参数校验型探测"),
    ("POST", "/api/v1/desk/kill-switch", "已实测",
     {"active": False}, "置为当前值 false（幂等 no-op，不改变状态）"),
    ("POST", "/api/v1/desk/orders", "参数校验型探测",
     {"symbol": "600519.SH", "side": "__invalid__", "order_amount": 100000}, "非法 side → 40000，不下单"),
    ("POST", "/api/v1/export/backtest", "已实测",
     {"start": "2024-01-01", "end": "2024-06-30", "top_k": 10}, "真实重跑并导出 xlsx（无持久副作用）"),
    ("POST", "/api/v1/export/strategy-backtest", "已实测",
     {"start": "2024-01-01", "end": "2024-06-30", "symbols": ["600519"]}, "真实重跑并导出 xlsx（无持久副作用）"),
    ("POST", "/api/v1/monitor/run", "跳过（原因）", None,
     "高危：真实监控重算并写快照；无请求体，无法参数校验型探测"),
    ("POST", "/api/v1/ops/dag/rerun", "参数校验型探测",
     {"trade_date": "9999-99-99"}, "非法交易日 → 40000，不跑流水线"),
    ("POST", "/api/v1/ops/quality-scan", "已实测",
     {}, "只读 QC 扫描（不写数据）"),
    ("POST", "/api/v1/report/daily/generate", "已实测", None, "重新生成日报（幂等）"),
    ("POST", "/api/v1/settings/apikeys/rotate", "已实测", None, "实现为硬编码拒绝 → 40000，无副作用"),
    ("POST", "/api/v1/settings/data/cache/clear", "跳过（原因）", None,
     "高危：清空 Redis aqp:* 与 LRU；无请求体"),
    ("POST", "/api/v1/settings/data/sync", "跳过（原因）", None,
     "高危：触发真实增量数据同步；无请求体"),
    ("POST", "/api/v1/settings/db/backup", "跳过（原因）", None,
     "高危：磁盘水位 93%，避免写入大备份文件；无请求体"),
    ("PUT", "/api/v1/settings/engine", "参数校验型探测",
     {"commission_pct": "abc"}, "非法类型 → 40000，不写配置"),
    ("PUT", "/api/v1/settings/preferences", "参数校验型探测",
     {"refresh_freq": "abc"}, "非法类型 → 40000，不写偏好"),
    ("POST", "/api/v1/studio/factors", "参数校验型探测",
     {"name": "qa_probe", "expression": "1 +"}, "非法表达式 → 53001，不入库"),
    ("DELETE", "/api/v1/studio/factors/999999", "已实测", None, "不存在的因子 → 51001"),
    ("POST", "/api/v1/studio/mining/cancel/qa-probe-nonexistent", "已实测", None, "不存在的任务 → 51001"),
    ("POST", "/api/v1/studio/mining/start", "参数校验型探测",
     {"fields": ["__no_such_field__"]}, "不存在的字段 → 51001，不启动挖掘"),
]


def snapshot():
    rules = jget("/api/v1/alerts/rules")
    excl = jget("/api/v1/desk/exclusion")
    orders = jget("/api/v1/desk/orders")
    factors = jget("/api/v1/studio/factors")
    sync = jget("/api/v1/datacenter/sync/status")
    train = jget("/api/v1/datacenter/train/status")
    ks = jget("/api/v1/desk/kill-switch")
    return {
        "alerts_rules": len(rules.get("data") or []) if isinstance(rules.get("data"), list) else None,
        "exclusion": len(excl.get("data") or []) if isinstance(excl.get("data"), list) else None,
        "orders": len(orders.get("data") or []) if isinstance(orders.get("data"), list) else None,
        "factors": len(factors.get("data") or []) if isinstance(factors.get("data"), list) else None,
        "sync_running": (sync.get("data") or {}).get("running"),
        "train_running": (train.get("data") or {}).get("running"),
        "kill_switch": (ks.get("data") or {}).get("kill_switch"),
    }


def main():
    pre = snapshot()
    print("PRE :", json.dumps(pre, ensure_ascii=False))
    rows = []
    for method, path, category, body, note in PROBES:
        if category.startswith("跳过"):
            rows.append([method, path, category, "", "", "", "", "", note])
            print(f"{method:6} {path:52} SKIP")
            continue
        st, el, raw, ct, err = call(method, path, body)
        bc = bizcode(raw) if raw else ""
        is_bin = "spreadsheet" in ct or "octet-stream" in ct
        shown = f"<binary {len(raw)}B>" if is_bin else prefix(raw)
        rows.append([method, path, category, json.dumps(body, ensure_ascii=False) if body else "",
                     st if st else "ERR", f"{el:.2f}", bc, "见复核", shown])
        print(f"{method:6} {path:52} {st} {el:6.2f}s code={bc} {shown[:70]}")
    post = snapshot()
    print("\nPOST:", json.dumps(post, ensure_ascii=False))

    with open(OUT, "w", newline="", encoding="utf-8-sig") as f:
        w = csv.writer(f)
        w.writerow(["method", "path", "category", "request", "status", "elapsed_s",
                    "bizcode", "side_effect", "note"])
        w.writerows(rows)
    print(f"\n写入 {OUT}，共 {len(rows)} 行")
    print("\n=== 副作用复核（PRE vs POST）===")
    for k in pre:
        flag = "OK" if pre[k] == post[k] else "!! 变化"
        print(f"  {k:14} {pre[k]!s:>8} -> {post[k]!s:<8} {flag}")


if __name__ == "__main__":
    main()
