"""
LightGBM 训练引擎（AQP ML，P0 修复后：三段切分 + gap + ICIR + registry）。

问题定义：预测未来 N 日收益率（回归主任务），
    Label = close[t + ML_LABEL_HORIZON] / close[t] - 1（按 symbol 分组 shift(-N) 构造）。

数据集切分（严格时间顺序，防泄漏，对应设计文档 8.4）：
    train ──gap── valid ──gap── test
    - train：拟合 + 特征筛选（RankIC 只用 train 段）；
    - valid：早停 + 超参观察；
    - test ：仅最终样本外评估，绝不参与特征筛选 / 早停 / 超参；
    - gap  ：默认 = horizon，保证 Label 窗口不跨越切分边界
             （train 末日的 label 需要 close[t+horizon]，必须落在 gap 内）。

防泄漏红线：
1. 特征只允许基于 T 日及之前的数据（features.py 保证，且特征基于 hfq 价格，
   具备 asof 稳定性，见 tests/test_feature_asof.py）；
2. 特征筛选只使用 train 段；
3. test 段只出现在最终 metrics 计算，代码路径上无法影响早停。

产出（MODEL_ROOT/lgbm_v1_<version>/ + model_registry 表）：
    model.lgbm / features.json / params.json / metrics.json
"""
from __future__ import annotations

import json
from dataclasses import replace
from datetime import datetime
from pathlib import Path
from typing import Any

import lightgbm as lgb
import numpy as np
import pandas as pd
from loguru import logger

from ..core.config import effective_cpu_threads, get_settings
from .features import FEATURE_VERSION
from .labeling import LabelMode, LabelPolicy, build_forward_return_labels
from .registry import exp_root, regime_windows, register_candidate

# 横截面去均值的最小截面宽度（n<2 的"截面"去均值会把标签整体变成 0）
_MIN_XSEC_NAMES = 2

# ---------- 默认超参（保守，CPU 友好；num_threads 由统一配置注入） ----------
DEFAULT_PARAMS: dict[str, Any] = {
    "objective": "regression",     # L2 回归：预测未来 N 日收益率
    "metric": "l2",
    "boosting_type": "gbdt",
    "learning_rate": 0.05,
    "num_leaves": 31,
    "max_depth": -1,
    "feature_fraction": 0.8,
    "bagging_fraction": 0.8,
    "bagging_freq": 5,
    "min_data_in_leaf": 200,
    "lambda_l2": 0.1,
    "verbose": -1,
    "seed": 42,
    "force_row_wise": True,
}


# ---------- IC 工具（不依赖 scipy） ----------
def rank_ic(pred: np.ndarray, actual: np.ndarray, min_samples: int = 30) -> float:
    """Spearman Rank IC：对秩做 Pearson 相关。有效样本 < min_samples 返回 NaN。"""
    mask = np.isfinite(pred) & np.isfinite(actual)
    if int(mask.sum()) < min_samples:
        return float("nan")
    p = pd.Series(pred[mask]).rank().to_numpy(dtype=np.float64)
    a = pd.Series(actual[mask]).rank().to_numpy(dtype=np.float64)
    if p.std() == 0 or a.std() == 0:
        return float("nan")
    with np.errstate(invalid="ignore", divide="ignore"):
        return float(np.corrcoef(p, a)[0, 1])


def pearson_ic(pred: np.ndarray, actual: np.ndarray) -> float:
    """Pearson IC（常数列直接判 NaN，避免除零警告）。"""
    mask = np.isfinite(pred) & np.isfinite(actual)
    if int(mask.sum()) < 30:
        return float("nan")
    p, a = pred[mask], actual[mask]
    if p.std() == 0 or a.std() == 0:
        return float("nan")
    return float(np.corrcoef(p, a)[0, 1])


