"""每日盘后流水线编排（Task 15 整改 A-P1-6b：自 data/pipeline 上移）。

流程（fail-fast，任一步失败即终止并记录）：
    update_daily → validate → rebuild_qfq → build_universe → build_features
    → infer → screener_dump → build_cs_mirror

步骤集为单一事实源常量 ``FULL_STEPS``（``STEPS`` 为其别名），默认即执行全量；
CLI ``python -m app.orchestrator`` 与 ``run_pipeline(steps=None)`` 因此包含
rebuild_qfq / build_universe / build_cs_mirror（缺陷 2 修复，详见 ``FULL_STEPS`` 注释）。

为何在 data 层之外：build_features/infer 步骤依赖 app.ml——数据采集层与
模型层不得互相依赖（data ⇄ ml 曾成环）。本模块是允许依赖 data+ml+db 的
编排层；data 层只提供原子步骤（见 app/data/pipeline.py）。

特性（自 pipeline 平移，行为不变）：
- data_jobs 表记录：job_type + trade_date 幂等（SUCCESS 不可重复执行）；
- 非交易日 FAILED（reason=non_trade_day）；--dry-run 只检查不落库；
- 失败通知默认关闭（NOTIFY_ENABLED），通知失败不影响流水线；
- STEP_FUNCTIONS 可注入替换（测试用）。

CLI：
    python -m app.orchestrator --date 2026-08-28 [--codes 600519,000001] [--dry-run]
"""
from __future__ import annotations

import argparse
import asyncio
import traceback as tb_module
from collections.abc import Callable
from datetime import date, datetime

from loguru import logger
from sqlalchemy import select

from .core.config import get_settings
from .core.errors import ERR_DATA_EMPTY, AQPException
from .core.logging import setup_logging
from .data.parquet_store import atomic_write_parquet
from .domain.a_share_rules import normalize_code
from .data.pipeline import step_rebuild_qfq, step_update_daily, step_validate
from .db.models import DataJob
from .db.session import get_session_factory


async def _create_or_get_job_async(trade_date: date, job_type: str) -> tuple[DataJob, bool]:
    """幂等控制：同 (job_type, trade_date) 已 SUCCESS 则拒绝重复执行。

    FAILED/PENDING 历史记录可重试（本次执行覆盖状态）。
    """
    factory = get_session_factory()
    async with factory() as sess:
        row = (await sess.scalars(
            select(DataJob).where(DataJob.job_type == job_type, DataJob.trade_date == trade_date)
        )).first()
        if row is not None and row.status == "SUCCESS":
            return row, False
        if row is None:
            row = DataJob(job_type=job_type, trade_date=trade_date, status="PENDING")
            sess.add(row)
            await sess.flush()
        row.status, row.started_at, row.error_message, row.traceback = (
            "RUNNING", datetime.now(), None, None)
        await sess.commit()
        await sess.refresh(row)
        return row, True


async def _finish_job_async(job_id: int, status: str, step: str | None, err: str | None,
                            tb: str | None, duration_ms: int) -> None:
    factory = get_session_factory()
    async with factory() as sess:
        row = await sess.get(DataJob, job_id)
        if row is None:
            return
        row.status, row.current_step = status, step
        row.error_message, row.traceback, row.duration_ms = err, tb, duration_ms
        if status in ("SUCCESS", "FAILED"):
            row.finished_at = datetime.now()
        await sess.commit()


# ---------------- 各步骤实现（真实逻辑） ----------------


