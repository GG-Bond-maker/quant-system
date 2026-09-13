"""GNN（产业链传导）训练 CLI（薄壳：argparse + 调用训练服务 + 打印）。

逻辑唯一真源在 app/ml/train_service.py（与数据中心 /train/start 同一套
代码，避免 CLI 与 API 各自维护后漂移）。

前置条件：
1. torch（CPU 版）——安装见 train_service 提示；
2. 关系数据（二选一，见 app/ml/graph.py）：
   - DATA_ROOT/relations/edges.parquet（供应链/股权显式边）
   - instrument 表 industry 列补齐（同行业 peer 边）
3. 特征已构建。无关系边时服务直接拒绝并给原因。

    python scripts/train_gnn.py --epochs 40
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

BACKEND_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(BACKEND_ROOT))

from app.core.logging import setup_logging  # noqa: E402
from app.ml.train_service import run_gnn_training  # noqa: E402


def main() -> None:
    ap = argparse.ArgumentParser(prog="train_gnn.py")
    ap.add_argument("--horizon", type=int, default=5)
    ap.add_argument("--holdout", type=int, default=252)
    ap.add_argument("--epochs", type=int, default=40)
    args = ap.parse_args()

    setup_logging()
    res = run_gnn_training(horizon=args.horizon, holdout=args.holdout,
                           epochs=args.epochs)
    if not res.get("ok"):
        print(json.dumps(res, ensure_ascii=False, indent=1))
        raise SystemExit(1)
    print(f"valid RankIC={res['valid_rank_ic']:.4f}({res['valid_days']}日)")
    print(f"模型目录: {res['model_dir']}")


if __name__ == "__main__":
    main()
