"""判决性实验：`psi.max` 的抬升来自「扩容（股票池变宽）」还是「真实 regime 漂移」？

背景（2026-10-03）
------------------
`tests/test_monitor_trigger_caliber.py` 的两条真实面板测试自扩容后持续失败
（`psi["max"]=0.5149 > PSI_DEGRADED=0.25`）。该文件 docstring 记录它 2026-09-22 标定时
基线为 **0.2024** —— 现在是 2.5 倍。

两种互斥解释：
  (a) **扩容副产物**：股票池 2500 → 5220，新增的小盘股改变了 `rank_close_60` 的
      **截面形状** ⇒ 指标口径问题，应重标定，不该当成市场信号；
  (b) **真实 regime 差异**：与池宽无关，近 20 日确实处于普跌 regime
      （滚动分位分布整体下移/右偏）⇒ 监控报 degraded 是**正确的**。

判据（**同一套代码、同一时间窗，只换股票池**）：
  在同一份特征面板上分别对
      FULL    = 全部标的
      OLD2500 = 与 `data/universe_2500.json`（扩容前清单）的交集
      NEW     = 面板中不在扩容前清单里的标的
  重算 PSI。
    * OLD2500 的 max 掉回 ~0.20 ⇒ 解释 (a) 成立；
    * OLD2500 的 max 仍 ~0.51 ⇒ 解释 (b) 成立。

⚠️ 口径说明：本脚本**只读**，不改任何数据、不改阈值、不改测试。
⚠️ `_load_real_panel` 与测试同口径（最新 version 目录 + 末 400×1.6 自然日窗口），
   否则与测试数字不可比。
"""
from __future__ import annotations

import json
import sys
from datetime import timedelta
from pathlib import Path

import polars as pl

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "backend"))

from app.ml import monitor as M  # noqa: E402


def load_panel() -> tuple[pl.DataFrame, str]:
    """与 `test_monitor_trigger_caliber._load_real_panel` **同口径**读真实面板。"""
    real = ROOT / "data" / "parquet" / "features"
    dirs = [d for d in real.iterdir() if d.is_dir() and d.name.startswith("version=")]
    version_dir = max(dirs, key=lambda d: max(f.stat().st_mtime_ns
                                              for f in d.rglob("*.parquet")))
    files = sorted(version_dir.rglob("*.parquet"))
    df = pl.concat([pl.read_parquet(f) for f in files], how="diagonal_relaxed")
    df = M._normalize_date_col(df)
    dmax = df["date"].max()
    return df.filter(pl.col("date") >= dmax - timedelta(days=int(400 * 1.6))), version_dir.name


def _report(name: str, sub: pl.DataFrame) -> dict:
    if sub.height == 0:
        print(f"{name}: EMPTY")
        return {}
    psi = M.compute_psi(sub)
    if not psi.get("ok"):
        print(f"{name}: PSI not ok -> {psi}")
        return {}
    deg = psi.get("degenerate") or {}
    print(f"{name}: rows={sub.height} syms={sub['symbol'].n_unique()} "
          f"dates={sub['date'].n_unique()} n_factors={psi['n_factors']}")
    print(f"    mean={psi['mean']}  max={psi['max']}  "
          f"drift_state={M._drift_state(psi['max'])}  raw.max={psi['raw']['max']}")
    print(f"    top5={[(t['factor'], t['psi']) for t in psi['top']]}")
    print(f"    degenerate: n_mad_zero={deg.get('n_mad_zero')} "
          f"n_std_fallback={deg.get('n_std_fallback')} n_constant={deg.get('n_constant')} "
          f"factors={deg.get('factors')}")
    return {"name": name, "rows": sub.height, "syms": sub["symbol"].n_unique(),
            "mean": psi["mean"], "max": psi["max"], "raw_max": psi["raw"]["max"],
            "top": psi["top"], "degenerate": deg}


def main() -> int:
    panel, ver = load_panel()
    print(f"=== 面板 version={ver} rows={panel.height} "
          f"dates={panel['date'].n_unique()} ===")

    panel_syms = set(panel["symbol"].unique().to_list())
    uni = json.loads((ROOT / "data" / "universe_2500.json").read_text(encoding="utf-8"))
    old_syms = {s["symbol"] for s in uni["symbols"]}
    inter = panel_syms & old_syms
    print(f"面板标的={len(panel_syms)}  扩容前清单={len(old_syms)}  交集={len(inter)}  "
          f"仅新增={len(panel_syms - old_syms)}")
    if not inter:
        print("⚠️ 交集为空 ⇒ 清单与面板 symbol 口径不匹配，实验无效（请先核对 symbol 格式）")
        return 2

    out = []
    out.append(_report("FULL   ", panel))
    out.append(_report("OLD2500", panel.filter(pl.col("symbol").is_in(sorted(inter)))))
    out.append(_report("NEWONLY", panel.filter(~pl.col("symbol").is_in(sorted(old_syms)))))

    full = next((r for r in out if r.get("name") == "FULL   "), {})
    old = next((r for r in out if r.get("name") == "OLD2500"), {})
    print("\n=== 判定 ===")
    if full and old:
        drop = round(full["max"] - old["max"], 4)
        print(f"FULL.max={full['max']}  OLD2500.max={old['max']}  差={drop}")
        if old["max"] <= M.PSI_DEGRADED:
            print("⇒ 解释 (a)：限定扩容前股票池后不再超阈值 ⇒ **抬升由扩容引起**，属口径问题")
        elif old["max"] > M.PSI_DEGRADED:
            print("⇒ 解释 (b)：限定扩容前股票池后**仍**超阈值 ⇒ **真实 regime 差异**，"
                  "监控报 degraded 是正确的")
    dest = ROOT / "data" / "diag_psi_universe.json"
    dest.write_text(json.dumps(out, ensure_ascii=False, indent=1), encoding="utf-8")
    print(f"结果已写入 {dest}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
