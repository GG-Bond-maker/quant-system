"""A 段（panic 根因守卫）回归：脏列走既有降级分支，而非不可捕获的 PanicException。

背景（架构师只读实测，2026-09-18）：polars 1.6.0 在 dtype==``pl.Null`` 的列上做
**单列** ``sort`` 会抛 ``pyo3_runtime.PanicException``（MRO =
``[PanicException, BaseException, object]``，**不是 ``Exception`` 子类**）——
Starlette 的 ``ServerErrorMiddleware`` / ``ExceptionMiddleware`` / ``BaseHTTPMiddleware``
均只 ``except Exception`` ⇒ 全部漏接 ⇒ 最终 uvicorn ``except BaseException`` 发**裸 500**
（绕过本项目 ``{code,message,data,trace_id,ts}`` 信封）。**仅单列 sort 才 panic**；
多列 sort 抛的是可捕获的 ``InvalidOperationError``。

A 段在 3 个根因点加「列**可用性**」守卫（缺列 **或** ``dtype == pl.Null``），就地转成
**可捕获**的 ``AQPException(ERR_DATA_EMPTY)``，交由调用方既有的降级分支处理：
1. ``app/data/screening.py::filter_universe``（覆盖 /screener 今日榜、昨日对比、
   /market/overview… 等所有经它的调用方）；
2. ``app/api/v1/report.py::_score_shift_section``（**不经过** filter_universe，独立守卫）；
3. ``app/api/v1/screener.py::_screen`` 今日榜（把 ``_build_items`` 纳入
   ``ERR_DATA_EMPTY ⇒ status="unavailable"`` 降级）。

本文件只测 **A 段根因守卫本身**（与 B 段全局兜底 ``core/panic_guard.py`` 解耦）。
自包含：不写共享 DATA_ROOT（用 tmp_path）、不联网。
"""
from __future__ import annotations

import sys
from datetime import date, timedelta
from pathlib import Path

import polars as pl
import pytest
from fastapi.testclient import TestClient

BACKEND_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(BACKEND_ROOT))

from app.api.v1 import screener as screener_api  # noqa: E402
from app.cache.memory import lru_clear  # noqa: E402
from app.core.config import get_settings  # noqa: E402
from app.core.errors import ERR_DATA_EMPTY, AQPException  # noqa: E402
from app.data.screening import filter_universe  # noqa: E402
from app.main import app  # noqa: E402

_ADMIN = {"Authorization": "Bearer aqp-dev-token-change-me"}
DAY = date(2026, 9, 17)


def _null_pred_score_frame() -> pl.DataFrame:
    """symbol/date 正常，**pred_score 列整列全空** ⇒ dtype=pl.Null 的多列帧。"""
    return pl.DataFrame({
        "symbol": ["600000.SH", "600001.SH", "600002.SH"],
        "date": [DAY, DAY, DAY],
        "pred_score": pl.Series([None, None, None], dtype=pl.Null),
    })


# ---------------- 1) filter_universe：Null dtype / 缺列 ⇒ AQPException(ERR_DATA_EMPTY) ----------------
def test_filter_universe_null_pred_score_raises_data_empty():
    """``pred_score`` 整列全空（dtype=pl.Null）⇒ 抛 AQPException(ERR_DATA_EMPTY)，**非 PanicException**。

    ⚠️ 若守卫失效，``df.sort("pred_score")`` 会抛 ``PanicException``（BaseException 子类），
    ``pytest.raises(AQPException)`` 根本抓不住 ⇒ 用例直接 error。故此断言本身即证明
    「**没有** panic」，且错误码为既有降级口径 ERR_DATA_EMPTY。
    """
    df = _null_pred_score_frame()
    assert df.schema["pred_score"] == pl.Null, "前提：pred_score 必须为 Null dtype"
    with pytest.raises(AQPException) as ei:
        filter_universe(df, DAY.isoformat(), "main")
    assert ei.value.code == ERR_DATA_EMPTY
    assert type(ei.value) is AQPException, "必须是可捕获的 AQPException，而非 PanicException"


