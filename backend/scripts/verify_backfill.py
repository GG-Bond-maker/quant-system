"""增量补齐**验收**脚本（task #3）。

用法（必须先 ``cd backend``）::

    ./.venv/Scripts/python.exe scripts/verify_backfill.py
    ./.venv/Scripts/python.exe scripts/verify_backfill.py --target 2026-09-30 --sample 40

五项判据 —— 每一项都对应本项目**真实踩过**的坑，不是通用体检项：

1. **覆盖度**：raw / hfq 两侧尾部日期分布 + 达标率。
   只看一侧会把「raw 到位、hfq 落后」误判为完成（``build_plan`` 正是为此取两侧较早者）。
2. **无重复日期**：抽样的 ``date`` 必须唯一。
   ``write_partition`` 靠 ``unique(subset=["date"], keep="last")`` 去重，一旦该步骤被
   绕过（旁路写入 / 手工拼帧），增量就会在库里堆出重复 K 线 —— 前端按 date 取值会
   随机命中旧值。
3. **单位恒等式**：``amount / (volume × close) ≈ 1``。
   🔴 这是判定 volume 单位（股 vs 手）的**唯一可靠手段**，不能读注释：
   本项目曾有 3 处注释互相印证同一个错误前提「daily_bar=手」。
   若该比值 ≈0.01 说明 volume 其实是「手」；≈1 才是「股」。
4. **manifest 一致性**：manifest 登记的 ``rows`` 与磁盘实际行数一致。
   manifest 只增量登记，旁路写入不登记 ⇒ 二者漂移会让 ``/datasets`` 与 ``/overview``
   给出与磁盘事实不符的区间。
5. **源端抽查**：对抽样标的重新拉一次源数据，比对目标日 OHLC 是否与库内一致。
   前四项都是"自洽性"检查，**自洽不能证明正确**（合成错误同样自洽）⇒ 必须有
   一项与**外部事实**对齐。默认关闭（会外呼），加 ``--source-check`` 开启。
"""
from __future__ import annotations

import argparse
import json
import statistics
import sys
from collections import Counter
from datetime import date
from pathlib import Path

BACKEND_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(BACKEND_ROOT))

import polars as pl  # noqa: E402

from app.core.config import get_settings  # noqa: E402
from app.data.calendar_store import get_calendar  # noqa: E402
from app.data.parquet_store import read_all_symbols  # noqa: E402
from app.domain.calendar import last_completed_trade_day  # noqa: E402
# 复用生产/补齐共用的同一判据（防"谁该抓"分叉）
from app.services.sync_service import _symbol_last_date_in  # noqa: E402

_OK = "✅"
_BAD = "❌"
_WARN = "⚠️"


def _partition(dataset: str, sym: str, year: int) -> Path:
    return get_settings().DATA_ROOT / dataset / f"symbol={sym}" / f"year={year}.snappy.parquet"


def check_coverage(symbols: list[str], target: date) -> dict:
    """判据 1：两侧尾部日期分布与达标率。"""
    raw_hist: Counter[str] = Counter()
    hfq_hist: Counter[str] = Counter()
    raw_ok = hfq_ok = both_ok = 0
    lagging: list[tuple[str, str, str]] = []
    for sym in symbols:
        r = _symbol_last_date_in("daily_bar", sym, target.year)
        h = _symbol_last_date_in("daily_bar_hfq", sym, target.year)
        raw_hist[str(r)] += 1
        hfq_hist[str(h)] += 1
        ro, ho = (r is not None and r >= target), (h is not None and h >= target)
        raw_ok += ro
        hfq_ok += ho
        both_ok += (ro and ho)
        if not (ro and ho):
            lagging.append((sym, str(r), str(h)))
    n = len(symbols) or 1
    return {
        "n": len(symbols), "raw_ok": raw_ok, "hfq_ok": hfq_ok, "both_ok": both_ok,
        "raw_hist": raw_hist.most_common(8), "hfq_hist": hfq_hist.most_common(8),
        "rate": both_ok / n, "lagging": lagging,
    }