def daily_ic_pairs(df: pd.DataFrame, pred_col: str, label_col: str,
                   min_samples: int = 10) -> list[tuple[str, float]]:
    """按交易日分组的日度 IC 序列，**带日期标签**（审计 T8：窗口切分要能披露 regime）。

    同口径：同日先 Pearson、退化时用 Spearman Rank；
    日期升序（``groupby`` 默认排序）⇒ 可直接按顺序切连续窗口。
    """
    out: list[tuple[str, float]] = []
    for date, g in df.groupby("date"):
        ic = pearson_ic(g[pred_col].to_numpy(), g[label_col].to_numpy())
        if not np.isfinite(ic):
            ic = rank_ic(g[pred_col].to_numpy(), g[label_col].to_numpy(), min_samples)
        if np.isfinite(ic):
            out.append((str(date)[:10], float(ic)))
    return out


def icir(ics: list[float]) -> float:
    """ICIR = mean(IC) / std(IC)。IC 序列为空或零方差时返回 NaN。"""
    arr = np.asarray(ics, dtype=np.float64)
    if arr.size < 2 or arr.std() < 1e-12:
        return float("nan")
    return float(arr.mean() / arr.std())


def daily_rank_ic(df: pd.DataFrame, pred_col: str, label_col: str) -> float:
    """按交易日分组的日均 Rank IC（业界标准口径，比池化 IC 更稳健）。"""
    ics: list[float] = []
    for _, g in df.groupby("date"):
        ic = rank_ic(g[pred_col].to_numpy(), g[label_col].to_numpy(), min_samples=10)
        if np.isfinite(ic):
            ics.append(ic)
    return float(np.mean(ics)) if ics else float("nan")


# ---------- 时间切分（纯函数，可单测） ----------
def split_dates(
    dates: list[Any],
    holdout_days: int,
    test_days: int,
    gap_days: int,
) -> dict[str, Any]:
    """按日期计算 train/gap/valid/gap/test 边界（升序 dates）。

    返回 {train_end, valid_start, valid_end, test_start, gap1_start, gap2_start}。
    gap 语义：gap1 = [train_end+1, valid_start-1] 的日期集合，
    其长度 == gap_days，用于吸收 Label 的未来窗口（label(t)=close[t+horizon]）。
    """
    if len(dates) < holdout_days + test_days + 2 * gap_days + 20:
        raise ValueError(
            f"日期数不足({len(dates)})：三段切分至少需要 "
            f"holdout({holdout_days}) + test({test_days}) + 2*gap({gap_days}) + 20"
        )
    test_start_idx = len(dates) - test_days
    valid_start_idx = test_start_idx - gap_days - holdout_days
    train_end_idx = valid_start_idx - gap_days - 1
    return {
        # train_start 必须显式返回：model_registry.train_start 是 NOT NULL 列，
        # 缺失会让候选登记静默失败（旧代码 except 后只 warn，不阻断）。
        "train_start": dates[0],
        "train_end": dates[train_end_idx],
        "gap1_start": dates[train_end_idx + 1],
        "valid_start": dates[valid_start_idx],
        "valid_end": dates[test_start_idx - gap_days - 1],
        "gap2_start": dates[test_start_idx - gap_days],
        "test_start": dates[test_start_idx],
    }


