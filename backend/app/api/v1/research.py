"""策略研究工作台 API（四象限，全部真实计算）。

数据源（零模拟值）：
- 因子截面：features parquet（alpha_basic_v1，42 因子，2022~2026，120 只）
- 前向收益：features 内 hfq close（与训练标签同源）
- 优化/压力：daily_bar_hfq 真实收盘价
- 执行冲击：daily_bar 真实 OHLCV（日 VWAP = amount/volume）
- 实验/模型：model_registry 表 + models/exp 产物（gain 重要性/边际效应）
- 容量估算：universe_daily 真实成交额中位数 × 参与率上限 × 持仓数

⚠️ 微观结构模块为**日频执行口径**（无 L2/Tick 数据），前端如实标注。
"""
from __future__ import annotations

import json
import sqlite3
import time
from datetime import date, timedelta
from pathlib import Path

import numpy as np
import pandas as pd
import polars as pl
from fastapi import APIRouter, Depends, Query
from loguru import logger
from pydantic import BaseModel, Field

from ...core.config import get_settings
from ...core.auth import require_role
from ...core.compute_guard import compute_slot
from ...core.compute_pool import run_in_compute_pool
from ...core.errors import APIResponse, AQPException, ERR_DATA_EMPTY, ok
from ...data.features import read_feature_frame, resolve_feature_version
from ...data.parquet_store import (as_py_date, as_py_float,
                                   read_parquet_columns, read_symbol_dataset)
from ...domain.research import (
    STYLE_FACTOR_MAP,
    execution_impact_sim,
    factor_corr_matrix,
    factor_ic_decay_series,
    factor_ic_table,
    optimize_portfolio,
    portfolio_stress_replay,
    quantile_curves,
    stress_windows,
)
from ...ml.purged_cv import PurgedGroupTimeSeriesSplit

router = APIRouter()

# features 数据的进程内 TTL 缓存（48MB parquet 不宜每请求重读）
_FEAT_CACHE: dict[str, tuple[float, object]] = {}
_FEAT_TTL = 600.0
# P2-13：条目上限。每个 key 的 value 是整份 pandas 大表，
# 长期运行下多窗口键（desk 160 / impact-sim 120 / 其余 380…）堆积
# 会放大常驻内存；超限按写入时间淘汰最旧条目。
_FEAT_CACHE_MAX = 6
_HORIZONS = (1, 5, 20)


async def _run_sync(fn, *args):
    """在**专用计算池**执行同步计算（research 端点统一入口，P-8a）。

    2026-10-04 上线前全检：本文件的同步重活原先走 ``asyncio.to_thread`` ⇒
    event loop **默认池**（本机 22 槽），与 ``/health/ready`` 探针共用。research
    是全站第二重的计算族（因子 IC/ICIR、相关矩阵、特征重要度、年度实验室、寻优、
    压力测试，前端预算 100~180s）。

    ⚠️ 实测教训（勿重犯）：修这一处**并不**覆盖全部调用点 —— 本文件另有多处
    端点**直接**写 ``await asyncio.to_thread(_run)``，绕过了本入口。当时先只改了
    这里，静态守卫 ``test_no_heavy_to_thread_left_in_research_and_backtest``
    立刻报出 10 处遗漏。批量替换后才真正清零。新增重活端点时请**走本入口或
    ``run_in_compute_pool``**，别直接写 ``asyncio.to_thread``。
    """
    return await run_in_compute_pool(fn, *args)


def _cached(key: str, loader):
    now = time.monotonic()
    hit = _FEAT_CACHE.get(key)
    if hit and now - hit[0] < _FEAT_TTL:
        return hit[1]
    val = loader()
    _FEAT_CACHE[key] = (now, val)
    if len(_FEAT_CACHE) > _FEAT_CACHE_MAX:
        for old in sorted(_FEAT_CACHE, key=lambda k: _FEAT_CACHE[k][0])[
                :len(_FEAT_CACHE) - _FEAT_CACHE_MAX]:
            _FEAT_CACHE.pop(old, None)
    return val


def _norm_symbol(code: str) -> str:
    code = code.strip().upper()
    if "." in code:
        return code
    return f"{code}.{'SH' if code.startswith(('6', '9', '5')) else 'SZ'}"


def _load_features(days: int = 380, version: str | None = None) -> pd.DataFrame:
    """读取单一 features 版本的长表，并截取近 ``days`` 个交易日窗口。"""
    selected_version = resolve_feature_version(version)

    def _load() -> pd.DataFrame:
        _version, df = read_feature_frame(selected_version)
        dmax = as_py_date(df["date"].max())
        df = df.filter(pl.col("date") >= dmax - timedelta(days=int(days * 1.6)))
        pdf = df.to_pandas()
        pdf["date"] = pd.to_datetime(pdf["date"])
        return pdf

    # 缓存键必须同时含版本和窗口；否则版本切换或小窗口请求会污染后续计算。
    return _cached(f"features:{selected_version}:{days}", _load)


