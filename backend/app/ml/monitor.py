"""因子健康度与漂移监控（前沿演进 Phase 1 / 维度五 + Alpha 半衰期）。

每日对生产信号（predictions）计算逐日截面 RankIC 与多周期 IC 衰减（半衰期），
对 features 计算相对历史基线的 PSI 漂移；状态机 healthy / watch / degraded，
快照持久化 app_state（kv），状态变化与漂移告警经 events 总线推送顶栏铃铛；
PSI 超标且允许时自动触发重训（candidate 注册 + registry promote 门禁把关，
坏模型不会被自动上线）。

数据口径：全部来自真实落库数据（predictions / features / hfq close），不造数。
指标只读，"降权"以响应披露（factor_health 字段）表达，不静默改权重——
与平台一贯的披露原则一致。
"""
from __future__ import annotations

import math
import threading
import time
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd
import polars as pl
from loguru import logger

from ..core.config import get_settings
from ..db.kv import kv_get, kv_set

# ---------------- 常量与模块状态 ----------------
_HEALTH_KEY = "monitor_factor_health"
_RETRAIN_KEY = "monitor_last_retrain"

# IC 状态机参数（阈值与《前沿演进评审》一致）
RECENT_WINDOW = 15        # 近期窗口：最近 N 个可评估交易日
HIST_WINDOW = 105         # 历史基准窗口（近窗口之前）
MIN_EVAL_DATES = 10       # 近期窗口最少可评估天数，不足则状态 unknown
WATCH_SIGMA = 1.0
DEGRADED_SIGMA = 1.5
PSI_WATCH = 0.10
PSI_DEGRADED = 0.25

IC_HORIZONS = (1, 2, 3, 5, 10)   # 半衰期拟合的多周期截面
MIN_SYMBOLS_PER_DAY = 30         # 单日截面少于该数则跳过（120 只池下防窄截面）

_RETRAIN_LOCK = threading.Lock()
_FEATURES_CACHE: dict[str, tuple[tuple, pl.DataFrame]] = {}
_FEAT_LOCK = threading.Lock()

_STATE_RANK = {"unknown": 0, "healthy": 1, "watch": 2, "degraded": 3}


# ---------------- 数据加载（带签名缓存） ----------------
def _to_date(value: Any) -> date:
    """把 polars 标量（date/datetime/ISO 串）收敛为 ``date``。

    polars 的 ``Series.max()`` 静态类型是标量联合类型（int|float|Decimal|date|
    time|timedelta|str|bytes|list|None），直接做日期运算 mypy 无法校验。
    """
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    return date.fromisoformat(str(value)[:10])


def _normalize_date_col(df: pl.DataFrame) -> pl.DataFrame:
    """把 ``date`` 列统一收敛为 ``pl.Date``（**不静默丢数据**）。

    features / predictions 可能来自异构 parquet：既有 ``pl.Date``（生产 infer 经
    ``atomic_write_parquet`` 直写），也有 ``pl.Datetime``（pandas ``datetime64`` 落盘），
    甚至 ``Utf8``（外部/测试工具写入 ``"2024-06-05 00:00:00.000"`` 这类毫秒串）。
    ``diagonal_relaxed`` 拼接异构文件时会取公共 supertype——Utf8 与 Datetime 混合时
    supertype 是 Utf8，Datetime 值被字符串化为毫秒串，直接 ``.cast(pl.Date)`` 即抛
    ``InvalidOperationError``（曾致 ``run_monitor`` 崩溃）。

    收敛口径（与 ``_to_date`` 的容错哲学一致，但绝不静默置空）：
    - ``pl.Date`` -> 原样返回；
    - ``pl.Datetime``（任意时间单位）-> ``cast(pl.Date)``；
    - ``Utf8`` / ``String`` -> 取前 10 位 ``YYYY-MM-DD`` 解析；**若原有非空值解析后
      变为空**，记 WARNING 并输出样例违规值（数据真实性红线：不吞数据）；
    - 其它 dtype -> 抛 ``TypeError`` 明确报出 dtype（fail loudly，不猜测）。
    """
    if "date" not in df.columns:
        raise KeyError("date 列缺失，无法归一化")
    dtype = df.schema["date"]
    if dtype == pl.Date:
        return df
    if isinstance(dtype, pl.Datetime):
        return df.with_columns(pl.col("date").cast(pl.Date))
    if dtype in (pl.Utf8, pl.String):
        before_non_null = df["date"].is_not_null()
        out = df.with_columns(
            pl.col("date").str.slice(0, 10).str.to_date(strict=False).alias("date"))
        newly_null = before_non_null & out["date"].is_null()
        if bool(newly_null.any()):
            bad = df.filter(newly_null)["date"].head(3).to_list()
            logger.warning(
                f"[monitor] date 列有 {int(newly_null.sum())} 个非空值无法解析为 "
                f"YYYY-MM-DD，已置空（不伪造）；样例：{bad}")
        return out
    raise TypeError(
        f"[monitor] 不支持的 date 列 dtype={dtype!r}；"
        "期望 Date / Datetime / Utf8(String)")


