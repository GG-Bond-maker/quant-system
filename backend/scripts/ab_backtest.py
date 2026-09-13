"""A/B 回测对比 runner（整改计划 Task 1 交付，后续回测类任务复用）。

用固定参数跑一次真实数据回测，把 metrics 落成 JSON——修复前跑一次存
before，修复后同参数再跑存 after，diff 即修复的量化证据（协议见
docs/superpowers/plans/2026-09-05-整改计划.md「执行约定」）。

用法（worktree 内，DATA_ROOT 指向被审数据仓）：
    DATA_ROOT=D:/Python_Project/Alpha Quant Platform/data/parquet \
    python scripts/ab_backtest.py --start 2025-08-08 --end 2026-08-28 \
        --top-k 10 --out ../../docs/audit/ab/task1-before.json

只读运行：除 --out 写 JSON 外不写任何数据文件（信号/universe 由既有
数据集加载，不触发构建）。
"""
from __future__ import annotations

import argparse
import json
import math
import sys
from datetime import date
from pathlib import Path

BACKEND_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(BACKEND_ROOT))

from app.api.v1.backtest import _load_universe_and_signals  # noqa: E402
from app.backtest.engine import run_backtest  # noqa: E402
from app.core.config import get_settings  # noqa: E402
from app.core.logging import setup_logging  # noqa: E402


def _jsonable(v: object) -> object:
    """NaN/Inf -> None，其余原样（orjson/eval 友好）。"""
    if isinstance(v, float) and not math.isfinite(v):
        return None
    return v


def main() -> None:
    ap = argparse.ArgumentParser(prog="ab_backtest.py")
    ap.add_argument("--start", default="2025-08-08")
    ap.add_argument("--end", default="2026-08-28")
    ap.add_argument("--top-k", type=int, default=10)
    ap.add_argument("--init-cash", type=float, default=1_000_000)
    ap.add_argument("--rebalance-freq", default="daily", choices=["daily", "weekly"])
    ap.add_argument("--weighting", default="equal")
    ap.add_argument("--enable-friction", action="store_true")
    ap.add_argument("--slippage-bps", type=float, default=5.0)
    ap.add_argument("--impact-model", default="sqrt", choices=["linear", "sqrt"])
    ap.add_argument("--impact-sqrt-coef-bps", type=float, default=10.0)
    ap.add_argument("--max-participation", type=float, default=0.05)
    ap.add_argument("--out", required=True, help="输出 JSON 路径")
    args = ap.parse_args()

    setup_logging()
    s = get_settings()
    uni_dir = s.DATA_ROOT / "universe_daily" / "symbol=__all__"
    pred_dir = s.DATA_ROOT / "predictions"
    uni_files = sorted(uni_dir.glob("year=*.parquet")) if uni_dir.exists() else []
    pred_files = sorted(pred_dir.glob("date=*.parquet")) if pred_dir.exists() else []
    if not uni_files or not pred_files:
        raise SystemExit(
            f"数据不可用：universe_daily={len(uni_files)} 个年分区, "
            f"predictions={len(pred_files)} 个日分区（DATA_ROOT={s.DATA_ROOT}）。"
            "请在主仓数据仓环境下运行（设 DATA_ROOT 环境变量）。")

    uni, sig, used_versions = _load_universe_and_signals(args.start, args.end)
    friction = None
    if args.enable_friction:
        from app.backtest.broker import BrokerConfig

        friction = BrokerConfig(slippage_bps=args.slippage_bps,
                                impact_model=args.impact_model,
                                impact_sqrt_coef_bps=args.impact_sqrt_coef_bps,
                                max_participation=args.max_participation,
                                enabled=True)
    res = run_backtest(uni.to_pandas(), sig.to_pandas(),
                       init_cash=args.init_cash, top_k=args.top_k,
                       rebalance_freq=args.rebalance_freq,
                       weighting=args.weighting, friction=friction)
    if res.nav_df.empty:
        raise SystemExit("回测无结果（universe/信号区间为空）")

    rejects: dict[str, int] = {}
    for t in res.trades:
        if t["reason"] != "filled":
            rejects[t["reason"]] = rejects.get(t["reason"], 0) + 1

    out = {
        "params": {"start": args.start, "end": args.end, "top_k": args.top_k,
                   "init_cash": args.init_cash,
                   "rebalance_freq": args.rebalance_freq,
                   "weighting": args.weighting,
                   "enable_friction": args.enable_friction,
                   "max_participation": args.max_participation},
        "friction_costs": {k: round(float(v), 2)
                           for k, v in res.friction_costs.items()},
        "data_provenance": {"used_model_versions": used_versions,
                            "universe_dir": str(uni_dir),
                            "universe_years": len(uni_files),
                            "pred_files": len(pred_files)},
        "n_days": len(res.nav_df),
        "nav_last": round(float(res.nav_df["nav"].iloc[-1]), 6),
        "filled_trades": sum(1 for t in res.trades if t["reason"] == "filled"),
        "rejected_trades": rejects,
        "metrics": {k: _jsonable(v) for k, v in res.metrics.items()},
    }
    out_path = Path(args.out)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(json.dumps(out, ensure_ascii=False, indent=1),
                        encoding="utf-8")
    print(f"AB runner done -> {out_path}")
    print(f"  nav_last={out['nav_last']} sharpe={out['metrics'].get('sharpe')} "
          f"annual={out['metrics'].get('annual_return')} "
          f"mdd={out['metrics'].get('max_drawdown')} "
          f"filled={out['filled_trades']}")


if __name__ == "__main__":
    main()
