# -*- coding: utf-8 -*-
"""回归验证 · /ops/lineage 独立复测（yan-regression）。

不含业务改动，仅探测。输出 stdout。
"""
import json
import os
import sys
import time
import urllib.error
import urllib.request

ROOT = os.path.dirname(os.path.abspath(__file__))
PROJ = os.path.dirname(os.path.dirname(ROOT))
BACKEND = os.path.join(PROJ, "backend")
sys.path.insert(0, BACKEND)

BASE = "http://127.0.0.1:8000"

# 从项目根 .env 读 ADMIN_TOKEN（不打印）
ADMIN_TOKEN = ""
env_path = os.path.join(PROJ, ".env")
try:
    with open(env_path, encoding="utf-8") as f:
        for line in f:
            if line.startswith("ADMIN_TOKEN="):
                ADMIN_TOKEN = line.split("=", 1)[1].strip()
                break
except OSError:
    pass


def req(method, path, body=None, timeout=90):
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
            raw = resp.read().decode("utf-8", "replace")
            return resp.status, time.time() - t0, raw, ""
    except urllib.error.HTTPError as e:
        return e.code, time.time() - t0, e.read().decode("utf-8", "replace"), ""
    except Exception as e:
        return None, time.time() - t0, "", f"{type(e).__name__}: {e}"


def http_rounds(n=6):
    print(f"\n=== HTTP 稳态复测（{n} 轮，前端 60s 超时）===")
    times = []
    for i in range(n):
        st, el, raw, err = req("GET", "/api/v1/ops/lineage", timeout=90)
        flag = ""
        try:
            j = json.loads(raw)
            d = j.get("data") or {}
            flag = f"code={j.get('code')} from_cache={d.get('from_cache')} stale={d.get('stale')} nodes={len(d.get('nodes') or [])}"
        except Exception:
            flag = (raw or err)[:80]
        times.append(el)
        print(f"  round {i+1}: status={st} {el*1000:.1f} ms | {flag}")
    print(f"  稳态：min={min(times)*1000:.1f}ms max={max(times)*1000:.1f}ms avg={sum(times)/len(times)*1000:.1f}ms")
    return times


def redis_key_info():
    print("\n=== Redis 缓存键状态 ===")
    os.environ.setdefault("REDIS_HOST", "127.0.0.1")
    from app.core.config import get_settings
    from redis import Redis
    s = get_settings()
    r = Redis(host=s.REDIS_HOST, port=s.REDIS_PORT, db=s.REDIS_DB,
              password=s.REDIS_PASSWORD, socket_timeout=3)
    import datetime
    k = "aqp:ops:lineage:" + datetime.date.today().isoformat()
    print(f"  key={k}")
    print(f"  exists={r.exists(k)} ttl={r.ttl(k)} bytes={r.strlen(k)}")
    print(f"  swr_exists={r.exists(k + ':swr')} swr_ttl={r.ttl(k + ':swr')}")
    return r, k, s


def swr_stale_test(r, k):
    """只删主键、保留影子键 -> 请求应瞬时返回 stale=True 并触发后台重建。"""
    print("\n=== SWR 降级验证：仅删主键、保留影子键 ===")
    r.delete(k)
    print(f"  删除主键后：exists={r.exists(k)} swr_exists={r.exists(k + ':swr')}")
    st, el, raw, err = req("GET", "/api/v1/ops/lineage", timeout=90)
    try:
        j = json.loads(raw)
        d = j.get("data") or {}
        print(f"  请求：status={st} {el*1000:.1f}ms code={j.get('code')} "
              f"from_cache={d.get('from_cache')} stale={d.get('stale')}")
    except Exception:
        print(f"  请求：status={st} {el*1000:.1f}ms {(raw or err)[:120]}")
    print(f"  请求后：主键exists={r.exists(k)} 影子exists={r.exists(k + ':swr')}")


def cold_compute():
    print("\n=== 冷路径：直接测 _compute_lineage() 纯计算耗时 ===")
    from app.api.v1.ops import _compute_lineage
    t0 = time.time()
    data = _compute_lineage()
    el = time.time() - t0
    print(f"  _compute_lineage 冷算耗时={el:.2f}s nodes={len(data['nodes'])} edges={len(data['edges'])}")
    return el


def main():
    http_rounds(6)
    r, k, s = redis_key_info()
    cold_compute()
    swr_stale_test(r, k)
    # 验证后台重建后主键恢复
    time.sleep(40)
    print("\n=== 后台重建后复查（等待 40s）===")
    print(f"  主键exists={r.exists(k)} ttl={r.ttl(k)}")
    st, el, raw, err = req("GET", "/api/v1/ops/lineage", timeout=90)
    try:
        j = json.loads(raw)
        d = j.get("data") or {}
        print(f"  请求：status={st} {el*1000:.1f}ms from_cache={d.get('from_cache')} stale={d.get('stale')}")
    except Exception:
        print(f"  请求：{(raw or err)[:120]}")


if __name__ == "__main__":
    main()
