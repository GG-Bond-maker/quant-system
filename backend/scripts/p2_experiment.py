"""P2 真实数据一次性实验：全量 universe → 批量推理 → 分组回测 → 摩擦对比。"""
from __future__ import annotations

import sqlite3
import sys
from pathlib import Path

BACKEND_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(BACKEND_ROOT))

import pandas as pd
import polars as pl
from loguru import logger

from app.backtest.broker import BrokerConfig
from app.backtest.engine import run_backtest_comparison, run_group_backtest
from app.core.config import get_settings
from app.core.logging import setup_logging
from app.domain.limit import InstrumentAttrs, calc_limit_prices
from app.ml.infer import load_prod_model, predict_with_contrib


def main() -> None:
    setup_logging()
    s = get_settings()

    # ---- 1) 全量 daily_bar -> 向量化 universe（含真实涨跌停） ----
    files = sorted((s.DATA_ROOT / "daily_bar").rglob("year=*.parquet"))
    bars = pl.concat([pl.read_parquet(f) for f in files], how="diagonal_relaxed")
    bars = bars.with_columns(pl.col("date").cast(pl.Date)).sort(["symbol", "date"])
    bars = bars.with_columns(
        pl.col("close").shift(1).over("symbol").alias("prev_close"),
        (pl.col("volume") == 0).alias("is_halted"))
    bars_pd = bars.to_pandas()
    bars_pd = bars_pd[bars_pd["prev_close"].notna() & (bars_pd["prev_close"] > 0)].copy()

    conn = sqlite3.connect(s.SQLITE_PATH)
    ins_map = {r[0]: r for r in conn.execute(
        "SELECT symbol, code, name, is_st, list_date FROM instrument")}
    conn.close()

    limits: list[tuple[float, float, float]] = []
    for sym, pc, d in zip(bars_pd["symbol"], bars_pd["prev_close"], bars_pd["date"]):
        r = ins_map.get(sym, (sym, sym, "未分类", 0, "2015-01-01"))
        ld = pd.Timestamp(r[4]).date() if r[4] else None
        a = InstrumentAttrs(sym, r[1], bool(r[3]), ld)
        try:
            up, dn, pct = calc_limit_prices(pc, a, d)
        except Exception:
            up, dn, pct = 0.0, 0.0, 0.0
        limits.append((float(up), float(dn), float(pct)))
    bars_pd["limit_up"] = [l[0] for l in limits]
    bars_pd["limit_down"] = [l[1] for l in limits]
    bars_pd["limit_pct"] = [l[2] for l in limits]
    bars_pd["board"] = "all"
    bars_pd["is_st"] = bars_pd["symbol"].map(
        lambda x: bool(ins_map.get(x, (0, 0, 0, 0, 0))[3]))
    bars_pd["name"] = bars_pd["symbol"].map(lambda x: ins_map.get(x, ("", "", "", "", ""))[2])
    bars_pd["days_since_list"] = 999
    uni_pd = bars_pd[["date", "symbol", "name", "board", "is_st", "days_since_list",
                      "is_halted", "limit_pct", "limit_up", "limit_down",
                      "close", "volume"]].copy()
    uni_pd = uni_pd[uni_pd["close"] > 0].copy()
    # 统一日期类型（polars to_pandas 产出 Timestamp，sig 产出 date）
    uni_pd["date"] = pd.to_datetime(uni_pd["date"]).dt.date
    logger.info(f"universe rows={len(uni_pd)} dates={uni_pd['date'].nunique()}")

    # ---- 2) 全历史批量推理 ----
    feat_parts = sorted((s.DATA_ROOT / "features" / "version=alpha_basic_v1").glob("year=*.parquet"))
    feats = pd.concat([pd.read_parquet(p) for p in feat_parts], ignore_index=True)
    feats = feats.sort_values(["symbol", "date"])
    booster, feats_cols, model_dir = load_prod_model()
    pred, _ = predict_with_contrib(booster, feats, feats_cols)
    sig_pd = feats[["date", "symbol"]].copy()
    sig_pd["pred_score"] = pred
    sig_pd["date"] = pd.to_datetime(sig_pd["date"]).dt.date

    # ---- 3) 分组回测 ----
    res_g = run_group_backtest(uni_pd, sig_pd, groups=5, init_cash=1_000_000)
    print("=== P2-3 分组回测 ===")
    for q in range(1, 6):
        m = res_g["group_metrics"][f"Q{q}"]
        print(f"Q{q}: 年化={m['annual_return']:+.2%} 夏普={m['sharpe']:.2f} MDD={m['max_drawdown']:.2%}")
    print(f"月度单调比例: {res_g['monthly_monotonic_ratio']:.0%}")

    # ---- 4) 摩擦对比 ----
    raw, fri = run_backtest_comparison(
        uni_pd, sig_pd,
        friction=BrokerConfig(slippage_bps=5, decay_bps=10, impact_pct=0.02,
                              impact_linear_bps=30, enabled=True),
        init_cash=1_000_000, top_k=10)
    print("=== P2-4 摩擦对比 Top-10 ===")
    print(f"raw   年化={raw.metrics['annual_return']:+.2%} 夏普={raw.metrics['sharpe']:.2f} "
          f"MDD={raw.metrics['max_drawdown']:.2%}")
    print(f"frict 年化={fri.metrics['annual_return']:+.2%} 夏普={fri.metrics['sharpe']:.2f} "
          f"MDD={fri.metrics['max_drawdown']:.2%}")
    print(f"成本: {fri.friction_costs}")


if __name__ == "__main__":
    main()
