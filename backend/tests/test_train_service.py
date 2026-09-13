"""训练服务（train_service）测试：门禁、白名单、取消生命周期、RankIC 口径。

torch 真训练的用例未装则 skip；门禁/生命周期用例不依赖 torch（monkeypatch
torch_ready 控制分支），保证在无 torch 环境也能验证启动拒绝路径。
"""
from __future__ import annotations

import sys
import time
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

BACKEND_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(BACKEND_ROOT))

from app.core.errors import AQPException, ERR_PARAMS, ERR_TRAIN  # noqa: E402
from app.ml import train_service  # noqa: E402
from app.ml.train_service import (MIN_TRAIN_SAMPLES, daily_rank_ic,  # noqa: E402
                                  start_training, training_status)


@pytest.fixture
def data_env(tmp_path, monkeypatch):
    """独立 DATA_ROOT + settings 缓存刷新（与 test_data_prep 同款）。"""
    data = tmp_path / "parquet"
    data.mkdir(parents=True)
    monkeypatch.setenv("DATA_ROOT", str(data))
    from app.core.config import get_settings

    get_settings.cache_clear()
    yield data
    get_settings.cache_clear()


# ---------------- 就绪度门禁 ----------------
def test_readiness_gates_empty_env(data_env):
    """空 DATA_ROOT：features 门禁必须为 False，绝不放行训练。"""
    r = train_service.train_readiness()
    assert r["features"]["exists"] is False
    assert r["tft_ready"] is False and r["gnn_ready"] is False
    assert r["min_samples"] == MIN_TRAIN_SAMPLES
    # torch_ready 必须如实反映环境，不得硬编码 True
    from app.ml.torch_models import torch_ready

    assert r["torch_ready"] == torch_ready()
    # torch_device 快照必须透出（前端 TrainPanel 展示设备依据）
    assert "torch_device" in r
    assert set(r["torch_device"]) == {"torch_installed", "device",
                                      "device_name", "torch_version"}
    assert r["torch_device"]["torch_installed"] == r["torch_ready"]


def test_readiness_reports_sample_math(data_env):
    """样本粗估公式：symbols × (dates - lookback - horizon + 1)。"""
    r = train_service.train_readiness()
    assert r["est_samples"] == 0  # 无 features → 0


# ---------------- 启动门禁（无 torch 环境必须明确拒绝） ----------------
def test_start_rejects_unknown_model():
    with pytest.raises(AQPException) as ei:
        start_training("lgbm_v9")
    assert ei.value.code == ERR_PARAMS


def test_start_rejected_without_torch(data_env, monkeypatch):
    """torch 未安装时启动必须拒绝（ERR_TRAIN），提示装依赖而不是静默降级。"""
    monkeypatch.setattr(train_service, "torch_ready", lambda: False)
    with pytest.raises(AQPException) as ei:
        start_training("tft")
    assert ei.value.code == ERR_TRAIN
    assert "PyTorch" in ei.value.message


def test_start_rejected_when_features_missing(data_env, monkeypatch):
    """torch 就绪但 features 缺失：同样拒绝，原因指明先 build_features。"""
    monkeypatch.setattr(train_service, "torch_ready", lambda: True)
    with pytest.raises(AQPException) as ei:
        start_training("gnn")
    assert ei.value.code == ERR_TRAIN
    assert "features" in ei.value.message


def test_start_rejected_when_samples_insufficient(data_env, monkeypatch):
    """features 存在但样本量不足：拒绝并提示先抓数据。"""
    monkeypatch.setattr(train_service, "torch_ready", lambda: True)
    feat = data_env / "features" / f"version={train_service.FEATURE_VERSION}"
    feat.mkdir(parents=True)
    # 构造一个极小的 features 文件（3 symbol × 40 日 → 估样本远 < 5000）
    dates = pd.date_range("2026-01-01", periods=40).strftime("%Y-%m-%d")
    df = pd.DataFrame(
        [(d, f"00000{i}.SZ", 10.0, 1.0) for d in dates for i in range(3)],
        columns=["date", "symbol", "close", "ret1"])
    pl = pytest.importorskip("polars")
    (feat / "year=2026.parquet").write_bytes(_pl_bytes(pl, df))

    with pytest.raises(AQPException) as ei:
        start_training("tft")
    assert ei.value.code == ERR_TRAIN
    assert "样本" in ei.value.message