def check_duplicates(sample: list[str], target: date) -> list[tuple[str, int]]:
    """判据 2：抽样标的的 date 必须唯一。返回 (symbol, 重复行数)。"""
    bad: list[tuple[str, int]] = []
    for sym in sample:
        p = _partition("daily_bar", sym, target.year)
        if not p.exists():
            continue
        df = pl.read_parquet(p, columns=["date"])
        n_dup = df.height - df["date"].n_unique()
        if n_dup:
            bad.append((sym, n_dup))
    return bad


def check_unit_identity(sample: list[str], target: date, tail: int = 5) -> dict:
    """判据 3：``amount/(volume*close)`` 的中位数必须 ≈ 1（volume 单位 = 股）。"""
    ratios: list[float] = []
    skipped: list[str] = []
    for sym in sample:
        p = _partition("daily_bar", sym, target.year)
        if not p.exists():
            skipped.append(f"{sym}:无分区")
            continue
        df = pl.read_parquet(p, columns=["date", "close", "volume", "amount"]).sort("date")
        df = df.tail(tail)
        for row in df.iter_rows(named=True):
            c, v, a = row["close"], row["volume"], row["amount"]
            if not c or not v or a is None:
                continue
            ratios.append(a / (v * c))
    if not ratios:
        return {"n": 0, "median": None, "min": None, "max": None, "skipped": skipped}
    return {
        "n": len(ratios),
        "median": statistics.median(ratios),
        "min": min(ratios), "max": max(ratios),
        "skipped": skipped,
    }


def check_manifest(symbols: list[str]) -> dict:
    """判据 4：manifest 登记 rows 与磁盘实际行数是否一致（抽样比对）。"""
    mp = get_settings().DATA_ROOT / ".manifest.json"
    if not mp.exists():
        return {"available": False}
    mf = json.loads(mp.read_text(encoding="utf-8"))
    entry = mf.get("daily_bar", {})
    # manifest 结构兼容：{symbol: {rows, first, last}} 或 {"symbols": {...}}
    rows_map = entry.get("symbols", entry) if isinstance(entry, dict) else {}
    mismatch: list[tuple[str, int, int]] = []
    checked = 0
    for sym in symbols[:80]:
        rec = rows_map.get(sym)
        if not isinstance(rec, dict) or "rows" not in rec:
            continue
        total = 0
        for y in range(2015, 2030):
            p = _partition("daily_bar", sym, y)
            if p.exists():
                total += pl.read_parquet(p, columns=["date"]).height
        checked += 1
        if total != rec["rows"]:
            mismatch.append((sym, rec["rows"], total))
    return {"available": True, "checked": checked, "mismatch": mismatch}


def check_source(sample: list[str], target: date) -> dict:
    """判据 5：抽样重新外呼源端，比对目标日收盘价（**唯一的外部事实对齐**）。"""
    from app.data.ingest.akshare_adapter import fetch_daily_bar
    from app.data.ingest.akshare_adapter import daily_source_priority

    ds = daily_source_priority()
    agree, disagree, failed = 0, [], 0
    for sym in sample:
        code = sym.split(".")[0]
        p = _partition("daily_bar", sym, target.year)
        if not p.exists():
            failed += 1
            continue
        local = pl.read_parquet(p, columns=["date", "close"])
        local = local.filter(pl.col("date").cast(pl.Date) == target)
        if local.height == 0:
            failed += 1
            continue
        try:
            rows = fetch_daily_bar(code, target.isoformat(), target.isoformat(), ds)
        except Exception:  # noqa: BLE001 源端失败计入 failed，不算"不一致"
            failed += 1
            continue
        if not rows:
            failed += 1
            continue
        remote_close = rows[-1].get("close")
        local_close = local["close"][0]
        if remote_close is None or local_close is None:
            failed += 1
        elif abs(remote_close - local_close) / max(abs(local_close), 1e-9) < 1e-4:
            agree += 1
        else:
            disagree.append((sym, local_close, remote_close))
    return {"agree": agree, "disagree": disagree, "failed": failed}


