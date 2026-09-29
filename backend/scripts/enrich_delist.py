"""回填退市名单到 instrument.delist_date（整改 Task 6 / 审计 §8.2 第 6 项）。

⚠️ 本脚本已**不是唯一入口**（这正是原缺陷：唯一调用方是手工脚本，``instrument.
delist_date`` 实测 0/5552 ⇒ 回测的退市剔除结构性空转）。同一实现
``app.data.ingest.tasks.enrich_delist_dates`` 现已由流水线步骤
``orchestrator.step_enrich_delist`` 每晚调用（FULL_STEPS / EVENING_STEPS，
位于 validate 之后、build_universe/build_universe_bt 之前）。本脚本仅用于人工
排障与立即补跑——两者行为完全一致（同一函数，无第二套逻辑）。

默认 dry-run：只取数并打印覆盖度，不写库、不写状态文件；--apply 才落库。
回填后如需让回测面板随之生效，重建 ``universe_daily_bt``
（``build_universe_backtest``，流水线里是 ``build_universe_bt`` 步骤）；
也可直接跑一次流水线，``build_universe_bt`` 紧随本步之后。

    python scripts/enrich_delist.py [--apply]
"""
from __future__ import annotations

import argparse
import asyncio
import json
import sys
from pathlib import Path

BACKEND_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(BACKEND_ROOT))

from app.core.logging import setup_logging  # noqa: E402
from app.data.ingest.tasks import enrich_delist_dates  # noqa: E402


def main() -> None:
    ap = argparse.ArgumentParser(prog="enrich_delist.py")
    ap.add_argument("--apply", action="store_true",
                    help="实际写库（默认 dry-run 只取数并打印覆盖度）")
    args = ap.parse_args()

    setup_logging()
    status = asyncio.run(enrich_delist_dates(apply=args.apply))
    print(json.dumps(status, ensure_ascii=False, indent=2))
    if status["availability"] != "ok":
        print(f"⚠️ 退市信息不可用/部分可用：availability={status['availability']} "
              f"reason={status['reason']}（覆盖率 {status['coverage_pct']}%，"
              f"退市剔除不可依赖）")
        return
    if not args.apply:
        print("dry-run：未写库、未写状态文件。加 --apply 执行回填。")


if __name__ == "__main__":
    main()