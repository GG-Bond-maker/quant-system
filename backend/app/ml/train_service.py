"""训练服务（3-1/3-2 数据前置的消费端：数据抓取到位后从数据中心一键训练）。

设计（与 datacenter 同步任务同款模式，见 api/v1/datacenter._SyncState）：
- **单任务串行**：进程内后台线程执行，环形日志缓冲，/train/status 轮询；
- **最后一次结果落 app_state（kv）**：进程重启后状态卡仍能看到上次训练产出；
- **逻辑唯一真源在本模块**：scripts/train_tft.py、train_gnn.py 只是 CLI 薄壳
  （argparse + 打印），避免同一段训练逻辑两处维护后漂移；
- **torch 是可选依赖**：未安装时 readiness 如实标注，start 明确拒绝（前端
  按钮给出安装指引），绝不静默降级；
- **样本 <MIN_TRAIN_SAMPLES 主动中止**：120 只池训深模型没有统计意义，
  这是研究判断而非工程缺陷（见《前沿演进评审》），reason 如实返回给前端。
"""
from __future__ import annotations

import json
import threading
import time
from collections.abc import Callable
from datetime import datetime

import numpy as np
import pandas as pd
from loguru import logger

from ..core.config import get_settings
from ..core.errors import (AQPException, ERR_PARAMS, ERR_PIPELINE_BUSY,
                            ERR_TRAIN)
from .features import FEATURE_VERSION
from .gnn_models import load_gnn, predict_gnn, save_gnn, train_gnn, torch_ready
from .graph import build_adjacency
from .registry import register_candidate
from .sequence_dataset import build_sequences, purged_time_split
from .torch_models import (load_sequence_model, predict_sequence,
                           save_sequence_model, train_sequence_model)

MIN_TRAIN_SAMPLES = 5000
JOB_KV_KEY = "train_job"


# ---------------------------------------------------------------------------
# 就绪度评估（前端按钮 disable/enable 的依据）
# ---------------------------------------------------------------------------
def _features_overview() -> dict:
    s = get_settings()
    feat_dir = s.DATA_ROOT / "features" / f"version={FEATURE_VERSION}"
    parts = sorted(feat_dir.glob("year=*.parquet")) if feat_dir.exists() else []
    if not parts:
        return {"exists": False, "rows": 0, "symbols": 0, "dates": 0,
                "last_date": None, "dir": str(feat_dir)}
    import polars as pl

    df = pl.concat([pl.read_parquet(p, columns=["symbol", "date"]) for p in parts],
                   how="diagonal_relaxed")
    return {"exists": True, "rows": df.height,
            "symbols": df["symbol"].n_unique(),
            "dates": df["date"].n_unique(),
            "last_date": str(df["date"].max()), "dir": str(feat_dir)}


def train_readiness() -> dict:
    """训练就绪度总览（数据前置是否闭环、torch/样本/关系数据三项门禁）。"""
    has_torch = torch_ready()
    feat = _features_overview()
    # 序列样本量上限粗估：每标的 n_dates - lookback - horizon + 1
    # （按默认 lookback=30 / horizon=5 估；实际样本还会因 NaN 窗口更少，
    #   真实值以训练启动后返回的 n 为准——这里只做按钮 enable 的粗判）
    est = 0
    if feat["exists"]:
        est = feat["symbols"] * max(0, feat["dates"] - 30 - 5 + 1)
    from .graph import relations_status

    rel = relations_status()
    gnn_edges = rel["explicit_edges"] + rel["industry_edges"]
    sample_ok = est >= MIN_TRAIN_SAMPLES
    from .torch_device import torch_device_info

    dev = torch_device_info()
    return {
        "torch_ready": has_torch,
        "torch_device": dev,
        "torch_install_hint": (
            "pip install torch --index-url https://download.pytorch.org/whl/xpu "
            "（Intel Arc 核显/独显加速，本机 Ultra 125H 适用）；"
            "纯 CPU 版改用 .../whl/cpu"),
        "features": feat,
        "est_samples": est,
        "min_samples": MIN_TRAIN_SAMPLES,
        "sample_ok": sample_ok,
        "gnn_edges": gnn_edges,
        "tft_ready": bool(has_torch and feat["exists"] and sample_ok),
        "gnn_ready": bool(has_torch and feat["exists"] and sample_ok and gnn_edges > 0),
        "relation_note": rel["note"],
        "note": ("就绪，可启动训练" if (has_torch and feat["exists"] and sample_ok)
                 else "前置条件未满足（torch / features / 样本量），见各项门禁"),
    }


