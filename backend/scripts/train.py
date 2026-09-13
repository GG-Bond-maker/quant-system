"""train：三段切分（train/gap/valid/gap/test）训练 LightGBM（P0-Major#5）。

合并 features 年分区 -> train_lgbm（含 ICIR 与 train/valid/test 全套指标）
-> 模型产物落盘 + model_registry 写入。

用法（backend 目录下，需先 build_features.py）：
    python scripts/train.py --horizon 5 --holdout 252 --test-days 252
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
from app.ml.features import FEATURE_VERSION  # noqa: E402
from app.ml.train_lgbm import train_lgbm  # noqa: E402


def main() -> None:
    parser = argparse.ArgumentParser(prog="train.py")
    parser.add_argument("--horizon", type=int, default=5)
    parser.add_argument("--holdout", type=int, default=252, help="valid 段交易日数")
    parser.add_argument("--test-days", type=int, default=252, help="test 段交易日数")
    parser.add_argument("--version-suffix", default="")
    args = parser.parse_args()

    setup_logging()
    s = get_settings()
    feat_dir = s.DATA_ROOT / "features" / f"version={FEATURE_VERSION}"
    parts = sorted(feat_dir.glob("year=*.parquet"))
    if not parts:
        raise SystemExit(f"features 不存在：{feat_dir}，请先运行 scripts/build_features.py")
    logger.info(f"merge {len(parts)} feature partitions")
    df = pd.concat([pd.read_parquet(p) for p in parts], ignore_index=True)

    result = train_lgbm(
        df,
        horizon=args.horizon,
        holdout_days=args.holdout,
        test_days=args.test_days,
        version_suffix=args.version_suffix,
    )
    logger.info(
        f"RESULT {result['version']}: "
        f"train_ic={result['train_ic']:.4f} "
        f"valid_ic={result['valid_ic']:.4f} valid_rank_ic={result['valid_rank_ic']:.4f} "
        f"valid_icir={result['valid_icir']:.4f} | "
        f"test_ic={result['test_ic']:.4f} test_rank_ic={result['test_rank_ic']:.4f} "
        f"test_icir={result['test_icir']:.4f}"
    )
    print(f"模型产物: {result['model_path']}")


if __name__ == "__main__":
    main()
