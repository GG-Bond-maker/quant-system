"""
标签（Label）构建与数据质量治理（AQP ML）。

问题背景（CRIT-002 的直接后果）：
    后复权数据一旦断裂，``close[t+N]/close[t]-1`` 会产出 +66,910% 这类荒谬标签。
    原始实现直接 ``shift(-N)`` 后 ``dropna`` 就送进 LightGBM 的 L2 目标，
    单个极端样本即主导整体损失（实测 train RMSE 4.78 vs valid 0.073），
    进而让早停在第 1 轮触发，模型退化成常数列。

本模块提供**显式**的质量策略，杜绝静默丢样本：

    reject     极端/非有限标签直接剔除（保守，样本量充足时推荐）
    winsorize  极端标签截尾到边界值（保留样本，削弱影响；默认）
    clip       （同 winsorize，别名，语义更直观）

每次构建都会产出 :class:`LabelQualityReport` 并打印审计日志：
    原始样本 -> 非有限样本 -> 极端样本 -> 处理后样本
"""
from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Literal

import numpy as np
import pandas as pd
from loguru import logger

LabelMode = Literal["reject", "winsorize", "clip"]

DEFAULT_MAX_ABS_RETURN = 0.5   # 5 日收益 |r| > 50% 视为极端（非 ST/新股已不可能）


@dataclass(frozen=True)
class LabelPolicy:
    """标签质量策略。阈值集中在此，禁止散落硬编码。"""

    horizon: int = 5
    mode: LabelMode = "winsorize"
    max_abs_return: float = DEFAULT_MAX_ABS_RETURN
    drop_non_finite: bool = True
    # t 与 t+N 之间允许的最大自然日跨度；0 = 自动（horizon*3+10，足以覆盖周末+假期）
    max_gap_calendar_days: int = 0

    def __post_init__(self) -> None:
        if self.horizon < 1:
            raise ValueError("horizon 必须 >= 1")
        if self.mode not in ("reject", "winsorize", "clip"):
            raise ValueError(f"未知 mode: {self.mode}")
        if self.max_abs_return <= 0:
            raise ValueError("max_abs_return 必须 > 0")

    @property
    def gap_limit_days(self) -> int:
        """t -> t+horizon 允许的最大自然日跨度。"""
        return self.max_gap_calendar_days or (self.horizon * 3 + 10)


@dataclass
class LabelQualityReport:
    """标签质量审计记录（会被打印并随模型产物落盘）。"""

    n_raw: int = 0                 # 原始行数
    n_missing_label: int = 0       # 尾部 horizon 行无标签
    n_non_finite: int = 0          # NaN / ±inf
    n_gap_invalid: int = 0         # 标签窗口跨越数据空洞（如停牌/缺失数月）
    n_extreme: int = 0             # |r| > max_abs_return
    n_dropped: int = 0             # 实际剔除
    n_winsorized: int = 0          # 实际截尾
    n_final: int = 0               # 处理后样本
    max_abs_before: float = float("nan")
    max_abs_after: float = float("nan")
    std_before: float = float("nan")
    std_after: float = float("nan")
    mode: str = ""
    horizon: int = 0
    max_abs_return: float = DEFAULT_MAX_ABS_RETURN
    extreme_samples: list[dict] = field(default_factory=list)

    def log(self) -> None:
        """打印审计日志（要求：原始 / 异常 / 处理后 必须可见）。"""
        logger.info(
            "[label] 原始样本={n_raw} "
            "| 无标签(尾部)={n_missing} "
            "| 跨越空洞={n_gap} "
            "| 非有限={n_non_finite} "
            "| 极端(|r|>{th:.0%})={n_extreme} "
            "| 剔除={n_drop} 截尾={n_win} "
            "| 处理后={n_final}",
            n_raw=self.n_raw, n_missing=self.n_missing_label,
            n_gap=self.n_gap_invalid,
            n_non_finite=self.n_non_finite, n_extreme=self.n_extreme,
            th=self.max_abs_return, n_drop=self.n_dropped,
            n_win=self.n_winsorized, n_final=self.n_final,
        )
        logger.info(
            "[label] |r| max: {b:.4f} -> {a:.4f} ; std: {sb:.4f} -> {sa:.4f} ; "
            "mode={mode} horizon={h}",
            b=self.max_abs_before, a=self.max_abs_after,
            sb=self.std_before, sa=self.std_after,
            mode=self.mode, h=self.horizon,
        )
        if self.extreme_samples:
            logger.warning(
                f"[label] 极端标签样例（前 5 条）: {self.extreme_samples[:5]}")

    def as_dict(self) -> dict:
        d = {k: v for k, v in self.__dict__.items() if k != "extreme_samples"}
        d["extreme_sample_count"] = len(self.extreme_samples)
        return d


