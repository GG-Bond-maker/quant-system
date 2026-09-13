"""预测链路的「模型特征 ↔ 特征来源」契约测试（防 P0 回归）。

背景（2026-09-11 审核发现）：生产模型特征空间为 v2g（含 g1_* 图传导列），
而 predict 曾只走 build_factors（v1），导致任意标的 /predict 必现 500
（ValueError: 特征列缺失 g1_*）。本文件把两条契约固化下来：

1. 生产模型要求的特征必须被 v2g 特征面板完整覆盖；
2. apply_propagate 必须始终产出 g{h}_* 列（无关系边时补 NaN，列名稳定）。

真实生产数据不存在时（CI 的临时 DATA_ROOT）自动 skip，不制造假数据充数。
"""
from __future__ import annotations

from pathlib import Path

import numpy as np
import pandas as pd
import polars as pl
import pytest

from app.ml.features import FEATURE_VERSION, apply_propagate, build_factors

# backend/tests/ -> backend/ -> 仓库根；与 Settings 默认保持一致：
# DATA_ROOT = <root>/data/parquet，MODEL_ROOT = <root>/data/models
# （不受 conftest 的临时目录重定向影响）
_ROOT = Path(__file__).resolve().parents[2]
REAL_DATA_ROOT = _ROOT / "data" / "parquet"
REAL_MODEL_ROOT = _ROOT / "data" / "models"
REAL_FEAT_DIR = REAL_DATA_ROOT / "features" / f"version={FEATURE_VERSION}"


def test_prod_model_features_covered_by_panel() -> None:
    """生产模型要求的每个特征都必须存在于 v2g 特征面板中。

    Raises:
        AssertionError: 出现模型要求但面板缺失的特征列（线上即 predict 500）。
    """
    import json

    if not REAL_FEAT_DIR.exists():
        pytest.skip("本地无 v2g 特征面板（CI 临时环境）")
    # 生产模型由 model_registry（SQLite，测试环境已被重定向）指定，这里退一步：
    # 直接校验 prod/ 下所有模型的 features.json —— 它们都是可被 promote 的候选
    feat_files = sorted((REAL_MODEL_ROOT / "prod").glob("*/features.json"))
    if not feat_files:
        pytest.skip("本地无生产模型（CI 临时环境）")

    parts = sorted(REAL_FEAT_DIR.glob("year=*.parquet"))
    if not parts:
        pytest.skip("特征面板为空")
    panel_cols = set(pl.read_parquet_schema(parts[-1]).keys())

    for f in feat_files:
        feats = json.loads(f.read_text(encoding="utf-8"))
        missing = [c for c in feats if c not in panel_cols]
        assert not missing, (
            f"{f.parent.name} 的特征未被 v2g 面板覆盖（predict 将 500）："
            f"{missing[:10]}（共 {len(missing)} 个）"
        )


def _synth_panel(n_symbols: int = 3, n_days: int = 300) -> pd.DataFrame:
    """构造多标的 OHLCV（仅供因子函数契约测试，不进入任何生产口径）。"""
    rng = np.random.default_rng(7)
    dates = pd.bdate_range("2023-01-02", periods=n_days)
    frames = []
    for i in range(n_symbols):
        close = 10 + np.cumsum(rng.normal(0, 0.1, n_days))
        close = np.clip(close, 1.0, None)
        frames.append(pd.DataFrame({
            "symbol": f"60{i:04d}.SH",
            "date": dates,
            "open": close + 0.01,
            "high": close + 0.05,
            "low": close - 0.05,
            "close": close,
            "volume": rng.integers(1e5, 1e6, n_days).astype(float),
            "amount": rng.integers(1e7, 1e8, n_days).astype(float),
        }))
    return pd.concat(frames, ignore_index=True)


def test_apply_propagate_always_emits_g_columns() -> None:
    """apply_propagate 必须稳定产出 g{h}_* 列，无关系边时补 NaN 而非静默返回原表。"""
    feats = build_factors(_synth_panel())
    base_cols = [c for c in feats.columns if c not in {"symbol", "date"}]
    out = apply_propagate(feats, hops=1)

    g_cols = [c for c in out.columns if c.startswith("g1_")]
    assert g_cols, "apply_propagate 未产出任何 g1_* 列"
    # 列名集合必须与基础因子一一对应（下游按列名取用，缺列即 KeyError/500）
    expected = {f"g1_{c}" for c in base_cols}
    assert expected.issubset(set(out.columns)), (
        f"g1_* 列缺失：{sorted(expected - set(out.columns))[:10]}"
    )
    # 无关系边时允许全 NaN（不造数原则），但列必须存在
    assert len(out) == len(feats)


def test_predict_discloses_hfq_close_basis(monkeypatch) -> None:
    """predict 响应必须披露 latest_close 为**后复权**口径（数据真实性红线）。

    背景：latest_close 取自 hfq 行情/特征面板，其数值以 IPO 为基准累计，
    远大于实际成交价（如 000001.SZ 实测 1792 vs 现价 11.85）。字段名容易被
    读成"现价"，故必须随响应返回 close_basis 口径说明。

    本用例用 monkeypatch 切断真实模型/IO 依赖（合成数据仅用于契约校验）。
    """
    import numpy as np
    import pandas as pd

    from app.data import parquet_store
    from app.ml import predict as P

    class _ModelDir:
        name = "fake_model"

    # 强制走"实时构建"分支（面板不可用），并切断全部重依赖
    monkeypatch.setattr(P, "_latest_panel_row", lambda symbol: None)
    monkeypatch.setattr(P, "load_prod_model",
                        lambda *a, **k: (object(), ["f1", "f2"], _ModelDir()))
    monkeypatch.setattr(P, "build_factors", lambda df: pd.DataFrame(
        {"date": [pd.Timestamp("2026-09-04")], "close": [11.85],
         "f1": [0.1], "f2": [0.2]}))
    monkeypatch.setattr(P, "apply_propagate", lambda df, **k: df)
    monkeypatch.setattr(P, "predict_with_contrib",
                        lambda booster, row, feats: (np.array([0.01]),
                                                     np.array([[0.3, 0.2]])))
    monkeypatch.setattr(P, "top_factor_contributions", lambda c, f, k=5: [])
    monkeypatch.setattr(P, "_model_confidence", lambda d: None)
    # predict_symbol 内部是惰性 import，必须 patch 源模块
    monkeypatch.setattr(parquet_store, "read_symbol_dataset",
                        lambda ds, sym: pl.DataFrame({"close": [11.85] * 300}))

    out = P.predict_symbol("000001.SZ")
    assert out["feature_source"] == "realtime"
    assert "close_basis" in out, out.keys()
    assert "后复权" in out["close_basis"]
    # feature_source 的披露同样不可缺失（P0-1 契约）
    assert "panel=" in out["feature_basis"]
