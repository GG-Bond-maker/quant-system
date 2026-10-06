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


def cpcv_pbo_analysis(
    df: pd.DataFrame,
    feature_cols: list[str] | None = None,
    label_col: str | None = None,
    n_groups: int = 6,
    k_test: int = 2,
    purge_window: int = 5,
    embargo_window: int = 2,
) -> dict:
    """组合净化交叉验证 (CPCV) 与回测过拟合概率 (PBO, Bailey & López de Prado 2014)。

    算法流程：
    1. 将样本历史按交易日切为 N 个等长区块 G_1, ..., G_N；
    2. 生成全部 C(N, k) 个测试子集组合；
    3. 每种组合对训练集施加 Purge（剔除重叠标签）与 Embargo（时序隔离带）；
    4. 对一组候选 Alpha / 因子模型分别计算样本内 (IS) 与样本外 (OOS) RankIC；
    5. 选取样本内表现最优模型 M*，测算其在 OOS 测试集上的真实分位数排位 ω；
    6. PBO = P(ω <= 0.5)，即样本内最优模型在样本外跌入中位数以下的概率。
    """
    import itertools

    if df.empty or "date" not in df.columns or "symbol" not in df.columns:
        return {"has_data": False, "error": "数据为空或缺少 date/symbol 列"}

    data = df.copy()
    unique_dates = np.sort(data["date"].unique())
    n_dates = len(unique_dates)
    if n_dates < n_groups * 8:
        return {
            "has_data": False,
            "error": f"有效日期不足（当前 {n_dates} 天，建议 >= {n_groups * 8} 天）",
        }

    # 确定目标收益列
    if label_col and label_col in data.columns:
        fwd_ret_col = label_col
    else:
        # 自行计算 5 日前向收益
        if "close" not in data.columns:
            return {"has_data": False, "error": "缺少 close 列计算前向收益"}
        data["fwd_5d"] = (
            data.groupby("symbol")["close"]
            .shift(-5)
            .div(data["close"])
            .sub(1.0)
        )
        fwd_ret_col = "fwd_5d"

    # 确定待评估的候选因子集
    if not feature_cols:
        exclude = {"date", "symbol", "close", "fwd_5d", label_col}
        numeric_cols = [
            c for c in data.columns
            if c not in exclude and np.issubdtype(data[c].dtype, np.number)
        ]
        # 选取 6~10 个特征
        feature_cols = numeric_cols[:8]

    if not feature_cols:
        return {"has_data": False, "error": "未找到可评估的数值特征因子"}

    # 预先计算每日每个因子的 RankIC，大幅加速网格遍历
    daily_ic_dict: dict[str, dict[Any, float]] = {f: {} for f in feature_cols}
    for dt, group in data.groupby("date"):
        if len(group) < 8 or group[fwd_ret_col].isna().all():
            continue
        valid = group.dropna(subset=[fwd_ret_col])
        if len(valid) < 8:
            continue
        y = valid[fwd_ret_col].to_numpy()
        for f in feature_cols:
            if f in valid.columns:
                x = valid[f].to_numpy()
                if np.isfinite(x).sum() >= 8 and np.std(x) > 1e-12:
                    from scipy.stats import spearmanr
                    r, _ = spearmanr(x, y, nan_policy="omit")
                    if np.isfinite(r):
                        daily_ic_dict[f][dt] = float(r)

    # 切割 N 个组
    group_size = n_dates // n_groups
    groups: list[np.ndarray] = []
    for g in range(n_groups):
        start_i = g * group_size
        end_i = n_dates if g == n_groups - 1 else (g + 1) * group_size
        groups.append(unique_dates[start_i:end_i])

    date_to_pos = {d: i for i, d in enumerate(unique_dates)}

    # 生成 C(N, k) 组合
    combs = list(itertools.combinations(range(n_groups), k_test))
    n_paths = len(combs)

    path_results = []
    oos_rank_pcts = []
    is_best_scores = []
    oos_best_scores = []

    for path_idx, test_grp_indices in enumerate(combs):
        train_grp_indices = [g for g in range(n_groups) if g not in test_grp_indices]

        # 收集 test 日期
        test_dates_set = set()
        for tg in test_grp_indices:
            test_dates_set.update(groups[tg])

        # Purge 与 Embargo 过滤 train 日期
        purged_train_dates = []
        for trg in train_grp_indices:
            for d in groups[trg]:
                d_pos = date_to_pos[d]
                # 检查距离任何 test 块的边界
                too_close = False
                for tg in test_grp_indices:
                    tg_dates = groups[tg]
                    tg_start_pos = date_to_pos[tg_dates[0]]
                    tg_end_pos = date_to_pos[tg_dates[-1]]
                    # 在 test 前 purge 天
                    if 0 < tg_start_pos - d_pos <= purge_window:
                        too_close = True
                        break
                    # 在 test 后 embargo 天
                    if 0 < d_pos - tg_end_pos <= embargo_window:
                        too_close = True
                        break
                if not too_close:
                    purged_train_dates.append(d)

        # 对每个因子计算 IS 与 OOS IC
        is_scores = {}
        oos_scores = {}
        for f in feature_cols:
            f_ics = daily_ic_dict[f]
            tr_vals = [f_ics[d] for d in purged_train_dates if d in f_ics]
            te_vals = [f_ics[d] for d in test_dates_set if d in f_ics]
            is_scores[f] = float(np.mean(tr_vals)) if len(tr_vals) >= 10 else -999.0
            oos_scores[f] = float(np.mean(te_vals)) if len(te_vals) >= 10 else -999.0

        # 找出 IS 最优模型
        best_f = max(is_scores, key=lambda k: is_scores[k])
        is_best_val = is_scores[best_f]
        oos_best_val = oos_scores[best_f]

        # 测算 M* 在 OOS 排行榜上的相对排位
        valid_oos = sorted([oos_scores[f] for f in feature_cols if oos_scores[f] > -900.0])
        if len(valid_oos) > 1:
            rank = sum(1 for v in valid_oos if v <= oos_best_val)
            rank_pct = float(rank / len(valid_oos))
        else:
            rank_pct = 0.5

        oos_rank_pcts.append(rank_pct)
        if is_best_val > -900.0:
            is_best_scores.append(is_best_val)
        if oos_best_val > -900.0:
            oos_best_scores.append(oos_best_val)

        path_results.append({
            "path_id": path_idx + 1,
            "train_groups": [int(g + 1) for g in train_grp_indices],
            "test_groups": [int(g + 1) for g in test_grp_indices],
            "best_model": best_f,
            "is_rank_ic": round(is_best_val, 4) if is_best_val > -900 else None,
            "oos_rank_ic": round(oos_best_val, 4) if oos_best_val > -900 else None,
            "oos_rank_pct": round(rank_pct, 3),
            "is_overfit": rank_pct <= 0.5,
        })

    # 计算总体 PBO 指标
    pbo = float(np.mean([1.0 if r <= 0.5 else 0.0 for r in oos_rank_pcts]))
    mean_is = float(np.mean(is_best_scores)) if is_best_scores else 0.0
    mean_oos = float(np.mean(oos_best_scores)) if oos_best_scores else 0.0
    degradation_pct = round(float((mean_is - mean_oos) / max(abs(mean_is), 1e-6) * 100), 1)

    # 排序百分比直方图 (10 个桶 0.0 ~ 1.0)
    h_counts, h_bins = np.histogram(oos_rank_pcts, bins=10, range=(0.0, 1.0))
    rank_dist = [
        {"bin": f"{round(h_bins[i], 1)}-{round(h_bins[i+1], 1)}", "count": int(h_counts[i])}
        for i in range(len(h_counts))
    ]

    is_robust = pbo < 0.25
    summary = (
        f"CPCV 组合净检验完成：共切分 {n_groups} 个时序组，生成 {n_paths} 条无渗漏验证路径。"
        f"测得过拟合概率 PBO = {pbo:.1%}（阈值: <25% 稳健，>50% 高危过拟合）。"
        f"样本内优选模型平均 IC = {mean_is:.4f}，样本外衰减至 {mean_oos:.4f}（衰减率 {degradation_pct}%）。"
    )

    return {
        "has_data": True,
        "n_groups": n_groups,
        "k_test": k_test,
        "n_paths": n_paths,
        "purge_window": purge_window,
        "embargo_window": embargo_window,
        "models_evaluated": feature_cols,
        "pbo": round(pbo, 4),
        "is_robust": is_robust,
        "mean_is_ic": round(mean_is, 4),
        "mean_oos_ic": round(mean_oos, 4),
        "degradation_pct": degradation_pct,
        "rank_distribution": rank_dist,
        "paths": path_results,
        "summary": summary,
    }