def _wides(pdf: pd.DataFrame, factors: list[str], horizon: int):
    """构建因子宽表字典 + 前向收益宽表 + 规模代理宽表。"""
    close_w = pdf.pivot_table(index="date", columns="symbol",
                              values="close", aggfunc="last")
    fwd_w = close_w.shift(-horizon) / close_w - 1.0
    factor_wides = {
        f: pdf.pivot_table(index="date", columns="symbol", values=f, aggfunc="last")
        for f in factors
    }
    size_w = pdf.pivot_table(index="date", columns="symbol",
                             values="amount_per_share", aggfunc="last")
    return factor_wides, fwd_w, close_w, size_w


# ==================== 顶栏总览 ====================
@router.get("/overview", response_model=APIResponse[dict])
async def research_overview(
        _user: dict = Depends(require_role("researcher"))) -> APIResponse[dict]:
    """顶栏指标：因子库 / 模型版本 / 策略容量 / 最近同步（全部真实来源）。"""
    def _sync() -> dict:
        pdf = _load_features()
        factor_cols = [c for c in pdf.columns
                       if c not in ("symbol", "date", "close", "label_ret")]
        s = get_settings()
        # 模型版本数：读取失败时**不得**用 0 冒充"没有模型"（会误导用户以为模型库为空）。
        # 失败 ⇒ 返回 None（前端 `?? '—'` 显示为不可用）并附 note 说明，同时补 warning。
        model_n: int | None = None
        model_note: str | None = None
        try:
            with sqlite3.connect(s.SQLITE_PATH, timeout=30) as conn:
                model_n = conn.execute(
                    "SELECT COUNT(*) FROM model_registry").fetchone()[0]
        except Exception as e:  # noqa: BLE001 本地库读取失败：如实报不可得，不冒充 0
            logger.warning(
                f"[research] 读取 model_registry 版本数失败: {type(e).__name__}: {e!r}")
            model_note = "模型版本数暂不可用（本地库读取失败，详见服务端日志）"
        # 容量估算（真实口径）：universe 最新截面成交额（close×volume）中位数
        #   × 单标的参与率上限 1% × 组合持仓数 20 → 单日可执行规模
        cap = None
        uni_files = sorted((s.DATA_ROOT / "universe_daily").rglob("*.parquet"))
        if uni_files:
            uni = pl.concat([pl.read_parquet(f) for f in uni_files[-1:]],
                            how="diagonal_relaxed")
            if {"close", "volume"} <= set(uni.columns):
                amt = (uni["close"] * uni["volume"]).drop_nulls()
                med_amt = as_py_float(amt.median()) if len(amt) else 0.0
                if med_amt > 0:
                    cap = med_amt * 0.01 * 20
        return {
            "factor_count": len(factor_cols),
            "expression_count": 0,  # 由下方注入
            "model_version_count": model_n,
            # 与 model_version_count 配套的说明：非 None 时表示该计数不可得（勿当 0 读）。
            "model_version_note": model_note,
            "capacity_estimate_yi": round(cap / 1e8, 2) if cap else None,
            "capacity_formula": "universe日成交额中位数 × 1%参与率 × 20只持仓",
            "factor_universe": f"{pdf['symbol'].nunique()} 只 · "
                               f"{str(pdf['date'].max())[:10]} 截面",
        }
    from ...ml.alpha_expr import ALPHA158_LITE
    data = await run_in_compute_pool(_sync)
    data["expression_count"] = len(ALPHA158_LITE)
    return ok(data)


# ==================== 左上：因子研发 ====================
class FactorIcirRequest(BaseModel):
    factors: list[str] = Field(..., min_length=1, max_length=8)
    horizon: int = Field(5, ge=1, le=60)
    neutralize_size: bool = Field(False, description="市值中性化（log 成交额代理）")


@router.post("/factor-icir", response_model=APIResponse[dict])
async def factor_icir(req: FactorIcirRequest,
                      _user: dict = Depends(require_role("researcher")),
                      _compute: None = Depends(compute_slot)) -> APIResponse[dict]:
    """因子 IC/IR 面板：逐日截面 Rank IC（MAD 去极值 + 可选市值中性化）。"""
    def _run() -> dict:
        pdf = _load_features()
        missing = [f for f in req.factors if f not in pdf.columns]
        if missing:
            raise AQPException(ERR_DATA_EMPTY, f"features 中不存在因子: {missing}")
        fw, fwd, _close, size_w = _wides(pdf, req.factors, req.horizon)
        table = factor_ic_table(
            fw, fwd, size_w if req.neutralize_size else None)
        return {"horizon": req.horizon,
                "neutralize_size": req.neutralize_size,
                "rows": table,
                "available_factors": [c for c in pdf.columns
                                      if c not in ("symbol", "date", "close",
                                                   "label_ret")]}
    return ok(await run_in_compute_pool(_run))