def main() -> int:
    ap = argparse.ArgumentParser(description="增量补齐验收")
    ap.add_argument("--target", default=None, help="目标交易日（默认取最近已完成交易日）")
    ap.add_argument("--sample", type=int, default=30, help="抽样标的数（恒等式/重复/源端）")
    ap.add_argument("--source-check", action="store_true",
                    help="额外做源端抽查（会外呼，默认关闭）")
    args = ap.parse_args()

    cal = get_calendar()
    target = (date.fromisoformat(args.target) if args.target
              else last_completed_trade_day(cal))
    symbols = read_all_symbols("daily_bar")
    step = max(1, len(symbols) // max(1, args.sample))
    sample = symbols[::step][: args.sample]

    print("=" * 72)
    print(f"增量补齐验收（target = {target}）")
    print(f"标的池 = {len(symbols)}  抽样 = {len(sample)}")
    print("=" * 72)

    cov = check_coverage(symbols, target)
    print("\n【判据 1】覆盖度")
    print(f"  raw  达标 {cov['raw_ok']}/{cov['n']}   hfq 达标 {cov['hfq_ok']}/{cov['n']}"
          f"   两侧均达标 {cov['both_ok']}/{cov['n']} = {cov['rate']:.2%}")
    print(f"  raw 尾部日期分布 : {cov['raw_hist']}")
    print(f"  hfq 尾部日期分布 : {cov['hfq_hist']}")
    if cov["lagging"]:
        print(f"  {_WARN} 未达标 {len(cov['lagging'])} 只（前 10）：")
        for sym, r, h in cov["lagging"][:10]:
            print(f"      {sym}  raw={r}  hfq={h}")
    else:
        print(f"  {_OK} 全部标的双侧均达目标交易日")

    dups = check_duplicates(sample, target)
    print(f"\n【判据 2】重复日期（抽样 {len(sample)} 只）")
    if dups:
        print(f"  {_BAD} 发现重复：{dups}")
    else:
        print(f"  {_OK} 无重复 date")

    unit = check_unit_identity(sample, target)
    print(f"\n【判据 3】单位恒等式 amount/(volume×close)（抽样 {len(sample)} 只 × 近 5 日）")
    if unit["median"] is None:
        print(f"  {_BAD} 无有效观测")
    else:
        med = unit["median"]
        verdict = _OK if 0.9 <= med <= 1.1 else _BAD
        print(f"  {verdict} 中位数={med:.6f}  区间=[{unit['min']:.6f}, {unit['max']:.6f}]  "
              f"观测={unit['n']}")
        print("      判据：≈1 ⇒ volume 单位=股；≈0.01 ⇒ 手")
    if unit["skipped"]:
        print(f"  {_WARN} 跳过 {len(unit['skipped'])} 只：{unit['skipped'][:5]}")

    man = check_manifest(symbols)
    print("\n【判据 4】manifest 一致性")
    if not man.get("available"):
        print(f"  {_WARN} manifest 不存在（跳过）")
    elif not man.get("checked"):
        print(f"  {_WARN} manifest 无 daily_bar 明细（跳过）")
    elif man["mismatch"]:
        print(f"  {_BAD} {len(man['mismatch'])}/{man['checked']} 只行数不一致（前 5）："
              f"{man['mismatch'][:5]}")
    else:
        print(f"  {_OK} {man['checked']} 只行数与磁盘一致")

    if args.source_check:
        src = check_source(sample[:8], target)
        print(f"\n【判据 5】源端抽查（{len(sample[:8])} 只）")
        print(f"  一致={src['agree']}  不一致={len(src['disagree'])}  取不到={src['failed']}")
        for sym, lc, rc in src["disagree"]:
            print(f"      {_BAD} {sym} 库内={lc} 源端={rc}")

    print("\n" + "=" * 72)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
