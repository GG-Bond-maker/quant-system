"""定点补抓：修复质量检测发现的三类异常标的（2026-10-02）。

## 为什么需要它（而不是"改覆盖判定再全量跑一遍"）

质检暴露三类异常：

1. **尾部陈旧**：255 只末日 < 2026-09-30（其中 **243 只末日恰好都是 2026-09-17**，同一日 ⇒ 系统性）。
   根因是 `_classify_fetch_coverage` **只判起点**（`min(date) <= cover_min`）⇒ 起点达标的标的
   每次运行都被判"已覆盖"而跳过，**尾部永不更新**。
2. **两口径日期集合不一致**：`000695.SZ` raw 比 hfq 多 1 天 ⇒ 派生 qfq 会错位（当前阻断重建）。
3. **量纲异常**：`300269.SZ` / `300270.SZ` 的 `amount/(volume×close)` ≈ 100 ⇒ volume 是**手**而非**股**。

🔴 **本脚本刻意"定点补抓"，不去改 `_classify_fetch_coverage` 的语义** ——
那是安全关键函数（它守着"hfq 空洞不被静默传播到 qfq"）。语义改动应作为**独立改动**、
配对照实验后再做；此处只把异常标的**显式列出来重抓**，零语义风险。

写入路径与流水线**完全同源**（复用 `app.data.ingest.tasks.fetch_and_write_daily_bars`），
确保质量门禁与分区切分口径一致。

用法（cd backend）::

    # 自动探测三类异常并补抓
    ./.venv/Scripts/python.exe scripts/repair_fetch_anomalies.py
    # 只补抓指定标的
    ./.venv/Scripts/python.exe scripts/repair_fetch_anomalies.py --symbols 000695.SZ,300269.SZ
    # 只探测不抓（dry-run）
    ./.venv/Scripts/python.exe scripts/repair_fetch_anomalies.py --dry-run
"""
from __future__ import annotations

import argparse
import json
import sys
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import date, timedelta
from pathlib import Path

import polars as pl

PROJECT_ROOT = Path(__file__).resolve().parents[2]
DATA_ROOT = PROJECT_ROOT / "data" / "parquet"
UNIVERSE = PROJECT_ROOT / "data" / "universe_full.json"

START = date(2022, 1, 1)
END = date(2026, 9, 30)
# 尾部容差：末日早于「目标末日 − 容差」即视为陈旧。
# 取 7 自然日（≈5 个交易日）：既能抓住"被某次早期抓取截断"的系统性陈旧（本例滞后 13 天），
# 又不至于把刚停牌一两天的标的也拉进来。停牌更久的会被重复补抓 —— 那是**安全的过度抓取**。
TAIL_TOLERANCE_DAYS = 7
# 量纲恒等式合理区间（1.0 = 股）
RATIO_LO, RATIO_HI = 0.9, 1.1
WORKERS = 3


def _parts(sym: str, ds: str = "daily_bar") -> list[Path]:
    return sorted((DATA_ROOT / ds / f"symbol={sym}").glob("year=*.parquet"))


def _all_dates(files: list[Path]) -> set[str]:
    """读**全部年份分区**的 date 集合（与质检脚本同口径）。

    实测成本很低：只读 `date` 单列，约 1ms/分区 ⇒ 5220 只 × ~10 分区 ≈ 1 分钟。
    """
    out: set[str] = set()
    for f in files:
        out |= {str(x) for x in pl.read_parquet(f, columns=["date"])["date"].to_list()}
    return out


