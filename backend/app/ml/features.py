"""
alpha_basic_v1 基础因子提取（AQP ML）。

设计要点：
- 输入多标的 OHLCV（symbol, date, open, high, low, close, volume[, amount]），
  按 symbol 分组逐只计算，全部基于【当日及之前】数据（严格 PIT，禁止 shift(-N) 作特征）；
- ⚠️ 价格基准纪律（P0-Major#4 修复）：输入必须使用【后复权 hfq】价格。
  hfq 因子为 IPO 累计口径、只增不改——追加未来数据不会改写历史特征值
  （asof 稳定性，tests/test_feature_asof.py 守卫）。
  若使用 qfq（锚点=窗口末），全量重算会漂移历史特征 → train/serve skew；
- 价格类因子全部做归一化（除以 close），保证横截面可比、跨价格区间可比；
- 缺失值保留 NaN：LightGBM 原生处理缺失，强行填 0 会扭曲因子分布；
- 调用方需保证每只标的传入足够的历史预热窗口（>= 250 个交易日），
  否则长窗口因子（ma_gap_250 等）为 NaN 属预期行为。

技术指标说明（均为手写 pandas/numpy 实现，无 TA-Lib 系统库依赖）：
- MACD(12,26,9)：EMA12 - EMA26 = DIF；DEA = EMA9(DIF)；BAR = 2*(DIF-DEA)
- RSI(n)：Wilder 平滑（ewm alpha=1/n），与通达信口径一致
- BOLL(20,2)：位置 (close-mid)/(2*std) 与带宽 4*std/mid
- ATR(14)：真实波幅均值 / close
"""
from __future__ import annotations

from typing import Any

import numpy as np
import pandas as pd
from loguru import logger

# Sprint3 §4.2：生产特征空间 bump 到 v2g = v1 全部因子 + g1_（同业邻居传导）列。
# 版本号不用 alpha_basic_v2：已被 P2-6 扩展因子实验（features_v2.py，220 列方向，
# 未接线）占用；g = graph propagate。v1 分区保留在磁盘上供 A/B 对照。
FEATURE_VERSION = "alpha_basic_v2g"

# 供 A/B 对照的上一代特征空间：磁盘上仍保留 version=alpha_basic_v1 分区，
# 但生产链路一律使用 FEATURE_VERSION（v2g）。
LEGACY_FEATURE_VERSION = "alpha_basic_v1"

# 已知特征空间版本集合（feature_runs 等落库前的白名单，单一事实源）。
# - 生产：FEATURE_VERSION（v2g）；
# - A/B 对照：LEGACY_FEATURE_VERSION（v1）。
# ⚠️ features_v2（alpha_basic_v2，未接线的扩展实验）不在此列；将来接线生产
# 时必须在此登记，否则落库校验会 fail-fast 暴露（见 assert_known_feature_version）。
KNOWN_FEATURE_VERSIONS: frozenset[str] = frozenset({
    FEATURE_VERSION,
    LEGACY_FEATURE_VERSION,
})


class UnknownFeatureVersionError(ValueError):
    """feature_version 不在已知特征空间集合内（落库前 fail-fast）。"""


def assert_known_feature_version(value: str | None, *, where: str) -> str | None:
    """校验待落库的 feature_version，返回可安全入库的值。

    语义（D-02/T-08：feature_runs 曾硬编码 alpha_basic_v1 冒充生产版本）：
    - ``None``：确实取不到真值（如旧 predictions 分区缺列）—— 允许，记
      WARNING 后按 None 如实落库，**绝不用策略名/硬编码冒充**；
    - 已登记版本（v2g / v1）：原样返回；
    - 其它：记 ERROR 后抛 :class:`UnknownFeatureVersionError`（fail-fast），
      不静默写库。

    Args:
        value: 待校验的特征版本（来自 predictions 分区的真实列）。
        where: 调用点标识，用于日志定位。

    Returns:
        校验通过的原值（或 None）。

    Raises:
        UnknownFeatureVersionError: value 非 None 且不在 KNOWN_FEATURE_VERSIONS。
    """
    if value is None:
        logger.warning(f"[features] {where}: feature_version 缺失（旧分区），按 None 落库")
        return None
    if value not in KNOWN_FEATURE_VERSIONS:
        logger.error(
            f"[features] {where}: feature_version={value!r} 不在已知集合 "
            f"{sorted(KNOWN_FEATURE_VERSIONS)}，拒绝写库"
            "（新特征空间请先在 ml/features.py 登记）")
        raise UnknownFeatureVersionError(
            f"feature_version={value!r} 未登记于 KNOWN_FEATURE_VERSIONS "
            f"{sorted(KNOWN_FEATURE_VERSIONS)}")
    return value


