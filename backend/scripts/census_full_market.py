"""全市场标的覆盖普查（磁盘 ground truth，不依赖 manifest）。

对 universe_full.json 中的每个 symbol，直接检查
``daily_bar/symbol=X/year=*.parquet`` 与 ``daily_bar_hfq/...`` 的存在性与
最早年份，输出分类计数。这是"还差多少"的唯一权威判据 ——
``list_symbols_with_data`` 走 manifest，实测会滞后（2499→2501→2503），
不得用作范围判定依据。
"""
from __future__ import annotations

import json
import sys
from collections import Counter
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[2]  # scripts -> backend -> 项目根
DATA_ROOT = PROJECT_ROOT / "data" / "parquet"
UNIVERSE = PROJECT_ROOT / "data" / "universe_full.json"

# 路径写错会让普查静默给出 0（"无分区"看起来就像"待抓"），必须先自检
for _p in (DATA_ROOT, UNIVERSE):
    if not _p.exists():
        raise SystemExit(f"路径不存在，请修正 PROJECT_ROOT 推导：{_p}")


def _years(ds: str, sym: str) -> list[int]:
    d = DATA_ROOT / ds / f"symbol={sym}"
    if not d.is_dir():
        return []
    out: list[int] = []
    for f in d.glob("year=*.parquet"):
        try:
            out.append(int(f.name[5:9]))
        except ValueError:
            pass
    return sorted(out)


def main() -> int:
    uni = json.loads(UNIVERSE.read_text(encoding="utf-8"))
    syms = [x["symbol"] for x in uni["symbols"]]
    print(f"清单标的数 = {len(syms)}")

    # 磁盘上实际存在的 symbol 目录（权威，不走 manifest）
    disk_raw = {p.name.split("=", 1)[1] for p in (DATA_ROOT / "daily_bar").glob("symbol=*") if p.is_dir()}
    disk_hfq = {p.name.split("=", 1)[1] for p in (DATA_ROOT / "daily_bar_hfq").glob("symbol=*") if p.is_dir()}
    print(f"磁盘 daily_bar 目录数 = {len(disk_raw)}  daily_bar_hfq 目录数 = {len(disk_hfq)}")
    print(f"清单内已有 daily_bar 分区 = {len(set(syms) & disk_raw)}")
    print(f"清单内已有 hfq 分区       = {len(set(syms) & disk_hfq)}")

    cls: Counter[str] = Counter()
    min_year: Counter[int] = Counter()
    no_raw: list[str] = []
    for s in syms:
        yr = _years("daily_bar", s)
        yh = _years("daily_bar_hfq", s)
        if not yr and not yh:
            cls["两侧均无"] += 1
            no_raw.append(s)
        elif not yr:
            cls["仅 hfq"] += 1
        elif not yh:
            cls["仅 raw"] += 1
        else:
            cls["两侧均有"] += 1
            min_year[min(yr[0], yh[0])] += 1
    print("分类 =", dict(cls))
    print("两侧均有者的最早年份分布 =", dict(sorted(min_year.items())))
    print(f"两侧均无（= 待全历史抓取）样例 = {no_raw[:6]}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
