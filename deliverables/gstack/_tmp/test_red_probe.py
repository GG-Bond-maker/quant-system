"""test-red 诊断探针：真实面板 PSI 基线/注入的 max 与 body 统计量。"""
from __future__ import annotations
import sys
from pathlib import Path

BACKEND_ROOT = Path(__file__).resolve().parents[3] / "backend"
sys.path.insert(0, str(BACKEND_ROOT))

import polars as pl  # noqa: E402
from app.ml import monitor as M  # noqa: E402
import numpy as np  # noqa: E402

PROJECT_ROOT = BACKEND_ROOT.parent
REAL_FEATURES = PROJECT_ROOT / "data" / "parquet" / "features"


def _load_real_panel() -> pl.DataFrame | None:
    if not REAL_FEATURES.exists():
        return None
    dirs = [d for d in REAL_FEATURES.iterdir() if d.is_dir() and d.name.startswith("version=")]
    if not dirs:
        return None
    version_dir = max(dirs, key=lambda d: max(f.stat().st_mtime_ns for f in d.rglob("*.parquet")))
    files = sorted(version_dir.rglob("*.parquet"))
    if not files:
        return None
    df = pl.concat([pl.read_parquet(f) for f in files], how="diagonal_relaxed")
    df = M._normalize_date_col(df)
    dmax = df["date"].max()
    from datetime import timedelta
    return df.filter(pl.col("date") >= dmax - timedelta(days=int(400 * 1.6)))


def _inject_real(feat, targets, other, ratio):
    recent = feat["date"].unique().sort()[-20:]
    tmp = feat.with_columns(is_rec=pl.col("date").is_in(recent.to_list())).with_columns(
        cnt=pl.col("is_rec").cum_sum())
    n_mix = int(tmp.filter(pl.col("is_rec")).height * ratio)
    return tmp.with_columns(
        [pl.when(pl.col("is_rec") & (pl.col("cnt") <= n_mix))
         .then(pl.col(other)).otherwise(pl.col(t)).alias(t) for t in targets]
    ).drop(["is_rec", "cnt"])


def report(tag, psi):
    print(f"--- {tag} ---")
    print(f"  max={psi['max']}  mean={psi['mean']}  over={psi['over']}")
    print(f"  drift_state(psi)={M._drift_state(psi)}   drift_state(max)={M._drift_state(psi['max'])}")
    print(f"  top5={[(t['factor'], t['psi']) for t in psi['top']]}")
    print(f"  raw.max={psi['raw']['max']} raw.mean={psi['raw']['mean']}")
    print(f"  degenerate={psi.get('degenerate')}")


def main():
    feat = _load_real_panel()
    if feat is None:
        print("NO PANEL"); return
    print(f"panel height={feat.height} cols={len(feat.columns)} "
          f"dates={feat['date'].n_unique()} max_date={feat['date'].max()}")
    print(f"has ma_gap_250={'ma_gap_250' in feat.columns} ret_1={'ret_1' in feat.columns} "
          f"g1_ma_gap_250={'g1_ma_gap_250' in feat.columns}")

    base = M.compute_psi(feat)
    report("BASELINE", base)

    # 全因子 per-factor（含 ma_gap_250 / rank_close_60 / g1_ma_gap_250）
    full = M.compute_psi(feat, top_n=base["n_factors"])
    pf = {t["factor"]: t["psi"] for t in full["top"]}
    for f in ("ma_gap_250", "rank_close_60", "g1_ma_gap_250", "g1_ret_60"):
        print(f"  per-factor {f} = {pf.get(f)}")

    for ratio in (0.3, 0.5, 0.7):
        psi = M.compute_psi(_inject_real(feat, ["ma_gap_250"], "ret_1", ratio))
        report(f"INJECT ma_gap_250<-ret_1 @{ratio:.0%}", psi)
        full_i = M.compute_psi(_inject_real(feat, ["ma_gap_250"], "ret_1", ratio),
                               top_n=base["n_factors"])
        pfi = {t["factor"]: t["psi"] for t in full_i["top"]}
        print(f"  per-factor ma_gap_250 = {pfi.get('ma_gap_250')}  rank_close_60={pfi.get('rank_close_60')}")

    psi_g1 = M.compute_psi(_inject_real(feat, ["g1_ma_gap_250"], "ret_1", 0.5))
    report("INJECT g1_ma_gap_250<-ret_1 @50%", psi_g1)


if __name__ == "__main__":
    main()