class FactorCorrRequest(BaseModel):
    factors: list[str] = Field(..., min_length=2, max_length=8)
    window_days: int = Field(60, ge=20, le=250)


@router.post("/factor-corr", response_model=APIResponse[dict])
async def factor_corr(req: FactorCorrRequest,
                      _user: dict = Depends(require_role("researcher")),
                      _compute: None = Depends(compute_slot)) -> APIResponse[dict]:
    """因子正交化热力图：近 N 日逐日截面 Spearman 的时序均值矩阵。"""
    def _run() -> dict:
        pdf = _load_features()
        missing = [f for f in req.factors if f not in pdf.columns]
        if missing:
            raise AQPException(ERR_DATA_EMPTY, f"features 中不存在因子: {missing}")
        fw, _fwd, _close, _size = _wides(pdf, req.factors, 1)
        return factor_corr_matrix(fw, window_days=req.window_days)
    return ok(await run_in_compute_pool(_run))


class FactorQuantileRequest(BaseModel):
    factor: str = Field(..., min_length=1, max_length=64)
    horizon: int = Field(5, ge=1, le=60)
    n_quantiles: int = Field(5, ge=3, le=10)


@router.post("/factor-quantile", response_model=APIResponse[dict])
async def factor_quantile(req: FactorQuantileRequest,
                          _user: dict = Depends(require_role("researcher")),
                          _compute: None = Depends(compute_slot)) -> APIResponse[dict]:
    """因子分层收益图：Top/Middle/Bottom 分位组合累计净值（真实滚动持有）。"""
    def _run() -> dict:
        pdf = _load_features()
        if req.factor not in pdf.columns:
            raise AQPException(ERR_DATA_EMPTY, f"features 中不存在因子: {req.factor}")
        fw, _fwd, _close, _size = _wides(pdf, [req.factor], req.horizon)
        return quantile_curves(fw[req.factor], _close,
                               horizon=req.horizon, n_quantiles=req.n_quantiles)
    return ok(await run_in_compute_pool(_run))


class FactorDecayRequest(BaseModel):
    factor: str = Field(..., min_length=1, max_length=64)
    lags: list[int] = Field(default=[1, 2, 3, 5, 10, 15, 20])
    neutralize_size: bool = Field(False, description="市值中性化")


@router.post("/factor-decay", response_model=APIResponse[dict])
async def factor_decay(req: FactorDecayRequest,
                       _user: dict = Depends(require_role("researcher")),
                       _compute: None = Depends(compute_slot)) -> APIResponse[dict]:
    """因子 IC 衰减与换手半衰期分析：测算滞后 1~20 期收益预测力衰减。"""
    def _run() -> dict:
        pdf = _load_features()
        if req.factor not in pdf.columns:
            raise AQPException(ERR_DATA_EMPTY, f"features 中不存在因子: {req.factor}")
        fw, _fwd, close_w, size_w = _wides(pdf, [req.factor], 1)
        res = factor_ic_decay_series(
            fw[req.factor], close_w, lags=req.lags,
            size_wide=size_w if req.neutralize_size else None,
        )
        res["factor"] = req.factor
        res["neutralize_size"] = req.neutralize_size
        return res
    return ok(await run_in_compute_pool(_run))


# ==================== 右上：MLOps 建模中心 ====================
@router.get("/experiments", response_model=APIResponse[list])
async def ml_experiments(
        _user: dict = Depends(require_role("researcher"))) -> APIResponse[list]:
    """实验追踪：model_registry 全部实验 + metrics.json 真实指标。"""
    def _run() -> list[dict]:
        s = get_settings()
        try:
            with sqlite3.connect(s.SQLITE_PATH, timeout=30) as conn:
                conn.row_factory = sqlite3.Row
                rows = conn.execute(
                    "SELECT model_name, version, status, is_production, "
                    "feature_version, created_at, train_start, train_end, "
                    "valid_ic, valid_rank_ic, valid_icir, valid_rmse, "
                    "test_ic, test_rank_ic, test_icir, test_rmse, "
                    "params_json, model_path "
                    "FROM model_registry ORDER BY id DESC LIMIT 30").fetchall()
        except AQPException:
            raise
        except Exception as e:  # noqa: BLE001
            raise AQPException(ERR_DATA_EMPTY, f"model_registry 不可读: {e!r}")
        out: list[dict] = []
        for r in rows:
            item = dict(r)
            params_json = item.pop("params_json", None)
            item["metrics"] = {
                k: item.pop(k, None) for k in
                ("valid_ic", "valid_rank_ic", "valid_icir", "valid_rmse",
                 "test_ic", "test_rank_ic", "test_icir", "test_rmse")}
            item["hyperparams"] = {}
            if params_json:
                try:
                    p = json.loads(params_json)
                    item["hyperparams"] = {
                        k: p.get(k) for k in
                        ("learning_rate", "num_leaves", "max_depth",
                         "min_data_in_leaf", "feature_fraction")}
                except json.JSONDecodeError:
                    pass
            mdir = s.MODEL_ROOT / "exp" / f"{item['model_name']}_{item['version']}"
            # 产物路径以登记时为准（LGBM=model.lgbm / TFT、GNN=model.pt），
            # 硬编码 lgbm 会把深度模型候选拍成"无产物"；旧行 model_path 可能为
            # 空，此时退回目录约定。
            mp = item.pop("model_path", None)
            item["has_model_file"] = (
                bool(mp) and Path(mp).exists()) or (mdir / "model.lgbm").exists()
            out.append(item)
        return out
    return ok(await run_in_compute_pool(_run))


