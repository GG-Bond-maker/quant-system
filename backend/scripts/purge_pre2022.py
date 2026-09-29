"""把 DATA_ROOT 下 ``--before`` 年之前的行情/派生分区**移入隔离目录**（可回滚）。

背景（2026-09-21，P1-42 延伸决定）
--------------------------------
本项目 A 股行情数据虽在少数老标的上存在 2018–2021 年分区，但**覆盖面极不均**：
抽样 40 只 ``daily_bar_hfq`` 的最早日期中位数为 **2022-08-05**（仅个别老标的有
2018+ 分区）。这类"少数标的的早年数据"会带来两个真实危害：

1. **回测宇宙被污染**：``build_universe_history`` / ``build_universe_backtest``
   的骨架是「行情里出现过的全部交易日 × instrument 全表」的笛卡尔积，而
   ``instrument.list_date`` 有 97.8% 为 NULL（缺陷 B5-14）⇒ 上市判据
   ``list_date <= date`` 对绝大多数标的失效，早年每一天都会为**所有**标的生成
   占位行。实测 ``universe_daily`` 的 2018–2021 分区共 **2,313,074 行**（占其总量
   44.8%），其中绝大多数标的元数据为空、无任何行情。
2. **回测结果失真**：以 2018–2021 为窗口回测时，实际只有极少数标的可交易，
   却按"全市场"呈现，年化/夏普等指标不可比。

故决定：**有效历史从 2022 年起，不回填更早数据**，并清除既有的 2022 前分区。
夜间流水线**不会**把它们抓回来：``step_update_daily`` 只抓当日
（``data/pipeline.py``，``start=end=trade_date``），``rebuild_qfq`` 从 raw 重建，
raw 中 2022 前分区清掉后即无源可依。

⚠️ **不含** SQLite ``trade_calendar``：它是日历参考表而非行情，且跨年推断需要
2021-12-31 这类"边界前一日"。删掉会让 ``prev_trade_day(2022-01-04)`` 查不到，
``validate`` 退化为覆盖率判据、跨年校验失真。故本脚本只处理 parquet 数据集。

用法
----
    python scripts/purge_pre2022.py                  # 干跑（默认，只列清单）
    python scripts/purge_pre2022.py --apply          # 执行：移入隔离目录
    python scripts/purge_pre2022.py --restore        # 回滚：从隔离目录移回
    python scripts/purge_pre2022.py --before 2023    # 改分界年
    python scripts/purge_pre2022.py --dataset cs     # 只处理指定数据集（可重复）
"""
from __future__ import annotations

import argparse
import re
import shutil
import sys
from pathlib import Path

BACKEND_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(BACKEND_ROOT))

from app.core.config import PROJECT_ROOT, get_settings  # noqa: E402
from app.core.logging import setup_logging  # noqa: E402

# 隔离目录默认落在仓库 data/ 下（与 DATA_ROOT=data/parquet 平级，便于一并发现）
DEFAULT_QUARANTINE = PROJECT_ROOT / "data" / "_purged_pre2022"

# 判定"该文件属于哪一年"：路径段 year=YYYY / date=YYYYMMDD
# ⚠️ 不能用 `part.split("=")[1].isdigit()`：文件名本身形如
#    `year=2018.snappy.parquet`，会取到 "2018.snappy" 而误判为非年份。
_YEAR_RE = re.compile(r"^(?:year|date)=(\d{4})")


def year_of(path: Path) -> int | None:
    """取 path 中 ``year=`` / ``date=`` 段的 4 位年份；无分区标记返回 None。"""
    for part in path.parts:
        m = _YEAR_RE.match(part)
        if m:
            return int(m.group(1))
    return None


def rows_of(path: Path) -> int:
    """读 parquet footer 元数据取行数（不读数据体）。失败返回 0。"""
    try:
        import pyarrow.parquet as pq
        return int(pq.ParquetFile(path).metadata.num_rows)
    except Exception:  # noqa: BLE001
        return 0


def collect(data_root: Path, before: int,
            datasets: list[str] | None) -> dict[str, list[Path]]:
    """按数据集收集 ``before`` 年之前的 parquet 分区。"""
    out: dict[str, list[Path]] = {}
    for ds in sorted(p for p in data_root.iterdir() if p.is_dir()):
        if datasets and ds.name not in datasets:
            continue
        hits = [f for f in ds.rglob("*.parquet")
                if (y := year_of(f)) is not None and y < before]
        if hits:
            out[ds.name] = sorted(hits)
    return out


def prune_empty_dirs(root: Path) -> int:
    """自底向上删除空目录（只删空的，任何含文件/非空目录都保留）。"""
    removed = 0
    for d in sorted((p for p in root.rglob("*") if p.is_dir()),
                    key=lambda p: len(p.parts), reverse=True):
        try:
            if not any(d.iterdir()):
                d.rmdir()
                removed += 1
        except OSError:
            pass
    return removed


