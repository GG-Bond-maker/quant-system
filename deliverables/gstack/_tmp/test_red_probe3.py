"""test-red 诊断探针 v3：验证 rank_close_60 的 0.5149 是否为 MAD==0 污染残留。"""
from __future__ import annotations
import sys
from pathlib import Path

BACKEND_ROOT = Path(__file__).resolve().parents[3] / "backend"
sys.path.insert(0, str(BACKEND_ROOT))

import numpy as np  # noqa: E402
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


def main():
    feat = _load_real_panel()
    sh = M.prepare_xsec_frames(feat)
    zb, zr = sh["z_base"], sh["z_recent"]
    for f in ("rank_close_60", "g1_ret_60", "amount_per_share"):
        bz = zb[f].dropna().to_numpy()
        rz = zr[f].dropna().to_numpy()
        full = M._psi_one(bz, rz)
        # ① 剔除 |z|>100 的极端点
        bz2, rz2 = bz[np.abs(bz) <= 100], rz[np.abs(rz) <= 100]
        clip = M._psi_one(bz2, rz2)
        # ② winsorize 到 ±50
        clip50 = M._psi_one(np.clip(bz, -50, 50), np.clip(rz, -50, 50))
        print(f"[{f}] full={full} drop|z|>100={clip} (n_b={len(bz)}->{len(bz2)}, "
              f"n_r={len(rz)}->{len(rz2)}) winsor50={clip50} "
              f"max|z_b|={np.abs(bz).max():.2f} max|z_r|={np.abs(rz).max():.2f}")
    # 退化日期清单（rank_close_60 的 MAD==0 日）
    print("degenerate:", sh["degenerate"])


if __name__ == "__main__":
    main()