def step_build_features(trade_date: date, codes: list[str]) -> str:
    """增量重建因子（基于 hfq，asof 稳定），按年分区落盘（Task 11 整改）。

    FEATURE_INCREMENTAL=0 强制全量；默认增量：已落库特征保留，仅对
    "最新特征日 - 400 交易日预热窗"之后的原始数据重算（预热窗内重算行
    用于填充因子窗口，不直接写回），合并后原子写。
    增量一致性守卫：预热窗尾部的**历史段**（``guard_start <= date < d_last``）
    与既有特征逐值比对，不一致即放弃增量并自动回退全量重算；**前沿日**
    （``date == d_last``）允许增行（补齐被部分写入交易日的缺失 symbol），
    但断言不得减行（tests/test_feature_incremental.py 以"两段式投喂 ==
    一次性全量"及"前沿日补齐"端到端验证该保证）。
    """
    import os

    import numpy as np
    import pandas as pd
    import polars as pl

    from .ml.features import FEATURE_VERSION, apply_propagate, build_factors
    from .data.parquet_store import read_all_symbols, read_symbol_dataset

    settings = get_settings()
    symbols = read_all_symbols("daily_bar_hfq")
    out_root = settings.DATA_ROOT / "features" / f"version={FEATURE_VERSION}"
    out_root.mkdir(parents=True, exist_ok=True)
    incremental = os.environ.get("FEATURE_INCREMENTAL", "1") != "0"

    def _full() -> tuple[pd.DataFrame, str]:
        frames = [read_symbol_dataset("daily_bar_hfq", s).to_pandas()
                  for s in symbols]
        raw = pd.concat(frames, ignore_index=True)
        feats = apply_propagate(build_factors(raw))
        feats = feats.copy()
        feats["year"] = pd.to_datetime(feats["date"]).dt.year
        for year, g in feats.groupby("year"):
            # L3 压缩口径：features 是最大数据集，zstd-7 较 snappy 体积约 -35%
            atomic_write_parquet(out_root / f"year={year}.parquet",
                                 g.drop(columns=["year"]))
        # 统计口径排除 year 辅助列，与增量模式保持一致
        n_cols = len([c for c in feats.columns if c not in ("symbol", "date", "year")])
        return feats, f"full(cols={n_cols})"

    existing: pl.DataFrame | None = None
    if incremental:
        parts = sorted(out_root.glob("year=*.parquet"))
        if parts:
            existing = pl.concat([pl.read_parquet(p) for p in parts],
                                 how="diagonal_relaxed")
            # pandas 写路径会把 python date 存成 datetime[ms]，统一回 Date
            existing = existing.with_columns(pl.col("date").cast(pl.Date))
    if existing is None or existing.is_empty():
        feats, mode = _full()
        return f"mode={mode} rows={len(feats)}"

    try:
        d_last = existing["date"].max()
        # polars 标量静态类型含 bytes，显式 str() 收敛为日志/文案安全的字符串
        d_last_label = str(d_last)
        # 交易日轴直接取自已落库特征自身的日期（不依赖日历存储的可用性）
        trade_days = sorted(existing["date"].unique().to_list())
        if len(trade_days) < 2:
            raise ValueError("既有特征交易日过少，无法确定预热窗")
        # 预热窗：400 个交易日（> 最长 250 日因子窗口 + horizon 缓冲）
        warm_start = trade_days[-400] if len(trade_days) >= 400 else trade_days[0]

        frames = []
        for sym in symbols:
            df = read_symbol_dataset("daily_bar_hfq", sym)
            frames.append(df.filter(pl.col("date") >= warm_start).to_pandas())
        raw = pd.concat(frames, ignore_index=True)
        feats_new = pl.from_pandas(apply_propagate(build_factors(raw)))
        # 因子日期列经 build_factors 仍为 python date -> 统一 Date
        feats_new = feats_new.with_columns(pl.col("date").cast(pl.Date))

        # ---- 一致性守卫：历史段（guard_start <= date < d_last）与既有特征比对 ----
        # 缺陷 B（P0）：前沿日（date == d_last）此前被 `date > d_last` 严格过滤，
        # 一旦该交易日被"部分写入"（如 09-14 只落 1 行），d_last 即锁死该日，之后
        # 补再多行情也永不回填。现改为：历史段原样保留并逐值校验；前沿日允许增行
        # （补齐新 symbol），但断言不得减行，否则视为数据损坏 → 抛错回退全量。
        guard_start = trade_days[-60] if len(trade_days) >= 60 else warm_start
        overlap = feats_new.filter(
            (pl.col("date") < d_last) & (pl.col("date") >= guard_start))
        old_overlap = existing.filter(
            (pl.col("date") < d_last) & (pl.col("date") >= guard_start))
        if overlap.height != old_overlap.height:
            raise ValueError(f"重叠段行数不一致 {overlap.height} != {old_overlap.height}")
        if overlap.height:
            key = ["date", "symbol"]
            j = (overlap.join(old_overlap, on=key, how="inner", suffix="_old"))
            factor_cols = [c for c in overlap.columns
                           if c not in (*key, "year") and c in old_overlap.columns]
            a = j.select([pl.col(c) for c in factor_cols]).to_pandas().to_numpy(dtype="float64")
            b = j.select([pl.col(f"{c}_old") for c in factor_cols]).to_pandas().to_numpy(dtype="float64")
            # 容差说明：ewm 类因子（MACD/RSI）在截断起点上的浮点尾差约 1e-7
            # 相对量级；真实漂移（复权基准/数据损坏）在 1e-2 量级——1e-5 可区分两者
            if not np.allclose(a, b, rtol=1e-5, atol=1e-8, equal_nan=True):
                raise ValueError("重叠段因子值与既有特征不一致（asof 假设被破坏）")

        # 前沿日（== d_last）允许增行、禁止减行
        frontier_new = feats_new.filter(pl.col("date") == d_last)
        frontier_old = existing.filter(pl.col("date") == d_last)
        if frontier_new.height < frontier_old.height:
            raise ValueError(
                f"前沿日 {d_last_label} 行数减少 {frontier_new.height} < "
                f"{frontier_old.height}（数据损坏）")

        # 整表并集（严格不减行）：tail_new 只覆盖 date >= d_last，因此
        #   - date <  d_last 的行仍全部来自 existing → 历史段逐字不动（asof 稳定）
        #   - date >= d_last 上既有行被重算行覆盖（keep="last"），未重算的旧行原样保留
        # 不用「head_old 截断 + tail_new」写法的原因：若某 symbol 在前沿日有旧行、但本次
        # 重算没有产出它（分区被 quarantine / 该日 raw 缺失），截断写法会把该行静默丢掉，
        # 而上面的高度比对因为同时新增了别的 symbol 而看不出来（+3 -1 仍是净增）。
        tail_new = feats_new.filter(pl.col("date") >= d_last)
        merged = pl.concat([existing, tail_new], how="diagonal_relaxed")
        merged = merged.unique(subset=["date", "symbol"], keep="last").sort(["date", "symbol"])
        years = sorted({int(y) for y in merged["date"].dt.year().unique().to_list()})
        for year in years:
            atomic_write_parquet(out_root / f"year={year}.parquet",
                                 merged.filter(pl.col("date").dt.year() == year))
        feats = merged.to_pandas()
        # 可观测性：前沿日有旧行、但本次未重算的 symbol（其旧特征行被原样保留）
        stale = len(set(frontier_old["symbol"].to_list())
                    - set(frontier_new["symbol"].to_list()))
        if stale:
            logger.warning(f"[pipeline] 前沿日 {d_last_label} 有 {stale} 只标的未重算，"
                           f"保留其旧特征行（未丢行；如属数据损坏请查该日 raw/隔离清单）")
        filled = merged.filter(pl.col("date") == d_last).height - frontier_old.height
        mode = (f"incremental(warm_start={warm_start}, frontier={d_last_label}, "
                f"filled={filled}, stale={stale})")
        return f"mode={mode} rows={len(feats)}"
    except Exception as e:
        logger.warning(f"[pipeline] 增量构建校验失败（{e!r}），回退全量重算")
        feats, mode = _full()
        return f"mode={mode} rows={len(feats)}"