def _resolve_feature_dir(root: Path) -> tuple[str, Path] | None:
    """解析当前**单一有效** features 版本目录（口径对齐 data.features.resolve_feature_version）。

    背景（P0 混读）：``features`` 是版本化数据集，目录下可能同时存在
    ``version=alpha_basic_v1``（5 文件）与 ``version=alpha_basic_v2g``（9 文件）。
    旧 ``_signature`` 用 ``rglob("*.parquet")`` 把两个版本一并纳入，随后被
    ``pl.concat`` 成一份面板 —— 监控指标会把两个特征版本的行**混算**。
    ``alerts`` / ``research`` / ``studio`` 均已通过 ``read_feature_frame`` 接入
    单版本读取，唯独 monitor 没接（本次修复）。

    解析口径与 ``data.features.resolve_feature_version`` 一致：
    - ``FEATURE_VERSION`` 显式指定优先；
    - 留空时选**写入时间最新**的版本（按版本名 tie-break），并记 WARNING
      （留空口径可能随新版本静默切换，故刻意用 WARNING 而非 info 以便被发现）。

    之所以在 monitor 内自行解析而不直接调用 ``read_feature_frame``：① ``read_feature_frame``
    用 data.features 模块自己的 ``get_settings``，与本模块可被替换的 settings 不同源
    （测试需注入 DATA_ROOT）；② 它对非 ``pl.Date`` 的 ``date`` 列直接 ``cast(pl.Date)``，
    会拒绝 features 里异构的 ``Utf8`` 毫秒串——而 monitor 必须保留
    ``_normalize_date_col`` 的容错。故此处只复用「单版本解析」语义，读取仍走
    ``_normalize_date_col``。

    ⚠️ 防漂移：本函数与 ``data/features.resolve_feature_version`` 是**同一口径**的两处
    实现（本轮就地复刻的唯一可行解）。一致性由
    ``tests/test_monitor_feature_version_guard.py::test_resolve_parity_auto_select`` /
    ``::test_resolve_parity_explicit_version`` 在同一 fixture 上断言「两者解析出同一版本」
    来守卫；若 ``data.features.resolve_feature_version`` 变更选版本规则，**必须同步本处**，
    否则 parity 测试会失败。
    """
    requested = (getattr(get_settings(), "FEATURE_VERSION", "") or "").strip()
    if requested:
        version_dir = root / f"version={requested}"
        return (requested, version_dir) if version_dir.is_dir() else None
    candidates: list[tuple[int, str, Path]] = []
    for version_dir in root.glob("version=*"):
        if not version_dir.is_dir():
            continue
        files = list(version_dir.rglob("*.parquet"))
        if files:
            newest = max(f.stat().st_mtime_ns for f in files)
            candidates.append((newest, version_dir.name.split("=", 1)[1], version_dir))
    if not candidates:
        return None
    _newest, name, version_dir = max(candidates, key=lambda item: (item[0], item[1]))
    logger.warning(
        f"[monitor] FEATURE_VERSION 未设置，按写入时间自动选择最新特征版本: {name}"
        "（口径可能随新版本写入静默切换，生产建议显式固定 FEATURE_VERSION）")
    return name, version_dir


