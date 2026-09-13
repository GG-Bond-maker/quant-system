"""序列样本构建（3-1 时序模型数据前置：2D 截面 → 3D 张量）。

把 features 长表转成 (Batch, Time_Steps, Features) 张量供 TFT/iTransformer
等时序模型消费；切分沿用平台纪律（purged：段间 gap = horizon，杜绝标签跨
界泄漏）。纯 numpy/pandas，无 torch 依赖——数据没扩池前也能先行验证管线。

样本定义（每个 symbol 独立、时间连续）：
    窗口 = [i-lookback+1, i] 的 F 个特征；标签 = close[i+horizon]/close[i] - 1
    含 NaN 的窗口/标签整行剔除（不造数），剔除比例如实返回。
"""
from __future__ import annotations

import numpy as np
import pandas as pd
from numpy.lib.stride_tricks import sliding_window_view


def build_sequences(
    features: pd.DataFrame,
    lookback: int = 30,
    horizon: int = 5,
    feature_cols: list[str] | None = None,
    max_rows_per_symbol: int | None = None,
) -> dict:
    """features 长表 → 序列样本。

    Args:
        features: pd.DataFrame(symbol, date, <features...>, close)——close 用于
            前向收益标签（hfq 口径，与 alpha_basic_v1 一致）。
        lookback: 时序窗口长度（建议 20~60 个交易日）。
        horizon: 标签前向天数（与 ML_LABEL_HORIZON 对齐）。
        feature_cols: 缺省用除 symbol/date/close/label_ret 外全部数值列。
        max_rows_per_symbol: 每标的最多取最近 N 个样本（None = 全部）。

    Returns:
        {X: (N,L,F) float32, y: (N,) float32, dates: (N,) datetime64,
         symbols: (N,) object, feature_cols, n_dropped, n_raw}
    """
    if feature_cols is None:
        skip = {"symbol", "date", "close", "label_ret"}
        feature_cols = [c for c in features.columns
                        if c not in skip and pd.api.types.is_numeric_dtype(features[c])]
    if "close" not in features.columns:
        raise ValueError("features 缺少 close 列（标签需要 hfq close）")
    if lookback < 2 or horizon < 1:
        raise ValueError("lookback>=2 且 horizon>=1")

    df = features.copy()
    df["date"] = pd.to_datetime(df["date"])
    df = df.sort_values(["symbol", "date"])

    X_list, y_list, d_list, s_list = [], [], [], []
    n_raw = 0
    for sym, g in df.groupby("symbol", sort=False):
        vals = g[feature_cols].to_numpy(dtype=np.float64)
        closes = g["close"].to_numpy(dtype=np.float64)
        dates = g["date"].to_numpy()
        n = len(g)
        if n <= lookback + horizon:
            continue
        fwd = np.full(n, np.nan)
        fwd[:n - horizon] = closes[horizon:] / closes[:n - horizon] - 1.0
        # 滑窗一次成型：wins[k] = vals[k : k+lookback]，对应标签 fwd[k+lookback-1]。
        # 逐样本切片在 5000 只 ×1131 日是千万级 Python 迭代，必须避免。
        wins = np.ascontiguousarray(
            sliding_window_view(vals, lookback, axis=0).transpose(0, 2, 1))
        labels = fwd[lookback - 1:]
        n_raw += len(labels)
        ok = np.isfinite(wins).all(axis=(1, 2)) & np.isfinite(labels)
        if not ok.any():
            continue
        X_list.append(wins[ok].astype(np.float32))
        y_list.append(labels[ok].astype(np.float32))
        d_list.append(dates[lookback - 1:][ok])
        s_list.append(np.full(int(ok.sum()), sym, dtype=object))

    if X_list:
        X = np.concatenate(X_list, axis=0)
        y = np.concatenate(y_list)
        ds = np.concatenate(d_list)
        ss = np.concatenate(s_list)
    else:
        X = np.empty((0, lookback, len(feature_cols)), dtype=np.float32)
        y = np.empty(0, dtype=np.float32)
        ds = np.empty(0, dtype="datetime64[ns]")
        ss = np.empty(0, dtype=object)

    if max_rows_per_symbol and len(y):
        # 样本已按 (symbol, 日期) 有序，取每组末 N 条即"最近 N 个样本"
        order = pd.DataFrame({"s": ss}).groupby("s", sort=False).cumcount()
        cnt = pd.Series(ss).groupby(ss, sort=False).transform("size").to_numpy()
        keep = np.flatnonzero(cnt - order.to_numpy() - 1 < max_rows_per_symbol)
        X, y, ds, ss = X[keep], y[keep], ds[keep], ss[keep]
    return {"X": X, "y": y, "dates": ds, "symbols": ss,
            "feature_cols": feature_cols,
            "n_raw": n_raw, "n_dropped": n_raw - len(y)}


def purged_time_split(dates: np.ndarray, holdout_days: int = 252,
                      gap_days: int = 5, test_days: int = 0) -> dict[str, np.ndarray]:
    """按交易日切分 train/valid(/test)，段间 gap（与 train_lgbm 同纪律）。

    日期升序去重后：test 取最末 test_days 天（可关）；valid 紧接其前
    holdout_days 天；train = 早于 valid 起点 - gap 的全部样本。
    段界由「交易日天数」而非样本数决定，gap 防标签跨段泄漏。
    """
    uniq = pd.DatetimeIndex(pd.unique(pd.Series(pd.to_datetime(dates)))).sort_values()
    n = len(uniq)
    # 先把"日期 → 交易日序号"映射出来，之后每段只是一次 O(N) 比较；
    # 逐样本做 set 查找在百万级样本上会成为瓶颈。
    pos = pd.Series(np.arange(n), index=uniq).reindex(
        pd.to_datetime(pd.Series(dates))).to_numpy()

    def indices(lo: int, hi: int) -> np.ndarray:
        return np.flatnonzero((pos >= lo) & (pos < hi))

    test_start = n - test_days if test_days else n
    valid_start = max(0, test_start - holdout_days)
    train_end = max(0, valid_start - gap_days)
    out = {"train": indices(0, train_end),
           "valid": indices(valid_start, test_start)}
    if test_days:
        out["test"] = indices(test_start, n)
    return out