def step_infer(trade_date: date, codes: list[str]) -> str:
    """对 trade_date 特征推理 -> predictions/date=*.parquet。"""
    import pandas as pd

    from .ml.infer import load_prod_model, predict_with_contrib
    from .ml.features import FEATURE_VERSION

    settings = get_settings()
    # 特征目录版本必须与生产模型的 feature_version 一致（Sprint3 §4.2 bump 后
    # v1/v2g 并存）：registry 是唯一事实源，目录缺失时回退当前 FEATURE_VERSION 并告警。
    from .ml.registry import get_production

    prod = get_production("lgbm_v1")
    fv = (prod or {}).get("feature_version") or FEATURE_VERSION
    feat_dir = settings.DATA_ROOT / "features" / f"version={fv}"
    if not feat_dir.exists() and fv != FEATURE_VERSION:
        logger.warning(f"[infer] 特征目录 version={fv} 不存在，回退 {FEATURE_VERSION}")
        feat_dir = settings.DATA_ROOT / "features" / f"version={FEATURE_VERSION}"
    parts = sorted(feat_dir.glob("year=*.parquet"))
    if not parts:
        raise ValueError("features 不存在，无法推理")
    df = pd.concat([pd.read_parquet(p) for p in parts], ignore_index=True)
    target = pd.Timestamp(trade_date)
    day_df = df[pd.to_datetime(df["date"]) == target]
    if day_df.empty:
        raise ValueError(f"特征中不存在交易日 {trade_date}")
    booster, feats_list, model_dir = load_prod_model()
    pred, _ = predict_with_contrib(booster, day_df, feats_list)
    out = day_df[["date", "symbol"]].copy()
    out["pred_score"] = pred
    out["model_version"] = model_dir.name
    out["feature_version"] = fv
    out["label_horizon"] = int(settings.ML_LABEL_HORIZON)
    out_dir = settings.DATA_ROOT / "predictions"
    out_dir.mkdir(parents=True, exist_ok=True)
    atomic_write_parquet(out_dir / f"date={trade_date.strftime('%Y%m%d')}.parquet", out)
    return f"rows={len(out)} model={model_dir.name}"