class CvFoldRequest(BaseModel):
    n_splits: int = Field(5, ge=2, le=10)
    purge_window: int = Field(5, ge=0, le=60)
    embargo_window: int = Field(2, ge=0, le=60)


@router.post("/cv-folds", response_model=APIResponse[dict])
async def cv_folds(req: CvFoldRequest,
                   _user: dict = Depends(require_role("researcher")),
                   _compute: None = Depends(compute_slot)) -> APIResponse[dict]:
    """Purged CV 甘特图：真实特征日期上的 Purge+Embargo 划分边界。"""
    def _run() -> dict:
        pdf = _load_features()
        dates = np.sort(pdf["date"].unique())
        cv = PurgedGroupTimeSeriesSplit(
            n_splits=req.n_splits, purge_window=req.purge_window,
            embargo_window=req.embargo_window)
        folds: list[dict] = []
        pos = {d: i for i, d in enumerate(dates)}
        for i, (tr, te) in enumerate(cv.split(groups=np.asarray(dates, dtype=object))):
            tr_d, te_d = dates[tr], dates[te]
            folds.append({
                "fold": i + 1,
                "train_start": str(tr_d[0])[:10], "train_end": str(tr_d[-1])[:10],
                "test_start": str(te_d[0])[:10], "test_end": str(te_d[-1])[:10],
                "train_days": int(len(tr_d)), "test_days": int(len(te_d)),
                "gap_days": int(pos[te_d[0]] - pos[tr_d[-1]] - 1),
                "purge_window": req.purge_window,
                "embargo_window": req.embargo_window,
            })
        return {"total_days": int(len(dates)),
                "date_start": str(dates[0])[:10], "date_end": str(dates[-1])[:10],
                "folds": folds}
    return ok(await run_in_compute_pool(_run))


def _resolve_production_lgbm() -> tuple[str, Path]:
    """从 model_registry 解析唯一生产 LightGBM 产物，绝不回退实验目录。"""
    settings = get_settings()
    with sqlite3.connect(settings.SQLITE_PATH, timeout=30) as conn:
        row = conn.execute(
            "SELECT version, model_path FROM model_registry "
            "WHERE is_production=1 ORDER BY id DESC LIMIT 1"
        ).fetchone()
    if row is None:
        raise AQPException(ERR_DATA_EMPTY, "无 production 模型，请先训练并显式 promote")

    version, stored_path = str(row[0]), str(row[1] or "")
    model_path = Path(stored_path)
    if not stored_path or not model_path.is_file():
        raise AQPException(
            ERR_DATA_EMPTY,
            f"生产模型文件缺失：version={version} path={stored_path or '<empty>'}",
        )
    if model_path.suffix != ".lgbm":
        raise AQPException(
            ERR_DATA_EMPTY,
            f"生产模型 {version} 不是 LightGBM 产物，无法计算 gain 特征重要性",
        )
    return version, model_path


@router.get("/feature-importance", response_model=APIResponse[dict])
async def feature_importance(
        top_k: int = Query(12, ge=1, le=1000),
        _user: dict = Depends(require_role("researcher"))) -> APIResponse[dict]:
    """特征重要性（production 模型 gain）+ Top 特征边际效应曲线（部分依赖）。

    `top_k` 上下界（F10 同族）：此前无界 ⇒ `top_k=-1` 会走 `[:−1]`（"除最后一个"的
    怪异语义）、`top_k=1e9` 静默返回全部特征，调用方无法分辨"要多少给多少"。
    """
    def _run() -> dict:
        model_version, mpath = _resolve_production_lgbm()
        mdir = mpath.parent
        import lightgbm as lgb
        booster = lgb.Booster(model_file=str(mpath))
        names = booster.feature_name()
        gains = booster.feature_importance(importance_type="gain")
        order = np.argsort(gains)[::-1][:top_k]
        items = [{"feature": names[i], "gain": round(float(gains[i]), 2)}
                 for i in order if gains[i] > 0]

        # Top 特征的边际效应（部分依赖）：其余特征取中位数，扫描真实分位网格
        pdf = _load_features()
        feat_json = mdir / "features.json"
        kept = json.loads(feat_json.read_text(encoding="utf-8")) \
            if feat_json.exists() else names
        curves: list[dict] = []
        for it in items[:4]:
            fname = it["feature"]
            if fname not in pdf.columns or fname not in kept:
                continue
            sub = pdf[kept].dropna()
            if sub.empty:
                continue
            med = sub.median()
            grid = np.quantile(sub[fname].to_numpy(dtype=np.float64),
                               np.linspace(0.02, 0.98, 24))
            Xg = pd.DataFrame([med] * len(grid), columns=kept)
            Xg[fname] = grid
            pred = booster.predict(Xg.to_numpy(dtype=np.float64))
            curves.append({"feature": fname,
                           "points": [{"x": round(float(g), 5),
                                       "y": round(float(p), 6)}
                                      for g, p in zip(grid, pred)]})
        return {"model_version": model_version, "items": items, "effect_curves": curves}
    return ok(await run_in_compute_pool(_run))