def report(hits: dict[str, list[Path]], verdict: str) -> int:
    """打印清单表；返回总行数。"""
    if not hits:
        print(f"\n没有需要处理的文件（{verdict}）。")
        return 0
    print(f"\n{'数据集':<26}{'文件数':>8}{'行数':>14}{'磁盘':>11}   {verdict}")
    print("-" * 78)
    total_rows = total_bytes = total_files = 0
    for name, files in hits.items():
        n_rows = sum(rows_of(f) for f in files)
        n_bytes = sum(f.stat().st_size for f in files)
        total_rows += n_rows
        total_bytes += n_bytes
        total_files += len(files)
        print(f"{name:<26}{len(files):>8}{n_rows:>14,}{n_bytes / 1e6:>10.1f}MB")
    print("-" * 78)
    print(f"{'合计':<26}{total_files:>8}{total_rows:>14,}{total_bytes / 1e6:>10.1f}MB")
    return total_rows


def do_move(hits: dict[str, list[Path]], data_root: Path,
            quarantine: Path) -> int:
    """移入隔离目录，保持相对路径；返回移动文件数。"""
    moved = 0
    for name, files in hits.items():
        for src in files:
            rel = src.relative_to(data_root)
            dst = quarantine / rel
            if dst.exists():
                print(f"  ⚠ 目标已存在，跳过：{rel}")
                continue
            dst.parent.mkdir(parents=True, exist_ok=True)
            shutil.move(str(src), str(dst))
            moved += 1
        print(f"  {name}: {len(files)} 个文件已移入隔离目录")
    return moved


def do_restore(quarantine: Path, data_root: Path,
               datasets: list[str] | None) -> int:
    """把隔离目录内容移回 DATA_ROOT，保持相对路径；返回移动文件数。"""
    if not quarantine.exists():
        print(f"隔离目录不存在：{quarantine}")
        return 0
    moved = 0
    for f in sorted(quarantine.rglob("*.parquet")):
        rel = f.relative_to(quarantine)
        if datasets and rel.parts[0] not in datasets:
            continue
        dst = data_root / rel
        if dst.exists():
            print(f"  ⚠ 目标已存在，跳过：{rel}")
            continue
        dst.parent.mkdir(parents=True, exist_ok=True)
        shutil.move(str(f), str(dst))
        moved += 1
    return moved


def main(argv: list[str] | None = None) -> int:
    # setup_logging 走 loguru 的 enqueue=True（multiprocessing.SimpleQueue → 命名管道）。
    # 受限沙箱会在此处 WinError 5，而本脚本的清单输出全走 print，日志非必需 ⇒ 降级继续，
    # 不让"禁止命名管道"的环境把一次数据治理动作卡死。
    try:
        setup_logging()
    except OSError as e:  # noqa: BLE001
        print(f"（日志初始化被环境拒绝，继续执行：{e!r}）")
    ap = argparse.ArgumentParser(description="清除（隔离）分界年之前的 parquet 分区")
    ap.add_argument("--before", type=int, default=2022,
                    help="分界年：删除**早于**该年的分区（默认 2022）")
    ap.add_argument("--quarantine", type=Path, default=DEFAULT_QUARANTINE,
                    help=f"隔离目录（默认 {DEFAULT_QUARANTINE}）")
    ap.add_argument("--dataset", action="append", default=None,
                    help="只处理指定数据集（可重复；默认全部）")
    g = ap.add_mutually_exclusive_group()
    g.add_argument("--apply", action="store_true", help="执行移动（默认只干跑）")
    g.add_argument("--restore", action="store_true", help="从隔离目录移回")
    args = ap.parse_args(argv)

    data_root = Path(get_settings().DATA_ROOT)
    print(f"DATA_ROOT   = {data_root}")
    print(f"隔离目录    = {args.quarantine}")
    print(f"分界年      = {args.before}（清理早于该年的分区）")

    if args.restore:
        n = do_restore(args.quarantine, data_root, args.dataset)
        prune_empty_dirs(args.quarantine)
        print(f"\n已移回 {n} 个文件。")
        return 0

    hits = collect(data_root, args.before, args.dataset)
    report(hits, "移入隔离目录" if args.apply else "待处理（干跑，未改动）")
    if not args.apply:
        print("\n这是干跑。确认无误后加 --apply 执行：数据会**移入**隔离目录而非物理删除，"
              "\n可随时用 --restore 移回。确认无需保留后再手动删除隔离目录。")
        return 0

    print()
    moved = do_move(hits, data_root, args.quarantine)
    n_dirs = prune_empty_dirs(data_root)
    print(f"\n完成：移动 {moved} 个文件，清理空目录 {n_dirs} 个。")
    print(f"隔离副本位于 {args.quarantine}（可 --restore 回滚）")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())