def build_forward_return_labels(
    df: pd.DataFrame,
    policy: LabelPolicy | None = None,
    price_col: str = "close",
    symbol_col: str = "symbol",
    date_col: str = "date",
    label_col: str = "label_ret",
) -> tuple[pd.DataFrame, LabelQualityReport]:
    """构建"未来 N 日收益率"标签并施加质量策略。

    :param df: 因子宽表，需含 symbol/date/close；必须是**后复权**价格
               （hfq 具备 asof 稳定性，qfq 会随新增数据漂移 -> train/serve skew）
    :return:   (带标签的 DataFrame, 质量报告)

    防泄漏约定：
        标签只允许使用 t 日**之后**的信息（close[t+N]），
        特征只允许使用 t 日及之前的信息 —— 二者不得混用。
        本函数不修改任何特征列。
    """
    policy = policy or LabelPolicy()
    rep = LabelQualityReport(mode=policy.mode, horizon=policy.horizon,
                             max_abs_return=policy.max_abs_return)

    out = df.sort_values([symbol_col, date_col]).reset_index(drop=True).copy()
    rep.n_raw = len(out)

    if price_col not in out.columns:
        raise ValueError(f"缺少价格列 {price_col}")

    # ---- 1) 构造未来 N 日收益（按 symbol 分组 shift(-N)）----
    out[label_col] = out.groupby(symbol_col)[price_col].transform(
        lambda x: x.shift(-policy.horizon) / x - 1.0)

    # ---- 1b) 跨空洞标签：t 与 t+N 之间若存在长期停牌/数据缺失，
    #          shift(-N) 会跨过空洞把"几个月后的价格"当成"N 日后的价格"，
    #          产出 |r| 极大的假标签（实测 000001.SZ 因 2026 年 1~5 月数据缺失，
    #          2025-12 的标签被算成 -99%）。这类样本必须识别并剔除。
    #          用自然日跨度判定而非交易日计数，避免依赖日历可用性。
    if policy.gap_limit_days > 0:
        fut_dates = out.groupby(symbol_col)[date_col].shift(-policy.horizon)
        gap_days = (pd.to_datetime(fut_dates) - pd.to_datetime(out[date_col])).dt.days
        bad_gap = gap_days.notna() & (gap_days > policy.gap_limit_days)
        rep.n_gap_invalid = int(bad_gap.sum())
        if rep.n_gap_invalid:
            logger.warning(
                f"[label] {rep.n_gap_invalid} 条标签跨越数据空洞"
                f"（>{policy.gap_limit_days} 自然日），已剔除")
            out.loc[bad_gap, label_col] = np.nan

    # ---- 2a) 结构性缺失：**按位置**判定为每个 symbol 末尾 horizon 行。
    #          这些行没有"未来价格"，标签必然为 NaN —— 是设计使然，不是数据问题。
    #  ⚠️ 必须按位置而不是按 isna() 判定：
    #     若用 isna()，序列中间因价格为 NaN 而产生的坏标签会被误算进"结构性缺失"，
    #     掩盖真实的数据问题（这正是本模块要防的"静默丢样本"）。
    tail_mask = (out.groupby(symbol_col).cumcount(ascending=False)
                 < policy.horizon)
    rep.n_missing_label = int(tail_mask.sum())
    out = out.loc[~tail_mask].copy()

    # ---- 2b) 数据导致的非有限值（±inf / NaN）----
    vals = pd.to_numeric(out[label_col], errors="coerce")
    arr0 = vals.to_numpy(dtype="float64", na_value=np.nan)
    non_finite = ~np.isfinite(arr0)
    rep.n_non_finite = int(non_finite.sum())
    if rep.n_non_finite and policy.drop_non_finite:
        out = out.loc[~non_finite].copy()
        vals = pd.to_numeric(out[label_col], errors="coerce")

    # ---- 3) 极端值 ----
    abs_v = vals.abs()
    extreme = abs_v > policy.max_abs_return
    rep.n_extreme = int(extreme.sum())
    rep.max_abs_before = float(np.nanmax(np.abs(
        vals.to_numpy(dtype="float64", na_value=np.nan)))) if len(vals) else float("nan")
    rep.std_before = float(np.nanstd(vals.to_numpy(dtype="float64",
                                                   na_value=np.nan))) if len(vals) else float("nan")

    if rep.n_extreme:
        # 留痕：记录前 5 条极端样本，便于定位数据源问题
        idx = out.index[extreme][:5]
        rep.extreme_samples = [
            {symbol_col: str(out.at[i, symbol_col]),
             date_col: str(out.at[i, date_col])[:10],
             price_col: round(float(out.at[i, price_col]), 4),
             label_col: round(float(out.at[i, label_col]), 4)}
            for i in idx
        ]
        if policy.mode == "reject":
            out = out.loc[~extreme].copy()
            rep.n_dropped += rep.n_extreme
        else:  # winsorize / clip
            clipped = out[label_col].clip(-policy.max_abs_return,
                                          policy.max_abs_return)
            rep.n_winsorized = int((clipped != out[label_col]).sum())
            out[label_col] = clipped

    after = pd.to_numeric(out[label_col], errors="coerce")
    arr = after.to_numpy(dtype="float64", na_value=np.nan)
    rep.n_final = len(out)
    rep.max_abs_after = float(np.nanmax(np.abs(arr))) if len(arr) else float("nan")
    rep.std_after = float(np.nanstd(arr)) if len(arr) else float("nan")

    rep.log()
    return out.reset_index(drop=True), rep


