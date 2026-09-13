"""真实数据回测（第十三/十四阶段）。

1) 用**生产模型**对回测区间内每个交易日生成预测信号；
2) 读取 universe_daily（含涨跌停/停牌等真实交易约束）；
3) 跑真实 Broker/Engine；
4) 输出绩效 + A 股规则命中统计 + 仓位利用率。

    python scripts/run_backtest_real.py --start 2025-08-08 --end 2026-08-21 --top-k 10
"""
from __future__ import annotations

import argparse
import json
import sys
from datetime import date
from pathlib import Path

BACKEND_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(BACKEND_ROOT))

import numpy as np  # noqa: E402
import pandas as pd  # noqa: E402
import polars as pl  # noqa: E402

from app.backtest.engine import run_backtest  # noqa: E402
from app.data.parquet_store import atomic_write_parquet  # noqa: E402
from app.core.config import get_settings  # noqa: E402
from app.core.logging import setup_logging  # noqa: E402
from app.ml.features import FEATURE_VERSION  # noqa: E402
from app.ml.infer import load_prod_model, predict_with_contrib  # noqa: E402
from app.ml.registry import prod_model_info  # noqa: E402


def generate_predictions(start: date, end: date, out_dir: Path) -> int:
    """用生产模型对 [start, end] 内每个交易日生成预测。"""
    s = get_settings()
    booster, feats, model_dir = load_prod_model()
    parts = sorted((s.DATA_ROOT / "features" / f"version={FEATURE_VERSION}")
                   .glob("year=*.parquet"))
    df = pd.concat([pd.read_parquet(p) for p in parts], ignore_index=True)
    df = df[(pd.to_datetime(df["date"]) >= pd.Timestamp(start))
            & (pd.to_datetime(df["date"]) <= pd.Timestamp(end))]
    if df.empty:
        raise SystemExit(f"区间 {start}~{end} 无特征数据")

    out_dir.mkdir(parents=True, exist_ok=True)
    n = 0
    for d, g in df.groupby(df["date"].astype(str).str[:10]):
        pred, _ = predict_with_contrib(booster, g, feats)
        out = g[["date", "symbol"]].copy()
        out["pred_score"] = pred
        out["model_version"] = model_dir.name
        atomic_write_parquet(out_dir / f"date={d.replace('-', '')}.parquet", out)
        n += 1
    print(f"生成预测：{n} 个交易日，模型={model_dir.name}")
    return n


def load_universe(start: date, end: date) -> pd.DataFrame:
    s = get_settings()
    # Task 1（整改）：与 API 同源——hfq 口径回测宇宙（除权缺口不再成为虚假亏损）
    base = s.DATA_ROOT / "universe_daily_bt" / "symbol=__all__"
    files = sorted(base.glob("year=*.parquet"))
    if not files:
        raise SystemExit("universe_daily_bt 为空：请先构建 hfq 回测宇宙——"
                         "python -c \"from app.data.universe import "
                         "build_universe_backtest; build_universe_backtest()\"")
    df = pl.concat([pl.read_parquet(f) for f in files], how="vertical_relaxed")
    if df.schema["date"] != pl.Date:
        df = df.with_columns(pl.col("date").cast(pl.Date))
    df = df.filter((pl.col("date") >= start) & (pl.col("date") <= end))
    return df.to_pandas()