_REQUIRED_COLS = {"symbol", "date", "open", "high", "low", "close", "volume"}

# 滚动窗口约定（集中管理，便于文档与前端展示对齐）
_MOMENTUM_WINDOWS = (1, 3, 5, 10, 20, 60)
_MA_WINDOWS = (5, 10, 20, 60, 120, 250)
_VOL_WINDOWS = (5, 10, 20, 60)


def _rsi(close: pd.Series, period: int) -> pd.Series:
    """RSI（Wilder 平滑）：RSI = 100 - 100 / (1 + RS)。"""
    delta = close.diff()
    gain = delta.clip(lower=0.0)
    loss = (-delta).clip(lower=0.0)
    avg_gain = gain.ewm(alpha=1.0 / period, adjust=False, min_periods=period).mean()
    avg_loss = loss.ewm(alpha=1.0 / period, adjust=False, min_periods=period).mean()
    rs = avg_gain / (avg_loss + 1e-12)
    return 100.0 - 100.0 / (1.0 + rs)


def _one_symbol_feats(df: pd.DataFrame) -> pd.DataFrame:
    """对单只标的的日期序列计算全部因子。df 需按 date 升序。"""
    df = df.sort_values("date").copy()
    c, o = df["close"], df["open"]
    h, l, v = df["high"], df["low"], df["volume"]

    out: dict[str, np.ndarray | pd.Series] = {}

    # ---- 动量 / 反转（动量窗口收益率） ----
    for w in _MOMENTUM_WINDOWS:
        out[f"ret_{w}"] = c.pct_change(w)

    # ---- 隔夜跳空（open_t / close_{t-1} - 1） ----
    out["overnight_gap"] = o / c.shift(1) - 1.0

    # ---- 均线偏离（close / MA_w - 1） ----
    for w in _MA_WINDOWS:
        out[f"ma_gap_{w}"] = c / c.rolling(w).mean() - 1.0
    # 均线斜率（5 日前 MA20 相对变化）
    ma20 = c.rolling(20).mean()
    out["ma_slope_20"] = ma20 / ma20.shift(5) - 1.0

    # ---- 波动率（日收益滚动标准差） ----
    ret1 = c.pct_change()
    for w in _VOL_WINDOWS:
        out[f"vol_{w}"] = ret1.rolling(w).std()

    # ---- 振幅 / 实体 ----
    out["hl_range"] = (h - l) / c
    out["co_range"] = (c - o) / c

    # ---- 量能 ----
    for w in (5, 10, 20, 60):
        out[f"v_ma_gap_{w}"] = v / v.rolling(w).mean() - 1.0
    out["v_rank_20"] = v.rolling(20).rank(pct=True)
    out["v_cv_5"] = v.rolling(5).std() / (v.rolling(5).mean() + 1e-12)
    if "amount" in df.columns:
        amount = df["amount"]
        out["amount_per_share"] = amount / np.maximum(v, 1.0)

    # ---- MACD(12,26,9)，除以 close 归一化 ----
    ema12 = c.ewm(span=12, adjust=False).mean()
    ema26 = c.ewm(span=26, adjust=False).mean()
    dif = ema12 - ema26
    dea = dif.ewm(span=9, adjust=False).mean()
    bar = 2.0 * (dif - dea)
    out["macd_dif"] = dif / c
    out["macd_dea"] = dea / c
    out["macd_bar"] = bar / c

    # ---- RSI(6) / RSI(14) ----
    out["rsi_6"] = _rsi(c, 6) / 100.0   # 归一化到 0~1
    out["rsi_14"] = _rsi(c, 14) / 100.0

    # ---- BOLL(20,2)：位置与带宽 ----
    mid = c.rolling(20).mean()
    std = c.rolling(20).std(ddof=0)
    out["boll_pos"] = ((c - mid) / (2.0 * std + 1e-12)).clip(-3.0, 3.0)
    out["boll_w"] = 4.0 * std / (mid + 1e-12)

    # ---- ATR(14) 归一化 ----
    prev_c = c.shift(1)
    tr = pd.concat(
        [h - l, (h - prev_c).abs(), (l - prev_c).abs()], axis=1
    ).max(axis=1)
    out["atr_14"] = tr.rolling(14).mean() / c

    # ---- 滚动分位（位置因子） ----
    out["rank_close_20"] = c.rolling(20).rank(pct=True)
    out["rank_close_60"] = c.rolling(60).rank(pct=True)
    out["rank_volume_20"] = v.rolling(20).rank(pct=True)

    # ---- 高阶统计：偏度 / 峰度 ----
    for w in (20, 60):
        out[f"skew_ret_{w}"] = ret1.rolling(w).skew()
        out[f"kurt_ret_{w}"] = ret1.rolling(w).kurt()

    res = pd.DataFrame(out, index=df.index)
    res.insert(0, "close", c.values)  # 供训练端构造 Label（特征白名单会排除它）
    res.insert(0, "date", df["date"].values)
    res.insert(0, "symbol", df["symbol"].iloc[0])
    return res