def future_contamination_check(
    build_fn: Callable[[pd.DataFrame], pd.DataFrame],
    raw: pd.DataFrame,
    cutoff_date: pd.Timestamp,
    feature_cols: list[str],
) -> tuple[bool, list[str]]:
    """通用"未来信息污染"检测。

    原理（asof / PIT 稳定性）：
        用**截止到 cutoff_date** 的数据构建特征，与用**全量**数据构建特征对比，
        cutoff 之前所有日期的特征值必须完全一致。
        若不一致，说明特征用到了 cutoff 之后的数据（即未来函数）。

    :param build_fn:     特征构建函数（如 ``ml.features.build_factors``）
    :param raw:          全量 OHLCV
    :param cutoff_date:  截断日
    :return:             (是否无污染, 不一致的列名)
    """
    full = build_fn(raw)
    truncated = build_fn(raw[pd.to_datetime(raw["date"]) <= cutoff_date])

    cut = pd.Timestamp(cutoff_date)
    a = full[pd.to_datetime(full["date"]) <= cut]
    b = truncated[pd.to_datetime(truncated["date"]) <= cut]
    if len(a) != len(b):
        return False, [f"行数不一致 {len(a)} != {len(b)}"]

    cols = [c for c in feature_cols if c in a.columns and c in b.columns]
    a = a.sort_values(["symbol", "date"]).reset_index(drop=True)
    b = b.sort_values(["symbol", "date"]).reset_index(drop=True)

    bad: list[str] = []
    for c in cols:
        va, vb = a[c].to_numpy(dtype="float64"), b[c].to_numpy(dtype="float64")
        if not np.allclose(va, vb, rtol=1e-9, atol=1e-9, equal_nan=True):
            bad.append(c)
    return (not bad), bad