# ---------------------------------------------------------------------------
# 训练核心（CLI 与 API 共用）
# ---------------------------------------------------------------------------
def _load_features() -> pd.DataFrame:
    s = get_settings()
    feat_dir = s.DATA_ROOT / "features" / f"version={FEATURE_VERSION}"
    parts = sorted(feat_dir.glob("year=*.parquet"))
    if not parts:
        raise FileNotFoundError(
            f"features 不存在：{feat_dir}（先在流水线页执行 build_features）")
    return pd.concat([pd.read_parquet(p) for p in parts], ignore_index=True)


def daily_rank_ic(pred, dates, y) -> tuple[float, int]:
    """逐日截面 RankIC 均值（与平台 IC 口径一致；单日样本 <10 不计）。"""
    df = pd.DataFrame({"date": dates, "p": pred, "y": y})
    ics = df.groupby("date").apply(
        lambda g: g["p"].rank().corr(g["y"].rank())
        if len(g) >= 10 else float("nan"), include_groups=False).dropna()
    return (float(ics.mean()), len(ics)) if len(ics) else (float("nan"), 0)


def _reject_samples(n: int, min_samples: int) -> dict:
    return {"ok": False,
            "reason": (f"序列样本 {n} < {min_samples}：扩池 1000+ 只、5 年以上"
                       "历史之前训练深模型没有统计意义，已主动中止。先抓数据。"),
            "samples": int(n)}


