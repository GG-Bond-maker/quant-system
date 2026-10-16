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


class SequenceArray:
    """对标主流量化系统（如 Microsoft Qlib TSDataSampler）的高性能时序窗口视图。

    设计：
    1. 零全量物化复制：底层仅引用唯一的 2D 特征矩阵 vals (N_rows, F)，内存开销固定（~2.2 GB）；
    2. 轻量索引寻址：每个 3D 样本仅保存一个一维起始行号 row_starts (N_samples,)，仅占 ~10 MB；
    3. 按需切片与批处理兼容：支持与 NumPy ndarray 相同的切片、多维索引、len、shape、dtype 以及 np.asarray/to_numpy 接口；
    4. 彻底杜绝 30 倍滑窗数据复制导致的 24~60 GiB 连续物理内存申请与 MemoryError。
    """

    def __init__(self, vals: np.ndarray, row_starts: np.ndarray, lookback: int):
        self.vals = vals
        self.row_starts = np.asarray(row_starts, dtype=np.int64)
        self.lookback = lookback

    @property
    def shape(self) -> tuple[int, int, int]:
        n_features = self.vals.shape[1] if self.vals.ndim > 1 else 0
        return (len(self.row_starts), self.lookback, n_features)

    @property
    def dtype(self):
        return self.vals.dtype

    @property
    def ndim(self) -> int:
        return 3

    @property
    def size(self) -> int:
        n_features = self.vals.shape[1] if self.vals.ndim > 1 else 0
        return len(self.row_starts) * self.lookback * n_features

    def __len__(self) -> int:
        return len(self.row_starts)

    def __getitem__(self, idx):
        if isinstance(idx, (int, np.integer)):
            start = self.row_starts[idx]
            return self.vals[start : start + self.lookback]
        else:
            return SequenceArray(self.vals, self.row_starts[idx], self.lookback)

    def to_numpy(self, dtype=None) -> np.ndarray:
        """显式转换为 3D NumPy ndarray（按需物化，仅在明确需要全局密集张量时调用）。"""
        if len(self.row_starts) == 0:
            n_features = self.vals.shape[1] if self.vals.ndim > 1 else 0
            return np.empty((0, self.lookback, n_features), dtype=dtype or self.dtype)
        out = np.empty(self.shape, dtype=dtype or self.dtype)
        for i, start in enumerate(self.row_starts):
            out[i] = self.vals[start : start + self.lookback]
        return out

    def __array__(self, dtype=None):
        return self.to_numpy(dtype)


def build_sequences(
    features: pd.DataFrame,
    lookback: int = 30,
    horizon: int = 5,
    feature_cols: list[str] | None = None,
    max_rows_per_symbol: int | None = None,
) -> dict:
    """features 长表 → 序列样本（懒加载 3D 视图，对齐 Qlib TSDataSampler 架构）。

    Args:
        features: pd.DataFrame(symbol, date, <features...>, close)——close 用于
            前向收益标签（hfq 口径，与 alpha_basic_v1 一致）。
        lookback: 时序窗口长度（建议 20~60 个交易日）。
        horizon: 标签前向天数（与 ML_LABEL_HORIZON 对齐）。
        feature_cols: 缺省用除 symbol/date/close/label_ret 外全部数值列。
        max_rows_per_symbol: 每标的最多取最近 N 个样本（None = 全部）。

    Returns:
        {X: SequenceArray(N,L,F), y: (N,) float32, dates: (N,) datetime64,
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
    df = df.sort_values(["symbol", "date"]).reset_index(drop=True)

    # 提取全局 2D 连续特征矩阵（单次分配，仅 ~2.2 GB，避免 30 倍 3D 冗余拷贝）
    full_vals = df[feature_cols].to_numpy(dtype=np.float32)

    offset = 0
    row_starts_list = []
    y_list, d_list, s_list = [], [], []
    n_raw = 0

    for sym, g in df.groupby("symbol", sort=False):
        n = len(g)
        sym_start = offset
        offset += n
        if n <= lookback + horizon:
            continue

        sym_vals = full_vals[sym_start : sym_start + n]
        closes = g["close"].to_numpy(dtype=np.float64)
        dates = g["date"].to_numpy()

        fwd = np.full(n, np.nan)
        fwd[:n - horizon] = closes[horizon:] / closes[:n - horizon] - 1.0
        labels = fwd[lookback - 1:]
        n_raw += len(labels)

        # 向量化无物化滑窗检查：在 1D 掩码上使用滑动窗口，杜绝提前申请 3D 冗余内存
        row_finite = np.isfinite(sym_vals).all(axis=1)
        wins_finite = sliding_window_view(row_finite, lookback).all(axis=1)
        ok = wins_finite & np.isfinite(labels)
        if not ok.any():
            continue

        valid_idx = np.flatnonzero(ok)
        if max_rows_per_symbol and len(valid_idx) > max_rows_per_symbol:
            valid_idx = valid_idx[-max_rows_per_symbol:]

        row_starts_list.append(sym_start + valid_idx)
        y_list.append(labels[valid_idx].astype(np.float32))
        d_list.append(dates[lookback - 1:][valid_idx])
        s_list.append(np.full(len(valid_idx), sym, dtype=object))

    if row_starts_list:
        row_starts = np.concatenate(row_starts_list)
        y = np.concatenate(y_list)
        ds = np.concatenate(d_list)
        ss = np.concatenate(s_list)
        X = SequenceArray(full_vals, row_starts, lookback=lookback)
    else:
        row_starts = np.empty(0, dtype=np.int64)
        y = np.empty(0, dtype=np.float32)
        ds = np.empty(0, dtype="datetime64[ns]")
        ss = np.empty(0, dtype=object)
        X = SequenceArray(full_vals, row_starts, lookback=lookback)

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