# ==================== 4. López de Prado 三重屏障法与元标签 (AFML 2018) ====================
def triple_barrier_labels(
    df: pd.DataFrame,
    pt_multiplier: float = 2.0,
    sl_multiplier: float = 2.0,
    horizon: int = 5,
    vol_window: int = 20,
    price_col: str = "close",
    high_col: str = "high",
    low_col: str = "low",
    date_col: str = "date",
    symbol_col: str = "symbol",
) -> pd.DataFrame:
    """Marcos López de Prado (2018) 三重屏障标注法（Triple Barrier Method）。

    以波动率自适应设置止盈（上横屏障）与止损（下横屏障），配合时间持有期（竖直屏障）：
    1. 动态日波动率 σ_t：基于严格 t 日及之前的收益率序列计算（绝无未来信息）；
    2. 止盈价位：P_t * (1 + pt_multiplier * σ_t)；
    3. 止损价位：P_t * (1 - sl_multiplier * σ_t)；
    4. 时间窗口：(t, t + horizon] 交易日内首个触及屏障作为事件退出时点。

    :return: 包含 tb_label (+1: 止盈, -1: 止损, 0: 时间到期未触碰),
             tb_ret (退出实现收益率), tb_barrier (触及屏障类型: pt/sl/time),
             tb_exit_date (退出日) 的 DataFrame。
    """
    if horizon < 1:
        raise ValueError("horizon 必须 >= 1")
    if pt_multiplier <= 0 or sl_multiplier <= 0:
        raise ValueError("pt_multiplier 与 sl_multiplier 必须 > 0")

    req_cols = {price_col, date_col, symbol_col}
    missing = req_cols - set(df.columns)
    if missing:
        raise ValueError(f"缺少必要数据列: {missing}")

    has_hl = high_col in df.columns and low_col in df.columns
    out = df.sort_values([symbol_col, date_col]).reset_index(drop=True).copy()

    # 1. 严格基于历史计算每只标的日收益率的历史波动率 σ_t (滚动标准差)
    # 防未来信息红线：shift(1) 确保 σ_t 仅使用 t-1 及更早的收益率计算
    ret_series = out.groupby(symbol_col)[price_col].pct_change()
    daily_vol = (
        ret_series.groupby(out[symbol_col])
        .rolling(window=vol_window, min_periods=max(5, vol_window // 4))
        .std()
        .reset_index(level=0, drop=True)
    )
    # 中位数兜底极少样本段
    default_vol = float(daily_vol.dropna().median()) if len(daily_vol.dropna()) > 0 else 0.02
    daily_vol = daily_vol.fillna(default_vol).clip(lower=0.002, upper=0.15)
    out["_vol"] = daily_vol

    tb_label = []
    tb_ret = []
    tb_barrier = []
    tb_exit_date = []

    # 按 symbol 独立切片扫描前向窗口，寻找首个触及屏障事件
    for _, sym_df in out.groupby(symbol_col, sort=False):
        n = len(sym_df)
        px = sym_df[price_col].to_numpy(dtype=np.float64)
        hi = sym_df[high_col].to_numpy(dtype=np.float64) if has_hl else px
        lo = sym_df[low_col].to_numpy(dtype=np.float64) if has_hl else px
        dates = sym_df[date_col].to_numpy()
        vols = sym_df["_vol"].to_numpy(dtype=np.float64)

        for i in range(n):
            if i + horizon >= n:
                # 序列尾部不足 horizon 个交易日，无有效竖直屏障
                tb_label.append(np.nan)
                tb_ret.append(np.nan)
                tb_barrier.append(None)
                tb_exit_date.append(None)
                continue

            p0 = px[i]
            vol0 = vols[i]
            upper_barrier = p0 * (1.0 + pt_multiplier * vol0)
            lower_barrier = p0 * (1.0 - sl_multiplier * vol0)

            triggered = False
            for step in range(1, horizon + 1):
                idx = i + step
                hit_pt = hi[idx] >= upper_barrier
                hit_sl = lo[idx] <= lower_barrier

                if hit_pt and hit_sl:
                    # 单日跨越两屏障：极端波动下遵循保守原则，判定止损触及
                    tb_label.append(-1)
                    tb_ret.append((lower_barrier - p0) / p0)
                    tb_barrier.append("sl")
                    tb_exit_date.append(dates[idx])
                    triggered = True
                    break
                elif hit_pt:
                    tb_label.append(1)
                    tb_ret.append((upper_barrier - p0) / p0)
                    tb_barrier.append("pt")
                    tb_exit_date.append(dates[idx])
                    triggered = True
                    break
                elif hit_sl:
                    tb_label.append(-1)
                    tb_ret.append((lower_barrier - p0) / p0)
                    tb_barrier.append("sl")
                    tb_exit_date.append(dates[idx])
                    triggered = True
                    break

            if not triggered:
                # 到期前未触碰横向屏障，以竖直时间屏障价格平仓
                final_idx = i + horizon
                p_end = px[final_idx]
                r_end = (p_end - p0) / p0
                lbl = 1 if r_end > 0 else (-1 if r_end < 0 else 0)
                tb_label.append(lbl)
                tb_ret.append(r_end)
                tb_barrier.append("time")
                tb_exit_date.append(dates[final_idx])

    out["tb_label"] = tb_label
    out["tb_ret"] = tb_ret
    out["tb_barrier"] = tb_barrier
    out["tb_exit_date"] = tb_exit_date
    out.drop(columns=["_vol"], inplace=True)
    return out


def meta_labeling(
    primary_signals: pd.Series | np.ndarray,
    actual_returns: pd.Series | np.ndarray,
    zero_action_as_loss: bool = True,
) -> pd.Series:
    """Marcos López de Prado 元标签构建（Meta-Labeling）。

    将主模型策略信号（做多 +1，做空 -1）与真实实现收益率结合，生成次级模型二元标签：
    - y_meta = 1：主模型信号正确（多头且收益 > 0，或空头且收益 < 0）；
    - y_meta = 0：主模型信号错误或无动作；
    从而将原预测问题转化为「判断主模型给出的交易机会是否值得下注（Bet Sizing / Trade Filtering）」。

    :param primary_signals: 主模型信号，取值通常为 {-1, 0, 1}
    :param actual_returns: 实际实现收益（如三重屏障实现的 tb_ret 或持有期超额收益）
    :param zero_action_as_loss: 主模型未产生动作 (0) 是否作为 0 标签
    :return: 0 或 1 的二元标签序列
    """
    sig = np.asarray(primary_signals, dtype=np.float64)
    ret = np.asarray(actual_returns, dtype=np.float64)

    if sig.shape != ret.shape:
        raise ValueError(f"信号维度 {sig.shape} 与收益维度 {ret.shape} 不一致")

    trade_pnl = sig * ret
    if zero_action_as_loss:
        meta = np.where(trade_pnl > 0, 1, 0)
    else:
        meta = np.where(sig == 0, np.nan, np.where(trade_pnl > 0, 1, 0))

    return pd.Series(meta, index=primary_signals.index if isinstance(primary_signals, pd.Series) else None)