def run_tft_training(*, lookback: int = 30, horizon: int = 5, holdout: int = 252,
                     test_days: int = 252, epochs: int = 30, batch: int = 256,
                     min_samples: int = MIN_TRAIN_SAMPLES,
                     log: Callable[[str], None] = print,
                     should_stop: Callable[[], bool] | None = None) -> dict:
    """SeqTransformer 时序模型训练全流程；返回结果 dict（CLI 打印 / API 落库）。"""
    if not torch_ready():
        raise RuntimeError(
            "未安装 PyTorch（可选依赖）。Intel Arc 核显/独显版："
            "pip install torch --index-url https://download.pytorch.org/whl/xpu；"
            "纯 CPU 版：pip install torch --index-url https://download.pytorch.org/whl/cpu")
    s = get_settings()
    df = _load_features()
    log(f"features: rows={len(df)} symbols={df['symbol'].nunique()}")

    seq = build_sequences(df, lookback=lookback, horizon=horizon)
    log(f"序列样本: n={len(seq['y'])} dropped={seq['n_dropped']} "
        f"cols={len(seq['feature_cols'])}")
    if len(seq["y"]) < min_samples:
        return _reject_samples(len(seq["y"]), min_samples)

    split = purged_time_split(seq["dates"], holdout_days=holdout,
                              gap_days=horizon, test_days=test_days)
    tr, va = split["train"], split["valid"]
    # 注意：split.get("test") 可能是 numpy 索引数组，length>1 时其布尔真值
    # 未定义（ValueError: truth value of an array is ambiguous），必须显式判 None。
    _te_raw = split.get("test")
    te: list = [] if _te_raw is None else list(_te_raw)
    log(f"split: train={len(tr)} valid={len(va)} test={len(te)}")

    model, history = train_sequence_model(
        seq["X"][tr], seq["y"][tr],
        seq["X"][va] if len(va) else None,
        seq["y"][va] if len(va) else None,
        epochs=epochs, batch_size=batch, should_stop=should_stop)
    if should_stop is not None and should_stop():
        log("收到取消请求，训练中止（未落盘未登记）")
        return {"ok": False, "cancelled": True, "samples": int(len(seq["y"]))}

    version = f"tft_v1_{datetime.now().strftime('%Y%m%d_%H%M%S')}"
    model_dir = s.MODEL_ROOT / "exp" / version
    model_dir.mkdir(parents=True, exist_ok=True)
    # 存"结构+权重"而非裸 state_dict，且存盘即回读校验——load 不出来就不登记
    save_sequence_model(model, model_dir / "model.pt")
    load_sequence_model(model_dir / "model.pt")
    with (model_dir / "metrics.json").open("w", encoding="utf-8") as f:
        json.dump({"model": "SeqTransformer", "lookback": lookback,
                   "horizon": horizon, "feature_cols": seq["feature_cols"],
                   "history_tail": history[-5:]},
                  f, ensure_ascii=False, indent=1)
    log(f"模型落盘+回读校验 OK: {model_dir / 'model.pt'}")

    vr, vn = (daily_rank_ic(predict_sequence(model, seq["X"][va]),
                            seq["dates"][va], seq["y"][va])
              if len(va) else (float("nan"), 0))
    ter, ten = (daily_rank_ic(predict_sequence(model, seq["X"][te]),
                              seq["dates"][te], seq["y"][te])
                if len(te) else (float("nan"), 0))

    def _dstr(arr, i: int) -> str | None:
        """取第 i 个交易日的 YYYY-MM-DD；越界/空段返回 None（可空列允许）。"""
        return str(arr[i])[:10] if len(arr) > abs(i) else None

    tr_dates = seq["dates"][tr]
    va_dates = seq["dates"][va]
    te_dates = seq["dates"][te]
    register_candidate(
        "tft_v1", version,
        {"valid_rank_ic": vr, "test_rank_ic": ter,
         "split": {"train_start": _dstr(tr_dates, 0),
                   "train_end": _dstr(tr_dates, -1),
                   "valid_start": _dstr(va_dates, 0),
                   "valid_end": _dstr(va_dates, -1),
                   "test_start": _dstr(te_dates, 0),
                   "valid_days": vn, "test_days": ten,
                   "lookback": lookback, "horizon": horizon}},
        model_dir, feature_version=FEATURE_VERSION, model_file="model.pt")
    log(f"候选已登记: tft_v1/{version}  valid RankIC={vr:.4f}({vn}日) "
        f"test RankIC={ter:.4f}({ten}日)")
    return {"ok": True, "model": "tft_v1", "version": version,
            "samples": int(len(seq["y"])), "n_features": len(seq["feature_cols"]),
            "device": (history[0].get("device") if history else None),
            "valid_rank_ic": vr, "valid_days": vn,
            "test_rank_ic": ter, "test_days": ten,
            "model_dir": str(model_dir), "history_tail": history[-5:]}