def _pl_bytes(pl, df: pd.DataFrame) -> bytes:
    import io

    buf = io.BytesIO()
    pl.from_pandas(df).write_parquet(buf)
    return buf.getvalue()


def test_param_whitelist_filters_unknown_kwargs(data_env, monkeypatch):
    """启动参数白名单：未登记的 kwarg 必须被丢弃，不透传下游函数。"""
    monkeypatch.setattr(train_service, "torch_ready", lambda: True)
    monkeypatch.setattr(train_service, "train_readiness", lambda: {
        "torch_ready": True, "features": {"exists": True},
        "sample_ok": True, "est_samples": 99_999, "min_samples": MIN_TRAIN_SAMPLES,
        "gnn_edges": 1, "tft_ready": True, "gnn_ready": True,
        "torch_install_hint": "", "relation_note": "", "note": ""})

    seen: dict = {}
    monkeypatch.setattr(train_service, "run_tft_training", lambda **kw: seen.update(kw) or {"ok": True})

    res = start_training("tft", {"epochs": 3, "lookback": 20, "evil_param": "x"})
    assert res["started"] is True
    # 等线程跑完（stub 立即返回）
    for _ in range(100):
        if not training_status()["running"]:
            break
        time.sleep(0.02)
    assert seen.get("epochs") == 3 and seen.get("lookback") == 20
    assert "evil_param" not in seen


# ---------------- 训练核心（torch 未装则 skip 真训练） ----------------
def test_run_tft_without_torch_raises(monkeypatch):
    """直接调用训练核心：无 torch 给明确 RuntimeError（不是 ImportError）。"""
    monkeypatch.setattr(train_service, "torch_ready", lambda: False)
    with pytest.raises(RuntimeError, match="PyTorch"):
        train_service.run_tft_training()


def test_run_gnn_without_torch_raises(monkeypatch):
    monkeypatch.setattr(train_service, "torch_ready", lambda: False)
    with pytest.raises(RuntimeError, match="PyTorch"):
        train_service.run_gnn_training()


# ---------------- 候选登记回归（真实 API 训练曾因 split 缺 train_start 翻车） ----------------
def _tiny_features_df(n_days: int = 40, n_symbols: int = 6) -> pd.DataFrame:
    """迷你特征长表：symbol/date/close + 两个数值因子列（build_sequences 口径）。"""
    rng = np.random.default_rng(3)
    dates = pd.date_range("2026-01-01", periods=n_days, freq="D")
    frames = []
    for s in range(n_symbols):
        close = 100.0 + np.cumsum(rng.normal(scale=0.5, size=n_days))
        frames.append(pd.DataFrame({
            "symbol": f"S{s}", "date": dates, "close": close,
            "f_mom": rng.normal(size=n_days), "f_vol": rng.normal(size=n_days)}))
    return pd.concat(frames, ignore_index=True)


def _assert_last_candidate_has_train_dates() -> None:
    """最后一个候选的 train_start/train_end 必须非空（NOT NULL 列的契约）。"""
    from app.ml.registry import _connect

    conn = _connect()
    try:
        row = conn.execute(
            "SELECT model_name, train_start, train_end FROM model_registry "
            "ORDER BY id DESC LIMIT 1").fetchone()
    finally:
        conn.close()
    assert row is not None, "训练完成后 model_registry 必须有候选记录"
    assert row[0] and row[1] and row[2], \
        f"train_start/train_end 不得为空：{row}"


