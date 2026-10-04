"""只读数据源探针（ds-verify, 2026-10-04）。

目标：逐个验证市场概览「外盘」块（道指/纳指/标普500/日经225/KOSPI）与
A 股大盘块（上证/深证/创业板/科创50/沪深300）的数据源能否返回有效数据。

纪律：
- **只读**：不写任何生产文件、不碰 Redis、不起服务。
- 外呼带 timeout 与重试上限（复用生产适配器自带的限速/熔断）。
- 先做「公网可达性」对照，用于区分「沙箱无网络」与「数据源/代码有问题」。
"""
from __future__ import annotations

import importlib
import socket
import sys
import time
import traceback
from pathlib import Path

BACKEND_ROOT = Path(__file__).resolve().parents[3] / "backend"
if not str(BACKEND_ROOT) in sys.path:
    sys.path.insert(0, str(BACKEND_ROOT))

import httpx  # noqa: E402


def _sep(t: str) -> None:
    print("\n" + "=" * 72)
    print(t)
    print("=" * 72, flush=True)


def net_reachability() -> None:
    _sep("① 公网可达性对照（区分沙箱无网络 vs 数据源问题）")
    # DNS 解析
    for host in ["qt.gtimg.cn", "hq.sinajs.cn", "finance.sina.com.cn",
                 "push2.eastmoney.com", "www.baidu.com"]:
        try:
            ip = socket.gethostbyname(host)
            print(f"  DNS  {host:28s} -> {ip}", flush=True)
        except Exception as e:  # noqa: BLE001
            print(f"  DNS  {host:28s} -> FAIL {type(e).__name__}: {e}", flush=True)
    # HTTP 直连（短超时，不重试）
    for url in ["https://www.baidu.com", "https://qt.gtimg.cn/q=usDJI"]:
        t0 = time.time()
        try:
            r = httpx.get(url, timeout=8.0, follow_redirects=True)
            print(f"  HTTP {url:45s} -> {r.status_code} "
                  f"({time.time()-t0:.2f}s, {len(r.content)}B)", flush=True)
        except Exception as e:  # noqa: BLE001
            print(f"  HTTP {url:45s} -> FAIL {type(e).__name__}: {e} "
                  f"({time.time()-t0:.2f}s)", flush=True)


def probe_global_indices() -> None:
    _sep("② 外盘指数：逐个直调适配器 fetch_global_index_daily")
    adapter = importlib.import_module("app.data.ingest.akshare_adapter")
    for kind, symbol, name in adapter.GLOBAL_INDICES:
        t0 = time.time()
        try:
            df = adapter.fetch_global_index_daily(kind, symbol)
            dt = time.time() - t0
            if df is None or df.is_empty():
                print(f"  [{name}] kind={kind} sym={symbol!r} -> 空表 "
                      f"({dt:.2f}s)", flush=True)
                continue
            last = df.tail(1)
            prev_close = float(df["close"][-2]) if df.height >= 2 else None
            close = float(last["close"][0])
            d = last["date"][0]
            pct = ((close / prev_close - 1) * 100) if prev_close else None
            print(f"  [{name}] kind={kind} sym={symbol!r} -> OK rows={df.height} "
                  f"close={close} pct={pct if pct is None else round(pct,2)} "
                  f"data_date={d} ({dt:.2f}s)", flush=True)
        except Exception as e:  # noqa: BLE001
            dt = time.time() - t0
            print(f"  [{name}] kind={kind} sym={symbol!r} -> FAIL "
                  f"{type(e).__name__}: {e} ({dt:.2f}s)", flush=True)
            print(f"      cause_chain={_cause_chain(e)}", flush=True)


def probe_tencent_quotes() -> None:
    _sep("③ 外盘美股：腾讯报价 fetch_global_index_quotes_tencent")
    realtime = importlib.import_module("app.data.realtime")
    syms = list(realtime.GLOBAL_INDEX_TENCENT_CODES)
    t0 = time.time()
    try:
        q = realtime.fetch_global_index_quotes_tencent(syms)
        dt = time.time() - t0
        print(f"  symbols={syms} -> {q} ({dt:.2f}s)", flush=True)
    except Exception as e:  # noqa: BLE001
        dt = time.time() - t0
        print(f"  symbols={syms} -> FAIL {type(e).__name__}: {e} ({dt:.2f}s)",
              flush=True)
        print(f"      cause_chain={_cause_chain(e)}", flush=True)