def run_gnn_training(*, horizon: int = 5, holdout: int = 252, epochs: int = 40,
                     min_samples: int = MIN_TRAIN_SAMPLES,
                     log: Callable[[str], None] = print,
                     should_stop: Callable[[], bool] | None = None) -> dict:
    """裸 GCN 产业链传导模型训练全流程（需关系边；无显式边时可用行业 peer 边）。"""
    if not torch_ready():
        raise RuntimeError(
            "未安装 PyTorch（可选依赖）。Intel Arc 核显/独显版："
            "pip install torch --index-url https://download.pytorch.org/whl/xpu；"
            "纯 CPU 版：pip install torch --index-url https://download.pytorch.org/whl/cpu")
    s = get_settings()
    df = _load_features()
    df["date"] = pd.to_datetime(df["date"])
    # 前向收益标签靠 groupby.shift 取"后 horizon 行"，其正确性完全依赖行内
    # 顺序；不显式排序就会把 parquet 文件内顺序当时间顺序，标签静默错位。
    df = (df.drop_duplicates(subset=["symbol", "date"], keep="last")
            .sort_values(["symbol", "date"], kind="mergesort")
            .reset_index(drop=True))

    symbols = sorted(df["symbol"].unique())
    idx, A = build_adjacency(symbols)
    if A.sum() == 0:
        return {"ok": False,
                "reason": "邻接矩阵为空（无任何关系边）——GNN 无从训练，"
                          "先补 relations/edges.parquet 或 instrument.industry"}
    skip = {"symbol", "date", "close", "label_ret"}
    cols = [c for c in df.columns
            if c not in skip and pd.api.types.is_numeric_dtype(df[c])]
    log(f"edges_sum={A.sum():.1f} symbols={len(symbols)} factors={len(cols)}")

    # 透视成 (date × symbol) 面板：一次成型（5000 只 ×1131 日逐行写法不可行）
    dates = pd.DatetimeIndex(np.sort(df["date"].unique()))
    grid = pd.MultiIndex.from_product([dates, symbols], names=["date", "symbol"])
    wide = (df.set_index(["date", "symbol"])[cols].reindex(grid)
              .unstack("symbol")
              .reindex(columns=pd.MultiIndex.from_product([cols, symbols])))
    X = (wide.to_numpy(dtype=np.float32)
             .reshape(len(dates), len(cols), len(symbols)).transpose(0, 2, 1))
    df["close_fwd"] = df.groupby("symbol", sort=False)["close"].shift(-horizon)
    df["y"] = df["close_fwd"] / df["close"] - 1.0
    Y = (df.set_index(["date", "symbol"])["y"].reindex(grid)
           .unstack("symbol").to_numpy(dtype=np.float32))
    log(f"panel: X={X.shape} 有效标签={int((Y == Y).sum())}")

    n_days = len(dates)
    valid_start = max(0, n_days - holdout)
    train_end = max(0, valid_start - horizon)
    tr, va = np.arange(train_end), np.arange(valid_start, n_days)
    log(f"split: train_days={len(tr)} valid_days={len(va)}")

    # 横截面去均值（与 train_lgbm xsec_demean 同理，2026-09-05 LGBM 实测
    # valid RankIC 0.072→0.092 / ICIR 0.42→0.78）：MSE 直接对原始收益训练时
    # 被市场共同波动主导，GCN 学不到截面信号（此前 valid RankIC≈0）。
    # 逐日减全市场均值只改训练目标；评估 RankIC 按日截面排序，对逐日
    # 常数平移不变，无需改评估。全 NaN 日不偏移（防 nanmean 传染）。
    Y_tr = Y[tr].copy()
    day_mean = np.nanmean(Y_tr, axis=1, keepdims=True)
    Y_tr -= np.where(np.isfinite(day_mean), day_mean, 0.0).astype(np.float32)
    model, history = train_gnn(X[tr], Y_tr, A, epochs=epochs,
                               should_stop=should_stop)
    if should_stop is not None and should_stop():
        log("收到取消请求，训练中止（未落盘未登记）")
        return {"ok": False, "cancelled": True}

    version = f"gnn_v1_{datetime.now().strftime('%Y%m%d_%H%M%S')}"
    model_dir = s.MODEL_ROOT / "exp" / version
    model_dir.mkdir(parents=True, exist_ok=True)
    save_gnn(model, model_dir / "model.pt")
    load_gnn(model_dir / "model.pt")  # 回读校验
    with (model_dir / "metrics.json").open("w", encoding="utf-8") as f:
        json.dump({"cols": cols, "symbols": symbols, "hops": 1,
                   "horizon": horizon, "history_tail": history[-5:]},
                  f, ensure_ascii=False, indent=1)
    log(f"模型落盘+回读校验 OK: {model_dir / 'model.pt'}")

    ics = []
    for d in va:
        if should_stop is not None and should_stop():
            log("评估阶段收到取消请求，中止")
            return {"ok": False, "cancelled": True}
        m = np.isfinite(Y[d])
        if m.sum() < 10:
            continue
        p = predict_gnn(model, X[d:d + 1], A)[0]
        ics.append(pd.Series(p[m]).rank().corr(pd.Series(Y[d][m]).rank()))
    vr = float(np.nanmean(ics)) if ics else float("nan")
    # train_start/train_end 是 model_registry 的 NOT NULL 列，缺失会让候选
    # 登记 IntegrityError（真实 API 训练曾因此失败；LGBM 路径有同款注释）。
    register_candidate(
        "gnn_v1", version,
        {"valid_rank_ic": vr,
         "split": {"train_start": str(dates[0].date()) if train_end > 0 else None,
                   "train_end": (str(dates[train_end - 1].date())
                                 if train_end > 0 else None),
                   "valid_start": (str(dates[valid_start].date())
                                   if n_days > valid_start else None),
                   "valid_end": str(dates[-1].date()) if n_days else None,
                   "valid_days": len(ics), "horizon": horizon}},
        model_dir, feature_version=FEATURE_VERSION, model_file="model.pt")
    log(f"候选已登记: gnn_v1/{version}  valid RankIC={vr:.4f}({len(ics)}日)")
    return {"ok": True, "model": "gnn_v1", "version": version,
            "device": (history[0].get("device") if history else None),
            "valid_rank_ic": vr, "valid_days": len(ics),
            "model_dir": str(model_dir), "history_tail": history[-5:]}


