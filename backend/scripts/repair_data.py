"""数据修复 CLI（CRIT-002）。

流程：scan -> 判定 -> 隔离/重建 -> schema 归一 -> 验收

    python scripts/repair_data.py                 # 干跑（只报告，不改动）
    python scripts/repair_data.py --apply         # 实际执行
    python scripts/repair_data.py --refetch       # 重新获取（需要网络）

判定规则（写死在这里，便于审计）：
  R1 分区含"不在交易日历"的日期            -> 合成数据，不可修复 -> 整只隔离
  R2 单年行数 > max_year_rows                -> 合成数据          -> 整只隔离
  R3 复权因子突变且复权收益异常（raw 误写入 hfq）
                                            -> 尝试按锚点因子重建 -> 复检 -> 失败则隔离
"""
from __future__ import annotations

import argparse
import json
import sys
from datetime import date, datetime
from pathlib import Path

BACKEND_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(BACKEND_ROOT))

import polars as pl  # noqa: E402

from app.core.config import PROJECT_ROOT, get_settings  # noqa: E402
from app.core.logging import setup_logging  # noqa: E402
from app.data.parquet_store import manifest_invalidate  # noqa: E402
from app.data.quality import (  # noqa: E402
    DEFAULT_THRESHOLDS, check_adjusted_continuity, load_trade_days,
    read_symbol_all, scan_dataset,
)
from app.data.repair import (  # noqa: E402
    QUARANTINE_ROOT, RepairStats, anchor_factor_at, normalize_dataset,
    quarantined_symbols, quarantine_partition, quarantine_symbol,
    rebuild_adj_year_from_factor, verify_dataset_aligned, verify_scannable,
)

DATASETS = ("daily_bar", "daily_bar_hfq", "daily_bar_qfq")
RAW_DS, HFQ_DS = "daily_bar", "daily_bar_hfq"


def _collect_bad_symbols(s: object, trade_days: object) -> dict[str, list[dict]]:
    """扫描 raw / hfq，返回 {symbol: [issue_dict]}（仅 error）。"""
    bad: dict[str, list[dict]] = {}
    for ds in (RAW_DS, HFQ_DS):
        rep = scan_dataset(s.DATA_ROOT, ds, DEFAULT_THRESHOLDS, trade_days)
        for i in rep.errors:
            bad.setdefault(i.symbol, []).append(i.as_row())
    return bad


def _is_synthetic(issues: list[dict]) -> bool:
    return any(i["kind"] in ("calendar", "year_density") for i in issues)


