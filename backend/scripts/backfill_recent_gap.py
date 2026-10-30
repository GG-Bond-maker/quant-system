"""backfill_recent_gap.py —— 专门补全 2026-10-22 至 2026-10-28 历史行情的极速批处理脚本。

背景：
  全市场此前在 2026-10-29 写入了单日快照，但 2026-10-22 ~ 2026-10-28 这 5 个交易日
  因跳日增量同步机制未被包含，导致全市场 5200+ 标的存在 5 天的数据断档。
  本脚本通过 Eastmoney HTTP 接口并发补齐 raw 与 hfq，随后触发 QFQ 重建与 manifest 刷新。
"""
from __future__ import annotations

import argparse
import datetime
import sys
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

import polars as pl
import requests

PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT))

from app.core.config import get_settings  # noqa: E402
from app.data.parquet_store import (  # noqa: E402
    atomic_write_parquet,
    manifest_invalidate,
    manifest_scan_and_store,
    read_all_symbols,
)
from app.data.repair import anchor_factor_at, build_qfq_dataset  # noqa: E402

_URL = "http://push2test.eastmoney.com/api/qt/stock/kline/get"
_TARGET_DATES = [
    datetime.date(2026, 10, 22),
    datetime.date(2026, 10, 23),
    datetime.date(2026, 10, 26),
    datetime.date(2026, 10, 27),
    datetime.date(2026, 10, 28),
]


def _make_session(concurrency: int) -> requests.Session:
    s = requests.Session()
    adapter = requests.adapters.HTTPAdapter(
        pool_connections=concurrency,
        pool_maxsize=concurrency,
        max_retries=3,
    )
    s.mount("http://", adapter)
    return s


def backfill_symbol(
    session: requests.Session,
    root: Path,
    symbol: str,
    beg: str = "20261022",
    end: str = "20261029",
) -> tuple[str, int, str]:
    """回补单只标的。返回 (symbol, 写入行数, 状态信息)。"""
    if not (symbol.endswith(".SH") or symbol.endswith(".SZ")):
        return symbol, 0, "skip_non_a"

    code = symbol.split(".")[0]
    secid = ("1." if symbol.endswith(".SH") else "0.") + code

    p_raw = root / "daily_bar" / f"symbol={symbol}" / "year=2026.snappy.parquet"
    p_hfq = root / "daily_bar_hfq" / f"symbol={symbol}" / "year=2026.snappy.parquet"

    # 检查是否缺失
    if p_raw.exists() and p_hfq.exists():
        try:
            cur_raw = pl.read_parquet(p_raw)
            existing_dates = set(cur_raw["date"].to_list())
            missing = [d for d in _TARGET_DATES if d not in existing_dates]
            if not missing:
                return symbol, 0, "already_complete"
        except Exception:
            pass

    # 请求 Eastmoney 获取 K 线
    try:
        r = session.get(
            _URL,
            params={
                "secid": secid,
                "klt": 101,
                "fqt": 0,
                "beg": beg,
                "end": end,
                "fields1": "f1",
                "fields2": "f51,f52,f53,f54,f55,f56,f57,f58,f59,f60,f61",
            },
            timeout=5.0,
        )
        if r.status_code != 200:
            return symbol, 0, f"http_{r.status_code}"
        klines = (r.json().get("data") or {}).get("klines") or []
    except Exception as exc:
        return symbol, 0, f"fetch_error:{type(exc).__name__}"

    if not klines:
        return symbol, 0, "no_klines"

    # 解析行数据
    raw_rows = []
    for k in klines:
        p = k.split(",")
        d = datetime.date.fromisoformat(p[0])
        open_val = float(p[1])
        close_val = float(p[2])
        high_val = float(p[3])
        low_val = float(p[4])
        vol_val = float(p[5]) * 100.0  # 股
        amt_val = float(p[6])  # 元
        turn_val = float(p[10]) / 100.0 if len(p) > 10 and p[10] else 0.0

        raw_rows.append({
            "date": d,
            "open": open_val,
            "high": high_val,
            "low": low_val,
            "close": close_val,
            "volume": vol_val,
            "amount": amt_val,
            "turnover": turn_val,
            "code": code,
            "symbol": symbol,
            "source": "akshare",
        })

    new_raw_df = pl.DataFrame(
        raw_rows,
        schema={
            "date": pl.Date,
            "open": pl.Float64,
            "high": pl.Float64,
            "low": pl.Float64,
            "close": pl.Float64,
            "volume": pl.Float64,
            "amount": pl.Float64,
            "turnover": pl.Float64,
            "code": pl.String,
            "symbol": pl.String,
            "source": pl.String,
        },
    )

    # 1. 写入 daily_bar
    if p_raw.exists():
        existing_raw = pl.read_parquet(p_raw)
        merged_raw = pl.concat([existing_raw, new_raw_df]).unique(subset=["date"], keep="last").sort("date")
    else:
        merged_raw = new_raw_df.sort("date")
    p_raw.parent.mkdir(parents=True, exist_ok=True)
    atomic_write_parquet(p_raw, merged_raw)

    # 2. 计算复权因子并写入 daily_bar_hfq
    factor_info = anchor_factor_at(root, symbol, datetime.date(2026, 10, 21))
    if factor_info is None:
        # 若 10-21 之前找不到，尝试取 10-29 之后或默认 1.0
        factor_info = anchor_factor_at(root, symbol, datetime.date(2026, 10, 29))
    factor = factor_info[0] if factor_info is not None else 1.0

    new_hfq_df = new_raw_df.with_columns([
        (pl.col("open") * factor).alias("open"),
        (pl.col("high") * factor).alias("high"),
        (pl.col("low") * factor).alias("low"),
        (pl.col("close") * factor).alias("close"),
        pl.lit("akshare_snapshot").alias("source"),
    ])

    if p_hfq.exists():
        existing_hfq = pl.read_parquet(p_hfq)
        merged_hfq = pl.concat([existing_hfq, new_hfq_df]).unique(subset=["date"], keep="last").sort("date")
    else:
        merged_hfq = new_hfq_df.sort("date")
    p_hfq.parent.mkdir(parents=True, exist_ok=True)
    atomic_write_parquet(p_hfq, merged_hfq)

    return symbol, len(raw_rows), "ok"