def _signature(root_name: str) -> tuple[tuple, list[Path]] | None:
    """数据集文件签名（路径名 + mtime）；目录不存在返回 None。

    ⚠️ 口径：``features`` 是**版本化**数据集，签名（缓存键 / 变更检测用）**只统计
    当前有效单一版本**（``_resolve_feature_dir`` 解析，与
    ``data.features.resolve_feature_version`` 同口径）；否则 v1 + v2g 的文件会把
    两个特征版本的指纹与读取搅在一起，导致监控混算。其它无版本分区的数据集
    （predictions 等）仍对目录内全部 parquet 取指纹。
    """
    s = get_settings()
    root = s.DATA_ROOT / root_name
    if not root.exists():
        return None
    if root_name == "features":
        resolved = _resolve_feature_dir(root)
        if resolved is None:
            return None
        _version, version_dir = resolved
        files = sorted(version_dir.rglob("*.parquet"))
    else:
        files = sorted(root.rglob("*.parquet"))
    if not files:
        return None
    return (tuple((str(f.relative_to(root)), f.stat().st_mtime_ns) for f in files), files)


def _features_frame(days: int = 400) -> pl.DataFrame:
    """features 长表（近 days 个自然日窗口），签名缓存（与 studio 同款策略）。

    ⚠️ 只读取**单一有效版本**（``_signature('features')`` 已限定到当前版本目录），
    避免把 ``version=v1`` 与 ``version=v2g`` 的行 concat 后混算监控指标。日期列
    仍经 ``_normalize_date_col`` 归一（容忍 Date/Datetime/Utf8 异构）。
    """
    sig = _signature("features")
    if sig is None:
        raise RuntimeError("features 数据不存在")
    files_sig, files = sig
    with _FEAT_LOCK:
        hit = _FEATURES_CACHE.get("all")
        if hit and hit[0] == files_sig:
            df = hit[1]
        else:
            df = pl.concat([pl.read_parquet(f) for f in files], how="diagonal_relaxed")
            # 归一化必须在写入缓存之前：缓存只允许存 Date 口径，否则一个 Utf8
            # 命中帧会绕过修复直接喂给下游。
            df = _normalize_date_col(df)
            _FEATURES_CACHE.clear()
            _FEATURES_CACHE["all"] = (files_sig, df)
    dmax = _to_date(df["date"].max())
    return df.filter(pl.col("date") >= dmax - timedelta(days=int(days * 1.6)))


def _predictions_frame() -> pl.DataFrame:
    """predictions 长表（date/symbol/pred_score/model_version）。"""
    sig = _signature("predictions")
    if sig is None:
        raise RuntimeError("predictions 数据不存在（请先运行流水线 infer 步骤）")
    df = pl.concat([pl.read_parquet(f) for f in sig[1]], how="diagonal_relaxed")
    df = _normalize_date_col(df)
    return df.unique(subset=["date", "symbol"], keep="last")


def _close_wide(feat: pl.DataFrame) -> pd.DataFrame:
    """hfq 收盘宽表（date × symbol，来自 features.close，与因子同源同口径）。"""
    pdf = feat.select(["date", "symbol", "close"]).to_pandas()
    pdf["date"] = pd.to_datetime(pdf["date"])
    return pdf.pivot_table(index="date", columns="symbol", values="close", aggfunc="last")


# ---------------- IC / 半衰期 ----------------
def compute_ic_series(preds: pl.DataFrame, close_w: pd.DataFrame,
                      horizon: int = 5) -> pd.DataFrame:
    """逐日截面 RankIC（spearman）；截面过窄或前向收益不足的日期记 NaN。"""
    fwd = close_w.shift(-horizon) / close_w - 1.0
    pp = preds.to_pandas()
    pp["date"] = pd.to_datetime(pp["date"])
    pred_w = pp.pivot_table(
        index="date", columns="symbol", values="pred_score", aggfunc="last").sort_index()
    rows = []
    for d, score in pred_w.iterrows():
        n_pred = int(score.notna().sum())   # 预测覆盖数（与可评估性分开披露）
        if d not in fwd.index:
            rows.append({"date": d, "n_symbols": n_pred, "ic": np.nan})
            continue
        r = fwd.loc[d]
        pair = pd.concat([score, r], axis=1, keys=["p", "f"]).dropna()
        n = len(pair)
        if n < MIN_SYMBOLS_PER_DAY:
            # 末尾 horizon 窗口未到期的日期 ic 为 NaN——不是缺数据，是未来尚不可知
            rows.append({"date": d, "n_symbols": n_pred, "ic": np.nan})
            continue
        rows.append({"date": d, "n_symbols": n_pred,
                     "ic": float(pair["p"].rank().corr(pair["f"].rank()))})
    return pd.DataFrame(rows).set_index("date").sort_index()