def main() -> None:
    ap = argparse.ArgumentParser(prog="repair_data.py")
    ap.add_argument("--apply", action="store_true", help="实际执行（默认干跑）")
    ap.add_argument("--refetch", action="store_true",
                    help="尝试从数据源重新获取（需要网络）")
    ap.add_argument("--report", default=str(PROJECT_ROOT / "data" / "repair_report.json"))
    args = ap.parse_args()

    setup_logging()
    s = get_settings()
    trade_days = load_trade_days(s.SQLITE_PATH)
    stats = RepairStats()
    actions: list[dict] = []

    print("=" * 76)
    print(f"数据修复  DATA_ROOT={s.DATA_ROOT}")
    print(f"模式：{'APPLY（实际执行）' if args.apply else 'DRY-RUN（只报告）'}")
    print("=" * 76)

    bad = _collect_bad_symbols(s, trade_days)
    print(f"\n检出有 error 的标的：{len(bad)} 个")
    for sym, issues in sorted(bad.items()):
        kinds = sorted({i["kind"] for i in issues})
        print(f"  {sym}: {kinds}")

    # ---------- 判定 + 执行 ----------
    for sym, issues in sorted(bad.items()):
        if _is_synthetic(issues):
            reason = ("含合成数据（"
                      + "; ".join(f"{i['kind']}: {i['detail'][:60]}"
                                  for i in issues if i["kind"] in ("calendar", "year_density"))
                      + "）")
            print(f"\n[R1/R2 合成数据] {sym} -> 整只隔离")
            print(f"   原因：{reason}")
            actions.append({"symbol": sym, "action": "quarantine_symbol",
                            "reason": reason})
            if args.apply:
                quarantine_symbol(s.DATA_ROOT, sym, reason, stats=stats)
            continue

        # R3：raw 误写入 hfq -> 尝试重建
        jump_years = sorted({
            str(i["sample_dates"].split(",")[0])[:4] for i in issues
            if i["kind"] == "factor_jump" and i["sample_dates"]
        })
        print(f"\n[R3 复权因子损坏] {sym} 受影响年份={jump_years} -> 尝试锚点重建")
        for y in jump_years:
            year = int(y)
            anchor = anchor_factor_at(s.DATA_ROOT, sym, date(year - 1, 12, 31))
            if anchor is None:
                r = f"{year} 年无可用锚点因子（无上一年数据）"
                print(f"   {year}: 隔离（{r}）")
                actions.append({"symbol": sym, "action": "quarantine_year",
                                "dataset": HFQ_DS, "year": year, "reason": r})
                if args.apply:
                    quarantine_partition(s.DATA_ROOT, HFQ_DS, sym, year, r, stats)
                continue
            factor, adate = anchor
            print(f"   {year}: 锚点 {adate} factor={factor:.6f}")
            if not args.apply:
                actions.append({"symbol": sym, "action": "rebuild", "year": year,
                                "factor": factor, "anchor_date": str(adate)})
                continue
            n = rebuild_adj_year_from_factor(s.DATA_ROOT, HFQ_DS, sym, year,
                                             factor, adate)
            stats.rebuilt_files += 1
            stats.rebuilt_rows += n
            # 复检：重建后若仍有制度外跳变 -> 期间发生公司行为 -> 隔离
            adj = read_symbol_all(s.DATA_ROOT, HFQ_DS, sym)
            check = check_adjusted_continuity(
                adj.filter(pl.col("date").dt.year() == year), HFQ_DS, sym,
                DEFAULT_THRESHOLDS)
            if check:
                r = (f"{year} 年重建后仍检测到复权跳变："
                     + check[0].detail[:90])
                print(f"   {year}: 重建失败 -> 隔离（{r}）")
                quarantine_partition(s.DATA_ROOT, HFQ_DS, sym, year, r, stats)
                actions.append({"symbol": sym, "action": "quarantine_after_rebuild",
                                "year": year, "reason": r})
            else:
                print(f"   {year}: 重建成功 rows={n}，复检通过")
                actions.append({"symbol": sym, "action": "rebuilt", "year": year,
                                "rows": n, "factor": factor,
                                "anchor_date": str(adate)})

    # ---------- schema 归一 ----------
    print("\n" + "=" * 76)
    print("schema 归一")
    print("=" * 76)
    for ds in DATASETS:
        n = normalize_dataset(s.DATA_ROOT, ds, stats) if args.apply else 0
        ok, bad_list = verify_dataset_aligned(s.DATA_ROOT, ds)
        print(f"  {ds}: 归一 {n} 个文件；列顺序一致 = {ok}"
              + ("" if ok else f"  不一致 {len(bad_list)} 个"))
        for b in bad_list[:5]:
            print(f"      {b}")

    # ---------- 验收 ----------
    print("\n" + "=" * 76)
    print("验收：scan_parquet 可读性")
    print("=" * 76)
    verify: dict[str, dict] = {}
    for ds in DATASETS:
        ok, msg = verify_scannable(s.DATA_ROOT, ds)
        col_ok, bad_list = verify_dataset_aligned(s.DATA_ROOT, ds)
        verify[ds] = {"scannable": ok, "detail": msg, "schema_aligned": col_ok,
                      "misaligned": bad_list[:5]}
        print(f"  {ds}: scan={ok} ({msg})  schema_aligned={col_ok}")

    # 缺陷 C：本 CLI 通过 quarantine/rebuild/normalize 旁路写 DATA_ROOT，
    # 会改动 symbol 集合与行数——显式失效 manifest，杜绝下次读取把有数据的
    # 标的静默排除（本次 apply 的各函数虽已各自失效，此处再做一次收尾兜底）。
    if args.apply:
        manifest_invalidate()

    report = {
        "generated_at": datetime.now().isoformat(timespec="seconds"),
        "mode": "apply" if args.apply else "dry-run",
        "data_root": str(s.DATA_ROOT),
        "actions": actions,
        "stats": stats.as_dict(),
        "verify": verify,
        "quarantined_symbols": quarantined_symbols(QUARANTINE_ROOT),
    }
    Path(args.report).write_text(json.dumps(report, ensure_ascii=False, indent=2),
                                 encoding="utf-8")
    print(f"\n报告：{args.report}")
    print(f"隔离清单：{report['quarantined_symbols']}")
    if not args.apply:
        print("\n（干跑模式，未修改任何数据。加 --apply 执行）")


if __name__ == "__main__":
    main()