def test_filter_universe_missing_pred_score_raises_data_empty():
    """``pred_score`` **缺列** ⇒ 同样抛 AQPException(ERR_DATA_EMPTY)（而非裸 ColumnNotFoundError）。"""
    df = pl.DataFrame({"symbol": ["600000.SH"], "date": [DAY]})  # 无 pred_score 列
    assert "pred_score" not in df.columns
    with pytest.raises(AQPException) as ei:
        filter_universe(df, DAY.isoformat(), "main")
    assert ei.value.code == ERR_DATA_EMPTY


# ---------------- 2) report._score_shift_section：同款守卫落在既有 except Exception 降级分支内 ----------------
def _write_two_pred_partitions(root: Path, *, drop_pred_score: bool, null_dtype: bool) -> None:
    """写两个 predictions 分区（_score_shift_section 需 len(files)>=2 才进入分数迁移块）。"""
    pred_dir = root / "predictions"
    pred_dir.mkdir(parents=True, exist_ok=True)
    for d in ("20260916", "20260917"):
        if drop_pred_score:
            df = pl.DataFrame({"symbol": ["600000.SH", "600001.SH"], "date": [DAY, DAY]})
        elif null_dtype:
            df = pl.DataFrame({
                "symbol": ["600000.SH", "600001.SH"],
                "date": [DAY, DAY],
                "pred_score": pl.Series([None, None], dtype=pl.Null),
            })
        else:  # pragma: no cover - 正常数据不在本用例范围
            df = pl.DataFrame({"symbol": ["600000.SH"], "date": [DAY], "pred_score": [0.1]})
        df.write_parquet(pred_dir / f"date={d}.parquet")


def test_score_shift_section_null_pred_score_degrades(tmp_path, monkeypatch):
    """``report._score_shift_section`` 分数迁移块：pred_score=Null ⇒ 守卫转 AQPException,
    被该块既有的 ``except Exception`` 接住 ⇒ 读路径**不 panic**（否则 PanicException 穿透 → 裸 500）。

    降级后该块不产出「模型分数迁移」行（如实跳过，不造数）。
    """
    monkeypatch.setattr(get_settings(), "DATA_ROOT", tmp_path)
    _write_two_pred_partitions(tmp_path, drop_pred_score=False, null_dtype=True)
    from app.api.v1.report import _score_shift_section

    sec = _score_shift_section()   # 守卫失效时：PanicException 穿透，用例 error（非 fail）
    assert sec is None or all("模型分数迁移" not in ln for ln in sec.get("lines", []))


def test_score_shift_section_missing_pred_score_degrades(tmp_path, monkeypatch):
    """``report._score_shift_section``：pred_score 缺列 ⇒ 守卫转 AQPException ⇒ 既有降级分支接住。"""
    monkeypatch.setattr(get_settings(), "DATA_ROOT", tmp_path)
    _write_two_pred_partitions(tmp_path, drop_pred_score=True, null_dtype=False)
    from app.api.v1.report import _score_shift_section

    sec = _score_shift_section()
    assert sec is None or all("模型分数迁移" not in ln for ln in sec.get("lines", []))


# ---------------- 3) _unavailable_body 行为保持（逐字段） ----------------
def test_unavailable_body_fields_exact():
    """``_unavailable_body`` 抽取后逐字段固定：stats/coverage 为既定空态，reason/message 原样。"""
    body = screener_api._unavailable_body(
        "2026-09-17", "alpha_basic_v1", 50, "all",
        reason="pred_score_unavailable", message="X")
    assert body == {
        "date": "2026-09-17",
        "as_of": "2026-09-17",
        "strategy": "alpha_basic_v1",
        "top_k": 50,
        "board": "all",
        "count": 0,
        "items": [],
        "stats": {"today": screener_api._stats([], 0), "prev": None, "prev_date": None},
        "status": "unavailable",
        "reason": "pred_score_unavailable",
        "message": "X",
        "coverage": {"available": 0, "total": 0, "ratio": None},
    }


