"""诊断：``_xsec_standardize`` 的 ``MAD==0`` 退化对 PSI 的影响（修复前/后对照）。

缺陷复现
--------
``rank_close_60`` 是 60 日滚动时序百分位，取值只有 60 个离散档。在"全市场普跌"的
截面里中位数掉到 0.0167、**当日 MAD 恰好 = 0** ⇒ 旧实现的分母只剩 ``+1e-12``
⇒ ``z`` 冲到 ~1e12 ⇒ 基线等频分箱边界被拉飞 ⇒ 真实观测几乎全落进 bin0
⇒ PSI 被人为抬高（``psi.max`` > 0.25）⇒ 生产监控报**假降级**。

本脚本：
  1. 用**内联的旧公式**（分母 ``mad*1.4826 + 1e-12``）复算 PSI —— 作为修复前的固定参照；
  2. 调用**当前磁盘上的** ``app.ml.monitor.compute_psi`` —— 反映修复后的真实行为；
  3. 逐因子统计「基线窗口内 MAD==0 的交易日」清单，核对退化计数口径。

用法::

    cd backend && ./.venv/Scripts/python.exe scripts/dev_diag_psi_mad0.py

跑完保留本脚本，便于后续复现。
"""
from __future__ import annotations

import sys
from datetime import timedelta
from pathlib import Path

import numpy as np
import pandas as pd
import polars as pl

BACKEND_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(BACKEND_ROOT))

from app.ml import monitor as M  # noqa: E402

PROJECT_ROOT = BACKEND_ROOT.parent
REAL_FEATURES = PROJECT_ROOT / "data" / "parquet" / "features"
PSI_DEGRADED = 0.25
RECENT_DAYS = 20
BASELINE_DAYS = 250
SKIP = {"symbol", "date", "close", "label_ret", "year"}


def load_real_panel() -> pl.DataFrame | None:
    """按 monitor 同口径读真实面板（单一特征版本 + 近 400×1.6 自然日窗口）。"""
    if not REAL_FEATURES.exists():
        return None
    dirs = [d for d in REAL_FEATURES.iterdir()
            if d.is_dir() and d.name.startswith("version=")]
    if not dirs:
        return None
    version_dir = max(dirs, key=lambda d: max(f.stat().st_mtime_ns
                                              for f in d.rglob("*.parquet")))
    files = sorted(version_dir.rglob("*.parquet"))
    if not files:
        return None
    df = pl.concat([pl.read_parquet(f) for f in files], how="diagonal_relaxed")
    df = M._normalize_date_col(df)
    dmax = df["date"].max()
    return df.filter(pl.col("date") >= dmax - timedelta(days=int(400 * 1.6)))


def _factors_of(pdf: pd.DataFrame) -> list[str]:
    return [c for c in pdf.columns
            if c not in SKIP and pd.api.types.is_numeric_dtype(pdf[c])]


def _split(pdf: pd.DataFrame) -> tuple[pd.DataFrame, pd.DataFrame, list[str]]:
    dates = np.sort(pdf["date"].unique())
    recent = pdf[pdf["date"].isin(set(dates[-RECENT_DAYS:]))]
    baseline = pdf[pdf["date"].isin(set(dates[-(RECENT_DAYS + BASELINE_DAYS):-RECENT_DAYS]))]
    return recent, baseline, _factors_of(pdf)


def legacy_xsec(pdf: pd.DataFrame, factors: list[str]) -> pd.DataFrame:
    """修复前的旧公式（分母 ``mad*1.4826 + 1e-12``）——缺陷的固定参照。"""
    out = pdf[["date"] + factors].copy()
    med = out.groupby("date", sort=False)[factors].transform("median")
    absdev = (out[factors] - med).abs()
    mad = absdev.groupby(out["date"], sort=False)[factors].transform("median")
    out[factors] = (out[factors] - med) / (mad * M.XSEC_MAD_SCALE + 1e-12)
    return out