def compute_ic_by_horizon(preds: pl.DataFrame, close_w: pd.DataFrame,
                          window_end: pd.Timestamp, window_days: int = 120,
                          horizons: tuple[int, ...] = IC_HORIZONS) -> dict[int, float]:
    """近窗口内各 horizon 的平均 RankIC（半衰期拟合输入）。"""
    out: dict[int, float] = {}
    for h in horizons:
        ic = compute_ic_series(preds, close_w, horizon=h)["ic"]
        w = ic.loc[:window_end].dropna().tail(window_days)
        out[h] = float(w.mean()) if len(w) >= 10 else np.nan
    return out


def fit_half_life(ic_by_h: dict[int, float]) -> tuple[float | None, str]:
    """IC 衰减拟合：ln(IC_h) = a - b·h，半衰期 = ln2 / b（单位：交易日）。

    IC 非正 / 随期数上升 / 有效点不足时返回 (None, 说明)。
    """
    pts = sorted((h, ic) for h, ic in ic_by_h.items()
                 if ic is not None and not (isinstance(ic, float) and math.isnan(ic)) and ic > 0)
    if len(pts) < 3:
        return None, "IC 有效周期点不足或存在非正值，无法拟合半衰期"
    hs = np.array([p[0] for p in pts], dtype=float)
    lns = np.log(np.array([p[1] for p in pts], dtype=float))
    slope = float(np.polyfit(hs, lns, 1)[0])   # ln(IC) 斜率 = -b
    if slope >= -1e-9:
        return None, "IC 未随期数衰减（斜率非负），半衰期无意义"
    return round(math.log(2.0) / -slope, 2), "指数衰减拟合（交易日）"


# ---------------- PSI 漂移 ----------------
def compute_psi(feat: pl.DataFrame, recent_days: int = 20,
                baseline_days: int = 250, bins: int = 10) -> dict:
    """特征 PSI：近 recent_days 个交易日 vs 之前 baseline_days 个交易日基线。

    分箱边界取基线分位数（等频），小桶比例以 1e-6 下限防 log(0)。
    """
    pdf = feat.to_pandas()
    dates = np.sort(pdf["date"].unique())
    if len(dates) < recent_days + 30:
        return {"ok": False, "error": "features 交易日不足，无法计算 PSI"}
    recent_dates = set(dates[-recent_days:])
    baseline_dates = set(dates[-(recent_days + baseline_days):-recent_days])
    recent = pdf[pdf["date"].isin(recent_dates)]
    baseline = pdf[pdf["date"].isin(baseline_dates)]
    skip = {"symbol", "date", "close", "label_ret", "year"}
    factors = [c for c in pdf.columns
               if c not in skip and pd.api.types.is_numeric_dtype(pdf[c])]
    eps = 1e-6
    per_factor: dict[str, float] = {}
    for f in factors:
        b = baseline[f].dropna()
        r = recent[f].dropna()
        if len(b) < 100 or len(r) < 30:
            continue
        edges = np.unique(np.quantile(b, np.linspace(0, 1, bins + 1)[1:-1]))
        if len(edges) < 2:      # 基线近常数（如复权因子）——无信息，跳过
            continue
        b_pct = np.histogram(b, bins=np.concatenate([[-np.inf], edges, [np.inf]]))[0] / len(b)
        r_pct = np.histogram(r, bins=np.concatenate([[-np.inf], edges, [np.inf]]))[0] / len(r)
        b_pct, r_pct = np.clip(b_pct, eps, None), np.clip(r_pct, eps, None)
        per_factor[f] = float(np.sum((r_pct - b_pct) * np.log(r_pct / b_pct)))
    if not per_factor:
        return {"ok": False, "error": "无有效特征可计算 PSI"}
    top = sorted(per_factor.items(), key=lambda kv: -kv[1])[:5]
    return {"ok": True, "recent_days": recent_days, "baseline_days": baseline_days,
            "n_factors": len(per_factor),
            "mean": round(float(np.mean(list(per_factor.values()))), 4),
            "max": round(max(per_factor.values()), 4),
            "top": [{"factor": f, "psi": round(v, 4)} for f, v in top]}