def test_screen_no_prediction_body_field_by_field(tmp_path, monkeypatch):
    """行为保持：目标日**无预测文件** ⇒ 既有 ``model_not_ready`` 空态体逐字段不变。"""
    monkeypatch.setattr(get_settings(), "DATA_ROOT", tmp_path)
    missing = date(2025, 1, 1)   # 无对应分区文件
    body, fv = screener_api._screen(missing, "alpha_basic_v1", 50, "all")
    assert fv is None
    assert body == {
        "date": "2025-01-01",
        "as_of": "2025-01-01",
        "strategy": "alpha_basic_v1",
        "top_k": 50,
        "board": "all",
        "count": 0,
        "items": [],
        "stats": {"today": screener_api._stats([], 0), "prev": None, "prev_date": None},
        "status": "unavailable",
        "reason": "model_not_ready",
        "message": "模型尚未产出该交易日的预测结果，请先运行训练与推理流水线",
        "coverage": {"available": 0, "total": 0, "ratio": None},
    }


def test_screen_empty_prediction_body_field_by_field(tmp_path, monkeypatch):
    """行为保持：预测分区**存在但为空** ⇒ 既有 ``model_not_ready``（分区为空）空态体逐字段不变。"""
    monkeypatch.setattr(get_settings(), "DATA_ROOT", tmp_path)
    pred_dir = tmp_path / "predictions"
    pred_dir.mkdir(parents=True, exist_ok=True)
    empty = pl.DataFrame({
        "symbol": pl.Series([], dtype=pl.String),
        "date": pl.Series([], dtype=pl.Date),
        "pred_score": pl.Series([], dtype=pl.Float64),
    })
    empty.write_parquet(pred_dir / f"date={DAY.strftime('%Y%m%d')}.parquet")

    body, fv = screener_api._screen(DAY, "alpha_basic_v1", 50, "all")
    assert fv is None
    assert body["status"] == "unavailable"
    assert body["reason"] == "model_not_ready"
    assert body["message"] == "模型预测分区为空，请重新运行推理流水线"
    assert body["count"] == 0 and body["items"] == []
    assert body["stats"] == {"today": screener_api._stats([], 0), "prev": None, "prev_date": None}
    assert body["coverage"] == {"available": 0, "total": 0, "ratio": None}
    assert body["date"] == body["as_of"] == DAY.isoformat()


# ---------------- 4) /screener 端到端：Null dtype ⇒ HTTP200 + status=unavailable（非裸 500/非 51001） ----------------
def test_screener_endpoint_null_pred_score_returns_unavailable(tmp_path, monkeypatch):
    """``GET /screener`` 在 ``pred_score`` 整列全空下必须返回 HTTP200 + ``status="unavailable"``,
    **不是**裸 500，**也不是** 全局 Exception 处理器的 ``code=51001`` 错误信封。
    """
    monkeypatch.setattr(get_settings(), "DATA_ROOT", tmp_path)
    # 关掉快照优先路径（本用例必须走到实时算榜 _screen）
    monkeypatch.setattr(screener_api, "load_screener_snapshot", lambda *a, **k: None)
    pred_dir = tmp_path / "predictions"
    pred_dir.mkdir(parents=True, exist_ok=True)
    _null_pred_score_frame().write_parquet(
        pred_dir / f"date={DAY.strftime('%Y%m%d')}.parquet")

    lru_clear()
    try:
        with TestClient(app) as c:
            r = c.get("/api/v1/screener",
                      params={"date": DAY.isoformat(), "board": "all"},
                      headers=_ADMIN)
        body = r.json()
        assert r.status_code == 200, r.text
        assert body["code"] == 0, body
        assert body["code"] != 51001, "不得退化成全局 Exception 处理器的 51001 错误信封"
        assert body["data"]["status"] == "unavailable"
        assert body["data"]["reason"] == "pred_score_unavailable"
        assert body["data"]["items"] == []
        assert body["data"]["coverage"] == {"available": 0, "total": 0, "ratio": None}
        assert body["trace_id"], "信封必须带 trace_id"
    finally:
        lru_clear()


# 守卫未被破坏的旁证：同一帧正常 pred_score（Float64）时 filter_universe 不抛、可排序
def test_filter_universe_healthy_pred_score_still_sorts():
    df = pl.DataFrame({"symbol": ["600000.SH", "600001.SH"],
                       "date": [DAY, DAY],
                       "pred_score": [0.1, 0.9]})
    out, pool = filter_universe(df, DAY.isoformat(), "main")
    assert pool == 2
    assert out["pred_score"].to_list() == [0.9, 0.1]   # 降序