# ==================== 左下：组合与风控 ====================
class OptimizeRequest(BaseModel):
    assets: list[dict] = Field(..., min_length=2, max_length=20,
                               description="[{code, weight}]")
    method: str = Field("risk_parity",
                        pattern=r"^(risk_parity|max_div|mvo|inverse_vol)$")
    weight_cap: float = Field(0.0, ge=0.0, le=1.0, description="单股上限（0=不限）")
    turnover_penalty: float = Field(0.0, ge=0.0, le=100.0)
    cov_window: int = Field(120, ge=60, le=500)


@router.post("/optimize", response_model=APIResponse[dict])
async def portfolio_optimize(req: OptimizeRequest,
                             _user: dict = Depends(require_role("researcher")),
                             _compute: None = Depends(compute_slot)) -> APIResponse[dict]:
    """组合优化控制台：真实收益协方差（LW 收缩+RMT）求解 + 风格暴露前后对比。"""
    def _run() -> dict:
        codes = [_norm_symbol(a["code"]) for a in req.assets]
        prev_w = np.array([float(a.get("weight", 0)) for a in req.assets])
        ssum = prev_w.sum()
        prev_w = prev_w / ssum if ssum > 0 else np.full(len(codes), 1 / len(codes))
        closes: dict[str, pd.Series] = {}
        for sym in codes:
            df = read_symbol_dataset("daily_bar_hfq", sym)
            if df.is_empty():
                raise AQPException(ERR_DATA_EMPTY,
                                   f"{sym} 无本地 hfq 行情（请先数据同步）")
            dcol = df.schema["date"]
            if dcol != pl.Date:
                df = df.with_columns(pl.col("date").cast(pl.Date))
            closes[sym] = df.sort("date").select(["date", "close"]).tail(
                req.cov_window + 1).to_pandas().set_index("date")["close"]
        close_w = pd.DataFrame(closes).dropna()
        rets = close_w.pct_change().dropna()
        if len(rets) < 60:
            raise AQPException(ERR_DATA_EMPTY,
                               f"有效收益样本不足（{len(rets)} < 60）")
        sol = optimize_portfolio(rets.to_numpy(dtype=np.float64),
                                 list(close_w.columns), req.method,
                                 weight_cap=req.weight_cap,
                                 turnover_penalty=req.turnover_penalty,
                                 prev_weights=prev_w)
        new_w = sol["weights"]

        # 风格暴露：features 最新截面 zscore × 权重（优化前 vs 优化后）
        pdf = _load_features(days=120)
        latest = pdf.sort_values("date").groupby("symbol").tail(1)

        def _exposure(weights: dict[str, float]) -> dict[str, float | None]:
            out: dict[str, float | None] = {}
            for style, col in STYLE_FACTOR_MAP.items():
                if col not in latest.columns:
                    out[style] = None
                    continue
                v = latest[col].to_numpy(dtype=np.float64)
                mu, sd = np.nanmean(v), np.nanstd(v, ddof=1)
                if sd < 1e-12:
                    out[style] = None
                    continue
                zmap = dict(zip(latest["symbol"], (v - mu) / sd))
                acc = sum(float(w) * float(zmap[sym])
                          for sym, w in weights.items()
                          if sym in zmap and np.isfinite(zmap[sym]))
                out[style] = round(acc, 4)
            return out

        expo_before = _exposure(dict(zip(codes, prev_w)))
        expo_after = _exposure(new_w)
        return {"method": sol["method"], "symbols": list(close_w.columns),
                "prev_weights": {s: round(float(w), 6)
                                 for s, w in zip(codes, prev_w)},
                "weights": new_w,
                "exposure_before": expo_before,
                "exposure_after": expo_after,
                "style_factor_map": {k: v for k, v in STYLE_FACTOR_MAP.items()},
                "n_obs": int(len(rets)),
                # 审计 P1-13 / B2-11：两个"看起来正常但实际不是"的口径必须回传给控制台
                "weight_cap_info": sol.get("weight_cap_info", {}),
                "expected_returns": sol.get("expected_returns", {})}
    return ok(await run_in_compute_pool(_run))