# ---------------------------------------------------------------------------
# 任务管理（单任务串行，与 datacenter._SyncState 同款）
# ---------------------------------------------------------------------------
class _TrainJob:
    def __init__(self) -> None:
        self.lock = threading.Lock()
        self.running = False
        self.model: str | None = None
        self.params: dict = {}
        self.started_at: float | None = None
        self.finished_at: float | None = None
        self.error: str | None = None
        self.last_result: dict | None = None
        self.cancel_event = threading.Event()
        self.logs: list[dict] = []

    def log(self, msg: str) -> None:
        with self.lock:
            self.logs.append({
                "ts": datetime.now().strftime("%H:%M:%S"),
                "message": str(msg)[:200]})
            self.logs = self.logs[-200:]
            logger.info(f"[train-job] {msg[:160]}")

    def snapshot(self) -> dict:
        with self.lock:
            return {
                "running": self.running,
                "model": self.model,
                "params": self.params,
                "error": self.error,
                "started_at": self.started_at,
                "elapsed_ms": int((time.monotonic()
                                   - (self.started_at or time.monotonic())) * 1000),
                "logs": self.logs[-60:],
                "cancelled": self.cancel_event.is_set(),
            }


_job = _TrainJob()

# 模型名 → 允许的启动参数（白名单，防任意 kwarg 注入下游函数）
_ALLOWED_PARAMS: dict[str, set[str]] = {
    "tft": {"lookback", "horizon", "holdout", "test_days", "epochs", "batch"},
    "gnn": {"horizon", "holdout", "epochs"},
}


def _persist_job_result(model: str, res: dict) -> None:
    try:
        from ..db.kv import kv_set

        kv_set(JOB_KV_KEY, {"model": model, "finished_at": time.time(),
                            "result": res})
    except Exception as e:  # noqa: BLE001 持久化失败不影响训练本身
        logger.warning(f"[train-job] persist fail: {e!r}")