def step_build_universe(trade_date: date, codes: list[str]) -> str:
    """向量化重建全历史 universe_daily（按年分区；Top-K 回测数据前提）。

    修复报告 §11 遗留项落地：build_universe_history 已就绪但长期未进默认步骤集，
    导致 universe_daily 随新数据落库逐渐过期。缺陷 2 起本步已并入默认
    ``FULL_STEPS``（手动「重跑」ops.dag_rerun 亦随之执行）。
    """
    from .data.universe import build_universe_history

    df = build_universe_history(persist=True)
    return f"rows={df.height} dates={df['date'].n_unique()}"


def step_build_cs_mirror(trade_date: date, codes: list[str]) -> str:
    """增量重建 daily_bar 三口径的截面分区镜像（MED-003 数据前置）。

    镜像只在行情落库后才有意义；缺陷 2 起并入默认 ``FULL_STEPS``——此前仅由
    晚间例行显式包含，手动「重跑」（ops.dag_rerun）会漏跑，导致 ``cs/`` 截面
    镜像停更、``/screener/stocks`` 静默回退到旧截面。
    """
    from .data.cross_section import MIRROR_DATASETS, build_mirror

    parts = [build_mirror(ds) for ds in MIRROR_DATASETS]
    built = sum(p["built"] for p in parts)
    return f"built={built} dates={[p['dates'] for p in parts]}"


