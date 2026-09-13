"""infer：读取最新特征 -> 加载最新训练版本 -> 生成 predictions 分区（P0-Major#5）。

用法（backend 目录下，需先 train.py）：
    python scripts/infer.py                    # 默认推理特征中最近一个交易日
    python scripts/infer.py --date 20260828
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

BACKEND_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(BACKEND_ROOT))

import pandas as pd  # noqa: E402
from loguru import logger  # noqa: E402

from app.core.config import get_settings  # noqa: E402
from app.core.logging import setup_logging  # noqa: E402
from app.data.parquet_store import atomic_write_parquet  # noqa: E402
from app.ml.features import FEATURE_VERSION  # noqa: E402
from app.ml.infer import load_prod_model, predict_with_contrib  # noqa: E402


def main() -> None:
    parser = argparse.ArgumentParser(prog="infer.py")
    parser.add_argument("--date", default=None, help="YYYYMMDD（默认特征中最近交易日）")
    args = parser.parse_args()

    setup_logging()
    s = get_settings()
    feat_dir = s.DATA_ROOT / "features" / f"version={FEATURE_VERSION}"
    parts = sorted(feat_dir.glob("year=*.parquet"))
    if not parts:
        raise SystemExit(f"features 不存在：{feat_dir}，请先运行 scripts/build_features.py")
    df = pd.concat([pd.read_parquet(p) for p in parts], ignore_index=True)
    df = df.sort_values(["symbol", "date"])

    target_raw = pd.to_datetime(args.date) if args.date else pd.to_datetime(df["date"]).max()
    target = target_raw.date() if hasattr(target_raw, "date") else target_raw
    # 统一经 Timestamp 比较（parquet 读回的 date 列 dtype 可能是 date 对象或 datetime64）
    day_df = df[pd.to_datetime(df["date"]) == pd.Timestamp(target)].copy()
    if day_df.empty:
        raise SystemExit(f"特征中不存在日期 {target}")

    booster, feats, model_dir = load_prod_model()
    pred, _ = predict_with_contrib(booster, day_df, feats)
    out = day_df[["date", "symbol"]].copy()
    out["pred_score"] = pred

    out_dir = s.DATA_ROOT / "predictions"
    out_dir.mkdir(parents=True, exist_ok=True)
    out_path = out_dir / f"date={target.strftime('%Y%m%d')}.parquet"
    atomic_write_parquet(out_path, out)
    logger.info(f"infer done({model_dir.name}): rows={len(out)} "
                f"score=[{pred.min():.4f},{pred.max():.4f}] -> {out_path}")


if __name__ == "__main__":
    main()
