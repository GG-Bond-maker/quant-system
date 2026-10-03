"""测量：漂移判定 body 统计量的历史分布 + 当前/注入场景（用于导出 DRIFT_* 阈值）。

方法学（关键优化）
------------------
``_xsec_standardize`` 是**逐交易日截面**标准化 ⇒ 任一日的 z 值与"窗口"无关。
故对**整面板只标准化一次**，再对每个滚动窗口切出 baseline/recent 的 z 值算 per-factor
PSI —— 与 ``compute_psi`` 逐窗口重算**数学等价**，但快 ~150×。

产出：
  A. 滚动窗口（recent 逐步后移）的 ``psi["mean"]`` 与
     ``ratio_watch=n(psi>0.10)/n`` / ``ratio_degraded=n(psi>0.25)/n`` 分布。
  B. 当前窗口 + 各注入场景（30%/50%/70%、g1_ 变体）的
     mean / ratio_watch / ratio_degraded / **ma_gap_250 自己的 PSI**。

用法::

    cd backend && ./.venv/Scripts/python.exe scripts/dev_measure_drift_body.py [--step 1]
"""
from __future__ import annotations

import argparse
import sys
from datetime import timedelta
from pathlib import Path

import numpy as np
import polars as pl

BACKEND_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(BACKEND_ROOT))

from app.ml import monitor as M  # noqa: E402

PROJECT_ROOT = BACKEND_ROOT.parent
REAL_FEATURES = PROJECT_ROOT / "data" / "parquet" / "features"
PSI_WATCH = M.PSI_WATCH
PSI_DEGRADED = M.PSI_DEGRADED
RECENT_DAYS = 20
BASELINE_DAYS = 250
SKIP = {"symbol", "date", "close", "label_ret", "year"}


def load_real_panel() -> pl.DataFrame | None:
    if not REAL_FEATURES.exists():
        return None
    dirs = [d for d in REAL_FEATURES.iterdir()
            if d.is_dir() and d.name.startswith("version=")]
    if not dirs:
        return None
    vd = max(dirs, key=lambda d: max(f.stat().st_mtime_ns for f in d.rglob("*.parquet")))
    files = sorted(vd.rglob("*.parquet"))
    if not files:
        return None
    df = pl.concat([pl.read_parquet(f) for f in files], how="diagonal_relaxed")
    df = M._normalize_date_col(df)
    dmax = df["date"].max()
    return df.filter(pl.col("date") >= dmax - timedelta(days=int(400 * 1.6)))


def _metrics_from_z(zb_np: np.ndarray, zr_np: np.ndarray, factors: list[str],
                    bins: int = 10) -> dict:
    """给定 baseline/recent 的 z 值矩阵（rows×factors），算 body 统计量。"""
    vals: dict[str, float] = {}
    for j in range(len(factors)):
        b = zb_np[:, j]
        b = b[np.isfinite(b)]
        r = zr_np[:, j]
        r = r[np.isfinite(r)]
        if len(b) < 100 or len(r) < 30:
            continue
        v = M._psi_one(b, r, bins)
        if v is not None:
            vals[factors[j]] = v
    if not vals:
        return {"n": 0}
    arr = np.array(list(vals.values()))
    n = len(arr)
    top = max(vals.items(), key=lambda kv: kv[1])
    return {"n": n,
            "mean": float(arr.mean()),
            "max": float(arr.max()),
            "n_watch": int((arr > PSI_WATCH).sum()),
            "n_degraded": int((arr > PSI_DEGRADED).sum()),
            "ratio_watch": float((arr > PSI_WATCH).sum()) / n,
            "ratio_degraded": float((arr > PSI_DEGRADED).sum()) / n,
            "top": top[0], "top_psi": top[1],
            "vals": vals}


def _dist(name: str, xs: list[float]) -> str:
    a = np.array(xs, dtype=float)
    return (f"  {name:16s} min={a.min():.4f}  median={np.median(a):.4f}  "
            f"p90={np.percentile(a, 90):.4f}  max={a.max():.4f}")