# ---------- 特征筛选 ----------
def select_features_by_ic(
    X: pd.DataFrame,
    y_ret: pd.Series,
    min_abs_rank_ic: float = 0.003,
    top_k: int = 60,
    dates: pd.Series | None = None,
) -> list[str]:
    """基于训练段 IC 筛选因子：|IC| >= 阈值的因子按 |IC| 降序取前 top_k。

    ⚠️ 只允许用 train 段调用本函数，valid/test 数据不得参与。

    **口径（审计 P1-38）**：传入 ``dates`` 时用**逐日截面 RankIC 的均值**
    （与训练目标、报表 ``valid_rank_ic`` 同一口径，见 :func:`daily_rank_ic`）；
    逐日口径在横截面过窄（每日 < ``min_samples``）时数学上无定义，此时该列
    退回池化 Spearman IC（兼容单标的合成数据的链路测试）。

    为什么必须改：池化 IC 把"跨日水平差异"也算进相关性，而训练目标是逐日截面
    排序、评估指标是逐日 RankIC ⇒ 口径错配会把最强的**截面**因子剔除。
    真实面板实测 ``atr_14`` 池化 0.0003 / 逐日 0.0816（270×），``hl_range``/
    ``vol_20``/``v_rank_20`` 同批被误剔。
    """
    y = pd.Series(y_ret).to_numpy(dtype=np.float64)
    day_groups: list[np.ndarray] | None = None
    if dates is not None:
        d = pd.Series(np.asarray(dates)).to_numpy()
        positions = np.arange(len(d))
        # `.indices` 给出每个日期在**原序列中的位置**，故按位取值即可保持对齐
        day_groups = [np.asarray(pos) for pos in pd.Series(positions).groupby(d).indices.values()]
    ics: list[tuple[str, float]] = []
    for col in X.columns:
        vals = X[col].to_numpy(dtype=np.float64)
        ric = float("nan")
        if day_groups is not None:
            per_day = [rank_ic(vals[g], y[g], min_samples=10) for g in day_groups]
            per_day = [v for v in per_day if np.isfinite(v)]
            if per_day:
                ric = float(np.mean(per_day))
            else:
                # 逐日截面样本不足 ⇒ 该口径无定义（如单标的合成数据），退回池化
                ric = rank_ic(vals, y)
        else:
            ric = rank_ic(vals, y)
        if np.isfinite(ric) and abs(ric) >= min_abs_rank_ic:
            ics.append((col, abs(ric)))
    ics.sort(key=lambda x: x[1], reverse=True)
    keep = [c for c, _ in ics[:top_k]]
    logger.info(
        f"feature select: {len(keep)}/{len(X.columns)} kept "
        f"(min_abs_rank_ic={min_abs_rank_ic}, top_k={top_k})"
    )
    return keep


# ---------- model_registry 落库（第四阶段：只登记为 candidate） ----------
def _register_model(version: str, metrics: dict[str, Any], model_dir: Path,
                    kept: list[str], horizons: dict[str, Any]) -> None:
    """训练完成后把产物登记为 **candidate**（status=candidate, is_production=0）。

    ⚠️ 第三/五阶段纪律：
    - 训练**绝不**自动写 is_production=1，必须由 promote_model 显式提升；
    - 产物落在 ``models/exp/``，与 ``models/prod/`` 物理隔离；
    - 注册表写入失败仅告警不阻断（产物已在磁盘，可后续补登记）。
    """
    try:
        from ..core.config import get_settings as _gs

        s = _gs()
        if not s.SQLITE_PATH.exists():
            logger.warning("model_registry 跳过：SQLite 不存在（先运行 init/bootstrap）")
            return
        rid = register_candidate(
            model_name="lgbm_v1", version=version, metrics=metrics,
            model_dir=model_dir,
            feature_version=FEATURE_VERSION,
            dataset_version=str(metrics.get("dataset_version") or ""),
        )
        logger.info(f"model_registry 登记 candidate: lgbm_v1/{version} id={rid}")
    except Exception as e:  # 注册表失败不阻断训练产物
        logger.warning(f"model_registry 写入失败（不阻断）: {e!r}")


