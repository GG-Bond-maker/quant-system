"""alpha_basic_v2（P2-6）：v1 全部因子 + 扩展块，目标总列数 >= 220。

扩展方向（全部 PIT：仅用 T 日及之前数据；无全样本标准化）：
- 量价关系：close/volume 滚动相关（多窗口）、量价背离、OBV 斜率
- Amihud 非流动性：|ret|/amount 多窗口
- 隔夜跳空族：gap/gap_ma/gap_std
- Carhart/Barra 代理：MOM(120/250)、BETA（对等权市场收益的滚动 β）、
  RESVOL（残差波动）、LIQUIDITY（Amihud/量能分位）、SIZE（log 成交额代理）
- 多窗口展开：ROC/CCI/Williams %R/偏度/峰度/分位（系统性覆盖多窗口）

⚠️ 口径说明：SIZE 用 log(amount20) 代理（无股本数据）；VAL/GROW/LEV/QUAL 属
财务因子，由 financial_report（announce_date PIT）另行 join，不在本模块生成。
"""
from __future__ import annotations

from typing import Any

import numpy as np
import pandas as pd
from loguru import logger

from .features import build_factors
from .features import factor_columns as v1_columns

FEATURE_VERSION = "alpha_basic_v2"


def _extra_blocks(df: pd.DataFrame) -> pd.DataFrame:
    """单标的扩展因子块（df 需含 date/open/high/low/close/volume/amount，升序）。"""
    c, o = df["close"], df["open"]
    h, l, v = df["high"], df["low"], df["volume"]
    ret1 = c.pct_change()
    out: dict[str, pd.Series | np.ndarray] = {}

    # ---- 量价关系 ----
    for w in (5, 10, 20, 60):
        out[f"pv_corr_{w}"] = c.rolling(w).corr(v)
        out[f"amihud_{w}"] = (ret1.abs() / df["amount"].clip(lower=1.0)).rolling(w).mean()
    out["pv_divergence"] = c.rolling(20).mean() / c.rolling(20).mean().shift(20) - (
        v.rolling(20).mean() / v.rolling(20).mean().shift(20))

    # ---- 隔夜跳空族 ----
    gap = o / c.shift(1) - 1.0
    out["gap"] = gap
    out["gap_ma_5"] = gap.rolling(5).mean()
    out["gap_std_20"] = gap.rolling(20).std()

    # ---- 影线 / 实体结构 ----
    rng = (h - l).replace(0, np.nan)
    out["upper_shadow"] = (h - np.maximum(c, o)) / rng
    out["lower_shadow"] = (np.minimum(c, o) - l) / rng
    out["body_ratio"] = (c - o).abs() / rng

    # ---- 动量族扩展（Carhart MOM） ----
    for w in (120, 250):
        out[f"mom_{w}"] = c.pct_change(w)
    out["mom_12_1"] = c.shift(21) / c.shift(252) - 1.0  # 12-1 动量（经典口径）

    # ---- ROC / CCI / Williams %R 多窗口 ----
    for w in (5, 10, 20, 60):
        out[f"roc_{w}"] = c / c.shift(w) - 1.0
        tp = (h + l + c) / 3.0
        ma_tp = tp.rolling(w).mean()
        md = (tp - ma_tp).abs().rolling(w).mean()
        out[f"cci_{w}"] = (tp - ma_tp) / (0.015 * md + 1e-12)
        hh = h.rolling(w).max()
        ll = l.rolling(w).min()
        out[f"williams_{w}"] = (hh - c) / (hh - ll + 1e-12)

    # ---- 偏度/峰度/分位多窗口 ----
    for w in (10, 40, 120, 250):
        out[f"skew_{w}"] = ret1.rolling(w).skew()
        out[f"kurt_{w}"] = ret1.rolling(w).kurt()
        out[f"rank_ret_{w}"] = ret1.rolling(w).rank(pct=True)

    # ---- 系统性多窗口展开（bias / 距极值 / 通道位置 / 量能弹性） ----
    for w in (3, 5, 10, 15, 20, 30, 40, 60, 90, 120, 180, 250):
        ma = c.rolling(w).mean()
        hh = h.rolling(w).max()
        ll = l.rolling(w).min()
        out[f"bias_{w}"] = c / (ma + 1e-12) - 1.0
        out[f"dist_max_{w}"] = c / (hh + 1e-12) - 1.0
        out[f"dist_min_{w}"] = c / (ll + 1e-12) - 1.0
        out[f"channel_pos_{w}"] = (c - ll) / (hh - ll + 1e-12)
        out[f"vma_gap_{w}"] = v / (v.rolling(w).mean() + 1e-12) - 1.0
        out[f"vol_of_vol_{w}"] = (v.rolling(w).std()) / (v.rolling(w).mean() + 1e-12)
        out[f"range_mean_{w}"] = ((h - l) / c).rolling(w).mean()
        out[f"close_pos_in_range_{w}"] = ((c - l) / (h - l + 1e-12)).rolling(w).mean()
        out[f"ma_slope_{w}"] = ma / ma.shift(5) - 1.0
        out[f"ret_skew_rank_{w}"] = ret1.rolling(w).apply(
            lambda x, w=w: float((x[-1] - x.mean()) / (x.std() + 1e-12)), raw=True)
        out[f"amount_z_{w}"] = (df["amount"] / df["amount"].rolling(w).mean() - 1.0)

    # ---- LIQUIDITY / SIZE 代理 ----
    out["log_amount_20"] = np.log(df["amount"].rolling(20).mean().clip(lower=1.0))
    out["log_volume_20"] = np.log(v.rolling(20).mean().clip(lower=1.0))
    out["turnover_z_60"] = (v / v.rolling(60).mean() - 1.0) / (v.rolling(60).std() + 1e-12)

    # ---- 波动结构 ----
    out["vol_ratio_5_20"] = ret1.rolling(5).std() / (ret1.rolling(20).std() + 1e-12)
    out["vol_ratio_20_60"] = ret1.rolling(20).std() / (ret1.rolling(60).std() + 1e-12)
    out["downside_vol_20"] = ret1.clip(upper=0).rolling(20).std()
    out["upside_vol_20"] = ret1.clip(lower=0).rolling(20).std()
    out["resvol_20"] = ret1.rolling(20).std()  # RESVOL 代理（未剔除市场成分前）

    res = pd.DataFrame(out, index=df.index)
    return res


