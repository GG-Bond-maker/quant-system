"""重标定可行性实验：把 `PSI_DEGRADED` 上调，会不会同时打坏「注入灵敏度」？

背景（2026-10-03）
------------------
`tests/test_monitor_trigger_caliber.py::test_real_panel_injection_sensitivity_table`
用**注入异质样本**来证明指标仍有判别力，断言形态是：

    for ratio, expect in ((0.3, True), (0.5, True)):
        assert (psi["max"] > M.PSI_DEGRADED) is expect

其 docstring 记录的实测表（**2026-09-22**，当时基线 `psi.max = 0.2024`）：
    30% 注入 → 0.3540 ✅超阈值    50% 注入 → 0.4166 ✅超阈值
    70% 注入 → 0.2271 ❌（非单调）  g1_ 变体 → 0.2024 ❌（该因子无响应）

⇒ 若为了让两条 regime 测试转绿而把 `PSI_DEGRADED` 从 0.25 上调到 ~0.60，
**必须确认 30%/50% 注入的值仍高于新阈值**，否则等于「按下假降级，浮起漏报」——
把监控变成只会沉默的空转。本脚本就是测这个。

⚠️ 只读；不改阈值、不改测试。注入逻辑**逐字复用**测试里的 `_inject_real` 口径。
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


def load_panel() -> pl.DataFrame:
    real = ROOT / "data" / "parquet" / "features"
    dirs = [d for d in real.iterdir() if d.is_dir() and d.name.startswith("version=")]
    version_dir = max(dirs, key=lambda d: max(f.stat().st_mtime_ns
                                              for f in d.rglob("*.parquet")))
    files = sorted(version_dir.rglob("*.parquet"))
    df = pl.concat([pl.read_parquet(f) for f in files], how="diagonal_relaxed")
    df = M._normalize_date_col(df)
    dmax = df["date"].max()
    return df.filter(pl.col("date") >= dmax - timedelta(days=int(400 * 1.6)))


def inject_real(feat: pl.DataFrame, targets: list[str], other: str,
                ratio: float) -> pl.DataFrame:
    """与 `test_monitor_trigger_caliber._inject_real` **同口径**（逐字复用其逻辑）。"""
    recent = feat["date"].unique().sort()[-20:]
    tmp = feat.with_columns(
        is_rec=pl.col("date").is_in(recent.to_list())).with_columns(
        cnt=pl.col("is_rec").cum_sum())
    n_mix = int(tmp.filter(pl.col("is_rec")).height * ratio)
    return tmp.with_columns(
        [pl.when(pl.col("is_rec") & (pl.col("cnt") <= n_mix))
         .then(pl.col(other)).otherwise(pl.col(t)).alias(t) for t in targets]
    ).drop(["is_rec", "cnt"])


def main() -> int:
    feat = load_panel()
    base = M.compute_psi(feat)
    print(f"=== 基线：mean={base['mean']} max={base['max']} "
          f"raw.max={base['raw']['max']} n_factors={base['n_factors']} ===")

    cases = [
        (["ma_gap_250"], "ret_1", 0.30),
        (["ma_gap_250"], "ret_1", 0.50),
        (["ma_gap_250"], "ret_1", 0.70),
        (["g1_ma_gap_250"], "ret_1", 0.50),
    ]
    rows = []
    for targets, other, ratio in cases:
        psi = M.compute_psi(inject_real(feat, targets, other, ratio))
        rows.append({"target": targets[0], "ratio": ratio,
                     "max": psi["max"], "top1": psi["top"][0]["factor"],
                     "top1_psi": psi["top"][0]["psi"]})
        print(f"注入 {targets[0]:15s} ← {other:6s} @{ratio:.0%}: "
              f"psi.max={psi['max']}  top1={psi['top'][0]['factor']} "
              f"({psi['top'][0]['psi']})")

    print("\n=== 阈值上调的后果（判据：30%/50% 注入必须仍 > 阈值）===")
    for thr in (0.25, 0.35, 0.45, 0.55, 0.60, 0.65):
        ok30 = rows[0]["max"] > thr
        ok50 = rows[1]["max"] > thr
        verdict = "✅ 灵敏度保住" if (ok30 and ok50) else (
            "❌ **灵敏度丢失**（注入不再触发降级 ⇒ 监控变空转）")
        print(f"PSI_DEGRADED={thr:.2f}: 30%@{rows[0]['max']:.4f}->{ok30}  "
              f"50%@{rows[1]['max']:.4f}->{ok50}  {verdict}")

    dest = ROOT / "data" / "diag_psi_recalib.json"
    dest.write_text(json.dumps({"baseline": {"mean": base["mean"], "max": base["max"],
                                             "raw_max": base["raw"]["max"]},
                                "injections": rows}, ensure_ascii=False, indent=1),
                    encoding="utf-8")
    print(f"\n结果已写入 {dest}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