def step_screener_dump(trade_date: date, codes: list[str]) -> str:
    """生成选股快照并记录 feature_runs（P1-5 验收依据之一）。

    L2-1 扩展：parquet top-50 快照保留（历史兼容），同时把四板块 top-200
    富化快照物化进 SQLite screener_snapshot（/screener 读路径 <200ms 的数据源）。

    D-02/T-08：feature_runs.feature_version 取 predictions 分区的真实列
    （step_infer 写入的 fv），严禁再硬编码 alpha_basic_v1 冒充生产版本
    （生产特征空间是 alpha_basic_v2g，v1 仅供 A/B 对照）；旧分区缺列时如实
    落 None 并告警——真实性优先于「填个好看的数」。
    """
    import polars as pl

    from .data.screening import STRATEGY, write_screener_snapshot
    from .ml.features import assert_known_feature_version

    settings = get_settings()
    pred_path = settings.DATA_ROOT / "predictions" / f"date={trade_date.strftime('%Y%m%d')}.parquet"
    if not pred_path.exists():
        raise ValueError(f"predictions 不存在: {pred_path}")
    df = pl.read_parquet(pred_path)
    # [AQP panic 守卫 2026-09-18 / panic 收口 C] 与 filter_universe 内 A1 同款
    # 「列可用性」守卫（缺列 或 dtype==pl.Null）。**此处的单列 sort 在 raw predictions
    # 分区上直接执行，早于下方 write_screener_snapshot → filter_universe（L31x）**，
    # 故**不受 A1 覆盖**（架构复核曾误判此处已经由 filter_universe 兜住）。
    # polars 1.6.0 在 dtype=pl.Null 的 pred_score 上单列 sort 抛
    # pyo3_runtime.PanicException（BaseException，非 Exception）⇒ 会穿透
    # _run_pipeline_impl 步骤执行器的 ``except Exception``（fail-fast 分支），终结整条
    # **后台**晚间例行调度协程（asyncio.create_task；不经过 ASGI 中间件栈，PanicGuard
    # 亦不覆盖）——且 build_cs_mirror 起后续步骤全部静默停更。就地转**可捕获**的
    # AQPException(ERR_DATA_EMPTY) ⇒ 被步骤执行器 ``except Exception`` 接住，如实将本步
    # 标 FAILED（可观测：data_jobs.status=FAILED + notify_failure），不波及后台协程存活。
    if "pred_score" not in df.columns or df.schema["pred_score"] == pl.Null:
        raise AQPException(
            ERR_DATA_EMPTY,
            "预测分区 pred_score 列不可用（缺列或整列全空），无法排序")
    df = df.sort("pred_score", descending=True)
    out_dir = settings.DATA_ROOT / "screener"
    out_dir.mkdir(parents=True, exist_ok=True)
    atomic_write_parquet(
        out_dir / f"date={trade_date.strftime('%Y%m%d')}.parquet", df.head(50))
    snap_summary = write_screener_snapshot(trade_date.isoformat(), df)

    # 真实特征版本：来自 step_infer 落盘的 predictions 分区列（单一事实源）。
    # 用 assert_known_feature_version 做白名单校验（未登记版本 fail-fast，拒绝冒名写库）。
    fv_cell = (df["feature_version"][0]
               if ("feature_version" in df.columns and df.height) else None)
    raw_fv = str(fv_cell) if fv_cell is not None else None
    feature_version = assert_known_feature_version(
        raw_fv, where="orchestrator.step_screener_dump")

    async def _log_run() -> None:
        from .db.models import FeatureRun

        factory = get_session_factory()
        async with factory() as sess:
            sess.add(FeatureRun(
                strategy=STRATEGY, trade_date=trade_date,
                model_version=(df["model_version"][0]
                               if "model_version" in df.columns else None),
                feature_version=feature_version, top_k=50,
            ))
            await sess.commit()
    # 缺陷 6（P1）：不得依赖「调用方已在本线程 set 过 event loop」。
    # asyncio.get_event_loop() 在**无当前 loop 的线程**（恢复脚本 / 单元测试 /
    # CLI 直接调 STEP_FUNCTIONS["screener_dump"]）会抛
    #   RuntimeError: There is no current event loop in thread 'MainThread'。
    # 本函数**先**写 top50 parquet、write_screener_snapshot()、特征版本白名单校验，
    # **最后**才写 FeatureRun 审计行 ⇒ 崩在末行时数据其实都已落库，但整步被标
    # FAILED；而 _run_pipeline_impl 是 fail-fast ⇒ 紧随其后的 build_cs_mirror 被
    # 中止 ⇒ 截面镜像**静默停更**（与缺陷 2 同类的连环静默）。
    # 修法：无 loop 时就地新建并 set（同一线程复用，后续步骤可用）。
    # ⚠️ 绝不可改成 asyncio.run(_log_run())：它会另起一个 loop，与 _run_pipeline_impl
    # 当前 loop 不是同一个；异步 SQLAlchemy 引擎的连接池绑定 loop，跨 loop 复用连接
    # 会永久 pending（本仓已有同类事故前例）。
    try:
        loop = asyncio.get_event_loop()
    except RuntimeError:
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
    loop.run_until_complete(_log_run())
    return f"top50 dumped; snapshot {snap_summary}"


