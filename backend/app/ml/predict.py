"""
在线预测引擎（AQP ML，任务定义的 app/ml/predict.py）。

单只标的的完整预测链路（同步、CPU 密集，供 API 用 asyncio.to_thread 调用）：
    Parquet 历史行情 -> build_factors -> 加载预训练 LightGBM ->
    推理未来 N 日预期收益率 -> TreeSHAP Top-5 因子贡献 -> 结构化 payload

说明：
- pred_return 即回归头输出的未来 N 日收益率点估计；
- confidence 当前为【模型级】置信度占位（由验证集 RankIC 映射），
  个股级置信度合成公式（校准度/稳定性/历史IC/流动性四分项）在 P2 实现；
- 本模块不查 Redis（缓存由 API 层负责），保持引擎可独立复用/测试。
"""
from __future__ import annotations

import json
import math
from pathlib import Path
from typing import TYPE_CHECKING

from loguru import logger

if TYPE_CHECKING:  # 仅为类型检查提供 pandas 名称（运行时按需使用 pandas）
    import pandas as pd

from ..core.config import get_settings
from ..core.errors import ERR_DATA_EMPTY, ERR_INFER, AQPException
from .features import FEATURE_VERSION, apply_propagate, build_factors
from .infer import load_prod_model, predict_with_contrib, top_factor_contributions

# 因子计算需要的最小历史长度（少于该值时长窗口因子全 NaN，预测无意义）
_MIN_HISTORY_ROWS = 60


def _model_confidence(model_dir: Path) -> float | None:
    """模型级置信度：clip(0.5 + 2 * valid_rank_ic, 0, 1)。

    metrics 缺失/解析失败/NaN 时返回 None（如实披露"无法计算"，
    不再静默回退 0.5 冒充真实值）。
    """
    metrics_path = model_dir / "metrics.json"
    if not metrics_path.exists():
        return None
    try:
        ric = float(json.loads(metrics_path.read_text(encoding="utf-8"))["valid_rank_ic"])
    except (KeyError, ValueError, json.JSONDecodeError):
        return None
    if math.isnan(ric):
        return None
    return float(min(1.0, max(0.0, 0.5 + 2.0 * ric)))


def _latest_panel_row(symbol: str) -> "pd.DataFrame | None":
    """从生产特征面板（version=FEATURE_VERSION）取该标的最新一行。

    为什么优先走磁盘面板而不是实时构建：面板由
    ``apply_propagate(build_factors(全市场行情))`` 生成（见
    ``app/orchestrator.py`` 的 build_features 步骤），含**真实的 g1_* 同业传导值**，
    与训练、批量推理（选股/监控）同源；而单只标的实时构图没有一跳邻居，
    propagate 只能产出全 NaN 的 g1_* 列，反而与训练分布不一致。

    Args:
        symbol: 标准代码，如 "000001.SZ"。

    Returns:
        单行 pandas DataFrame（含全部 v2g 特征列）；面板不存在或无该标的时 None。
    """
    import polars as pl

    s = get_settings()
    feat_dir = s.DATA_ROOT / "features" / f"version={FEATURE_VERSION}"
    parts = sorted(feat_dir.glob("year=*.parquet")) if feat_dir.exists() else []
    if not parts:
        return None
    try:
        # 谓词下推：按 symbol 过滤后再落盘读取，避免把 18 万行全量载入内存
        scans = [pl.scan_parquet(p).filter(pl.col("symbol") == symbol) for p in parts]
        df = pl.concat(scans, how="diagonal_relaxed").sort("date").collect()
    except Exception as e:  # noqa: BLE001 面板损坏/缺列时透明降级到实时构建
        logger.warning(f"[predict] 特征面板读取失败 symbol={symbol}: {e!r}")
        return None
    if df.is_empty():
        return None
    return df.tail(1).to_pandas()


