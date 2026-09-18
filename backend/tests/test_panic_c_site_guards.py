"""panic 收口 C 段回归：**(c) 类**站点（raw predictions 分区上直接 sort，早于任何
``filter_universe``，故不受 A 段 A1 守卫覆盖）的就地根因守卫——把**不可捕获**的
``pyo3_runtime.PanicException`` 转成**可捕获**的 ``AQPException(ERR_DATA_EMPTY)``。

背景（架构师只读实测 + 本轮复核，polars 1.6.0）
--------------------------------------------
``dtype == pl.Null`` 的列上做**单列** ``sort`` 抛 ``PanicException``
（MRO=``[PanicException, BaseException, object]``，**不是 ``Exception`` 子类**），
Starlette 中间件栈与业务层 ``except Exception`` 均接不住。**多列** sort / ``max`` /
``rank`` 则不然：多列 sort 与 ``max`` 抛可捕获的 ``InvalidOperationError``，
``rank`` **根本不抛**（实测 OK）。

⚠️ 附加实测：触发该 panic 需**多列帧**——单列帧（只有 pred_score 一列）polars 走
fast-path 不 panic。生产 predictions 分区恒为多列，故真实路径必 panic。

为何只测 (c) 类（本段的分类结论，2026-09-18 裁决）
------------------------------------------------
全仓 ``pred_score`` 单列 sort 共 6 处，分三类：
  (a) 自身有守卫：``data/screening.py:132``（A1，L128-131 紧邻其前）、
      ``api/v1/report.py:240``（A2，L236-239 紧邻其前）；
  (b) 位于 ``filter_universe`` 调用**下游**：``api/v1/market.py:310``（上游 L303）、
      ``api/v1/alerts.py:351``（上游 L346）、``api/v1/alerts.py:358``（上游 L372）——
      ``filter_universe`` 在**返回之前**就因 A1 抛 ``AQPException``，脏帧**走不到**
      这些 sort ⇒ 就地守卫是**死分支**（本段曾误加，已按裁决撤回；其不变式由
      ``test_filter_universe_never_returns_null_pred_score`` 钉死）；
  (c) **独立且可达**：``orchestrator.py:322`` —— ``df = pl.read_parquet(pred_path)``
      之后**立刻**单列 sort，而 ``write_screener_snapshot(...)``（→ ``filter_universe``）
      在其**之后**才调用 ⇒ A1 保护不到。

为何 (c) 致命（后台静默）
----------------------
``step_screener_dump`` 跑在**后台**晚间例行 / 流水线里（``asyncio.create_task`` →
``to_thread``），**不经过 ASGI 中间件栈** ⇒ B 段全局 ``PanicGuard`` 兜底**不覆盖**；
而 ``_run_pipeline_impl`` 的步骤执行器只 ``except Exception``（fail-fast）⇒
PanicException 穿透 ⇒ **终结整条调度协程**，``build_cs_mirror`` 起后续步骤静默停更。
就地转 ``AQPException`` 后：被该 ``except Exception`` 接住 ⇒ 本步如实标 FAILED
（可观测：``data_jobs.status=FAILED`` + ``notify_failure``），后台协程存活。

隔离：DATA_ROOT 指向 ``tmp_path``、不写共享 ``data/``、不联网。
"""
from __future__ import annotations

import sys
from datetime import date
from pathlib import Path

import polars as pl
import pytest

BACKEND_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(BACKEND_ROOT))

from app.core.config import get_settings  # noqa: E402
from app.core.errors import ERR_DATA_EMPTY, AQPException  # noqa: E402
from app.data.screening import filter_universe  # noqa: E402
from app.orchestrator import step_screener_dump  # noqa: E402

DAY = date(2026, 9, 17)


def _null_frame() -> pl.DataFrame:
    """``symbol``/``date`` 正常，``pred_score`` **整列全空** ⇒ dtype=pl.Null（多列帧）。"""
    return pl.DataFrame({
        "symbol": ["600000.SH", "600001.SH"],
        "date": [DAY, DAY],
        "pred_score": pl.Series([None, None], dtype=pl.Null),
    })


def _healthy_frame() -> pl.DataFrame:
    return pl.DataFrame({
        "symbol": ["600000.SH", "600001.SH"],
        "date": [DAY, DAY],
        "pred_score": [0.9, 0.1],
    })


def _write_null_pred_partition(root: Path, day: date) -> None:
    """写一份 **pred_score 整列全空** 的 predictions 分区（多列帧）。"""
    pred_dir = root / "predictions"
    pred_dir.mkdir(parents=True, exist_ok=True)
    _null_frame().write_parquet(pred_dir / f"date={day.strftime('%Y%m%d')}.parquet")