# ---------------------------------------------------------------------------
# 单一事实源（缺陷 2，P1）：每日流水线的**完整**步骤集与唯一顺序。
#
# orchestrator.STEPS、晚间例行（jobs/evening_routine）、手动入口
# （api/v1/ops.dag_rerun）全部从本常量派生，禁止再各自硬编码字面量列表——
# 此前默认 STEPS 缺 rebuild_qfq/build_universe/build_cs_mirror，而晚间例行带全套，
# 手动「重跑」（文案「真实重跑每日流水线」）却落到精简默认集 ⇒ qfq / universe /
# 截面镜像永不重建且无任何提示（审计 FIX-SPEC §2）。
#
# 顺序与模块头部 docstring 一致：
#   update_daily → validate → rebuild_qfq → build_universe → build_features
#   → infer → screener_dump → build_cs_mirror
#
# 行为变更提示：``STEPS`` 同时是 ``python -m app.orchestrator`` CLI 与
# ``run_pipeline(steps=None)`` 的默认集；本常量补全后，CLI 与默认调用会**多跑**
# rebuild_qfq / build_universe / build_cs_mirror 三步（均离线、幂等，依赖
# instrument 表与行情就绪）。
FULL_STEPS: list[str] = [
    "update_daily",
    "validate",
    "rebuild_qfq",
    "build_universe",
    "build_features",
    "infer",
    "screener_dump",
    "build_cs_mirror",
]
# 兼容别名：CLI / 既有调用方（含 tests）以 ``STEPS`` 引用默认步骤集，实例相等。
STEPS = FULL_STEPS

# 晚间例行（jobs/evening_routine，17:30）专用步骤集：从 FULL_STEPS **有序剔除** 1 步。
#   - update_daily：autoSync(15:45) 已同步行情；此步无「已最新则跳过」判据
#     （step_update_daily 对每个 code 无条件 fetch），全市场 2499 只 × 2 口径
#     ≈ 4998 次网络调用，在全局限速（AKSHARE_RATE_LIMIT 默认 1.2s，_throttle 为
#     全局串行锁）下下限约 100min，属纯冗余重下载，会把 features/infer/日报整体推迟。
# 注意：validate **保留**在晚间例行——它是「昨有今无」式整日数据丢失（缺陷 3）的
# 唯一门禁（09-14 那天正是昨有今无；带此步即 FAILED 告警，而非静默用陈旧数据产出
# 榜单）。其全市场直跑的安全性由 data/pipeline.py 的 VALIDATE_MISSING_TOLERANCE_*
# 容差保证（临时停牌股不会误杀）。
# 顺序与 FULL_STEPS 同源、只做有序剔除 ⇒ 与单一事实源不可能漂移
# （tests/test_sync_integrity.py 钉死该派生关系）。
_EVENING_EXCLUDED: tuple[str, ...] = ("update_daily",)
EVENING_STEPS: list[str] = [s for s in FULL_STEPS if s not in _EVENING_EXCLUDED]


STEP_FUNCTIONS: dict[str, Callable[[date, list[str]], str]] = {
    "update_daily": step_update_daily,
    "validate": step_validate,
    "build_universe": step_build_universe,
    "rebuild_qfq": step_rebuild_qfq,
    "build_features": step_build_features,
    "infer": step_infer,
    "screener_dump": step_screener_dump,
    "build_cs_mirror": step_build_cs_mirror,
}


def notify_failure(message: str) -> None:
    """失败通知：默认关闭；任何异常只记日志，绝不反向导致流水线失败。"""
    s = get_settings()
    if not s.NOTIFY_ENABLED:
        return
    try:
        if s.NOTIFY_WEBHOOK_URL:
            import httpx

            httpx.post(s.NOTIFY_WEBHOOK_URL, json={"content": f"[AQP] pipeline failed: {message}"},
                       timeout=5.0)
        logger.info(f"notify sent: {message[:120]}")
    except Exception as e:
        logger.warning(f"notify failed (ignored): {e!r}")