# ==================== 右下：策略执行与压力 ====================
class ImpactSimRequest(BaseModel):
    symbol: str = Field(..., min_length=1, max_length=16)
    side: str = Field("buy", pattern=r"^(buy|sell)$")
    order_amount: float = Field(5_000_000, gt=0, le=1e10)
    participation_cap: float = Field(0.05, ge=0.001, le=0.5)
    algo: str = Field("market", pattern=r"^(market|vwap|twap)$")
    split_days: int = Field(5, ge=1, le=20)
    lookback_days: int = Field(20, ge=5, le=120)


@router.post("/impact-sim", response_model=APIResponse[dict])
async def impact_sim(req: ImpactSimRequest,
                     _user: dict = Depends(require_role("researcher")),
                     _compute: None = Depends(compute_slot)) -> APIResponse[dict]:
    """日内成交冲击成本图（日频执行口径）：真实 OHLCV + sqrt 冲击模型。"""
    def _run() -> dict:
        sym = _norm_symbol(req.symbol)
        df = read_symbol_dataset("daily_bar", sym)
        if df.is_empty():
            raise AQPException(ERR_DATA_EMPTY, f"{sym} 无本地日线（请先数据同步）")
        need = {"open", "close", "volume", "amount"}
        missing = need - set(df.columns)
        if missing:
            raise AQPException(ERR_DATA_EMPTY,
                               f"{sym} 日线缺少字段: {sorted(missing)}")
        dcol = df.schema["date"]
        if dcol != pl.Date:
            df = df.with_columns(pl.col("date").cast(pl.Date))
        pdf = df.sort("date").tail(req.lookback_days).to_pandas().set_index("date")
        # 数据质量闸门：volume 坏点会让 vwap=amount/volume 虚高百倍
        #（如 2026-08-28 的 000001.SZ volume 缩水 -> vwap 1161 元 vs close 11.65）。
        # 偏离 close ±50% 的 VWAP 视为坏点，以 close 修正并如实告知。
        vwap_raw = pdf["amount"] / pdf["volume"].clip(lower=1.0)
        bad = (vwap_raw / pdf["close"] - 1.0).abs() > 0.5
        data_warnings: list[str] = []
        if bool(bad.any()):
            bad_dates = [str(d)[:10] for d in pdf.index[bad]]
            pdf.loc[bad, "volume"] = pdf.loc[bad, "amount"] / pdf.loc[bad, "close"]
            data_warnings.append(
                f"{len(bad_dates)} 个交易日 VWAP 异常（volume 坏点），已按 close 修正："
                + "、".join(bad_dates[:3]))
        res = execution_impact_sim(pdf, req.side, req.order_amount,
                                   req.participation_cap, req.algo,
                                   req.split_days)
        res["symbol"] = sym
        res["data_warnings"] = data_warnings
        price_series = []
        for row in pdf.itertuples():
            dt_val = getattr(row, "Index", getattr(row, "date", ""))
            open_val = getattr(row, "open", 0.0)
            close_val = getattr(row, "close", 0.0)
            vol_val = max(float(getattr(row, "volume", 1.0)), 1.0)
            amt_val = float(getattr(row, "amount", 0.0))
            price_series.append({
                "date": str(dt_val)[:10],
                "open": round(float(open_val), 4),
                "close": round(float(close_val), 4),
                "vwap": round(float(amt_val / vol_val), 4),
            })
        res["price_series"] = price_series
        return res
    return ok(await run_in_compute_pool(_run))


class StressTestRequest(BaseModel):
    assets: list[dict] = Field(..., min_length=1, max_length=20,
                               description="[{code, weight}]")
    top_windows: int = Field(5, ge=3, le=8)
    window: int = Field(20, ge=10, le=120)