def test_orchestrator_screener_dump_null_pred_score_raises_aqp(monkeypatch, tmp_path):
    """(c) 类：``pred_score`` 整列全空（dtype=pl.Null）⇒ 就地守卫抛
    ``AQPException(ERR_DATA_EMPTY)``，**不是** PanicException。

    ⚠️ 变异反证即本断言本身：删掉守卫 ⇒ ``pl.read_parquet(...).sort("pred_score")``
    在多列 Null 帧上抛 ``PanicException``（BaseException）⇒ ``pytest.raises(AQPException)``
    抓不住 ⇒ 用例 **error**（而非 fail）。证明守卫确实拦截了 panic。
    """
    monkeypatch.setattr(get_settings(), "DATA_ROOT", tmp_path)
    _write_null_pred_partition(tmp_path, DAY)

    with pytest.raises(AQPException) as ei:
        step_screener_dump(DAY, [])
    assert ei.value.code == ERR_DATA_EMPTY
    assert type(ei.value) is AQPException, "必须是可捕获的 AQPException，而非 PanicException"
    assert "pred_score" in ei.value.message


def test_orchestrator_screener_dump_missing_pred_score_raises_aqp(monkeypatch, tmp_path):
    """(c) 类：``pred_score`` **缺列** ⇒ 同款守卫抛 AQPException(ERR_DATA_EMPTY)
    （而非裸 ColumnNotFoundError）。"""
    monkeypatch.setattr(get_settings(), "DATA_ROOT", tmp_path)
    pred_dir = tmp_path / "predictions"
    pred_dir.mkdir(parents=True, exist_ok=True)
    pl.DataFrame({"symbol": ["600000.SH"], "date": [DAY]}).write_parquet(
        pred_dir / f"date={DAY.strftime('%Y%m%d')}.parquet")

    with pytest.raises(AQPException) as ei:
        step_screener_dump(DAY, [])
    assert ei.value.code == ERR_DATA_EMPTY


def test_step_executor_catches_aqp_but_not_panic():
    """契约：复刻 ``_run_pipeline_impl`` 步骤执行器的捕获粒度（``except Exception``，fail-fast）。

    - 守卫抛的 ``AQPException`` **必被接住** ⇒ 本步标 FAILED（可观测，后台协程存活）；
    - polars 单列 sort 在多列 Null 帧上抛的 ``PanicException``（BaseException）**接不住**
      ⇒ 会穿透并终结后台调度协程 —— 正是本守卫要消除的差异。
    """
    def _executor(fn):  # noqa: ANN001, ANN202
        try:
            fn()
        except Exception as e:  # noqa: BLE001 复刻执行器 L50x 的捕获粒度
            return f"FAILED:{type(e).__name__}"
        return "OK"

    def _raise_aqp() -> None:
        raise AQPException(ERR_DATA_EMPTY, "boom")

    assert _executor(_raise_aqp) == "FAILED:AQPException"

    with pytest.raises(BaseException) as ei:
        _executor(lambda: _null_frame().sort("pred_score"))
    assert not isinstance(ei.value, Exception), "PanicException 必须不是 Exception 子类"
    assert type(ei.value).__name__ == "PanicException"


def test_filter_universe_never_returns_null_pred_score():
    """钉死 **(b) 类**判定的不变式：``filter_universe`` 在**返回之前**就抛（A1），
    其**返回值**永不可能是 Null dtype 的 pred_score。

    这条不变式是「``market.py:310`` / ``alerts.py:351`` / ``alerts.py:358`` 无需就地
    守卫」的**运行时依据**：它们排的帧全部来自 ``filter_universe`` 的返回值，脏帧
    走不到。若有人把 A1 放宽成「静默返回脏帧」，本用例立刻变红 ⇒ (b) 类需重判。
    """
    null_df = _null_frame()
    assert null_df.schema["pred_score"] == pl.Null, "前提：帧的 pred_score 确为 Null dtype"

    # 脏帧：必须抛（不得静默返回）
    with pytest.raises(AQPException) as ei:
        filter_universe(null_df, DAY.isoformat(), "all")
    assert ei.value.code == ERR_DATA_EMPTY

    # 正常帧：返回值可用（守卫未误伤）
    out, pool = filter_universe(_healthy_frame(), DAY.isoformat(), "all")
    assert pool == 2
    assert out.schema["pred_score"] != pl.Null
    assert out["pred_score"].to_list() == [0.9, 0.1]   # 降序