def main() -> None:
    ap = argparse.ArgumentParser(prog="run_backtest_real.py")
    ap.add_argument("--start", default="2025-08-08")
    ap.add_argument("--end", default="2026-08-21")
    ap.add_argument("--top-k", type=int, default=10)
    ap.add_argument("--init-cash", type=float, default=1_000_000)
    ap.add_argument("--friction", action="store_true", help="启用滑点/衰减/冲击成本")
    ap.add_argument("--json", default=None)
    args = ap.parse_args()

    setup_logging()
    s = get_settings()
    start, end = date.fromisoformat(args.start), date.fromisoformat(args.end)

    info = prod_model_info()
    if not info.get("has_production"):
        raise SystemExit("没有生产模型，请先 scripts/retrain.py --promote")
    print("=" * 74)
    print(f"生产模型: {info['version']}  feature={info['feature_version']}")
    print(f"训练期: {info['train_period']}   valid 期: {info['valid_period']}")
    print(f"valid_rank_ic={info['valid_rank_ic']:.4f}  "
          f"test_rank_ic={info['test_rank_ic']:.4f}")
    print("=" * 74)

    pred_dir = s.DATA_ROOT / "predictions"
    n = generate_predictions(start, end, pred_dir)

    # ---- 信号 ----
    sig_files = sorted(pred_dir.glob("date=*.parquet"))
    sig = pl.concat([pl.read_parquet(f).select(["date", "symbol", "pred_score"])
                     for f in sig_files], how="vertical_relaxed")
    if sig.schema["date"] != pl.Date:
        sig = sig.with_columns(pl.col("date").cast(pl.Date))
    sig_pd = sig.to_pandas()

    # ---- universe ----
    uni_pd = load_universe(start, end)
    print(f"universe: rows={len(uni_pd)} dates={uni_pd['date'].nunique()} "
          f"symbols={uni_pd['symbol'].nunique()}")
    print(f"signal  : rows={len(sig_pd)} dates={sig_pd['date'].nunique()}")

    from app.backtest.broker import BrokerConfig

    friction = (BrokerConfig(enabled=True) if args.friction else None)
    res = run_backtest(uni_pd, sig_pd, init_cash=args.init_cash,
                       top_k=args.top_k, rebalance_freq="daily",
                       friction=friction)

    if res.nav_df.empty:
        raise SystemExit("回测无结果")

    # ---- 绩效 ----
    nav = res.nav_df["nav"].to_numpy(dtype=float)
    rets = pd.Series(nav).pct_change().dropna()
    n_days = len(nav)
    years = n_days / 244.0
    total_ret = nav[-1] / nav[0] - 1.0
    ann_ret = (1 + total_ret) ** (1 / years) - 1 if years > 0 else 0.0
    vol = float(rets.std() * np.sqrt(244)) if len(rets) else 0.0
    sharpe = float(ann_ret / vol) if vol > 0 else 0.0
    peak = np.maximum.accumulate(nav)
    mdd = float(np.max((peak - nav) / peak))
    win_rate = float((rets > 0).mean()) if len(rets) else 0.0
    turnover = float(res.nav_df["turnover"].mean())
    util = float(((res.nav_df["equity"] - res.nav_df["cash"])
                  / res.nav_df["equity"]).mean())

    rejects: dict[str, int] = {}
    for t in res.trades:
        if t["reason"] != "filled":
            rejects[t["reason"]] = rejects.get(t["reason"], 0) + 1
    filled = [t for t in res.trades if t["reason"] == "filled"]

    print()
    print("=" * 74)
    print(f"回测区间 {start} ~ {end}  top_k={args.top_k}  "
          f"摩擦={'开' if args.friction else '关'}")
    print("=" * 74)
    print(f"  交易日数     = {n_days}")
    print(f"  累计收益     = {total_ret:+.2%}")
    print(f"  年化收益     = {ann_ret:+.2%}")
    print(f"  年化波动     = {vol:.2%}")
    print(f"  Sharpe       = {sharpe:.3f}")
    print(f"  最大回撤     = {mdd:.2%}")
    print(f"  日胜率       = {win_rate:.2%}")
    print(f"  日均换手     = {turnover:.2%}")
    print(f"  平均仓位利用率 = {util:.2%}")
    print(f"  成交笔数     = {len(filled)}   拒绝笔数 = {sum(rejects.values())}")
    print(f"  拒绝原因分布 = {rejects}")

    # ---- 等权校验 ----
    print()
    print("=" * 74)
    print("等权校验：按**市值**比较（等权 = 等金额，不是等股数；")
    print("         不同股价不同，股数天然不相等）")
    print("=" * 74)
    # 用当日收盘价把股数折成市值
    u = uni_pd.assign(d=lambda x: pd.to_datetime(x["date"]).dt.date)
    px_close = u.set_index(["d", "symbol"])["close"]
    px_open = u.set_index(["d", "symbol"])["open"]

    def _dev(h: dict, d, px) -> tuple[float, int] | None:
        vals = []
        for sym, qty in h.items():
            try:
                p = float(px.loc[(d, sym)])
            except (KeyError, TypeError):
                continue
            if p and p == p:  # noqa: PLR0124
                vals.append(qty * p)
        if len(vals) < 2:
            return None
        return (max(vals) - min(vals)) / max(vals), len(vals)

    worst_open = worst_close = 0.0
    rows = []
    for h in reversed(res.holdings_history):
        if len(h["holdings"]) <= 1:
            continue
        d = pd.Timestamp(h["date"]).date()
        a, b = _dev(h["holdings"], d, px_open), _dev(h["holdings"], d, px_close)
        if not a or not b:
            continue
        worst_open, worst_close = max(worst_open, a[0]), max(worst_close, b[0])
        rows.append((d, a[1], a[0], b[0]))
        if len(rows) >= 5:
            break

    print(f"  {'日期':<12}{'n':>3}{'开盘时偏差':>12}{'收盘时偏差':>12}")
    for d, n, o, c in rows:
        print(f"  {str(d):<12}{n:>3}{o:>12.2%}{c:>12.2%}")
    print(f"\n  最大偏差：开盘 {worst_open:.2%} / 收盘 {worst_close:.2%}")
    print("  解读：开盘偏差才反映资金分配质量；收盘偏差额外包含当日价格波动。")
    print("        剩余残差主要来自 A 股 100 股整手粒度（单笔约 9 万时，")
    print("        1 手在高价股上可占该仓位的 10% 以上，属于制度性下限）。")

    if args.json:
        Path(args.json).write_text(json.dumps({
            "production_model": info["version"],
            "feature_version": info["feature_version"],
            "period": [str(start), str(end)],
            "top_k": args.top_k, "friction": args.friction,
            "trading_days": n_days, "prediction_days": n,
            "total_return": round(float(total_ret), 6),
            "annual_return": round(float(ann_ret), 6),
            "volatility": round(vol, 6), "sharpe": round(sharpe, 4),
            "max_drawdown": round(mdd, 6), "win_rate": round(win_rate, 6),
            "avg_turnover": round(turnover, 6),
            "avg_utilization": round(util, 6),
            "filled_trades": len(filled), "rejected": rejects,
            "engine_metrics": {k: (float(v) if isinstance(v, (int, float, np.floating))
                                   else v) for k, v in res.metrics.items()},
            "friction_costs": res.friction_costs,
        }, ensure_ascii=False, indent=2, default=str), encoding="utf-8")
        print(f"\n结果已导出: {args.json}")


if __name__ == "__main__":
    main()