def part_a(pdf, factors, step: int) -> None:
    print("\n=== A. 滚动窗口 body 统计量分布 ===")
    z_all = M._xsec_standardize(pdf, factors)
    date_arr = z_all["date"].to_numpy()
    Z = z_all[factors].to_numpy()
    del z_all
    uniq = np.sort(np.unique(date_arr))
    n_dates = len(uniq)
    print(f"面板：dates={n_dates}  factors={len(factors)}  rows={len(pdf)}  step={step}")

    means, rw, rd, maxs = [], [], [], []
    ends = list(range(RECENT_DAYS + BASELINE_DAYS - 1, n_dates, step))
    for e in ends:
        rec = uniq[e - RECENT_DAYS + 1: e + 1]
        base = uniq[e - RECENT_DAYS - BASELINE_DAYS + 1: e - RECENT_DAYS + 1]
        mr = np.isin(date_arr, rec)
        mb = np.isin(date_arr, base)
        m = _metrics_from_z(Z[mb], Z[mr], factors)
        if m["n"] == 0:
            continue
        means.append(m["mean"]); rw.append(m["ratio_watch"])
        rd.append(m["ratio_degraded"]); maxs.append(m["max"])
    print(f"  窗口数 = {len(means)}（recent 窗从第 {ends[0]} 日滚到第 {ends[-1]} 日）")
    print(_dist("psi.mean", means))
    print(_dist("ratio_watch", rw))
    print(_dist("ratio_degraded", rd))
    print(_dist("psi.max(单因子)", maxs))
    print(f"  psi.mean  p50/p95/p99 = "
          f"{np.percentile(means,50):.4f}/{np.percentile(means,95):.4f}/"
          f"{np.percentile(means,99):.4f}")
    print(f"  ratio_degraded p50/p95/p99 = "
          f"{np.percentile(rd,50):.4f}/{np.percentile(rd,95):.4f}/"
          f"{np.percentile(rd,99):.4f}")
    print(f"  ratio_watch p50/p95/p99 = "
          f"{np.percentile(rw,50):.4f}/{np.percentile(rw,95):.4f}/"
          f"{np.percentile(rw,99):.4f}")


def _inject_real(feat: pl.DataFrame, targets: list[str], other: str,
                 ratio: float) -> pl.DataFrame:
    recent = feat["date"].unique().sort()[-20:]
    tmp = feat.with_columns(
        is_rec=pl.col("date").is_in(recent.to_list())).with_columns(
        cnt=pl.col("is_rec").cum_sum())
    n_mix = int(tmp.filter(pl.col("is_rec")).height * ratio)
    return tmp.with_columns(
        [pl.when(pl.col("is_rec") & (pl.col("cnt") <= n_mix))
         .then(pl.col(other)).otherwise(pl.col(t)).alias(t) for t in targets]
    ).drop(["is_rec", "cnt"])


def _scenario(feat: pl.DataFrame, label: str) -> dict:
    sh = M.prepare_xsec_frames(feat)
    zb = sh["z_base"][sh["factors"]].to_numpy()
    zr = sh["z_recent"][sh["factors"]].to_numpy()
    m = _metrics_from_z(zb, zr, sh["factors"])
    mg = m["vals"].get("ma_gap_250")
    print(f"  {label:26s} mean={m['mean']:.4f}  r_watch={m['ratio_watch']:.4f} "
          f"r_degraded={m['ratio_degraded']:.4f}  max={m['max']:.4f}({m['top']})  "
          f"ma_gap_250_psi={mg if mg is None else round(mg, 4)}")
    return m


def part_b(feat: pl.DataFrame) -> None:
    print("\n=== B. 当前窗口 + 注入场景 ===")
    _scenario(feat, "baseline(current)")
    _scenario(_inject_real(feat, ["ma_gap_250"], "ret_1", 0.3), "ma_gap_250<-ret_1 @30%")
    _scenario(_inject_real(feat, ["ma_gap_250"], "ret_1", 0.5), "ma_gap_250<-ret_1 @50%")
    _scenario(_inject_real(feat, ["ma_gap_250"], "ret_1", 0.7), "ma_gap_250<-ret_1 @70%")
    _scenario(_inject_real(feat, ["g1_ma_gap_250"], "ret_1", 0.5), "g1_ma_gap_250<-ret_1 @50%")
    _scenario(_inject_real(feat, ["ma_gap_250"], "ret_1", 0.9), "ma_gap_250<-ret_1 @90%")


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--step", type=int, default=1)
    ap.add_argument("--skip-a", action="store_true")
    ap.add_argument("--skip-b", action="store_true")
    args = ap.parse_args()

    feat = load_real_panel()
    if feat is None or feat.height < 100_000:
        print("真实面板不可用，退出")
        return 1
    pdf = feat.to_pandas()
    factors = [c for c in pdf.columns
               if c not in SKIP and np.issubdtype(pdf[c].dtype, np.number)]
    print(f"面板 rows={feat.height} dates={pdf['date'].nunique()} factors={len(factors)}")
    if not args.skip_a:
        part_a(pdf, factors, args.step)
    if not args.skip_b:
        part_b(feat)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