def build_alpha_v2(raw: Any, market_ret: pd.Series | None = None) -> pd.DataFrame:
    """v1 因子 + 扩展块 + 截面 BETA（对等权市场收益的 250 日滚动 β）。

    :param raw: 与 build_factors 相同的多标的 OHLCV 宽表
    :param market_ret: 按 date 的等权市场日收益（由全数据集截面均值计算，
                       仅使用当日及之前信息，PIT 安全）；None 时内部计算
    """
    v1 = build_factors(raw)
    pdf = raw.copy() if isinstance(raw, pd.DataFrame) else raw.to_pandas()

    # 市场收益（等权截面均值，逐日独立，PIT 安全）
    if market_ret is None:
        tmp = pdf.copy()
        tmp["ret1"] = tmp.groupby("symbol")["close"].pct_change()
        market_ret = tmp.groupby("date")["ret1"].mean()

    chunks: list[pd.DataFrame] = []
    total = pdf["symbol"].nunique()
    mkt = market_ret.rename("mkt_ret")
    for i, (sym, g) in enumerate(pdf.groupby("symbol", sort=False), 1):
        try:
            g = g.sort_values("date").reset_index(drop=True)
            ext = _extra_blocks(g)
            # BETA：个股收益对市场收益的 250 日滚动协方差 / 市场方差
            g2 = g.copy()
            g2["mkt_ret"] = g2["date"].map(mkt)
            r = g2["close"].pct_change()
            cov = r.rolling(250).cov(g2["mkt_ret"])
            var = g2["mkt_ret"].rolling(250).var()
            ext["beta_250"] = cov / (var + 1e-12)
            ext["resvol_250"] = (r - ext["beta_250"] * g2["mkt_ret"]).rolling(20).std()
            ext["symbol"] = sym
            ext["date"] = g["date"].values
            chunks.append(ext)
        except Exception as e:  # 单只失败跳过
            logger.warning(f"[v2] factor build fail {sym}: {e!r}")
        if i % 50 == 0:
            logger.info(f"[v2] progress {i}/{total}")

    if not chunks:
        raise ValueError("v2 因子构建失败：无任何标的产出")
    ext_all = pd.concat(chunks, ignore_index=True).replace([np.inf, -np.inf], np.nan)
    merged = v1.merge(ext_all, on=["symbol", "date"], how="left",
                      suffixes=("", "_v2dup"))
    dup_cols = [c for c in merged.columns if c.endswith("_v2dup")]
    merged = merged.drop(columns=dup_cols)
    logger.info(f"alpha_basic_v2: shape={merged.shape} cols={len(v1_columns(merged))}")
    return merged
