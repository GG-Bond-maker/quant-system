"""TFT/时序 Transformer 训练 CLI（薄壳：argparse + 调用训练服务 + 打印）。

逻辑唯一真源在 app/ml/train_service.py（与数据中心 /train/start 同一套
代码，避免 CLI 与 API 各自维护后漂移）。

前置条件：
1. torch（CPU 版）：pip install torch --index-url https://download.pytorch.org/whl/cpu
2. features 已构建（pipeline build_features / scripts/build_features.py）
3. 建议股票池 1000+ 只、5 年以上历史——120 只 × 4.6 年训深模型必然过拟合
   （服务内建 MIN_TRAIN_SAMPLES 门禁，样本不足会主动中止并给原因）。

    python scripts/train_tft.py --lookback 30 --epochs 30
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

BACKEND_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(BACKEND_ROOT))

from app.core.logging import setup_logging  # noqa: E402
from app.ml.train_service import run_tft_training  # noqa: E402


def main() -> None:
    ap = argparse.ArgumentParser(prog="train_tft.py")
    ap.add_argument("--lookback", type=int, default=30)
    ap.add_argument("--horizon", type=int, default=5)
    ap.add_argument("--holdout", type=int, default=252)
    ap.add_argument("--test", type=int, default=252)
    ap.add_argument("--epochs", type=int, default=30)
    ap.add_argument("--batch", type=int, default=256)
    args = ap.parse_args()

    setup_logging()
    res = run_tft_training(
        lookback=args.lookback, horizon=args.horizon, holdout=args.holdout,
        test_days=args.test, epochs=args.epochs, batch=args.batch)
    if not res.get("ok"):
        print(json.dumps(res, ensure_ascii=False, indent=1))
        raise SystemExit(1)
    print(f"valid RankIC={res['valid_rank_ic']:.4f}({res['valid_days']}日)  "
          f"test RankIC={res['test_rank_ic']:.4f}({res['test_days']}日)")
    print(f"模型目录: {res['model_dir']}")
    print("promote: python scripts/promote_model.py（门禁把关）")


if __name__ == "__main__":
    main()
