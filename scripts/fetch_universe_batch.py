"""批量扩容抓取：从 instrument 池分层抽样 N 只，抓日线并落库 daily_bar。

背景（2026-09-04）：东方财富接口对本机出口断连（TLS 握手后被掐，代理/直连均复现），
akshare_adapter.fetch_daily_bar 的"东财主源→新浪备源"双源冗余每次调用都要先等东财
失败重试（约 3-4 秒），1000 只 × 3 个复权口径会白白多耗约 3 小时。本脚本提供
--source sina 选项绕过东财直接走新浪备源，但**完全复用**平台抓取写路径
（fetch_and_write_daily_bars -> write_daily_bars -> 质量门禁），不改任何生产代码；
东财恢复后用 --source default 即回到标准双源冗余。

用法（backend 目录取虚拟环境）：
    python ../scripts/fetch_universe_batch.py --limit 12              # 试点
    python ../scripts/fetch_universe_batch.py --limit 1000            # 扩容 1000 只
    python ../scripts/fetch_universe_batch.py --limit 100 --start 2024-01-01

特性：
- 分层抽样：按代码前 3 位轮转（round-robin），跨深主板/创业板/沪主板/科创板均匀铺开，
  避免"按代码排序前 N 只"全挤在 000/002 深主板段造成样本偏斜；
- 断点续跑：daily_bar 的 symbol=X 分区目录已存在则跳过（--force 强制重抓）；
- 状态落盘：data/.fetch_universe_state.json 记录成功/失败/无数据清单，可审计；
- 北交所 92xxxx 段新浪不支持（_sina_symbol 误映射为 sh），默认排除不浪费名额。
"""
from __future__ import annotations

import argparse
import json
import os
import socket
import sqlite3
import sys
import time
from collections import defaultdict
from datetime import date
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT / "backend"))

from app.core.config import get_settings  # noqa: E402


def _load_universe(exclude_existing: bool) -> list[str]:
    """读 instrument 全量代码；exclude_existing 时剔除已有 daily_bar 分区的标的。"""
    s = get_settings()
    conn = sqlite3.connect(f"file:{s.SQLITE_PATH}?mode=ro", uri=True)
    try:
        rows = [r[0] for r in conn.execute("SELECT symbol FROM instrument ORDER BY symbol")]
    finally:
        conn.close()
    if not exclude_existing:
        return rows
    # L2-3：跳过逻辑统一走 parquet_store 公共函数（manifest 优先，免全量 glob）
    from app.data.parquet_store import list_symbols_with_data

    existing = list_symbols_with_data("daily_bar")
    return [x for x in rows if x not in existing]


def _stratified_pick(symbols: list[str], limit: int) -> list[str]:
    """按 3 位代码前缀轮转抽样，保证板块覆盖均匀；排除新浪不支持的 92xxxx 段。"""
    buckets: dict[str, list[str]] = defaultdict(list)
    for sym in symbols:
        if sym.split(".")[0].startswith("92"):
            continue  # 北交所新浪日线不支持，浪费名额
        buckets[sym[:3]].append(sym)
    prefixes = sorted(buckets)
    picked: list[str] = []
    i = 0
    while len(picked) < limit:
        advanced = False
        for p in prefixes:
            if i < len(buckets[p]):
                picked.append(buckets[p][i])
                advanced = True
                if len(picked) >= limit:
                    break
        if not advanced:
            break
        i += 1
    return picked