def ks_two_sample(b: np.ndarray, r: np.ndarray) -> float:
    """两样本 KS 统计量 D = max|F_b(x) − F_r(x)|（经验 CDF 逐点比较，无 scipy）。

    对两组合并后的每个观测点取两侧累积比例之差的最大值——与
    scipy.stats.ks_2samp 的双侧统计量一致（p 值不在此计算）。
    """
    bs, rs = np.sort(b), np.sort(r)
    grid = np.concatenate([bs, rs])
    cdf_b = np.searchsorted(bs, grid, side="right") / len(bs)
    cdf_r = np.searchsorted(rs, grid, side="right") / len(rs)
    return float(np.max(np.abs(cdf_b - cdf_r)))


def compute_ks(feat: pl.DataFrame, recent_days: int = 20,
               baseline_days: int = 250) -> dict:
    """特征 KS：与 compute_psi 同日期切分同特征清单，双口径互验。

    漂移状态判定仍以 PSI 为准（_drift_state 不变）；KS 在此仅作并列披露。
    每因子另给 α=0.05 的渐近临界值 1.36·sqrt((n1+n2)/(n1·n2))，
    超越该值的因子计数如实报告（n_over_crit）。
    """
    pdf = feat.to_pandas()
    dates = np.sort(pdf["date"].unique())
    if len(dates) < recent_days + 30:
        return {"ok": False, "error": "features 交易日不足，无法计算 KS"}
    recent_dates = set(dates[-recent_days:])
    baseline_dates = set(dates[-(recent_days + baseline_days):-recent_days])
    recent = pdf[pdf["date"].isin(recent_dates)]
    baseline = pdf[pdf["date"].isin(baseline_dates)]
    skip = {"symbol", "date", "close", "label_ret", "year"}
    factors = [c for c in pdf.columns
               if c not in skip and pd.api.types.is_numeric_dtype(pdf[c])]
    per_factor: dict[str, float] = {}
    n_over_crit = 0
    for f in factors:
        b = baseline[f].dropna().to_numpy()
        r = recent[f].dropna().to_numpy()
        if len(b) < 100 or len(r) < 30:   # 与 PSI 同下限，口径一致
            continue
        d = ks_two_sample(b, r)
        per_factor[f] = d
        crit = 1.36 * math.sqrt((len(b) + len(r)) / (len(b) * len(r)))
        if d > crit:
            n_over_crit += 1
    if not per_factor:
        return {"ok": False, "error": "无有效特征可计算 KS"}
    top = sorted(per_factor.items(), key=lambda kv: -kv[1])[:5]
    return {"ok": True, "recent_days": recent_days, "baseline_days": baseline_days,
            "n_factors": len(per_factor),
            "mean": round(float(np.mean(list(per_factor.values()))), 4),
            "max": round(max(per_factor.values()), 4),
            "n_over_crit": n_over_crit,
            "note": "KS 与 PSI 同切分互验；漂移判定仍以 PSI 为准",
            "top": [{"factor": f, "ks": round(v, 4)} for f, v in top]}


# ---------------- 状态机 ----------------
def _ic_state(mean_ic15: float, hist_mean: float | None, hist_std: float | None) -> str:
    """近期 IC 状态：反向 / 1.5σ 外 → degraded；1σ 外 → watch。"""
    if hist_mean is None or hist_std is None:
        return "degraded" if mean_ic15 < 0 else "healthy"
    if mean_ic15 < 0 or mean_ic15 < hist_mean - DEGRADED_SIGMA * hist_std:
        return "degraded"
    if mean_ic15 < hist_mean - WATCH_SIGMA * hist_std:
        return "watch"
    return "healthy"