def predict_symbol(symbol: str, horizon: int | None = None) -> dict:
    """对单只标的执行"读数 -> 因子 -> 推理 -> 解释"完整链路。

    :param symbol:  标准代码，如 "600519.SH"
    :return:        {symbol, date, horizon, pred_return, confidence,
                     model_version, base_value, top5_factors, latest_close,
                     feature_source, feature_basis, close_basis}
    """
    from ..data.parquet_store import read_symbol_dataset

    s = get_settings()
    horizon = horizon or s.ML_LABEL_HORIZON

    # 1) 历史行情：必须使用 hfq（后复权）价格 —— 与训练特征同基准，
    #    且 hfq 因子序列为 IPO 累计口径，具备 asof 稳定性（见 tests/test_feature_asof.py）
    df = read_symbol_dataset("daily_bar_hfq", symbol)
    if df.is_empty() or df.height < _MIN_HISTORY_ROWS:
        raise AQPException(
            ERR_DATA_EMPTY,
            f"本地无 {symbol} 的充足 hfq 历史行情（需 >= {_MIN_HISTORY_ROWS} 行，"
            f"dataset=daily_bar_hfq），请先运行 scripts/update_daily.py",
        )

    # 2) 模型（不存在时给出可操作的错误信息）
    try:
        booster, feats, model_dir = load_prod_model()
    except FileNotFoundError as e:
        raise AQPException(ERR_INFER, str(e)) from e

    # 3) 特征取最新一行：优先生产特征面板（含真实 g1_* 图传导列，与训练同源），
    #    面板缺该标的时回退实时构建（单标的无邻居 → g1_* 为 NaN，来源已披露）
    row = _latest_panel_row(symbol)
    feature_source = "panel"
    if row is None or not set(feats).issubset(set(row.columns)):
        row = apply_propagate(build_factors(df)).tail(1)
        feature_source = "realtime"
        logger.warning(
            f"[predict] {symbol} 生产特征面板不可用，回退实时构建（g1_* 图特征将为 NaN）"
        )
    trade_date = str(row["date"].iloc[0])[:10]

    # 4) 推理 + TreeSHAP 解释
    pred, contrib = predict_with_contrib(booster, row, feats)
    top5 = top_factor_contributions(contrib[0], feats, k=5)

    return {
        "symbol": symbol,
        "date": trade_date,
        "horizon": horizon,
        "pred_return": float(pred[0]),
        "confidence": _model_confidence(model_dir),
        # 口径披露：confidence 是模型级常数映射（非个股上涨概率），
        # 前端据此渲染 ⓘ 提示（数据真实性红线：派生指标必须披露口径）
        "confidence_basis": (
            "模型级置信度：clip(0.5 + 2×验证集RankIC, 0, 1)，"
            "同一模型下所有个股同值，非个股上涨概率；"
            "模型 metrics 缺失时为 null"
        ),
        "model_version": model_dir.name,
        "base_value": float(contrib[0][-1]),
        "top5_factors": top5,
        # 面板路径下面板本身就带 close（与特征同日）；实时路径取行情末行
        "latest_close": (float(row["close"].iloc[0])
                         if feature_source == "panel" and "close" in row.columns
                         else float(df["close"][-1])),
        # 口径披露：latest_close 是**后复权**价（与训练特征同基准），
        # 不等于行情软件里的实际成交价，前端必须据此加 ⓘ 说明，避免当作现价
        "close_basis": (
            "latest_close 为后复权（hfq）收盘价，与训练特征/标签同基准；"
            "非实际成交价（后复权以 IPO 为基准累计，数值通常远大于现价）"
        ),
        # 口径披露：特征来源直接影响 g1_* 图特征是否为真实值
        "feature_source": feature_source,
        "feature_basis": (
            "panel=取自生产特征面板（全市场构图，g1_* 为真实同业传导值，与训练同源）；"
            "realtime=面板缺失时按本地行情实时构建，单标的无邻居，g1_* 全为 NaN"
        ),
    }
