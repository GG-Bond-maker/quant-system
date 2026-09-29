"""修复后重训 + 显式 promote（第十二阶段）。

流程： build_features -> train(candidate) -> 质量门禁评估 -> 显式 promote -> 报告

    python scripts/retrain.py                    # 只训练并登记候选
    python scripts/retrain.py --promote          # 训练并尝试 promote
    python scripts/retrain.py --promote --force  # 跳过门槛（仅调试，会明确告警）

输出 model_version / feature_version / dataset_version。
"""
from __future__ import annotations

import argparse
import json
import sys
from datetime import date
from pathlib import Path

BACKEND_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(BACKEND_ROOT))

import pandas as pd  # noqa: E402

from app.core.config import get_settings  # noqa: E402
from app.core.logging import setup_logging  # noqa: E402
from app.data.parquet_store import read_all_symbols, read_symbol_dataset  # noqa: E402
from app.ml.features import FEATURE_VERSION  # noqa: E402
from app.ml.registry import (  # noqa: E402
    PromotePolicy, auto_min_rank_ic, evaluate_candidate, get_model,
    get_production, promote_model,
)
from app.ml.train_lgbm import train_lgbm  # noqa: E402


def compute_dataset_version() -> str:
    """数据集指纹：标的数 + 交易日数 + 日期范围 + 行数。

    用于把模型产物与"它是在哪份数据上训出来的"绑定，避免数据更新后
    仍拿旧模型推理而无人知晓。
    """
    get_settings()  # 确保运行期设置（DATA_ROOT 等）已初始化
    syms = read_all_symbols("daily_bar_hfq")
    rows = 0
    dmin: date | None = None
    dmax: date | None = None
    for sym in syms:
        df = read_symbol_dataset("daily_bar_hfq", sym)
        if df.is_empty():
            continue
        rows += df.height
        a, b = df["date"].min(), df["date"].max()
        dmin = a if dmin is None else min(dmin, a)
        dmax = b if dmax is None else max(dmax, b)
    stamp = f"{len(syms)}s{rows}r"
    if dmin is not None and dmax is not None:
        stamp += f"{dmin:%Y%m%d}-{dmax:%Y%m%d}"
    return f"ds_{stamp}"


