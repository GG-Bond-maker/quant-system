"""派生数据全量重建：截面镜像 -> universe -> ML 特征 -> 当日推理（批量抓取后收尾）。

背景：daily_bar 扩容后，四套派生数据仍是旧标的池的产物——
  1. cs mirror（daily_bar 三口径截面分区，选股中心数据前提，MED-003）
  2. universe_daily（股票池/Top-K 回测前提）
  3. features/version=alpha_basic_v1（训练 readiness / GNN-TFT-LGBM 的输入）
  4. predictions/date=当日（选股中心榜单起点；不重跑则 pool_size 还是旧池）
本脚本按依赖顺序逐一重建并计时，幂等（重复跑只是覆盖同分区）。
注意 infer 依赖生产模型（model_registry is_production=1）与当日特征。

用法（backend 目录取虚拟环境）：
    python ../scripts/rebuild_derivatives.py                        # 全部四步
    python ../scripts/rebuild_derivatives.py --only features mirror
"""
from __future__ import annotations

import argparse
import sys
import time
from datetime import date
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT / "backend"))

from app.core.config import get_settings  # noqa: E402
# STEP_FUNCTIONS 定义在 app.orchestrator（app.data.pipeline 只有同名 step_* 函数）
from app.orchestrator import STEP_FUNCTIONS  # noqa: E402


def main() -> None:
    ap = argparse.ArgumentParser(description="派生数据全量重建（镜像/universe/特征/推理）")
    ap.add_argument("--only", nargs="+",
                    default=["mirror", "universe", "features", "infer"],
                    choices=["mirror", "universe", "features", "infer"],
                    help="只跑指定步骤（默认全部，按依赖顺序）")
    ap.add_argument("--date", default=None, help="记账交易日 YYYY-MM-DD（默认今天）")
    args = ap.parse_args()

    td = date.fromisoformat(args.date) if args.date else date.today()

    runners = {
        "mirror": lambda: STEP_FUNCTIONS["build_cs_mirror"](td, []),
        "universe": lambda: STEP_FUNCTIONS["build_universe"](td, []),
        "features": lambda: STEP_FUNCTIONS["build_features"](td, []),
        "infer": lambda: STEP_FUNCTIONS["infer"](td, []),
    }
    results: dict[str, str] = {}
    failed: dict[str, str] = {}
    for name in args.only:
        t0 = time.time()
        print(f"[{name}] start ...", flush=True)
        try:
            detail = runners[name]()
            results[name] = detail
            print(f"[{name}] ok: {detail}  ({time.time()-t0:.1f}s)", flush=True)
        except Exception as e:  # noqa: BLE001
            failed[name] = f"{type(e).__name__}: {e}"[:300]
            print(f"[{name}] FAILED {failed[name]}", flush=True)

    print(f"\n=== 重建完成：成功 {len(results)} / 失败 {len(failed)} ===", flush=True)
    if failed:
        print("失败步骤:", failed, flush=True)
        sys.exit(1)
    # 收尾提示：features 落盘后 readiness/screener 的进程内缓存需失效或重启后端
    print(f"提示：如后端在运行，请调用 POST /datacenter/sync 无副作用地换缓存，"
          f"或重启后端让 features/universe 新分区生效"
          f"（DATA_ROOT={get_settings().DATA_ROOT}）", flush=True)


if __name__ == "__main__":
    main()