def _worker(model: str, params: dict) -> None:
    from ..core.pipeline_lock import pipeline_slot

    log = _job.log
    try:
      with pipeline_slot("training"):
        if model == "tft":
            res = run_tft_training(log=log,
                                   should_stop=_job.cancel_event.is_set, **params)
        else:
            res = run_gnn_training(log=log,
                                   should_stop=_job.cancel_event.is_set, **params)
        with _job.lock:
            _job.last_result = res
        _persist_job_result(model, res)
        if res.get("ok"):
            log(f"训练完成：{res.get('model')}/{res.get('version')} "
                f"valid RankIC={res.get('valid_rank_ic')}")
        else:
            log(f"训练未产出：{res.get('reason') or res.get('error') or '已取消'}")
    except Exception as e:  # noqa: BLE001 线程内兜底，状态必须闭环
        log(f"训练异常: {type(e).__name__}: {e}")
        with _job.lock:
            _job.error = f"{type(e).__name__}: {e}"
            _job.last_result = {"ok": False, "error": f"{type(e).__name__}: {e}"}
        _persist_job_result(model, {"ok": False,
                                    "error": f"{type(e).__name__}: {e}"})
    finally:
        with _job.lock:
            _job.running = False
            _job.finished_at = time.monotonic()


def start_training(model: str, params: dict | None = None) -> dict:
    """启动一次训练（前置门禁前置到启动时，失败原因直接返回给前端）。"""
    if model not in _ALLOWED_PARAMS:
        raise AQPException(ERR_PARAMS, f"model 仅支持 {list(_ALLOWED_PARAMS)}，收到 {model!r}")
    params = {k: v for k, v in (params or {}).items() if k in _ALLOWED_PARAMS[model]}

    from ..core.pipeline_lock import current_pipeline_owner

    owner = current_pipeline_owner()
    if owner:
        # 管道互斥（C-03）：训练是最重的资源路径，不能与 sync/pipeline/mirror 并行
        raise AQPException(ERR_PIPELINE_BUSY,
                           f"管道任务 [{owner}] 执行中，暂不能开始训练")

    ready = train_readiness()
    gate = ready["tft_ready"] if model == "tft" else ready["gnn_ready"]
    if not gate:
        reasons = []
        if not ready["torch_ready"]:
            reasons.append(f"未安装 PyTorch（{ready['torch_install_hint']}）")
        if not ready["features"]["exists"]:
            reasons.append("features 未构建（先执行流水线 build_features）")
        if not ready["sample_ok"]:
            reasons.append(f"预估样本 {ready['est_samples']} < {MIN_TRAIN_SAMPLES}"
                           "（先抓数据扩池）")
        if model == "gnn" and ready["gnn_edges"] <= 0:
            reasons.append("无关系边（GNN 无从训练）")
        raise AQPException(ERR_TRAIN, "；".join(reasons))

    with _job.lock:
        if _job.running:
            raise AQPException(ERR_PARAMS, "已有训练任务在运行，请先等待完成或取消")
        _job.running = True
        _job.model = model
        _job.params = params
        _job.started_at = time.monotonic()
        _job.finished_at = None
        _job.error = None
        _job.last_result = None
        _job.cancel_event.clear()
        _job.logs = []
    threading.Thread(target=_worker, args=(model, params),
                     name=f"aqp-train-{model}", daemon=True).start()
    return {"started": True, "model": model, "params": params}


def cancel_training() -> dict:
    """请求优雅取消（训练循环逐 epoch 检查，当前 epoch 走完即停）。"""
    _job.cancel_event.set()
    return {"cancel_requested": True, "running": _job.running}


def training_status() -> dict:
    """当前/最近一次训练任务状态；无内存结果时回读 kv（进程重启后仍可见）。"""
    snap = _job.snapshot()
    last = _job.last_result
    if last is None and not snap["running"]:
        try:
            from ..db.kv import kv_get

            stored = kv_get(JOB_KV_KEY)
            if stored:
                last = stored.get("result")
                snap["finished_at_wall"] = stored.get("finished_at")
                snap["last_model"] = stored.get("model")
        except Exception:  # noqa: BLE001
            pass
    snap["last_result"] = last
    return snap