def main() -> None:
    ap = argparse.ArgumentParser(prog="retrain.py")
    ap.add_argument("--horizon", type=int, default=5)
    ap.add_argument("--holdout", type=int, default=252, help="valid 段交易日")
    ap.add_argument("--test", type=int, default=252, help="test 段交易日")
    ap.add_argument("--rounds", type=int, default=1000)
    ap.add_argument("--stopping", type=int, default=50)
    ap.add_argument("--promote", action="store_true", help="训练后尝试 promote")
    ap.add_argument("--no-xsec-demean", action="store_true",
                    help="关闭标签横截面去均值（默认开启=生产配方；仅供 A/B 对照）")
    ap.add_argument("--force", action="store_true",
                    help="跳过质量门槛强制 promote（仅供调试，会明确告警）")
    ap.add_argument("--json", default=None)
    args = ap.parse_args()

    setup_logging()
    s = get_settings()

    # ---- 1) 特征 ----
    feat_dir = s.DATA_ROOT / "features" / f"version={FEATURE_VERSION}"
    parts = sorted(feat_dir.glob("year=*.parquet"))
    if not parts:
        raise SystemExit(f"features 不存在：{feat_dir}，请先 scripts/build_features.py")
    df = pd.concat([pd.read_parquet(p) for p in parts], ignore_index=True)
    dataset_version = compute_dataset_version()
    print("=" * 72)
    print(f"数据集: rows={len(df)} symbols={df['symbol'].nunique()} "
          f"dates={df['date'].nunique()}")
    print(f"feature_version = {FEATURE_VERSION}")
    print(f"dataset_version = {dataset_version}")
    print("=" * 72)

    current = get_production()
    print(f"当前生产模型: {current['version'] if current else '（无）'}")

    # ---- 2) 训练（只产生 candidate）----
    r = train_lgbm(df, horizon=args.horizon, holdout_days=args.holdout,
                   test_days=args.test, num_boost_round=args.rounds,
                   stopping_rounds=args.stopping, version_suffix="repaired",
                   dataset_version=dataset_version,
                   xsec_demean=not args.no_xsec_demean)

    print()
    print("=" * 72)
    print(f"候选模型 {r['version']}  best_iteration={r['best_iteration']}")
    print("=" * 72)
    for seg in ("train", "valid", "test"):
        print(f"  [{seg:5}] IC={r[f'{seg}_ic']:+.4f}  RankIC={r[f'{seg}_rank_ic']:+.4f}  "
              f"ICIR={r[f'{seg}_icir']:+.4f}  RMSE={r[f'{seg}_rmse']:.4f}")
    lq = r.get("label_quality", {})
    if lq:
        print(f"\n  标签治理: 原始={lq.get('n_raw')} 极端={lq.get('n_extreme')} "
              f"跨空洞={lq.get('n_gap_invalid')} 处理后={lq.get('n_final')} "
              f"| std {lq.get('std_before', 0):.4f} -> {lq.get('std_after', 0):.4f}")

    out = {
        "model_version": r["version"],
        "feature_version": FEATURE_VERSION,
        "dataset_version": dataset_version,
        "best_iteration": r["best_iteration"],
        "metrics": {k: r[k] for k in
                    ("train_ic", "train_rank_ic", "train_icir", "train_rmse",
                     "valid_ic", "valid_rank_ic", "valid_icir", "valid_rmse",
                     "test_ic", "test_rank_ic", "test_icir", "test_rmse")},
        "label_quality": lq,
        "promoted": False,
    }

    # ---- 3) promote ----
    if args.promote:
        cand = get_model("lgbm_v1", r["version"])
        if cand is None:
            raise SystemExit("候选未登记，无法 promote")
        if args.force:
            print("\n⚠️ --force：已跳过质量门槛，此操作不应出现在生产环境")
            policy = PromotePolicy(min_valid_rank_ic=-9, min_valid_icir=-9,
                                   rank_ic_tolerance=1e9, icir_tolerance=1e9,
                                   max_rmse_worsen_ratio=1e9,
                                   # R14/T8：--force 的语义是"跳过门槛"，
                                   # 也就不要用 regime 判据替代不可比的相对比较。
                                   allow_incomparable_lineage=True)
        else:
            # 第六阶段：绝对下限按样本规模推导，不要用写死的 0.03
            n_sym = int(r.get("n_symbols") or df["symbol"].nunique())
            n_val_days = int(r.get("valid_days") or 250)
            thr = auto_min_rank_ic(n_sym, n_val_days)
            policy = PromotePolicy(min_valid_rank_ic=thr)
            print(f"\n样本规模: 横截面 {n_sym} 只 × valid {n_val_days} 交易日")
            print(f"推导出的 RankIC 下限 = {thr:.4f}"
                  f"（统计显著性 2σ 与经济性下限取大者）")
        decision = evaluate_candidate(cand, current, policy)
        print(f"\n门禁评估: promote={decision.promote}  原因: {decision.reason}")
        for k, v in decision.checks.items():
            print(f"    [{('PASS' if v.get('pass') else 'FAIL')}] {k}: {v}")
        res = promote_model("lgbm_v1", r["version"], by="retrain.py",
                            reason=decision.reason, policy=policy)
        out["promoted"] = bool(res["promoted"])
        out["promote_result"] = res
        print()
        print("=" * 72)
        print(f"promote {'成功' if res['promoted'] else '被拒绝'}")
        print("=" * 72)
    else:
        print("\n（未指定 --promote，仅登记为 candidate。生产模型未变动）")

    prod = get_production()
    out["production_model"] = prod["version"] if prod else None
    print(f"\nmodel_version    = {out['model_version']}")
    print(f"feature_version  = {out['feature_version']}")
    print(f"dataset_version  = {out['dataset_version']}")
    print(f"production_model = {out['production_model']}")

    if args.json:
        Path(args.json).write_text(
            json.dumps(out, ensure_ascii=False, indent=2, default=str),
            encoding="utf-8")
        print(f"\n结果已导出: {args.json}")


if __name__ == "__main__":
    main()