def build_factors(raw: Any) -> pd.DataFrame:
    """从多标的 OHLCV 构建基础因子宽表。

    :param raw: pd.DataFrame / pl.DataFrame，含 symbol, date, open, high, low, close, volume
                （可选 amount），每只标的需要足够长的历史预热窗口；
    :return:    pd.DataFrame[symbol, date, <40+ 因子列>]，缺失值保留 NaN。
    """
    if isinstance(raw, pd.DataFrame):
        df = raw.copy()
    else:
        import polars as pl

        if isinstance(raw, pl.DataFrame):
            df = raw.to_pandas()
        else:
            raise TypeError(f"unsupported raw type: {type(raw)}")

    missing = _REQUIRED_COLS - set(df.columns)
    if missing:
        raise ValueError(f"raw 缺失列: {sorted(missing)}")

    chunks: list[pd.DataFrame] = []
    symbols = df["symbol"].unique()
    total = len(symbols)
    for i, (sym, g) in enumerate(df.groupby("symbol", sort=False), 1):
        try:
            chunks.append(_one_symbol_feats(g.reset_index(drop=True)))
        except Exception as e:
            logger.warning(f"factor build fail symbol={sym}: {e!r}")
        if i % 500 == 0:
            logger.info(f"factor progress: {i}/{total}")

    if not chunks:
        return pd.DataFrame()

    out = pd.concat(chunks, ignore_index=True)
    out = out.replace([np.inf, -np.inf], np.nan)
    feat_cols = [c for c in out.columns if c not in {"symbol", "date"}]
    logger.info(
        f"features built: shape={out.shape}, n_factor_cols={len(feat_cols)}, "
        f"version={FEATURE_VERSION}"
    )
    return out


def factor_columns(df: pd.DataFrame) -> list[str]:
    """从因子宽表中提取因子列名（排除 symbol/date）。"""
    return [c for c in df.columns if c not in {"symbol", "date"}]


def apply_propagate(df: "pd.DataFrame", hops: int = 1,
                    universe: "list[str] | None" = None) -> "pd.DataFrame":
    """为因子面板追加 g{hops}_ 邻居传导列（§4.2 propagate 接线）。

    对 factor_columns(df) 的全部因子做同日截面邻居均值（行业边，
    relations/edges.parquet）。无关系数据时 graph.propagate 透明降级为
    全 NaN 列（列名稳定存在，符合不造数原则）。

    Args:
        df: build_factors 产出面板（symbol/date/因子列）。
        hops: 传导跳数（1 = 一跳邻居均值）。
        universe: **冻结的邻接节点集**（graph.resolve_universe 产出）。缺省 None
            ⇒ 用面板自身符号，此时 g1_* 随面板成员变化（P1-18 缺陷本体）——
            生产/离线构建必须传冻结集，保证同一历史日的 g1_* 逐值可复现。

    Returns:
        追加 g{hops}_* 列后的同一 DataFrame（原地扩展语义与 propagate 一致）。
    """
    from .graph import build_adjacency, propagate

    symbols = sorted(df["symbol"].unique())
    idx, A = build_adjacency(symbols, universe=universe)
    out = propagate(df, A, idx, factor_columns(df), hops=hops)
    n_cov = sum(1 for c in out.columns
                if c.startswith(f"g{hops}_") and out[c].notna().any())
    logger.info(f"[features] propagate hops={hops}: +"
                f"{len([c for c in out.columns if c.startswith('g')])} g-columns, "
                f"edges={int(A.sum())}, nodes={len(idx)}"
                + (f", universe=frozen({len(universe)})" if universe
                   else ", universe=panel(未冻结)")
                + f", g{hops}_有值列={n_cov}")
    return out