@router.post("/stress-test", response_model=APIResponse[dict])
async def stress_test(req: StressTestRequest,
                      _user: dict = Depends(require_role("researcher")),
                      _compute: None = Depends(compute_slot)) -> APIResponse[dict]:
    """压力测试情景归因：真实历史最深回撤窗口 + 组合历史重演（VaR/CVaR 历史法）。"""
    def _run() -> dict:
        codes = [_norm_symbol(a["code"]) for a in req.assets]
        weights = {c: float(a.get("weight", 0)) for c, a in zip(codes, req.assets)}
        closes: dict[str, pd.Series] = {}
        for sym in codes:
            df = read_symbol_dataset("daily_bar_hfq", sym)
            if df.is_empty():
                continue
            dcol = df.schema["date"]
            if dcol != pl.Date:
                df = df.with_columns(pl.col("date").cast(pl.Date))
            closes[sym] = df.sort("date").select(["date", "close"]).to_pandas() \
                .set_index("date")["close"]
        if not closes:
            raise AQPException(ERR_DATA_EMPTY, "所选资产均无本地 hfq 行情")
        close_w = pd.DataFrame(closes).dropna(how="all").ffill().dropna()
        # 市场基准：全 universe 等权日收益（真实截面）
        #
        # P-7（2026-10-04 上线前全检）：原实现整表全列读 3 个年分区（实测 66 MB）
        # 并 pivot 出全历史，只为得到一条与 ``close_w`` 对齐的基准净值曲线。
        # 两处收敛：
        #   ① 投影到 ["date","symbol","close"]（并行 footer 校验 + 投影读）；
        #   ② 只保留 ``close_w`` 覆盖到的日期区间 —— 更早的年份 pivot 出来也会在
        #      下方 ``market_nav.index.isin(close_w.index)`` 被丢掉，纯属白算。
        #      ``close_w`` 来自用户请求的资产窗口，故这不是"截断历史"，而是
        #      去掉**必然被丢弃**的行。
        uni_files = sorted((get_settings().DATA_ROOT / "universe_daily")
                           .rglob("*.parquet"))
        if not uni_files:
            # B7a-08：`pl.concat([])` 抛未捕获的 ValueError → 全局兜底成**裸 50000**
            # （"系统故障"），而它其实是"没有数据"这种可解释的降级；与本函数
            # :545 的 `ERR_DATA_EMPTY` 保持同一口径（全新部署尚无本地数据即触发）。
            raise AQPException(ERR_DATA_EMPTY,
                              "本地 universe_daily 为空，无法构建市场基准")
        uni = read_parquet_columns(uni_files, ["date", "symbol", "close"])
        # ⚠️ 变量名与上面逐资产的 ``dcol``（同一函数作用域）刻意区分：重复绑定会让
        #    类型收窄失效（``dcol`` 已被推断为 ``DataType``，而此处可能为 ``None``）。
        uni_dcol = uni.schema["date"] if not uni.is_empty() else None
        if uni_dcol is not None and uni_dcol != pl.Date:
            uni = uni.with_columns(pl.col("date").cast(pl.Date))
        updf = uni.select(["date", "symbol", "close"]).to_pandas()
        updf["date"] = pd.to_datetime(updf["date"])
        if len(close_w) > 0:
            updf = updf[updf["date"] >= close_w.index.min()]
        mkt_w = updf.pivot_table(index="date", columns="symbol",
                                 values="close", aggfunc="last").ffill()
        market_nav = mkt_w.pct_change().mean(axis=1).dropna()
        market_nav = (1.0 + market_nav).cumprod()
        market_nav = market_nav[market_nav.index.isin(close_w.index)]
        wins = stress_windows(market_nav, window=req.window, top=req.top_windows)
        replay = portfolio_stress_replay(close_w, weights, wins)
        return {"window": req.window, "scenarios": replay,
                "note": "窗口由真实市场数据自动识别（滚动收益最深的互不重叠区间），"
                        "非硬编码事件；组合采用历史重演法（无 Monte Carlo 采样）"}
    return ok(await run_in_compute_pool(_run))


# ---------------- ML Lab（§4.5，Sprint4）：分年稳定性热力图 ----------------
# 计算前向收益所需的前瞻窗口（自然日）。终点年份必须按它外扩，见 _bar_year_window。
_BAR_FORWARD_WINDOW_DAYS = 10


def _bar_year_window(pred_min: date, pred_max: date,
                     *, fwd_days: int = _BAR_FORWARD_WINDOW_DAYS) -> tuple[int, int]:
    """给出读取 ``daily_bar`` 所需的年份闭区间 ``[y_from, y_to]``。

    🔴 **只裁起点安全；终点必须留足前瞻窗口**（项目既定规则）。
    计算前向收益要 ``shift(-1)`` —— 需要 ``pred_max`` **之后的**下一个交易日收盘价。
    若把终点卡在 ``pred_max`` 当年，年末样本的 ``ret`` 会被截成 null，
    并在下游 ``drop_nulls("ret")`` 里**静默丢样本**（不报错、结果偏小）。

    实证：``pred_max = 2022-12-30`` 时 ``y_to`` 必须是 **2023**，否则 2022 年最后一个
    交易日的样本全部消失。
    """
    return pred_min.year, (pred_max + timedelta(days=fwd_days)).year


def _prune_bar_files(files: list[Path], y_from: int, y_to: int) -> list[Path]:
    """按年份区间裁剪 ``symbol=*/year=*.parquet`` 文件列表。

    修复 G-4：原先无条件 glob 全部年份 ⇒ 读入远多于需要的分区（10 倍成本量级）。
    纯函数（只依赖文件名），故可离线单测。
    """
    return [f for f in files
            if y_from <= int(f.name.split("year=")[1][:4]) <= y_to]