def main() -> None:
    parser = argparse.ArgumentParser(description="补全 2026-10-22 ~ 2026-10-28 行情断档")
    parser.add_argument("--symbols", type=str, default="", help="指定标的列表，逗号分隔")
    parser.add_argument("--workers", type=int, default=25, help="并发网络线程数（默认 25）")
    parser.add_argument("--limit", type=int, default=0, help="处理标的上限数（0 为全部）")
    parser.add_argument("--skip-qfq", action="store_true", help="跳过 QFQ 重建")
    args = parser.parse_args()

    settings = get_settings()
    root = settings.DATA_ROOT

    if args.symbols:
        all_syms = [s.strip() for s in args.symbols.split(",") if s.strip()]
    else:
        all_syms = read_all_symbols("daily_bar")

    # 过滤出 A 股
    syms = [s for s in all_syms if s.endswith(".SH") or s.endswith(".SZ")]
    if args.limit > 0:
        syms = syms[:args.limit]

    print(f"[backfill] 开始补全行情断档: 共 {len(syms)} 只标的，并发线程数: {args.workers}", flush=True)
    t0 = time.time()
    session = _make_session(args.workers)

    success_cnt = 0
    already_cnt = 0
    fail_cnt = 0
    rows_total = 0

    with ThreadPoolExecutor(max_workers=args.workers, thread_name_prefix="gap-worker") as pool:
        futures = {pool.submit(backfill_symbol, session, root, s): s for s in syms}
        done = 0
        total = len(syms)
        for fut in as_completed(futures):
            sym, rows, status = fut.result()
            done += 1
            if status == "ok":
                success_cnt += 1
                rows_total += rows
            elif status == "already_complete":
                already_cnt += 1
            else:
                fail_cnt += 1

            if done % 200 == 0 or done == total:
                elapsed = time.time() - t0
                rate = done / max(0.1, elapsed)
                print(
                    f"  进度: {done}/{total} ({done*100.0/total:.1f}%) | "
                    f"已补入: {success_cnt} 只 ({rows_total} 行) | "
                    f"原本完整: {already_cnt} 只 | 异常: {fail_cnt} 只 | "
                    f"耗时: {elapsed:.1f}s ({rate:.1f} 只/s)",
                    flush=True,
                )

    print(f"\n[backfill] 阶段 1 完成: 成功补全 {success_cnt} 只标的，耗时 {time.time()-t0:.1f}s", flush=True)

    # 重建 QFQ
    if not args.skip_qfq:
        print("[backfill] 阶段 2: 正在并行重建 daily_bar_qfq 数据集...", flush=True)
        t_qfq = time.time()
        target_qfq_syms = syms if args.symbols else read_all_symbols("daily_bar_hfq")
        qfq_total = 0
        qfq_done = 0

        def _qfq_worker(sym: str) -> tuple[str, int]:
            return sym, build_qfq_dataset(root, sym, invalidate_manifest=False)

        with ThreadPoolExecutor(max_workers=8, thread_name_prefix="qfq-builder") as executor:
            futures = [executor.submit(_qfq_worker, s) for s in target_qfq_syms]
            for fut in as_completed(futures):
                _, n = fut.result()
                qfq_total += n
                qfq_done += 1
                if qfq_done % 500 == 0 or qfq_done == len(target_qfq_syms):
                    print(f"  ... QFQ 进度: {qfq_done}/{len(target_qfq_syms)} 标的完成，总行数={qfq_total}", flush=True)

        print(
            f"[backfill] QFQ 重建完成: {qfq_done} 只标的，{qfq_total} 行，耗时 {time.time()-t_qfq:.1f}s",
            flush=True,
        )

    # 批处理完成后统一刷新 Manifest 缓存
    print("[backfill] 正在刷新全量 Manifest 索引 (daily_bar, daily_bar_hfq, daily_bar_qfq)...", flush=True)
    manifest_invalidate()
    manifest_scan_and_store(["daily_bar", "daily_bar_hfq", "daily_bar_qfq"])
    print("[backfill] Manifest 刷新完成", flush=True)

    print(f"[backfill] 全流程完毕！总耗时: {time.time()-t0:.1f}s", flush=True)


if __name__ == "__main__":
    main()
