"""P1-1 每日流水线测试：PIPE-ORDER / SUCCESS / FAIL-FAST / RETRY / IDEMPOTENT / NON-TRADE-DAY。

测试环境：test_api 已将 SQLITE_URL / DATA_ROOT 隔离到测试目录；
此处自建表结构并注入日历（update_daily 步骤以本地种子替代真实网络拉取，
其余步骤全部真实执行——真实网络链路由 P1 全量验收另行覆盖）。
"""
from __future__ import annotations

import asyncio
import sys
from datetime import date
from pathlib import Path

import numpy as np
import pandas as pd
import polars as pl
import pytest

BACKEND_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(BACKEND_ROOT))

from app import orchestrator as orchestrator_mod  # noqa: E402
from app.data.calendar_store import set_calendar  # noqa: E402
from app.data.parquet_store import write_year_batch  # noqa: E402
from app.db.init_db import init_database  # noqa: E402
from app.db.session import get_session_factory  # noqa: E402
from app.domain.calendar import build_calendar  # noqa: E402
from app.ml.registry import PromotePolicy, promote_model  # noqa: E402
from app.ml.train_lgbm import train_lgbm  # noqa: E402

# 测试专用宽松策略：本文件验证流水线编排，不验证模型质量
PERMISSIVE = PromotePolicy(min_valid_rank_ic=-1.0, min_valid_icir=-1.0,
                           rank_ic_tolerance=1e9, icir_tolerance=1e9,
                           max_rmse_worsen_ratio=1e9, allow_missing_metrics=True)

TRADE_DAY = date(2024, 6, 5)      # Wednesday
SATURDAY = date(2024, 6, 8)
SYM = "600519.SH"
CODE = "600519"
calls: list[str] = []


def _seed_day(d: date) -> None:
    """种子：某交易日单标的日线（双口径），供 validate/后续步骤真实执行。"""
    rng = np.random.default_rng(3)
    pdf = pl.DataFrame({
        "symbol": [SYM], "code": [CODE], "date": [d],
        "open": [10.0], "high": [10.2], "low": [9.8], "close": [10.1],
        "volume": [50_000.0], "amount": [505_000.0],
        "pct": [0.5], "turnover": [1.0],
    })
    for adjust in ("", "hfq"):
        dataset = "daily_bar" if adjust == "" else f"daily_bar_{adjust}"
        write_year_batch(dataset, SYM, d.year, pdf)


def _seed_history_for_model() -> None:
    """为 infer 步骤准备最小可用模型（合成历史 -> 快速训练）。"""
    from app.ml.features import build_factors

    rng = np.random.default_rng(9)
    dates = pd.bdate_range("2023-01-02", periods=300)
    rets = np.zeros(300)
    for t in range(1, 300):
        rets[t] = 0.25 * rets[t - 1] + 0.02 * rng.standard_normal()
    close = 20.0 * np.exp(np.cumsum(rets))
    df = pd.DataFrame({
        "symbol": SYM, "code": CODE, "date": dates,
        "open": close, "high": close * 1.01, "low": close * 0.99, "close": close,
        "volume": rng.integers(1e4, 1e5, 300).astype(float),
    })
    pdf = pl.from_pandas(df)
    write_year_batch("daily_bar_hfq", SYM, 2023, pdf.filter(pl.col("date").dt.year() == 2023))
    write_year_batch("daily_bar_hfq", SYM, 2024, pdf.filter(pl.col("date").dt.year() == 2024))
    r = train_lgbm(build_factors(df), horizon=5, holdout_days=40, test_days=30,
                   gap_days=5, version_suffix="pipe", num_boost_round=40,
                   stopping_rounds=10, min_abs_rank_ic=0.0, top_k=30,
                   params={"min_data_in_leaf": 10, "num_leaves": 7})
    # 第三阶段纪律：训练只产生 candidate，step_infer 走 load_prod_model
    # 必须显式 promote。质量门槛在此放宽（本文件只验证流水线编排）。
    res = promote_model("lgbm_v1", r["version"], by="pytest",
                        reason="pipeline 测试", policy=PERMISSIVE)
    assert res["promoted"], f"测试前置 promote 失败：{res['reason']}"


