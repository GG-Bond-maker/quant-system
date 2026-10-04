"""A 股实时子块 + 东财直连定性（ds-verify, 2026-10-04）。

目的：确认 `/overview/rt` 降级的根因，并区分「沙箱网络不可达」与「东财源站断开」。
只读、无副作用、带超时。
"""
from __future__ import annotations

import sys
import time
from pathlib import Path

BACKEND_ROOT = Path(__file__).resolve().parents[3] / "backend"
if not str(BACKEND_ROOT) in sys.path:
    sys.path.insert(0, str(BACKEND_ROOT))

import httpx  # noqa: E402


def _chain(e: BaseException) -> str:
    out, seen, cur = [], set(), e
    while cur is not None and id(cur) not in seen:
        seen.add(id(cur)); out.append(type(cur).__name__)
        cur = cur.__cause__ or cur.__context__
    return " -> ".join(out)


def direct_eastmoney() -> None:
    print("\n=== ① 东财主机直连（区分网络 vs 源站） ===", flush=True)
    urls = [
        "https://push2.eastmoney.com/api/qt/clist/get?pn=1&pz=5&fs=m:1+t:2&fields=f12,f14",
        "https://push2.eastmoney.com/api/qt/ulist.np/get?fltt=2&secids=1.000001,0.399001&fields=f2,f3,f12,f14",
    ]
    for u in urls:
        t0 = time.time()
        try:
            r = httpx.get(u, timeout=8.0, follow_redirects=True,
                          headers={"User-Agent": "Mozilla/5.0"})
            print(f"  OK  {r.status_code} ({time.time()-t0:.2f}s, {len(r.content)}B) "
                  f"{u[:60]}...", flush=True)
        except Exception as e:  # noqa: BLE001
            print(f"  FAIL {type(e).__name__}: {e} ({time.time()-t0:.2f}s) "
                  f"{u[:60]}...", flush=True)
            print(f"       chain={_chain(e)}", flush=True)


def probe_heat() -> None:
    print("\n=== ② 直调 _build_heat（东财全市场快照，失败降级本地） ===", flush=True)
    import importlib
    market = importlib.import_module("app.api.v1.market")
    t0 = time.time()
    try:
        d = market._build_heat()
        print(f"  status={d.get('status')} source={d.get('source')} "
              f"data_date={d.get('data_date')} ({time.time()-t0:.2f}s)", flush=True)
    except Exception as e:  # noqa: BLE001
        print(f"  FAIL {type(e).__name__}: {e} ({time.time()-t0:.2f}s)", flush=True)
        print(f"       chain={_chain(e)}", flush=True)


def probe_money_flow() -> None:
    print("\n=== ③ 直调 _build_money_flow（东财资金流） ===", flush=True)
    import importlib
    svc = importlib.import_module("app.services.market_service")
    t0 = time.time()
    try:
        d = svc._build_money_flow()
        print(f"  status={d.get('status')} source={d.get('source')} "
              f"keys={list(d.keys())} ({time.time()-t0:.2f}s)", flush=True)
    except Exception as e:  # noqa: BLE001
        print(f"  FAIL {type(e).__name__}: {e} ({time.time()-t0:.2f}s)", flush=True)
        print(f"       chain={_chain(e)}", flush=True)


if __name__ == "__main__":
    direct_eastmoney()
    probe_heat()
    probe_money_flow()
    print("\n=== 结束 ===", flush=True)