def psi_block(z_base: pd.DataFrame, z_recent: pd.DataFrame,
              factors: list[str]) -> dict:
    per: dict[str, float] = {}
    for f in factors:
        bz = z_base[f].dropna()
        rz = z_recent[f].dropna()
        if len(bz) < 100 or len(rz) < 30:
            continue
        v = M._psi_one(bz.to_numpy(), rz.to_numpy(), 10)
        if v is not None:
            per[f] = v
    if not per:
        return {"n_factors": 0, "mean": None, "max": None, "over": []}
    over = sorted([f for f, v in per.items() if v > PSI_DEGRADED],
                  key=lambda f: -per[f])
    return {"n_factors": len(per),
            "mean": round(float(np.mean(list(per.values()))), 4),
            "max": round(max(per.values()), 4),
            "over": over}


def mad0_days_by_factor(baseline: pd.DataFrame, factors: list[str]) -> dict[str, list[str]]:
    """基线窗口内，逐因子列出 MAD==0 的交易日（退化点定位）。"""
    med = baseline.groupby("date", sort=False)[factors].transform("median")
    absdev = (baseline[factors] - med).abs()
    mad = absdev.groupby(baseline["date"], sort=False)[factors].transform("median")
    out: dict[str, list[str]] = {}
    for f in factors:
        bad = (mad[f] <= 0).to_numpy()
        if bad.any():
            ds = sorted({str(d) for d in baseline.loc[bad, "date"].unique()})
            out[f] = ds
    return out


def main() -> int:
    feat = load_real_panel()
    if feat is None or feat.height < 100_000:
        print("真实面板不可用，退出")
        return 1
    pdf = feat.to_pandas()
    recent, baseline, factors = _split(pdf)
    print(f"面板：rows={feat.height}  dates={pdf['date'].nunique()}  "
          f"factors={len(factors)}  recent_rows={len(recent)}  baseline_rows={len(baseline)}")

    # ---- 1) 修复前（内联旧公式） ----
    zr_old = legacy_xsec(recent, factors)
    zb_old = legacy_xsec(baseline, factors)
    old = psi_block(zb_old, zr_old, factors)
    print("\n[修复前·旧公式 mad*1.4826+1e-12]")
    print(f"  n_factors={old['n_factors']}  mean={old['mean']}  max={old['max']}")
    print(f"  超 0.25 因子数={len(old['over'])}  名单={old['over']}")
    zmax_old = float(max(zb_old[factors].abs().to_numpy().max(),
                         zr_old[factors].abs().to_numpy().max()))
    n_big_old = int((zb_old[factors].abs() > 100).to_numpy().sum()
                    + (zr_old[factors].abs() > 100).to_numpy().sum())
    print(f"  旧公式 |z|>100 观测数={n_big_old}  max|z|={zmax_old:.3e}")

    # ---- 2) 修复后（当前磁盘代码） ----
    psi = M.compute_psi(feat)
    print("\n[修复后·当前 app.ml.monitor.compute_psi]")
    print(f"  ok={psi.get('ok')}  basis={psi.get('basis')}  "
          f"n_factors={psi.get('n_factors')}  mean={psi.get('mean')}  max={psi.get('max')}")
    sh = M.prepare_xsec_frames(feat)
    per_now = psi_block(sh["z_base"], sh["z_recent"], sh["factors"])
    print(f"  超 0.25 因子数={len(per_now['over'])}  名单={per_now['over']}")
    print(f"  退化计数={psi.get('degenerate')}")
    print(f"  note={psi.get('note')}")
    print(f"  raw.max={psi.get('raw', {}).get('max')}")

    # ---- 3) 逐因子 MAD==0 交易日 ----
    deg = mad0_days_by_factor(baseline, factors)
    print("\n[基线窗口内 MAD==0 的因子 / 交易日]")
    if not deg:
        print("  无（所有因子所有交易日的 MAD 均 > 0）")
    for f, ds in deg.items():
        print(f"  {f}: {len(ds)} 天 -> {ds}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
