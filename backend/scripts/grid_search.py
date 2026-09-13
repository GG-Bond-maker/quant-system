"""P2-1：LightGBM 81 组网格搜索（valid 选优，test 仅记录不参与排序）。

参数空间（严格）：learning_rate {0.03,0.05,0.08} × num_leaves {31,63,127}
× feature_fraction {0.6,0.8,1.0} × min_child_samples {20,50,100} = 81 组。

纪律：Top-1 = 最高 valid_IC（平局依次 valid_RankIC → 较小 train/valid gap → 较短
train_time）。test 指标按规格要求【记录】，但排序键在代码路径上不含任何 test 字段。

用法：
    python scripts/grid_search.py [--features 版本目录名] [--quick]
"""
from __future__ import annotations

import argparse
import itertools
import json
import sqlite3
import sys
import time
from pathlib import Path

BACKEND_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(BACKEND_ROOT))

import pandas as pd
from loguru import logger

from app.core.config import get_settings
from app.core.logging import setup_logging
from app.ml.features import FEATURE_VERSION, assert_known_feature_version
from app.ml.train_lgbm import train_lgbm

GRID: dict[str, list] = {
    "learning_rate": [0.03, 0.05, 0.08],
    "num_leaves": [31, 63, 127],
    "feature_fraction": [0.6, 0.8, 1.0],
    "min_child_samples": [20, 50, 100],
}


def _record_run(conn: sqlite3.Connection, row: dict) -> None:
    conn.execute(
        """INSERT INTO feature_runs
           (strategy, trade_date, model_version, feature_version, top_k,
            params_json, train_ic, valid_ic, test_ic, train_rankic,
            valid_rankic, test_rankic, train_time, dataset_version)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
        (row["strategy"], row["trade_date"], row["model_version"],
         row["feature_version"], row["top_k"], row["params_json"],
         row["train_ic"], row["valid_ic"], row["test_ic"],
         row["train_rankic"], row["valid_rankic"], row["test_rankic"],
         row["train_time"], row["dataset_version"]))


def pick_top1(results: list[dict]) -> dict:
    """选优纪律：valid_ic 降序 -> valid_rank_ic 降序 -> gap 升序 -> train_time 升序。

    ⚠️ 排序键不含任何 test/holdout 字段（防 test-set 泄漏，由测试守卫）。
    """
    def key(r: dict) -> tuple:
        gap = abs(r["train_ic"] - r["valid_ic"])
        return (-(r["valid_ic"] if r["valid_ic"] == r["valid_ic"] else -9),
                -(r["valid_rankic"] if r["valid_rankic"] == r["valid_rankic"] else -9),
                gap, r["train_time"])
    return sorted(results, key=key)[0]


def run_grid_search(df: pd.DataFrame, *, holdout_days: int = 60, test_days: int = 60,
                    gap_days: int = 5, num_boost_round: int = 60,
                    stopping_rounds: int = 15, record: bool = True,
                    dataset_version: str = "p1_2022plus",
                    base_params: dict | None = None) -> tuple[list[dict], dict]:
    """遍历 81 组参数。返回 (results, top1)。每组 persist=False（不落盘模型）。"""
    combos = list(itertools.product(*GRID.values()))
    assert len(combos) == 81
    settings = get_settings()
    results: list[dict] = []
    for i, (lr, leaves, ff, mcs) in enumerate(combos, 1):
        params = dict(base_params or {})
        params.update({"learning_rate": lr, "num_leaves": leaves,
                       "feature_fraction": ff, "min_child_samples": mcs})
        t0 = time.perf_counter()
        res = train_lgbm(df, horizon=5, holdout_days=holdout_days, test_days=test_days,
                         gap_days=gap_days, params=params, persist=False,
                         num_boost_round=num_boost_round,
                         stopping_rounds=stopping_rounds,
                         min_abs_rank_ic=0.0, top_k=60)
        train_time = time.perf_counter() - t0
        row = {
            "strategy": f"grid_{i:02d}", "trade_date": pd.Timestamp.now().date(),
            "model_version": "", "feature_version": FEATURE_VERSION, "top_k": 60,
            "params_json": json.dumps({"learning_rate": lr, "num_leaves": leaves,
                                       "feature_fraction": ff,
                                       "min_child_samples": mcs}),
            "train_ic": res["train_ic"], "valid_ic": res["valid_ic"],
            "test_ic": res["test_ic"], "train_rankic": res["train_rank_ic"],
            "valid_rankic": res["valid_rank_ic"], "test_rankic": res["test_rank_ic"],
            "train_time": train_time, "dataset_version": dataset_version,
            "params": params,
        }
        results.append(row)
        logger.info(f"[grid {i:02d}/81] lr={lr} leaves={leaves} ff={ff} mcs={mcs} "
                    f"valid_ic={res['valid_ic']:.4f} ({train_time:.1f}s)")
    if record:
        # 落库前统一校验 feature_version（与 orchestrator / screener 共享同一白名单，
        # 单一事实源；不合法则 fail-fast，不静默写库）。校验先于任何 DB 写入。
        for row in results:
            assert_known_feature_version(
                row["feature_version"], where="grid_search.run_grid_search")
        conn = sqlite3.connect(settings.SQLITE_PATH)
        for row in results:
            _record_run(conn, row)
        conn.commit()
        conn.close()
        logger.info(f"feature_runs 写入 {len(results)} 行")
    top1 = pick_top1(results)
    return results, top1


def main() -> None:
    parser = argparse.ArgumentParser(prog="grid_search.py")
    parser.add_argument("--features", default=FEATURE_VERSION, help="特征集版本目录")
    parser.add_argument("--holdout", type=int, default=252)
    parser.add_argument("--test-days", type=int, default=252)
    parser.add_argument("--num-boost-round", type=int, default=500)
    args = parser.parse_args()

    setup_logging()
    s = get_settings()
    feat_dir = s.DATA_ROOT / "features" / f"version={args.features}"
    parts = sorted(feat_dir.glob("year=*.parquet"))
    if not parts:
        raise SystemExit(f"features 不存在: {feat_dir}")
    df = pd.concat([pd.read_parquet(p) for p in parts], ignore_index=True)

    # 默认基线
    base = train_lgbm(df, horizon=5, holdout_days=args.holdout,
                      test_days=args.test_days, persist=False,
                      num_boost_round=args.num_boost_round)
    logger.info(f"[default] valid_ic={base['valid_ic']:.4f} test_ic={base['test_ic']:.4f}")

    results, top1 = run_grid_search(
        df, holdout_days=args.holdout, test_days=args.test_days,
        num_boost_round=args.num_boost_round)

    # Top-1 以持久化方式重训（模型落盘 + registry），test 仅此一次正式评估
    final = train_lgbm(df, horizon=5, holdout_days=args.holdout,
                       test_days=args.test_days, params=top1["params"],
                       version_suffix="grid_top1",
                       num_boost_round=args.num_boost_round)
    print(json.dumps({
        "default_valid_ic": base["valid_ic"], "default_test_ic": base["test_ic"],
        "best_valid_ic": top1["valid_ic"], "best_params": json.loads(top1["params_json"]),
        "final_model": final["version"],
        "final_test_ic": final["test_ic"],
        "rows_recorded": len(results),
    }, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