# ---------------- 主入口 ----------------
def is_trade_day_checked(trade_date: date) -> bool:
    """复用 domain/calendar + data/calendar_store 判断交易日。"""
    from .data.calendar_store import get_calendar
    from .domain.calendar import is_trade_day

    return is_trade_day(trade_date, get_calendar())


def run_pipeline(trade_date: date, codes: list[str] | None = None,
                 dry_run: bool = False,
                 steps: list[str] | None = None) -> tuple[DataJob | None, bool]:
    """执行每日流水线（管道互斥：与 sync/mirror/training 互斥，见 core/pipeline_lock）。

    返回 (job, executed)；dry-run 时 job 为 None。
    冲突时抛 ``PipelineBusy``——调用方（evening_routine / ops.dag_rerun）的
    ``except Exception`` 兜底会优雅降级，不会挂起排队。
    """
    from .core.pipeline_lock import pipeline_slot

    with pipeline_slot("pipeline"):
        return _run_pipeline_impl(trade_date, codes, dry_run=dry_run, steps=steps)


def _run_pipeline_impl(trade_date: date, codes: list[str] | None = None,
                       dry_run: bool = False,
                       steps: list[str] | None = None) -> tuple[DataJob | None, bool]:
    """执行每日流水线。返回 (job, executed)；dry-run 时 job 为 None。

    steps: 显式指定步骤子集；None = 全量默认集 ``STEPS``（= ``FULL_STEPS``）。
    未知步骤名直接抛错（防静默漏跑）。
    """
    # codes 规范化：逐项 normalize_code + 保序去重（唯一收敛点，覆盖全部调用路径）
    #
    # 1) 为什么在这里做：流水线 ``codes`` 的语义是**纯 6 位代码**（CLI 默认值与下方
    #    兜底值都是裸码），但 jobs/evening_routine 传的是
    #    ``read_all_symbols("daily_bar")`` —— 返回带交易所后缀的 symbol
    #    （实测 2499 只全部形如 ``000001.SZ``）。后缀值进 step_validate 的
    #    ``code_to_symbol`` 即抛 ``ValueError: code 必须 6 位数字``，而本函数是
    #    fail-fast ⇒ validate 首步挂、后续 6 步全不执行 ⇒ 晚间例行每晚静默失效。
    #    入口是唯一能同时覆盖 evening_routine / ops.dag_rerun / CLI 三条调用路径
    #    的收敛点：只改某一侧调用方，另外两条依旧会漏（本仓教训：'只改一侧'最危险）。
    # 2) 为什么必须去重：step_validate 用 ``len(codes)`` 作分母算容差
    #    （``VALIDATE_MISSING_TOLERANCE_RATIO × n_expected``）与覆盖率，重复项会
    #    虚增分母 ⇒ 真实的整日丢失可能被静默放行。保序（dict.fromkeys）则保证
    #    步骤内处理顺序稳定、日志/报错里出现的次序与调用方一致。
    raw_codes: list[str] = list(codes) if codes else []
    normalized: list[str] = []
    for raw in raw_codes:
        try:
            normalized.append(normalize_code(raw))
        except ValueError as e:
            # 单条脏数据不得让整条流水线在「无错误信息」的状态下崩掉：显式带上
            # 原始入参抛出（normalize_code 已含 raw），便于值班直接定位脏数据来源。
            raise ValueError(f"流水线 codes 参数含非法标的: {e}") from e
    codes = list(dict.fromkeys(normalized)) or ["600519", "000001", "300750"]
    if len(raw_codes) != len(codes):
        # 可观测信号：N≠M 说明调用方传了带后缀/带空白/重复的错格式入参
        logger.info(f"[pipeline] codes 规范化: {len(raw_codes)} 项 -> {len(codes)} 项"
                    f"（入参口径应为纯 6 位代码）")
    steps = steps or STEPS
    unknown = [s for s in steps if s not in STEP_FUNCTIONS]
    if unknown:
        raise ValueError(f"未知流水线步骤: {unknown}（可用: {STEPS}）")

    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)
    try:
        # 0) 交易日判断（dry-run 也检查，但不写库）
        trade_ok = is_trade_day_checked(trade_date)
        if dry_run:
            checks = {
                "trade_day": trade_ok,
                "codes": len(codes),
                "steps": steps,
                "data_dir": str(get_settings().DATA_ROOT),
                "notify_enabled": get_settings().NOTIFY_ENABLED,
            }
            logger.info(f"[pipeline] dry-run 检查: {checks}")
            return None, False
        if not trade_ok:
            job, _ = loop.run_until_complete(_create_or_get_job_async(trade_date, "daily_pipeline"))
            loop.run_until_complete(_finish_job_async(
                job.id, "FAILED", None, "non_trade_day: 非交易日不执行流水线", None, 0))
            logger.warning(f"[pipeline] {trade_date} 非交易日，终止")
            job.status, job.error_message, job.duration_ms = "FAILED",                 "non_trade_day: 非交易日不执行流水线", 0
            return job, True

        job, should_run = loop.run_until_complete(
            _create_or_get_job_async(trade_date, "daily_pipeline"))
        if not should_run:
            logger.info(f"[pipeline] {trade_date} 已 SUCCESS，幂等跳过")
            return job, False

        t0 = datetime.now()
        for step in steps:
            fn = STEP_FUNCTIONS[step]
            logger.info(f"[pipeline] step={step} start")
            try:
                detail = fn(trade_date, codes)
                logger.info(f"[pipeline] step={step} ok: {detail}")
            except Exception as e:  # fail-fast：终止后续所有步骤
                tb = tb_module.format_exc()
                dur = int((datetime.now() - t0).total_seconds() * 1000)
                loop.run_until_complete(_finish_job_async(
                    job.id, "FAILED", step, f"{type(e).__name__}: {e}", tb, dur))
                logger.error(f"[pipeline] step={step} FAILED: {e!r}")
                notify_failure(f"{trade_date} {step}: {e}")
                job.status, job.current_step = "FAILED", step
                job.error_message, job.traceback = f"{type(e).__name__}: {e}", tb
                job.finished_at, job.duration_ms = datetime.now(), dur
                return job, True
        dur = int((datetime.now() - t0).total_seconds() * 1000)
        loop.run_until_complete(_finish_job_async(
            job.id, "SUCCESS", steps[-1], None, None, dur))
        job.status, job.current_step = "SUCCESS", steps[-1]
        job.finished_at, job.duration_ms = datetime.now(), dur
        return job, True
    finally:
        asyncio.set_event_loop(None)
        loop.close()


