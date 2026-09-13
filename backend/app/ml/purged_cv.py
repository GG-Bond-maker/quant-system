"""Purged Group TimeSeries Split（防泄漏交叉验证，López de Prado 口径）。

为什么不能用传统 K-Fold：
    标签 label(t) = close[t + horizon] / close[t] - 1 覆盖未来 horizon 个
    交易日。若 train 末尾与 test 开头间隔 < horizon（或样本按标的分组重叠），
    train 样本会"看到"test 期间的价格 —— 数据渗漏（data leakage），
    交叉验证分数虚高、线上表现崩塌。

本模块实现两道防线：
    Purge  ：剔除训练集中标签窗口与测试集重叠的样本（train 末尾 horizon 天）；
    Embargo ：在 train 与 test 之间再留出 embargo_days 个交易日的禁运期
              （吸收特征中的滚动窗口记忆 + 序列相关性）。

时间轴示意（n_splits=2, purge=5, embargo=2, 按"日期组"切分）::

    |---- train ----|-purge-|-embargo-|---- test ----|
    |---- train+valid -----|-purge-|-embargo-|-- test --|   （后续折外推）

另提供样本加权（sample weights）：按波动率倒数加权，抑制高噪声
（微盘/高波动）股票对损失的支配。
"""
from __future__ import annotations

from collections.abc import Iterator
from typing import Any

import numpy as np
import pandas as pd


class PurgedGroupTimeSeriesSplit:
    """带 Purge + Embargo 的时序交叉验证（groups = 日期，逐折外推）。

    与 sklearn 风格一致：split(X, y, groups) -> Iterator[(train_idx, test_idx)]。
    组（日期）必须可排序；训练集单调扩张（前向链条 expanding window）。
    """

    def __init__(self, n_splits: int = 5, purge_window: int = 5,
                 embargo_window: int = 2):
        if n_splits < 1:
            raise ValueError("n_splits 必须 >= 1")
        if purge_window < 0 or embargo_window < 0:
            raise ValueError("purge_window / embargo_window 必须 >= 0")
        self.n_splits = n_splits
        self.purge_window = purge_window
        self.embargo_window = embargo_window

    def split(self, X: Any = None, y: Any = None,
              groups: Any = None) -> Iterator[tuple[np.ndarray, np.ndarray]]:
        if groups is None:
            raise ValueError("groups（日期数组）不能为空")
        g = np.asarray(groups)
        unique_dates = np.unique(g)          # 升序
        n_dates = len(unique_dates)
        # 每折 test 的组数；首个 train 至少保留 2 个 test 段的组数
        group_size = n_dates // (self.n_splits + 2)
        if group_size < 1:
            raise ValueError(
                f"日期组数不足：{n_dates} 组无法做 {self.n_splits} 折 "
                f"(purge={self.purge_window}, embargo={self.embargo_window})")

        embargo = self.embargo_window + self.purge_window   # 两道防线合并为隔离带
        for i in range(self.n_splits):
            test_start = group_size * (i + 2)
            test_end = test_start + group_size
            if test_end > n_dates:
                break
            train_end = test_start - embargo                # purge + embargo
            if train_end < group_size:
                continue
            train_dates = unique_dates[:train_end]
            test_dates = unique_dates[test_start:test_end]
            train_idx = np.where(np.isin(g, train_dates))[0]
            test_idx = np.where(np.isin(g, test_dates))[0]
            yield train_idx, test_idx

    def get_n_splits(self, X: Any = None, y: Any = None, groups: Any = None) -> int:
        return self.n_splits


def verify_no_overlap(train_idx: np.ndarray, test_idx: np.ndarray,
                      groups: np.ndarray, horizon: int) -> bool:
    """验收：train 末组与 test 首组的间隔 >= horizon（标签不重叠）。

    组必须是可比较的日期（升序 unique 后按位置算间隔）。
    """
    uniq = np.unique(groups)
    pos = {d: i for i, d in enumerate(uniq)}
    train_last = max(pos[d] for d in groups[train_idx])
    test_first = min(pos[d] for d in groups[test_idx])
    return test_first - train_last >= horizon


def cv_rank_ic_report(
    df: pd.DataFrame,
    feature_cols: list[str],
    label_col: str,
    fit_predict_fn,
    n_splits: int = 5,
    purge_window: int = 5,
    embargo_window: int = 2,
) -> dict[str, Any]:
    """跑完整 Purged CV 并汇总逐折 Rank IC（与训练口径一致的验收入口）。

    :param fit_predict_fn: (X_train, y_train, X_test) -> np.ndarray 预测值
                           （如 LightGBM/XGBoost 的训练-预测闭包）
    :return: {fold_rank_ics, mean_rank_ic, std_rank_ic, n_folds,
              min_gap_ok(全部折是否满足隔离带 >= purge+embargo)}
    """
    if df.empty or not feature_cols:
        raise ValueError("df / feature_cols 不能为空")
    data = df.sort_values(["date", "symbol"]).reset_index(drop=True)
    groups = data["date"].to_numpy()
    cv = PurgedGroupTimeSeriesSplit(n_splits=n_splits, purge_window=purge_window,
                                    embargo_window=embargo_window)
    fold_ics: list[float] = []
    gaps_ok = True
    for fold, (tr, te) in enumerate(cv.split(groups=groups)):
        if not verify_no_overlap(tr, te, groups, horizon=purge_window + embargo_window):
            gaps_ok = False
        X_tr, y_tr = data.loc[tr, feature_cols], data.loc[tr, label_col]
        X_te = data.loc[te, feature_cols]
        pred = np.asarray(fit_predict_fn(X_tr, y_tr, X_te), dtype=np.float64)
        y_te = data.loc[te, label_col].to_numpy(dtype=np.float64)
        sub = pd.DataFrame({"pred": pred, "y": y_te, "date": groups[te]})
        ics = [
            sub.loc[g.index, "pred"].corr(g["y"], method="spearman")
            for _, g in sub.groupby("date") if len(g) >= 5
        ]
        ics = [float(ic) for ic in ics if pd.notna(ic)]
        if ics:
            fold_ics.append(float(np.mean(ics)))
    if not fold_ics:
        return {"fold_rank_ics": [], "mean_rank_ic": float("nan"),
                "std_rank_ic": float("nan"), "n_folds": 0, "min_gap_ok": gaps_ok}
    return {
        "fold_rank_ics": fold_ics,
        "mean_rank_ic": float(np.mean(fold_ics)),
        "std_rank_ic": float(np.std(fold_ics, ddof=1)) if len(fold_ics) > 1 else 0.0,
        "n_folds": len(fold_ics),
        "min_gap_ok": gaps_ok,
    }


def volatility_inverse_weights_from_close(
    df: pd.DataFrame, symbol_col: str = "symbol", window: int = 20,
    min_periods: int = 10, out_col: str = "sample_weight",
) -> pd.DataFrame:
    """按 symbol 分组的波动率倒数样本加权（df 需含 symbol/close，升序）。

    w_i = 1 / (σ_i × sqrt(252))；σ 不可估时取该股票可得序列的中位权重。
    """
    out = df.copy()
    ret = out.groupby(symbol_col)["close"].pct_change()
    vol = ret.groupby(out[symbol_col]).transform(
        lambda s: s.rolling(window, min_periods=min_periods).std() * np.sqrt(252))
    inv = 1.0 / vol.clip(lower=1e-6)
    inv = inv.fillna(inv.median() if np.isfinite(inv.median()) else 1.0)
    out[out_col] = inv
    return out