def test_gnn_training_registers_candidate_with_train_dates(data_env, monkeypatch):
    """GNN 端到端迷你训练：split 必须携带 train_start/train_end。

    回归背景：split 缺 NOT NULL 字段时 register_candidate 抛 IntegrityError，
    真实 API 训练失败（测试曾因 mock 掉 run_gnn_training 而漏网）。
    """
    pytest.importorskip("torch")
    from app.ml.torch_models import torch_ready

    if not torch_ready():
        pytest.skip("torch 未安装")
    df = _tiny_features_df()
    monkeypatch.setattr(train_service, "_load_features", lambda: df)

    n = df["symbol"].nunique()
    idx = {s: i for i, s in enumerate(sorted(df["symbol"].unique()))}
    A = np.zeros((n, n))
    for i in range(n - 1):  # 链式邻接（非空边即可）
        A[i, i + 1] = A[i + 1, i] = 1.0
    A = A / np.maximum(A.sum(axis=1, keepdims=True), 1)
    monkeypatch.setattr(train_service, "build_adjacency",
                        lambda symbols: (idx, A))

    res = train_service.run_gnn_training(holdout=5, horizon=1, epochs=2,
                                         min_samples=1)
    assert res.get("ok") is True, res
    assert res.get("device") in ("cpu", "xpu")
    _assert_last_candidate_has_train_dates()


def test_tft_training_registers_candidate_with_train_dates(data_env, monkeypatch):
    """TFT 同款回归：候选登记的 split 必须携带 train_start/train_end。"""
    pytest.importorskip("torch")
    from app.ml.torch_models import torch_ready

    if not torch_ready():
        pytest.skip("torch 未安装")
    monkeypatch.setattr(train_service, "_load_features", _tiny_features_df)

    res = train_service.run_tft_training(lookback=5, horizon=1, holdout=5,
                                         test_days=5, epochs=2, batch=16,
                                         min_samples=1)
    assert res.get("ok") is True, res
    assert res.get("device") in ("cpu", "xpu")
    _assert_last_candidate_has_train_dates()


def test_should_stop_honored_by_sequence_trainer():
    """逐 epoch 取消：should_stop=True 时首轮即停，history 带 cancelled 标记。"""
    pytest.importorskip("torch")
    from app.ml.torch_models import torch_ready, train_sequence_model

    if not torch_ready():
        pytest.skip("torch 未安装")
    rng = np.random.default_rng(0)
    X = rng.normal(size=(16, 4, 3)).astype(np.float32)
    y = rng.normal(size=16).astype(np.float32)
    model, history = train_sequence_model(X, y, epochs=5,
                                          should_stop=lambda: True)
    assert history[0].get("cancelled") is True and len(history) == 1


# ---------------- RankIC 口径与取消生命周期 ----------------
def test_daily_rank_ic_perfect_and_inverse():
    rng = np.random.default_rng(7)
    n_days, k = 3, 12
    dates = np.repeat(np.array([f"2026-01-0{i}" for i in range(1, n_days + 1)]), k)
    y = rng.normal(size=n_days * k)
    ic, days = daily_rank_ic(y.copy(), dates, y)
    assert ic == pytest.approx(1.0) and days == n_days
    ic2, _ = daily_rank_ic(-y, dates, y)
    assert ic2 == pytest.approx(-1.0)


def test_daily_rank_ic_skips_thin_days():
    """单日截面 <10 只不计入（避免噪声日稀释口径）。"""
    dates = np.array(["d1"] * 12 + ["d2"] * 5)
    y = np.arange(17, dtype=float)
    ic, days = daily_rank_ic(y, dates, y)
    assert days == 1 and ic == pytest.approx(1.0)


def test_cancel_and_status_lifecycle(data_env):
    """取消接口幂等可用；status 恒返回可序列化快照（含 last_result 字段）。"""
    res = train_service.cancel_training()
    assert res["cancel_requested"] is True
    snap = training_status()
    assert snap["running"] is False
    assert "last_result" in snap and isinstance(snap["logs"], list)