@router.get("/lab/yearly", response_model=APIResponse[list])
async def ml_lab_yearly(
        _user: dict = Depends(require_role("researcher"))) -> APIResponse[list]:
    """生产模型预测的分年稳定性：逐年 RankIC / ICIR / 方向命中率。

    数据源：predictions parquet × daily_bar 次日收益（真实前向收益，不造数）；
    与训练评估同口径的逐日截面 Spearman；候选四维对比已在 /research/experiments
    （registry 单一事实源），本端点补足「分年稳定性热力图」数据。
    """
    def _yearly() -> list[dict]:
        import polars as pl

        s = get_settings()
        pred_files = sorted((s.DATA_ROOT / "predictions").glob("date=*.parquet"))
        if not pred_files:
            return []
        # 逐文件读入后统一 cast date（predictions 历史分区可能混存 Date/Datetime）
        pred = pl.concat([
            pl.read_parquet(f, columns=["date", "symbol", "pred_score"])
            .with_columns(pl.col("date").cast(pl.Date))
            for f in pred_files])
        # 🔴 按业务日期跨度裁剪分区（G-4，2026-10-02）：原先无条件 glob 全部年份。
        # 需要的范围由 predictions 自身决定；终点经 `_bar_year_window` 外扩前瞻窗口。
        _y_from, _y_to = _bar_year_window(pred["date"].min(), pred["date"].max())
        bar_files = _prune_bar_files(
            sorted((s.DATA_ROOT / "daily_bar").glob("symbol=*/year=*.parquet")),
            _y_from, _y_to)
        if not bar_files:
            return []
        from ...data.parquet_store import read_parquet_columns

        bar = read_parquet_columns(bar_files, ["symbol", "date", "close"])
        bar = bar.sort("date").with_columns(
            (pl.col("close").shift(-1).over("symbol") / pl.col("close") - 1).alias("ret"))
        j = (pred.with_columns(pl.col("date").cast(pl.Date))
             .join(bar.select(["symbol", "date", "ret"]), on=["symbol", "date"],
                   how="inner").drop_nulls("ret")
             .with_columns(pl.col("date").dt.year().alias("year")))
        out: list[dict] = []
        for year_t, g in j.group_by("year"):
            # group_by 键可能是标量或单元素元组，统一收敛为 int 年份
            year_raw = year_t[0] if isinstance(year_t, tuple) else year_t
            year = (int(year_raw) if isinstance(year_raw, (int, float))
                    else int(str(year_raw)))
            n = g.height
            if n < 500:  # 样本过少的年份如实缺席（不造数）
                continue
            ics: list[float] = []
            hit = 0
            for _, day in g.group_by("date"):
                if day.height < 8:
                    continue
                v = day.select(pl.corr("pred_score", "ret",
                                       method="spearman").alias("ic"))["ic"][0]
                if v is not None and v == v:
                    ics.append(float(v))
                hit += int((day["pred_score"].sign() == day["ret"].sign()).sum())
            if len(ics) < 10:
                continue
            mean_ic = sum(ics) / len(ics)
            sd = (sum((x - mean_ic) ** 2 for x in ics) / (len(ics) - 1)) ** 0.5
            out.append({"year": year, "rank_ic": round(mean_ic, 4),
                        "icir": round(mean_ic / sd, 3) if sd > 1e-12 else None,
                        "hit_rate": round(hit / n, 4), "n_days": len(ics)})
        return sorted(out, key=lambda x: x["year"])

    return ok(await _run_sync(_yearly))


class CpcvRequest(BaseModel):
    n_groups: int = Field(6, ge=4, le=10)
    k_test: int = Field(2, ge=1, le=4)
    purge_window: int = Field(5, ge=0, le=20)
    embargo_window: int = Field(2, ge=0, le=20)
    factors: list[str] | None = Field(default=None, max_length=15)


@router.post("/cpcv", response_model=APIResponse[dict])
async def cpcv_analysis(
    req: CpcvRequest,
    _user: dict = Depends(require_role("researcher")),
    _compute: None = Depends(compute_slot),
) -> APIResponse[dict]:
    """组合净化交叉验证 (CPCV) 与回测过拟合概率 (PBO) 诊断。"""
    def _run() -> dict:
        from ...ml.purged_cv import cpcv_pbo_analysis

        pdf = _load_features(days=500)
        res = cpcv_pbo_analysis(
            df=pdf,
            feature_cols=req.factors,
            n_groups=req.n_groups,
            k_test=req.k_test,
            purge_window=req.purge_window,
            embargo_window=req.embargo_window,
        )
        if not res.get("has_data"):
            raise AQPException(ERR_DATA_EMPTY, res.get("error", "CPCV 计算数据不足"))
        return res

    return ok(await _run_sync(_run))