def main() -> None:
    ap = argparse.ArgumentParser(description="批量扩容抓取日线行情（分层抽样 + 断点续跑）")
    ap.add_argument("--limit", type=int, default=1000, help="本次目标抓取标的数")
    ap.add_argument("--start", default="2022-01-01", help="起始日期 YYYY-MM-DD")
    ap.add_argument("--end", default=date.today().isoformat(), help="结束日期，默认今天")
    ap.add_argument("--source", choices=["sina", "default"], default="sina",
                    help="sina=绕过东财直走备源；default=标准双源冗余")
    ap.add_argument("--force", action="store_true", help="已存在分区的标的也重抓")
    args = ap.parse_args()

    # 兜底超时：akshare 内部 requests 不显式设 timeout，遭遇半死连接会永久挂起
    # （实测卡死整批任务 28 分钟）。设全局 socket 超时后，挂起请求 60s 必抛
    # socket.timeout，被上层 try/except 当作单只失败跳过，任务不断流。
    socket.setdefaulttimeout(60)

    s = get_settings()
    state_path = s.DATA_ROOT.parent / ".fetch_universe_state.json"
    universe = _load_universe(exclude_existing=not args.force)
    todo = _stratified_pick(universe, args.limit)
    print(f"universe 可抓 {len(universe)} 只，本次目标 {len(todo)} 只 "
          f"({args.start} ~ {args.end}, source={args.source})")

    from app.data.ingest import akshare_adapter as ada
    from app.data.ingest.tasks import fetch_and_write_daily_bars

    if args.source == "sina":
        def fetcher(code, start, end, adjust):  # noqa: ANN001
            return ada._fetch_daily_bar_sina(code, start, end, adjust)
    else:
        fetcher = None  # 默认：东财主源 -> 新浪备源

    state: dict = {"started_at": time.strftime("%Y-%m-%d %H:%M:%S"),
                   "range": [args.start, args.end], "source": args.source,
                   "ok": [], "no_data": [], "failed": {}}

    t0 = time.time()

    def _flush_state(done: int) -> None:
        state["done"] = done
        state["elapsed_s"] = round(time.time() - t0, 1)
        state_path.write_text(json.dumps(state, ensure_ascii=False, indent=1),
                              encoding="utf-8")

    # 3 线程并行：新浪单请求延迟 ~6s（限速表现为变慢而非拒绝），
    # 线程把延迟重叠起来；适配器 _throttle 全局锁保证请求启动间隔 ≥1.2s+jitter，
    # 实际请求频率与单线程试点（~0.6 req/s，未触发封禁）同级。
    # 写路径按 (dataset, symbol, year) 文件隔离，不同标的并行写互不冲突。
    from concurrent.futures import ThreadPoolExecutor, as_completed

    def _one(sym: str) -> int:
        return fetch_and_write_daily_bars(
            sym.split(".")[0], args.start, args.end,
            adjusts=("", "hfq", "qfq"), fetcher=fetcher)

    done_cnt = 0
    with ThreadPoolExecutor(max_workers=3, thread_name_prefix="fetcher") as ex:
        futs = {ex.submit(_one, sym): sym for sym in todo}
        for fut in as_completed(futs):
            sym = futs[fut]
            done_cnt += 1
            t1 = time.time()
            try:
                n = fut.result()
            except Exception as e:  # noqa: BLE001
                state["failed"][sym] = f"{type(e).__name__}: {e}"[:160]
                n = 0
                print(f"[{done_cnt}/{len(todo)}] {sym} FAILED "
                      f"{state['failed'][sym]}", flush=True)
            else:
                if n:
                    state["ok"].append(sym)
                    print(f"[{done_cnt}/{len(todo)}] {sym} {n} 行  "
                          f"({time.time()-t1:.1f}s)", flush=True)
                else:
                    state["no_data"].append(sym)
                    print(f"[{done_cnt}/{len(todo)}] {sym} 无数据", flush=True)
            _flush_state(done_cnt)  # 每只都落盘：挂起/中断时进度可审计

    print(f"\n=== 完成：成功 {len(state['ok'])} / 无数据 {len(state['no_data'])} / "
          f"失败 {len(state['failed'])}，耗时 {(time.time()-t0)/60:.1f} 分钟 ===", flush=True)
    if state["failed"]:
        print("失败清单（前 20）:", list(state["failed"].items())[:20], flush=True)
    _flush_state(len(todo))


if __name__ == "__main__":
    main()