def _drift_state(psi_max: float | None) -> str:
    if psi_max is None:
        return "unknown"
    if psi_max > PSI_DEGRADED:
        return "degraded"
    if psi_max > PSI_WATCH:
        return "watch"
    return "healthy"


def _worst(states: list[str]) -> str:
    return max(states, key=lambda x: _STATE_RANK.get(x, -1))


# ---------------- 自动重训（promote 门禁把关） ----------------
def _compute_dataset_version() -> str:
    """数据集指纹（与 scripts/retrain.py 同口径）：标的数+行数+日期范围。"""
    from ..data.parquet_store import read_all_symbols, read_symbol_dataset

    syms = read_all_symbols("daily_bar_hfq")
    rows = 0
    dmin: date | None = None
    dmax: date | None = None
    for sym in syms:
        df = read_symbol_dataset("daily_bar_hfq", sym)
        if df.is_empty():
            continue
        rows += df.height
        a, b = _to_date(df["date"].min()), _to_date(df["date"].max())
        dmin = a if dmin is None else min(dmin, a)
        dmax = b if dmax is None else max(dmax, b)
    stamp = f"{len(syms)}s{rows}r"
    if dmin is not None and dmax is not None:
        stamp += f"{dmin:%Y%m%d}-{dmax:%Y%m%d}"
    return f"ds_{stamp}"


def maybe_auto_retrain(reason: str) -> dict:
    """漂移触发重训入口：KV 守卫（间隔/防并发）后后台线程执行。"""
    s = get_settings()
    now = time.time()
    with _RETRAIN_LOCK:
        last = kv_get(_RETRAIN_KEY) or {}
        if last.get("status") == "running" and now - last.get("started_ts", 0) < 7200:
            return {"attempted": False, "note": "上一次自动重训仍在进行中"}
        if now - last.get("finished_ts", 0) < s.RETRAIN_MIN_INTERVAL_HOURS * 3600:
            return {"attempted": False,
                    "note": f"距上次重训不足 {s.RETRAIN_MIN_INTERVAL_HOURS} 小时"}
        kv_set(_RETRAIN_KEY, {"status": "running", "started_ts": now,
                              "trigger": reason, "started_at": datetime.now().isoformat(timespec="seconds")})
    t = threading.Thread(target=_retrain_worker, args=(reason,),
                         name="aqp-monitor-retrain", daemon=True)
    t.start()
    return {"attempted": True, "reason": reason, "note": "重训已后台启动（promote 门禁把关）"}


def _retrain_worker(reason: str) -> None:
    """后台重训：复用 retrain.py 同款流程（candidate 注册 → 门禁 → 显式 promote）。"""
    from .features import FEATURE_VERSION
    from .registry import (PromotePolicy, auto_min_rank_ic, evaluate_candidate,
                           get_model, get_production, promote_model)
    from .train_lgbm import train_lgbm

    s = get_settings()
    result: dict = {}
    try:
        feat_dir = s.DATA_ROOT / "features" / f"version={FEATURE_VERSION}"
        parts = sorted(feat_dir.glob("year=*.parquet"))
        if not parts:
            raise RuntimeError(f"features 不存在：{feat_dir}")
        df = pd.concat([pd.read_parquet(p) for p in parts], ignore_index=True)
        dataset_version = _compute_dataset_version()
        # xsec_demean 与当前生产模型（20260905_003646_bulk1133_demean）的
        # 训练配方保持一致：漂移重训的产物要和门禁里的生产模型同口径可比。
        r = train_lgbm(df, horizon=s.ML_LABEL_HORIZON,
                       holdout_days=s.ML_HOLDOUT_DAYS, test_days=s.ML_TEST_DAYS,
                       num_boost_round=1000, stopping_rounds=50,
                       version_suffix="monitor", dataset_version=dataset_version,
                       xsec_demean=True)
        cand = get_model("lgbm_v1", r["version"])
        if cand is None:
            raise RuntimeError("候选模型未注册（train_lgbm 异常）")
        n_sym = int(r.get("n_symbols") or df["symbol"].nunique())
        n_val = int(r.get("valid_days") or 250)
        policy = PromotePolicy(min_valid_rank_ic=auto_min_rank_ic(n_sym, n_val))
        decision = evaluate_candidate(cand, get_production(), policy)
        res = promote_model("lgbm_v1", r["version"], by="monitor",
                            reason=decision.reason, policy=policy)
        result = {"status": "done", "model_version": r["version"],
                  "promoted": bool(res.get("promoted")),
                  "valid_rank_ic": r.get("valid_rank_ic"),
                  "gate_reason": decision.reason}
        msg = (f"漂移触发自动重训完成：候选 {r['version']} "
               f"valid RankIC={r.get('valid_rank_ic')}，"
               f"promote={'通过' if res.get('promoted') else '被门禁拒绝'}"
               f"（{decision.reason}）")
        logger.info(f"[monitor] {msg}")
    except Exception as e:  # noqa: BLE001 后台任务兜底：记录并告警
        result = {"status": "failed", "error": f"{type(e).__name__}: {e}"}
        logger.error(f"[monitor] 自动重训失败: {e!r}")
        msg = f"漂移触发自动重训失败：{type(e).__name__}: {e}"
    result.update({"finished_ts": time.time(),
                   "finished_at": datetime.now().isoformat(timespec="seconds"),
                   "trigger": reason})
    prev = kv_get(_RETRAIN_KEY) or {}
    prev.update(result)
    kv_set(_RETRAIN_KEY, prev)
    from ..core import events

    events.publish_threadsafe("monitor", msg)


