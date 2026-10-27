"""Rolling historical models fitted exclusively on the data before each fold."""
from __future__ import annotations

from datetime import date
from pathlib import Path
from uuid import uuid4

import lightgbm as lgb
import pandas as pd

from .signal_contract import predict_panel, write_predictions
from .train_lgbm import train_lgbm


def walk_forward_predictions(panel: pd.DataFrame, start: date, end: date,
                             output_dir: Path, *, horizon: int = 5, step_days: int = 63,
                             holdout_days: int = 63, test_days: int = 21,
                             num_boost_round: int = 300) -> pd.DataFrame:
    """Simulate a separate available model per fold; never promote or touch production signals."""
    if step_days < 1 or start > end:
        raise ValueError("滚动区间或步长无效")
    frame = panel.copy()
    frame["date"] = pd.to_datetime(frame["date"])
    dates = sorted(frame.loc[frame["date"].dt.date.between(start, end), "date"].unique())
    run_id = uuid4().hex
    parts = []
    for offset in range(0, len(dates), step_days):
        fold = dates[offset:offset + step_days]
        history = frame[frame["date"] < fold[0]].copy()
        if history.empty:
            raise ValueError("滚动训练窗口没有历史样本")
        cutoff = history["date"].max().date()
        metrics = train_lgbm(history, horizon=horizon, holdout_days=holdout_days,
            test_days=test_days, gap_days=horizon, simulation_asof=cutoff,
            version_suffix=f"wf_{offset}_{run_id[:8]}", num_boost_round=num_boost_round,
            stopping_rounds=20, persist=True)
        model_dir = Path(metrics["model_path"]).parent
        booster = lgb.Booster(model_file=str(metrics["model_path"]))
        signals = predict_panel(frame[frame["date"].isin(fold)], booster, metrics["kept_features"], model_dir, run_id)
        signals["evaluation_kind"] = "walk_forward"
        write_predictions(signals, output_dir)
        parts.append(signals)
    if not parts:
        raise ValueError("滚动测试窗口无特征数据")
    return pd.concat(parts, ignore_index=True)
