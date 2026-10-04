"""test-red 诊断探针 v2：内存友好，逐个 ratio 计算并释放。"""
from __future__ import annotations
import gc
import sys
from pathlib import Path

BACKEND_ROOT = Path(__file__).resolve().parents[3] / "backend"
sys.path.insert(0, str(BACKEND_ROOT))

import polars as pl  # noqa: E402
from app.ml import monitor as M  # noqa: E402

PROJECT_ROOT = BACKEND_ROOT.parent
REAL_FEATURES = PROJECT_ROOT / "data" / "parquet" / "features"


def _load_real_panel():
    dirs = [d for d in REAL_FEATURES.iterdir() if d.is_dir() and d.name.startswith("version=")]
    version_dir = max(dirs, key=lambda d: max(f.stat().st_mtime_ns for f in d.rglob("*.parquet")))
    files = sorted(version_dir.rglob("*.parquet"))
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


def measure(feat, tag, targets, other, ratio):
    if ratio == 0.0:
        inj, tgt = feat, targets[0]
    else:
        inj, tgt = _inject_real(feat, targets, other, ratio), targets[0]
    psi = M.compute_psi(inj, top_n=10_000)  # 取全表 per-factor
    pf = {t["factor"]: t["psi"] for t in psi["top"]}
    print(f"[{tag}] panel.max={psi['max']} panel.mean={psi['mean']} over={psi['over']} "
          f"state={M._drift_state(psi)} | {tgt}={pf.get(tgt)} "
          f"rank_close_60={pf.get('rank_close_60')} "
          f"n_degraded_perfactor={sum(1 for v in pf.values() if v > M.PSI_DEGRADED)}")
    del inj, psi, pf
    gc.collect()


def main():
    feat = _load_real_panel()
    print(f"panel h={feat.height} dates={feat['date'].n_unique()} max={feat['date'].max()}")
    measure(feat, "BASE", ["ma_gap_250"], "ret_1", 0.0)
    for r in (0.3, 0.5, 0.7):
        measure(feat, f"ma_gap_250<-ret_1@{r:.0%}", ["ma_gap_250"], "ret_1", r)
    measure(feat, "g1_ma_gap_250<-ret_1@50%", ["g1_ma_gap_250"], "ret_1", 0.5)


if __name__ == "__main__":
    main()