@pytest.fixture()
def pipeline_env(monkeypatch: pytest.MonkeyPatch):
    """初始化表 / 日历 / 种子数据 / 模型；捕获步骤调用顺序。"""
    global calls
    calls = []
    asyncio.run(init_database())
    # 每个测试独立：清理同 (job_type, trade_date) 的历史作业记录
    async def _clear_job() -> None:
        from sqlalchemy import delete as sa_delete

        from app.db.models import DataJob

        factory = get_session_factory()
        async with factory() as sess:
            await sess.execute(sa_delete(DataJob).where(
                DataJob.job_type == "daily_pipeline",
                DataJob.trade_date.in_([TRADE_DAY, SATURDAY])))
            await sess.commit()
    asyncio.run(_clear_job())
    # 日历：注入连续工作日（含 TRADE_DAY），周六不在其中
    import datetime as dt

    days = [date(2024, 6, 3) + dt.timedelta(days=i) for i in range(6)]
    set_calendar(build_calendar(d for d in days if d.weekday() < 5))

    _seed_day(TRADE_DAY)
    _seed_history_for_model()

    real_update = orchestrator_mod.STEP_FUNCTIONS["update_daily"]

    def counting_update(d: date, codes: list[str]) -> str:
        calls.append("update_daily")
        return real_update(d, codes)

    monkeypatch.setitem(orchestrator_mod.STEP_FUNCTIONS, "update_daily", counting_update)
    # 记录其余步骤顺序
    for step in ("validate", "build_features", "infer", "screener_dump"):
        real = orchestrator_mod.STEP_FUNCTIONS[step]

        def make(step: str, real) -> object:
            def _f(d: date, codes: list[str]) -> str:
                calls.append(step)
                return real(d, codes)
            return _f
        monkeypatch.setitem(orchestrator_mod.STEP_FUNCTIONS, step, make(step, real))

    yield
    monkeypatch.undo()
    # 清理本 fixture 写入**共享** DATA_ROOT/predictions 的分区：step_infer 经
    # ``pd.read_parquet`` -> ``pl.from_pandas`` 落盘，date 为 Datetime(ms) 口径
    # （``date=20240605.parquet``）。它不留痕会与其它模块的 Date / Utf8 口径
    # predictions 在 monitor 的 ``diagonal_relaxed`` 拼接时被抬升为 String
    # （Datetime 值串成 "2024-06-05 00:00:00.000"），令下游 ``.cast(pl.Date)`` 崩
    # ——这正是全量套件里 test_write_endpoints_smoke::test_monitor_run_runs 的
    # 顺序敏感 flake 根因。只删本模块写入的单个分区，绝不碰共享目录其它内容。
    from app.core.config import get_settings as _get_settings

    (_get_settings().DATA_ROOT / "predictions"
     / f"date={TRADE_DAY.strftime('%Y%m%d')}.parquet").unlink(missing_ok=True)


def test_pipe_order_and_success(pipeline_env):
    """PIPE-ORDER + PIPE-SUCCESS：五步按序执行，作业 SUCCESS。"""
    job, executed = orchestrator_mod.run_pipeline(TRADE_DAY, [CODE])
    assert executed is True
    assert job.status == "SUCCESS" and job.error_message is None
    assert calls == ["update_daily", "validate", "build_features", "infer", "screener_dump"]
    assert job.duration_ms >= 0 and job.finished_at is not None


def test_pipe_fail_fast(pipeline_env, monkeypatch: pytest.MonkeyPatch):
    """PIPE-FAIL-FAST：build_features 失败 -> infer/screener 不执行，记录 traceback。"""
    def boom(d: date, codes: list[str]) -> str:
        calls.append("build_features")
        raise RuntimeError("模拟特征构建失败")

    monkeypatch.setitem(orchestrator_mod.STEP_FUNCTIONS, "build_features", boom)
    job, _ = orchestrator_mod.run_pipeline(TRADE_DAY, [CODE])
    assert job.status == "FAILED" and job.current_step == "build_features"
    assert "模拟特征构建失败" in (job.error_message or "")
    assert job.traceback and "RuntimeError" in job.traceback
    assert "infer" not in calls and "screener_dump" not in calls


def test_pipe_retry_after_failure(pipeline_env, monkeypatch: pytest.MonkeyPatch):
    """PIPE-RETRY：FAILED 后修复重跑，同 (job_type, trade_date) 记录复用并 SUCCESS。"""
    real_validate = orchestrator_mod.STEP_FUNCTIONS["validate"]

    def boom(d: date, codes: list[str]) -> str:
        raise RuntimeError("临时故障")

    monkeypatch.setitem(orchestrator_mod.STEP_FUNCTIONS, "validate", boom)
    job1, _ = orchestrator_mod.run_pipeline(TRADE_DAY, [CODE])
    assert job1.status == "FAILED" and job1.current_step == "validate"
    # 修复后重试：同一 (job_type, trade_date) 记录被复用并翻转为 SUCCESS
    monkeypatch.setitem(orchestrator_mod.STEP_FUNCTIONS, "validate", real_validate)
    calls.clear()
    job2, executed = orchestrator_mod.run_pipeline(TRADE_DAY, [CODE])
    assert executed is True and job2.status == "SUCCESS"
    assert job2.id == job1.id, "重试应复用同一作业记录"
    assert calls == ["update_daily", "validate", "build_features", "infer", "screener_dump"]


def test_pipe_idempotent(pipeline_env):
    """PIPE-IDEMPOTENT：SUCCESS 后再次执行被幂等跳过，步骤零调用。"""
    job1, _ = orchestrator_mod.run_pipeline(TRADE_DAY, [CODE])
    assert job1.status == "SUCCESS"
    calls.clear()
    job2, executed = orchestrator_mod.run_pipeline(TRADE_DAY, [CODE])
    assert executed is False and job2.status == "SUCCESS"
    assert calls == [], "SUCCESS 作业不得重复执行任何步骤"


def test_pipe_non_trade_day(pipeline_env):
    """PIPE-NON-TRADE-DAY：周六 -> FAILED(reason=non_trade_day)，不产生行情。"""
    job, executed = orchestrator_mod.run_pipeline(SATURDAY, [CODE])
    assert executed is True and job.status == "FAILED"
    assert "non_trade_day" in (job.error_message or "")
    assert calls == []


def test_pipe_dry_run_no_data(pipeline_env):
    """PIPE-DRY-RUN：只检查不产生数据（不写 data_jobs、不调用任何步骤）。"""
    job, executed = orchestrator_mod.run_pipeline(TRADE_DAY, [CODE], dry_run=True)
    assert job is None and executed is False
    assert calls == []