def probe_build_global() -> None:
    _sep("④ 外盘块：直调 _build_global_indices（含腾讯覆盖 + 逐项 data_date）")
    market = importlib.import_module("app.api.v1.market")
    t0 = time.time()
    try:
        data = market._build_global_indices()
        dt = time.time() - t0
        print(f"  status={data.get('status')} failed={data.get('failed')} "
              f"({dt:.2f}s)", flush=True)
        for it in data.get("items", []):
            print(f"    - {it['name']:20s} close={it['close']} pct={it['pct']} "
                  f"data_date={it['data_date']} source={it['source']} "
                  f"as_of={it.get('as_of')} spark_len={len(it.get('sparkline') or [])}",
                  flush=True)
        if not data.get("items"):
            print(f"    reason={data.get('reason')}", flush=True)
    except Exception as e:  # noqa: BLE001
        dt = time.time() - t0
        print(f"  FAIL {type(e).__name__}: {e} ({dt:.2f}s)", flush=True)
        print(traceback.format_exc(), flush=True)


def probe_a_share_indices() -> None:
    _sep("⑤ A 股大盘：逐个直调 fetch_index_daily（CORE_INDICES）")
    adapter = importlib.import_module("app.data.ingest.akshare_adapter")
    for code, name in adapter.CORE_INDICES:
        t0 = time.time()
        try:
            df = adapter.fetch_index_daily(code)
            dt = time.time() - t0
            if df is None or df.is_empty():
                print(f"  [{name}] {code} -> 空表 ({dt:.2f}s)", flush=True)
                continue
            close = float(df["close"][-1])
            prev = float(df["close"][-2]) if df.height >= 2 else None
            pct = ((close / prev - 1) * 100) if prev else None
            print(f"  [{name}] {code} -> OK rows={df.height} close={close} "
                  f"pct={pct if pct is None else round(pct,2)} "
                  f"data_date={df['date'][-1]} ({dt:.2f}s)", flush=True)
        except Exception as e:  # noqa: BLE001
            dt = time.time() - t0
            print(f"  [{name}] {code} -> FAIL {type(e).__name__}: {e} ({dt:.2f}s)",
                  flush=True)
            print(f"      cause_chain={_cause_chain(e)}", flush=True)


def probe_build_a_indices() -> None:
    _sep("⑥ A 股大盘块：直调 _build_indices")
    market = importlib.import_module("app.api.v1.market")
    t0 = time.time()
    try:
        data = market._build_indices()
        dt = time.time() - t0
        print(f"  status={data.get('status')} failed={data.get('failed')} "
              f"({dt:.2f}s)", flush=True)
        for it in data.get("items", []):
            print(f"    - {it['name']:12s} {it['code']} close={it['close']} "
                  f"pct={it['pct']} spark_len={len(it.get('sparkline') or [])}",
                  flush=True)
        if not data.get("items"):
            print(f"    reason={data.get('reason')}", flush=True)
    except Exception as e:  # noqa: BLE001
        dt = time.time() - t0
        print(f"  FAIL {type(e).__name__}: {e} ({dt:.2f}s)", flush=True)
        print(traceback.format_exc(), flush=True)


def _cause_chain(e: BaseException) -> str:
    out = []
    seen: set[int] = set()
    cur: BaseException | None = e
    while cur is not None and id(cur) not in seen:
        seen.add(id(cur))
        out.append(type(cur).__name__)
        cur = cur.__cause__ or cur.__context__
    return " -> ".join(out)


if __name__ == "__main__":
    print(f"BACKEND_ROOT={BACKEND_ROOT}", flush=True)
    net_reachability()
    probe_global_indices()
    probe_tencent_quotes()
    probe_build_global()
    probe_a_share_indices()
    probe_build_a_indices()
    _sep("探针结束")