def _last_completed_trade_date(today: date | None = None) -> date:
    """默认目标日：今天；非交易日/未来则回退最近交易日（日历驱动）。"""
    from .data.calendar_store import get_calendar
    from .domain.calendar import is_trade_day, prev_trade_day

    today = today or date.today()
    cal = get_calendar()
    if is_trade_day(today, cal) and today <= date.today():
        return today
    return prev_trade_day(min(today, date.today()), cal)


def main() -> None:
    parser = argparse.ArgumentParser(prog="python -m app.orchestrator",
                                     description="AQP 每日盘后流水线")
    parser.add_argument("--date", default=None, help="YYYY-MM-DD（默认最近交易日）")
    parser.add_argument("--codes", default="600519,000001,300750")
    parser.add_argument("--dry-run", action="store_true", help="仅检查，不产生数据")
    args = parser.parse_args()

    setup_logging()
    trade_date = date.fromisoformat(args.date) if args.date else _last_completed_trade_date()
    codes = [c.strip() for c in args.codes.split(",") if c.strip()]
    job, _ = run_pipeline(trade_date, codes, dry_run=args.dry_run)
    if job is None:
        print(f"pipeline dry-run: {trade_date} 检查完成（未产生数据）")
    else:
        print(f"pipeline result: {trade_date} status={job.status} step={job.current_step} "
              f"error={job.error_message}")




if __name__ == "__main__":
    main()