# ---------------- 主入口 ----------------
def run_monitor(trigger: str = "manual") -> dict:
    """计算健康度 + 漂移 → 状态机 → 持久化 → 告警 →（漂移时）自动重训。

    数据缺失时返回 {"ok": False, "error": ...}，不抛异常（调度循环调用）。
    """
    s = get_settings()
    horizon = s.ML_LABEL_HORIZON
    snap: dict = {"ok": False, "computed_at": datetime.now().isoformat(timespec="seconds"),
                  "trigger": trigger, "model_horizon": horizon}
    try:
        feat = _features_frame()
        preds = _predictions_frame()
    except RuntimeError as e:
        snap["error"] = str(e)
        return snap

    close_w = _close_wide(feat)
    version_counts = (preds.group_by("model_version").len()
                      .sort("len", descending=True).head(3))
    snap["pred_model_versions"] = {r["model_version"]: r["len"]
                                   for r in version_counts.iter_rows(named=True)}

    # ---- 滚动 IC（主 horizon）----
    ic = compute_ic_series(preds, close_w, horizon=horizon)
    evaluable = ic["ic"].dropna()
    snap["n_pred_dates"] = int(len(ic))
    snap["n_eval_dates"] = int(len(evaluable))
    snap["ic_series_tail"] = [
        {"date": str(d.date()), "ic": (None if pd.isna(v) else round(float(v), 4)),
         "n_symbols": int(n)}
        for d, v, n in ic.tail(40)[["ic", "n_symbols"]].itertuples(name=None)
    ] if len(ic) else []

    recent = evaluable.tail(RECENT_WINDOW)
    snap["recent"] = {"window": RECENT_WINDOW, "n_days": int(len(recent))}
    if len(recent) >= 3:
        snap["recent"].update({
            "start": str(recent.index[0].date()), "end": str(recent.index[-1].date()),
            "mean_ic": round(float(recent.mean()), 4),
            "icir": round(float(recent.mean() / recent.std()), 3)
            if recent.std() and recent.std() > 1e-12 else None})

    prev_dates = evaluable.iloc[:-RECENT_WINDOW].tail(HIST_WINDOW)
    hist_mean = hist_std = None
    if len(prev_dates) >= 30:
        hist_mean = float(prev_dates.mean())
        hist_std = float(prev_dates.std()) or None
        snap["history"] = {"window": int(len(prev_dates)), "mean_ic": round(hist_mean, 4),
                           "std_ic": round(hist_std, 4) if hist_std else None}
    else:
        snap["history"] = {"window": int(len(prev_dates)),
                           "note": "历史基准不足 30 日，仅按 IC 正负判定"}

    # ---- 半衰期（多周期 IC 衰减，近 120 可评估日均值）----
    end_d = evaluable.index[-1] if len(evaluable) else ic.index.max()
    ic_by_h = compute_ic_by_horizon(preds, close_w, end_d)
    hl, hl_note = fit_half_life(ic_by_h)
    snap["half_life"] = {
        "value": hl, "note": hl_note,
        "mean_ic_by_horizon": {str(h): (None if v is None or (isinstance(v, float) and math.isnan(v))
                                        else round(float(v), 4))
                               for h, v in ic_by_h.items()}}

    # ---- PSI / KS（双口径互验，判定以 PSI 为准）----
    psi = compute_psi(feat)
    snap["psi"] = {k: v for k, v in psi.items()} if psi.get("ok") else {"ok": False,
                                                                        "error": psi.get("error")}
    ks = compute_ks(feat)
    snap["ks"] = {k: v for k, v in ks.items()} if ks.get("ok") else {"ok": False,
                                                                     "error": ks.get("error")}

    # ---- 状态机 ----
    mean_ic15 = float(recent.mean()) if len(recent) else None
    if len(recent) < MIN_EVAL_DATES or mean_ic15 is None:
        ic_state = "unknown"
    elif hist_mean is None:
        ic_state = _ic_state(mean_ic15, None, None)
    else:
        ic_state = _ic_state(mean_ic15, hist_mean, hist_std)
    drift_state = _drift_state(psi.get("max") if psi.get("ok") else None)
    state = _worst([ic_state, drift_state])
    prev_snap = kv_get(_HEALTH_KEY) or {}
    prev_state = prev_snap.get("state") if prev_snap.get("ok") else None
    snap.update({"ok": True, "ic_state": ic_state, "drift_state": drift_state,
                 "state": state, "prev_state": prev_state,
                 "thresholds": {"watch_sigma": WATCH_SIGMA,
                                "degraded_sigma": DEGRADED_SIGMA,
                                "reverse_ic": 0.0,
                                "psi_watch": PSI_WATCH, "psi_degraded": PSI_DEGRADED}})

    # 状态日志（跨快照保留最近 20 条）
    state_log = list(prev_snap.get("state_log") or [])
    if state != prev_state:
        state_log.append({"ts": snap["computed_at"], "state": state,
                          "ic_state": ic_state, "drift_state": drift_state,
                          "mean_ic15": mean_ic15 and round(mean_ic15, 4),
                          "psi_max": psi.get("max") if psi.get("ok") else None})
        snap["state_log"] = state_log[-20:]
        _notify_state_change(prev_state, state, snap)
    else:
        snap["state_log"] = state_log[-20:]

    # ---- 漂移联动自动重训 ----
    if (s.AUTO_RETRAIN_ON_DRIFT and psi.get("ok") and psi.get("max", 0) > PSI_DEGRADED):
        snap["retrain"] = maybe_auto_retrain(
            f"PSI={psi['max']}>{PSI_DEGRADED}（{psi['top'][0]['factor']} 漂移最大）")
    else:
        snap["retrain"] = None

    kv_set(_HEALTH_KEY, snap)
    logger.info(f"[monitor] state={state} ic={ic_state} drift={drift_state} "
                f"mean_ic15={mean_ic15} psi_max={psi.get('max') if psi.get('ok') else 'n/a'}")
    return snap


def _notify_state_change(prev: str | None, cur: str, snap: dict) -> None:
    """状态变化推送顶栏通知（publish_threadsafe 对循环内/外线程均安全）。"""
    from ..core import events

    label = {"healthy": "健康", "watch": "观察", "degraded": "降级", "unknown": "数据不足"}
    if cur == "degraded":
        kind_msg = "因子健康度【降级】"
    elif cur == "watch":
        kind_msg = "因子健康度转入【观察】"
    elif cur == "healthy" and prev in ("watch", "degraded"):
        kind_msg = "因子健康度恢复【健康】"
    else:
        return  # unknown/首次 healthy 不打扰
    events.publish_threadsafe("monitor", f"{kind_msg}：{snap.get('recent', {}).get('mean_ic')}")


def get_health_snapshot() -> dict | None:
    """读取最近一次监控快照（无则 None）。"""
    snap = kv_get(_HEALTH_KEY)
    return snap if snap and snap.get("ok") else None


def get_retrain_record() -> dict | None:
    return kv_get(_RETRAIN_KEY)