# ---------- 训练主入口 ----------
def train_lgbm(
    features: Path | str | pd.DataFrame,
    horizon: int | None = None,
    holdout_days: int | None = None,
    test_days: int | None = None,
    gap_days: int | None = None,
    params: dict[str, Any] | None = None,
    version_suffix: str = "",
    num_boost_round: int = 1000,
    stopping_rounds: int = 50,
    min_abs_rank_ic: float = 0.003,
    top_k: int = 60,
    persist: bool = True,
    label_policy: LabelPolicy | None = None,
    label_mode: LabelMode = "winsorize",
    dataset_version: str | None = None,
    max_abs_label_return: float = 0.5,
    xsec_demean: bool = True,
) -> dict[str, Any]:
    """训练 LightGBM 未来 N 日收益率预测模型，落盘 + 注册 + 返回指标。

    :param features:   因子宽表（pd.DataFrame）或 Parquet 路径，
                       需含 symbol, date, close + 因子列（基于 hfq 价格构建）；
    :param holdout_days: 验证段交易日数（默认读配置 ML_HOLDOUT_DAYS）；
    :param test_days:    测试段交易日数（默认读配置 ML_TEST_DAYS）；
    :param gap_days:     段间 embargo（默认 = horizon）。
    :param xsec_demean:  标签横截面去均值（label_ret 减同日全市场均值）。
                         L2 目标下原始 5 日收益被市场共同波动主导，官方指标
                         RankIC 只看截面排序——不去均值时早停在个位数轮
                         （实测 1133 只全量数据第 2 轮即停）。去均值后目标
                         变为截面相对收益，与 RankIC 同口径，树数与 ICIR
                         均显著改善。
                         **默认 True（审计 P1-37）**：此前默认 False，而唯一
                         打开它的是 `scripts/retrain.py` ⇒ 按文档跑
                         `scripts/train.py`/`grid_search.py` 得到的是退化模型
                         （真实面板实测 `best_iteration=1`、`valid_rank_ic`
                         0.0144，同数据 demeaning 后 **0.0832**，5.8×），且
                         两个入口的差异无任何产物字段留痕。训练口径随
                         `train_basis` 落盘（metrics/params/注册表），可审计。
    """
    s = get_settings()
    horizon = horizon or s.ML_LABEL_HORIZON
    holdout_days = holdout_days or s.ML_HOLDOUT_DAYS
    test_days = test_days or s.ML_TEST_DAYS
    gap_days = gap_days if gap_days is not None else horizon  # gap >= horizon
    merged_params = {**DEFAULT_PARAMS, **(params or {})}
    merged_params["num_threads"] = effective_cpu_threads()  # 统一线程配置
    version = datetime.now().strftime("%Y%m%d_%H%M%S") + (
        f"_{version_suffix}" if version_suffix else ""
    )
    # 第四阶段：产物默认落 models/exp/（生产模型只存在于 models/prod/）
    model_dir: Path = (exp_root() if persist else s.MODEL_ROOT) / f"lgbm_v1_{version}"
    if persist:
        model_dir.mkdir(parents=True, exist_ok=True)

    # ---- 加载 ----
    if isinstance(features, (Path, str)):
        logger.info(f"load features from {features}")
        df = pd.read_parquet(features)
    else:
        df = features.copy()
    df = df.sort_values(["symbol", "date"]).reset_index(drop=True)

    # ---- Label：未来 N 日收益率 + 质量策略（CRIT-002 治理）----
    # 原实现直接 shift(-N) 后 dropna 就送进 L2 目标，单个 +66,910% 的坏标签
    # 即可主导损失（实测 train RMSE 4.78 vs valid 0.073，早停在第 1 轮触发）。
    # 现在统一走 labeling.build_forward_return_labels，强制施加质量策略并留审计日志。
    label_policy = label_policy or LabelPolicy(
        horizon=horizon, mode=label_mode, max_abs_return=max_abs_label_return)
    if label_policy.horizon != horizon:  # NaN 判定的标准写法
        label_policy = replace(label_policy, horizon=horizon)
    df, _label_rep = build_forward_return_labels(df, label_policy)
    label_report = _label_rep.as_dict()
    if df.empty:
        raise ValueError("label 构造后为空：请检查数据长度是否 >= horizon")

    if xsec_demean:
        # 横截面去均值在质量策略之后施加（先清洗极端值，再去市场分量）。
        # 同日全市场均值视为市场分量；标签 = 个股 5 日收益 - 市场均值，
        # 与 RankIC 的截面排序口径一致。仅改训练目标，推理输出 pred_score
        # 的"截面内相对大小"语义不变，infer/promote 流程无感知。
        #
        # 退化截面守卫（P1-37 默认值改为 True 后必须处理）：n==1 的"截面"上
        # 减当日均值会把标签**整体变成 0** ⇒ 目标被抹掉、模型退化为常数。
        # 故只在横截面宽度 >= _MIN_XSEC_NAMES 的交易日去均值；若整段数据不存在
        # 任何这样的横截面（单标的合成数据/极薄池），按绝对收益口径训练，并把
        # **生效口径**写进 train_basis（不谎报请求值）。
        _cross_n = df.groupby("date")["symbol"].transform("nunique")
        _wide = _cross_n >= _MIN_XSEC_NAMES
        if bool(_wide.any()):
            _demeaned = (df["label_ret"]
                         - df.groupby("date")["label_ret"].transform("mean"))
            df["label_ret"] = _demeaned.where(_wide, df["label_ret"])
        else:
            xsec_demean = False  # 生效口径 = 绝对收益（无可去均值的横截面）
            logger.warning(
                "[train_lgbm] 请求 xsec_demean=True，但训练段不存在横截面宽度 "
                f">= {_MIN_XSEC_NAMES} 的交易日（n_symbols="
                f"{df['symbol'].nunique()}）⇒ 按**绝对收益**口径训练（已落盘）")

    # ⚠️ 特征白名单：排除 symbol/date/close/label_ret。
    # close 是原始价格（跨标的价格区间不可比），label_ret 就是标签本身——
    # 任何二者混入特征都是标签泄漏。
    _EXCLUDE = {"symbol", "date", "close", "label_ret"}
    feat_cols_all = [c for c in df.columns if c not in _EXCLUDE]
    X = df[feat_cols_all]
    y_ret = df["label_ret"]

    # ---- 三段时间切分（train | gap | valid | gap | test） ----
    dates = sorted(df["date"].unique())
    bounds = split_dates(dates, holdout_days, test_days, gap_days)
    train_mask = df["date"] <= bounds["train_end"]
    valid_mask = (df["date"] >= bounds["valid_start"]) & (df["date"] <= bounds["valid_end"])
    test_mask = df["date"] >= bounds["test_start"]
    logger.info(
        f"train: {dates[0]} ~ {bounds['train_end']} n={int(train_mask.sum())} | "
        f"gap1: {bounds['gap1_start']} ~ {bounds['valid_start']} | "
        f"valid: {bounds['valid_start']} ~ {bounds['valid_end']} n={int(valid_mask.sum())} | "
        f"gap2: {bounds['gap2_start']} ~ {bounds['test_start']} | "
        f"test: {bounds['test_start']} ~ {dates[-1]} n={int(test_mask.sum())}"
    )

    # ---- 特征筛选（仅 train 段） ----
    # 审计 P1-38：口径必须与训练目标/评估一致 —— 传 dates ⇒ 逐日截面 RankIC 均值
    # （池化 IC 会把跨日水平差异当信号，实测误剔最强截面因子 270×）。
    kept = select_features_by_ic(X.loc[train_mask], y_ret.loc[train_mask],
                                 min_abs_rank_ic=min_abs_rank_ic, top_k=top_k,
                                 dates=df.loc[train_mask, "date"])
    if not kept:
        raise ValueError("无任何因子通过 IC 筛选，请检查数据质量")
    X_tr, X_va = X.loc[train_mask, kept], X.loc[valid_mask, kept]
    # 注意：X_te（test 段特征）在最终 _eval(test_mask) 时才切片使用，
    # 此处刻意不提前构建，从代码路径上杜绝 test 参与训练/早停。

    # ---- 训练（早停只看 valid 段；test 不出镜） ----
    lgb_train = lgb.Dataset(X_tr, label=y_ret.loc[train_mask], params=merged_params)
    lgb_valid = lgb.Dataset(X_va, label=y_ret.loc[valid_mask], reference=lgb_train)
    booster = lgb.train(
        merged_params,
        lgb_train,
        num_boost_round=num_boost_round,
        valid_sets=[lgb_valid],
        valid_names=["valid"],
        callbacks=[
            lgb.early_stopping(stopping_rounds=stopping_rounds, verbose=True),
            lgb.log_evaluation(period=100),
        ],
    )

    # ---- 评估（train / valid / test；test 仅此处使用） ----
    def _eval(mask: pd.Series, name: str) -> tuple[dict[str, float],
                                                   list[tuple[str, float]]]:
        pred: np.ndarray = np.asarray(
            booster.predict(X.loc[mask, kept], num_iteration=booster.best_iteration or -1)
        )
        y = y_ret.loc[mask].to_numpy()
        sub = df.loc[mask, ["date"]].copy()
        sub["pred"], sub["y"] = pred, y
        pairs = daily_ic_pairs(sub, "pred", "y")
        ics = [v for _, v in pairs]
        return {
            f"{name}_ic": pearson_ic(pred, y),
            f"{name}_rank_ic": daily_rank_ic(sub, "pred", "y"),
            f"{name}_icir": icir(ics),
            f"{name}_rmse": float(np.sqrt(np.mean((pred - y) ** 2))),
        }, pairs

    m_train, _ = _eval(train_mask, "train")
    m_valid, valid_ic_pairs = _eval(valid_mask, "valid")
    m_test, _ = _eval(test_mask, "test")
    logger.info(
        f"train done: version={version} best_iter={booster.best_iteration} | "
        f"valid IC={m_valid['valid_ic']:.4f} RankIC={m_valid['valid_rank_ic']:.4f} "
        f"ICIR={m_valid['valid_icir']:.4f} | "
        f"test IC={m_test['test_ic']:.4f} RankIC={m_test['test_rank_ic']:.4f} "
        f"ICIR={m_test['test_icir']:.4f}"
    )

    # ---- 落盘（persist=False 时跳过：网格搜索等批量场景） ----
    #
    # 审计 P1-39：**训练口径必须落盘**。此前 `xsec_demean` 既不在 metrics、
    # 也不在 params、更不在注册表里，于是 promote 门禁会拿"绝对收益目标"的模型
    # 和"截面去均值目标"的模型**直接比 RMSE**（实测同 split 同 dataset、只差这一个
    # flag：0.07411 vs 0.05998，比值 1.25×，远超 max_rmse_worsen_ratio=0.05）
    # ⇒ 更优的候选被判「恶化超限」而恒被拒（P1-16/P1-6/P1-7 的根因）。
    # 凡影响"损失函数可比性/样本口径"的开关都进 basis，供门禁做可比性判断。
    train_basis: dict[str, Any] = {
        "target": "xsec_demean" if xsec_demean else "absolute_forward_return",
        "xsec_demean": bool(xsec_demean),
        "horizon": int(horizon),
        "label_mode": str(label_mode),
        "max_abs_label_return": float(max_abs_label_return),
        "dataset_version": str(dataset_version or ""),
        "feature_version": FEATURE_VERSION,
        "selection": {"min_abs_rank_ic": float(min_abs_rank_ic), "top_k": int(top_k),
                      # 审计 P1-38：筛选口径随口径一起落盘（此前无从判断用的是
                      # 池化 IC 还是逐日截面 IC —— 两者的入选集合不同）。
                      "ic_basis": "daily_xsec_rank_ic"},
    }
    merged_params["train_basis"] = train_basis

    # 审计 P1-48：**预测水平**必须随产物落盘，否则：
    #   ① 同一列 `pred_score` 混装两种语义（`_pipe` 版绝对收益口径 mean=+0.7077%、
    #      `_repaired` 版截面去均值口径 mean=−0.0138%）却只能靠分布反推；
    #   ② promote 门禁只看 RankIC，与"水平偏置"**完全正交**，结构上看不见它
    #      （生产模型实测逐日截面均值 ≈ −0.0016 ≈ −0.16%/5d）。
    # 落盘内容：valid 段的预测水平、同段真实标签水平，以及两者的差（校准偏差）
    # 与其标准误 —— 门禁据此做显著性判断，而不是拿一个凭空的绝对值阈值。
    _vp = np.asarray(booster.predict(
        X.loc[valid_mask, kept], num_iteration=booster.best_iteration or -1))
    _vy = y_ret.loc[valid_mask].to_numpy(dtype=np.float64)
    _vp_std = float(np.std(_vp, ddof=1)) if _vp.size > 1 else 0.0
    _vy_std = float(np.std(_vy, ddof=1)) if _vy.size > 1 else 0.0
    _bias = float(np.mean(_vp) - np.mean(_vy))
    _bias_se = (float(np.sqrt(_vp_std ** 2 / _vp.size + _vy_std ** 2 / _vy.size))
                if _vp.size > 1 else None)
    pred_level: dict[str, Any] = {
        "n": int(_vp.size),
        "pred_level_mean": float(np.mean(_vp)),
        "pred_level_std": _vp_std,
        "label_level_mean": float(np.mean(_vy)),
        "label_level_std": _vy_std,
        "level_bias": _bias,
        "level_bias_se": _bias_se,
        "unit": "forward_return",
        "horizon": int(horizon),
        "xsec_demean": bool(xsec_demean),
    }

    # 审计 T8（验证窗口 regime 化）：把 valid 段按日期切成 n 个**连续窗口**，
    # 落盘每段的 RankIC 均值/符号与窗口边界（哪段 regime、几个窗口）。
    # 目的：暴露"单一 valid 段绝对值"这个判据的比较对象错——生产 valid_rank_ic=
    # 0.1020 只来自一段 regime，train/valid/test 单调衰减说明关系非平稳。
    # 门禁侧（registry.regime_check）据此做跨窗口符号一致性 + max(0.002, 2·SE) 判据。
    valid_regime_windows = regime_windows(valid_ic_pairs, n_windows=4)
    logger.info(
        f"valid regime windows: available={valid_regime_windows.get('available')} "
        f"n={valid_regime_windows.get('n_windows')} "
        f"positive={valid_regime_windows.get('positive_windows')} "
        f"mean={valid_regime_windows.get('mean_rank_ic')} "
        f"threshold={valid_regime_windows.get('threshold')}")

    model_path = model_dir / "model.lgbm"
    if persist:
        booster.save_model(str(model_path))
        (model_dir / "features.json").write_text(
            json.dumps(kept, ensure_ascii=False, indent=2), encoding="utf-8"
        )
        (model_dir / "params.json").write_text(
            json.dumps(merged_params, ensure_ascii=False, indent=2, default=str),
            encoding="utf-8",
        )
    metrics: dict[str, Any] = {
        "version": version,
        "horizon": horizon,
        "holdout_days": holdout_days,
        "test_days": test_days,
        "gap_days": gap_days,
        "split": {k: str(v) for k, v in bounds.items()},
        "train_rows": int(train_mask.sum()),
        "valid_rows": int(valid_mask.sum()),
        "test_rows": int(test_mask.sum()),
        # 样本规模（第六阶段）：promote 门槛按横截面宽度推导统计显著性，
        # 缺了这两个数就无法判断 RankIC 是否显著异于 0。
        "n_symbols": int(df["symbol"].nunique()),
        "valid_days": int(df.loc[valid_mask, "date"].nunique()),
        "test_days_n": int(df.loc[test_mask, "date"].nunique()),
        "best_iteration": int(booster.best_iteration or 0),
        **m_train, **m_valid, **m_test,
        "label_quality": label_report,
        "dataset_version": dataset_version or "",
        # P1-39：口径落盘（门禁据此判断 RMSE 是否可比）+ 入选特征清单
        # （此前 metrics 不含 kept_features，注册表里该字段恒为空）。
        "train_basis": train_basis,
        "kept_features": list(kept),
        # P1-48：预测水平/校准披露（门禁的"水平偏置"维度据此判定）
        "pred_level": pred_level,
        "pred_level_mean": pred_level["pred_level_mean"],
        "pred_level_std": pred_level["pred_level_std"],
        # 审计 T8：逐窗口（regime）RankIC 披露，同时随 params_json 进注册表，
        # 使 promote 门禁能判断"跨窗口符号一致性"而不是只看单一 valid 段。
        "valid_regime_windows": valid_regime_windows,
    }
    if persist:
        (model_dir / "metrics.json").write_text(
            json.dumps(metrics, ensure_ascii=False, indent=2), encoding="utf-8")
        _register_model(version, metrics, model_dir, kept, bounds)

    return {
        "version": version,
        "model_path": str(model_path) if persist else "",
        "kept_features": kept,
        **metrics,
    }
