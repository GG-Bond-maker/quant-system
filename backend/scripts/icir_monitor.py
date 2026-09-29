"""P2-2：ICIR 因子监控（alpha_basic_v1，滚动 20 交易日）。

ICIR = mean(IC_20) / std(IC_20)；status: strong(>0.5) / risk_factor(<-0.5) / normal。
输出 reports/icir_YYYYWW.csv 与 .png。真实数据达不到目标时如实输出。
"""
from __future__ import annotations

import sys
from datetime import datetime
from pathlib import Path

BACKEND_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(BACKEND_ROOT))

import matplotlib
import numpy as np
import pandas as pd

matplotlib.use("Agg")
import matplotlib.pyplot as plt  # noqa: E402

from app.core.config import get_settings  # noqa: E402
from app.core.logging import setup_logging  # noqa: E402
from app.ml.features import FEATURE_VERSION, factor_columns  # noqa: E402


def status_of(icir: float) -> str:
    """风险标记（纯函数，可单测）。"""
    if np.isnan(icir):
        return "insufficient"
    if icir > 0.5:
        return "strong"
    if icir < -0.5:
        return "risk_factor"
    return "normal"


def daily_factor_ic(df: pd.DataFrame, factor: str, label_col: str = "label_ret",
                    min_names: int = 10) -> pd.Series:
    """按日截面 Pearson IC 序列（截面样本 < min_names 的日期跳过）。"""
    ics: dict[object, float] = {}
    for d, g in df.groupby("date"):
        x = g[factor].to_numpy(dtype=np.float64)
        y = g[label_col].to_numpy(dtype=np.float64)
        mask = np.isfinite(x) & np.isfinite(y)
        if mask.sum() < min_names:
            continue
        xs, ys = x[mask], y[mask]
        if xs.std() == 0 or ys.std() == 0:
            continue
        ics[d] = float(np.corrcoef(xs, ys)[0, 1])
    return pd.Series(ics, dtype=np.float64).sort_index()


def compute_icir_report(features: pd.DataFrame, window: int = 20,
                        horizon: int = 5) -> pd.DataFrame:
    """全部因子的滚动 ICIR 报告表。"""
    df = features.copy()
    df["label_ret"] = df.groupby("symbol")["close"].transform(
        lambda x: x.shift(-horizon) / x - 1.0)
    rows: list[dict] = []
    for factor in factor_columns(df):
        if factor == "label_ret":
            continue
        ics = daily_factor_ic(df, factor)
        recent = ics.iloc[-window:]
        mean_ic = float(recent.mean()) if len(recent) else float("nan")
        std_ic = float(recent.std(ddof=1)) if len(recent) > 1 else float("nan")
        icir = mean_ic / std_ic if std_ic and std_ic == std_ic and std_ic > 1e-12 else float("nan")
        rows.append({"factor": factor, "mean_ic_20": mean_ic, "std_ic_20": std_ic,
                     "icir_20": icir, "sample_count": int(recent.count()),
                     "status": status_of(icir)})
    return pd.DataFrame(rows)


def write_report(report: pd.DataFrame, out_dir: Path | None = None) -> tuple[Path, Path]:
    """写 reports/icir_YYYYWW.csv 与 .png（ICIR Top/Bottom 20 柱图）。"""
    get_settings()  # 确保运行期设置（DATA_ROOT 等）在写报告前已初始化
    out = out_dir or (BACKEND_ROOT / "reports")
    out.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now().isocalendar()
    name = f"icir_{stamp.year}{stamp.week:02d}"
    csv_path = out / f"{name}.csv"
    report.to_csv(csv_path, index=False, encoding="utf-8-sig")

    top = report.dropna(subset=["icir_20"]).nlargest(20, "icir_20")
    bot = report.dropna(subset=["icir_20"]).nsmallest(20, "icir_20")
    fig, ax = plt.subplots(figsize=(12, 6))
    combined = pd.concat([bot, top])
    colors = ["#22c55e" if v < 0 else "#ef4444" for v in combined["icir_20"]]
    ax.bar(range(len(combined)), combined["icir_20"], color=colors)
    ax.set_xticks(range(len(combined)))
    ax.set_xticklabels(combined["factor"], rotation=90, fontsize=6)
    ax.axhline(0.5, ls="--", lw=0.8, color="gray")
    ax.axhline(-0.5, ls="--", lw=0.8, color="gray")
    ax.set_title(f"ICIR (window=20) {name}")
    fig.tight_layout()
    png_path = out / f"{name}.png"
    fig.savefig(png_path, dpi=120)
    plt.close(fig)
    return csv_path, png_path


def main() -> None:
    setup_logging()
    s = get_settings()
    feat_dir = s.DATA_ROOT / "features" / f"version={FEATURE_VERSION}"
    parts = sorted(feat_dir.glob("year=*.parquet"))
    if not parts:
        raise SystemExit(f"features 不存在: {feat_dir}")
    df = pd.concat([pd.read_parquet(p) for p in parts], ignore_index=True)
    report = compute_icir_report(df)
    csv_path, png_path = write_report(report)
    n_strong = int((report["status"] == "strong").sum())
    n_risk = int((report["status"] == "risk_factor").sum())
    print(f"ICIR 报告: {csv_path} | {png_path}")
    print(f"因子总数={len(report)} strong={n_strong} risk_factor={n_risk} "
          f"(文档目标 strong>=20 的实际达成情况如上，未达标即如实报告)")


if __name__ == "__main__":
    main()