def detect(symbols: list[str]) -> dict[str, list[dict]]:
    """探测三类异常。只读首/末分区（分区按 year 落盘），不读全部年份。"""
    floor = END - timedelta(days=TAIL_TOLERANCE_DAYS)
    stale: list[dict] = []
    mismatch: list[dict] = []
    unit_bad: list[dict] = []

    for sym in symbols:
        raw = _parts(sym, "daily_bar")
        hfq = _parts(sym, "daily_bar_hfq")
        if not raw:
            continue
        try:
            last = pl.read_parquet(raw[-1], columns=["date"])
            mx = last["date"].max()
        except Exception:  # noqa: BLE001
            continue
        if mx is not None and mx < floor:
            stale.append({"symbol": sym, "last_date": str(mx)})

        # 🔴 hfq 尾部**必须单独判**（2026-10-02 修）：raw 尾部正常**不代表** hfq 正常。
        # 旧实现只看 raw ⇒ 「raw 到 09-30、hfq 停在 09-17」的标的**永远不会进补抓清单**
        # （实测漏 2 只：688015.SH / 688028.SH，hfq 缺 8 个交易日，qfq 派生也跟着缺）。
        # 只读末分区，成本可忽略。
        if hfq:
            try:
                hmx = pl.read_parquet(hfq[-1], columns=["date"])["date"].max()
                if hmx is not None and hmx < floor:
                    stale.append({"symbol": sym, "last_date": str(hmx), "caliber": "hfq"})
            except Exception:  # noqa: BLE001
                pass

        if raw and hfq:
            # 🔴 必须与**质检脚本同口径**：质检的 `hfq_date_set_mismatch` 读**全部年份分区**，
            # 若这里只比末分区，就会出现"补抓修不掉的项"⇒ 复检反复阻断（口径不一致的典型）。
            try:
                r_dates = _all_dates(raw)
                h_dates = _all_dates(hfq)
                if r_dates != h_dates:
                    mismatch.append({"symbol": sym,
                                     "raw_only": len(r_dates - h_dates),
                                     "hfq_only": len(h_dates - r_dates)})
            except Exception:  # noqa: BLE001
                pass

        try:
            first = pl.read_parquet(raw[0], columns=["close", "volume", "amount"])
            m = first.filter((pl.col("volume") > 0) & (pl.col("close") > 0)
                             & (pl.col("amount") > 0))
            if m.height:
                r = float((m["amount"] / (m["volume"] * m["close"])).median())
                if not (RATIO_LO <= r <= RATIO_HI):
                    unit_bad.append({"symbol": sym, "ratio_median": round(r, 4)})
        except Exception:  # noqa: BLE001
            pass

    return {"stale_tail": stale, "date_set_mismatch": mismatch, "unit_anomaly": unit_bad}


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--symbols", default="", help="逗号分隔；缺省=自动探测三类异常")
    ap.add_argument("--dry-run", action="store_true", help="只探测不抓取")
    args = ap.parse_args()

    sys.path.insert(0, str(PROJECT_ROOT / "backend"))
    uni = json.loads(UNIVERSE.read_text(encoding="utf-8"))
    all_syms = [x["symbol"] for x in uni["symbols"]]

    if args.symbols:
        targets = [s.strip() for s in args.symbols.split(",") if s.strip()]
        found = {"manual": [{"symbol": s} for s in targets]}
    else:
        print(f"探测 {len(all_syms)} 只标的的三类异常 …", flush=True)
        found = detect(all_syms)

    for k, v in found.items():
        print(f"  {k} = {len(v)}")
        for d in v[:8]:
            print(f"     {d}")

    targets = sorted({d["symbol"] for v in found.values() for d in v})
    if not targets:
        print("无需补抓")
        return 0
    if args.dry_run:
        print(f"\n[dry-run] 待补抓 {len(targets)} 只：{targets[:20]}")
        return 0

    from app.data.ingest import akshare_adapter as ada
    from app.data.ingest.tasks import fetch_and_write_daily_bars

    print(f"\n开始补抓 {len(targets)} 只（{START} ~ {END}，adjusts=('', 'hfq')）…", flush=True)
    ok, failed = [], {}

    def _one(sym: str) -> tuple[int, list[str]]:
        return fetch_and_write_daily_bars(sym.split(".")[0], START.isoformat(),
                                          END.isoformat(), adjusts=("", "hfq"),
                                          fetcher=ada._fetch_daily_bar_sina)

    with ThreadPoolExecutor(max_workers=WORKERS, thread_name_prefix="repair") as ex:
        futs = {ex.submit(_one, s): s for s in targets}
        for i, fut in enumerate(as_completed(futs), 1):
            sym = futs[fut]
            try:
                total, bad_adj = fut.result()
            except BaseException as e:  # noqa: BLE001 - 含 SystemExit
                failed[sym] = f"{type(e).__name__}: {e}"[:120]
                print(f"[{i}/{len(targets)}] {sym} FAILED {failed[sym]}", flush=True)
            else:
                if bad_adj:
                    failed[sym] = f"adjust_failed={bad_adj}"
                    print(f"[{i}/{len(targets)}] {sym} 口径失败 {bad_adj}", flush=True)
                else:
                    ok.append(sym)
                    print(f"[{i}/{len(targets)}] {sym} {total} 行", flush=True)

    print(f"\n补抓完成：成功 {len(ok)} / 失败 {len(failed)}")
    for s, r in list(failed.items())[:20]:
        print(f"  {s}: {r}")
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
