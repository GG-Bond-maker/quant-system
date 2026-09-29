"""§8.2 序 7：`ops` 端点「假成功 / 假清白」修复防回归（审计 F7 + F4）。

F7 `POST /ops/dag/rerun`（`ops.py` 原 448-455）
------------------------------------------------
`_run` 用 `except Exception` 把流水线异常与 `PipelineBusy` 一律吞成
`{"ok": False, "error": ...}`，外层再套 `ok()` ⇒ **失败的重跑返回
`code=0 / message=ok`**。实测（B7b F7）：`{"trade_date":"9999-99-99"}`
（形状合法、日历非法）→ `code=0`，对照 `{"trade_date":"bad"}` → 40000。

F4 `POST /ops/quality-scan`（`ops.py` 原 52）
--------------------------------------------
`sym_dirs[:200]` 硬截断（生产实测 2499 个 `daily_bar` 分区 ⇒ 只扫 8.0%），
响应只有 `symbols_scanned`，**无任何截断字段** ⇒ `n_issues:0` 被读成
「全库干净」。

测试纪律
--------
* F4 写**真实 parquet 分区树**（不 monkeypatch 被测函数），并让**被截断的第 201
  个起**才含 error 级数据，直接复现「假清白」的读法；
* F7 走 ASGI（而非直接调用端点函数），断言**信封** `code`；Starlette 在调用
  全局 `Exception` 处理器发出响应后**仍会重抛**，故 TestClient 必须关掉
  `raise_server_exceptions` 才能读到那个 50000 信封。
"""
from __future__ import annotations

import asyncio
import io
import sys
from datetime import date
from pathlib import Path

import polars as pl
import pytest
from fastapi.testclient import TestClient

BACKEND_ROOT = Path(__file__).resolve().parents[1]
if str(BACKEND_ROOT) not in sys.path:
    sys.path.insert(0, str(BACKEND_ROOT))

from app.api.v1 import ops as ops_mod  # noqa: E402
from app.core.config import get_settings  # noqa: E402
from app.core.errors import (  # noqa: E402
    ERR_PARAMS,
    ERR_PIPELINE_BUSY,
    ERR_SYSTEM,
)
from app.core.pipeline_lock import PipelineBusy  # noqa: E402
from app.main import app  # noqa: E402

_ADMIN = {"Authorization": "Bearer aqp-dev-token-change-me"}


@pytest.fixture(scope="module")
def client():
    with TestClient(app, raise_server_exceptions=False) as c:
        yield c


# ---------------------------------------------------------------------------
# F7：失败的重跑不得返回 code=0
# ---------------------------------------------------------------------------
def _rerun(client: TestClient, trade_date: str) -> dict:
    r = client.post("/api/v1/ops/dag/rerun", headers=_ADMIN,
                    json={"trade_date": trade_date})
    assert r.status_code == 200, "本项目契约：HTTP 恒 200"
    return r.json()


def test_rerun_invalid_calendar_date_is_param_error(client: TestClient) -> None:
    """**缺陷本体**：`9999-99-99` 只过 pattern、不过日历；修复前 code=0「假成功」。"""
    body = _rerun(client, "9999-99-99")
    assert body["code"] == ERR_PARAMS, body
    assert body["code"] != 0


def test_rerun_pipeline_failure_is_not_reported_as_success(
        client: TestClient, monkeypatch) -> None:
    """`run_pipeline` 抛异常 ⇒ 信封不得为 0（全局处理器给 50000 + 服务端堆栈日志）。"""
    def _boom(*_a, **_k):
        raise RuntimeError("pipeline exploded")

    monkeypatch.setattr("app.orchestrator.run_pipeline", _boom)
    body = _rerun(client, "2026-09-12")
    assert body["code"] == ERR_SYSTEM, body
    assert body["code"] != 0


def test_rerun_pipeline_busy_maps_to_dedicated_code(
        client: TestClient, monkeypatch) -> None:
    """管道互斥 ⇒ `ERR_PIPELINE_BUSY`（与 sync/fetch/mirror 一致），而非 0 或 50000。"""
    def _busy(*_a, **_k):
        raise PipelineBusy("sync")

    monkeypatch.setattr("app.orchestrator.run_pipeline", _busy)
    body = _rerun(client, "2026-09-12")
    assert body["code"] == ERR_PIPELINE_BUSY, body


def test_rerun_success_keeps_code_zero_and_ok_flag(
        client: TestClient, monkeypatch) -> None:
    """反向断言：成功路径仍 `code=0` 且 `data.ok=True`（前端 Pipeline 页读 data.ok）。"""
    monkeypatch.setattr("app.orchestrator.run_pipeline",
                        lambda *_a, **_k: ("job-1", True))
    body = _rerun(client, "2026-09-12")
    assert body["code"] == 0, body
    assert body["data"]["ok"] is True
    assert "job-1" in body["data"]["summary"]


# ---------------------------------------------------------------------------
# F4：quality-scan 的 200 只截断必须披露
# ---------------------------------------------------------------------------
def _pq_bytes(volume: float) -> bytes:
    """canonical schema 的单行分区（列序与 data.quality.CANONICAL_SCHEMA 一致）。"""
    buf = io.BytesIO()
    pl.DataFrame({
        "date": [date(2026, 1, 5)],
        "open": [10.0], "high": [10.5], "low": [9.5], "close": [10.2],
        "volume": [volume], "amount": [1020.0], "turnover": [0.001],
        "code": ["000001"], "symbol": ["000001.SZ"], "source": ["test"],
    }).write_parquet(buf)
    return buf.getvalue()


_CLEAN = _pq_bytes(1000.0)
# 负成交量 ⇒ check_ohlc 判 error 级：用来标记"被截断、从未被读到"的标的
_DIRTY = _pq_bytes(-1.0)


def _seed_daily_bar(root: Path, n_dirs: int, dirty_from: int | None = None) -> None:
    """造 n_dirs 个 `symbol=NNNNNN.SZ/year=2026.snappy.parquet`；`dirty_from` 起为坏数据。"""
    for i in range(n_dirs):
        d = root / "daily_bar" / f"symbol={i:06d}.SZ"
        d.mkdir(parents=True)
        blob = _DIRTY if (dirty_from is not None and i >= dirty_from) else _CLEAN
        (d / "year=2026.snappy.parquet").write_bytes(blob)


def _scan(root: Path) -> dict:
    res = asyncio.run(ops_mod.quality_scan(
        ops_mod.ScanRequest(dataset="daily_bar"), _user={}))
    return res.data if hasattr(res, "data") else res["data"]


@pytest.fixture()
def data_root(monkeypatch, tmp_path: Path) -> Path:
    monkeypatch.setattr(get_settings(), "DATA_ROOT", tmp_path)
    return tmp_path


def test_quality_scan_discloses_200_symbol_truncation(data_root: Path) -> None:
    """**缺陷本体**：250 个分区只扫前 200 个时必须披露截断与截断前总数。

    250 个分区里**第 201 个起**才含 error 级数据 ⇒ 修复前响应恰为
    `symbols_scanned=200 / n_issues=0 / n_errors=0`，无任何字段可辨「50 只从未被读」；
    修复后 `truncated=True` + `symbols_available=250` 反驳「全库干净」的读法。
    """
    _seed_daily_bar(data_root, 250, dirty_from=ops_mod._SCAN_SYMBOL_LIMIT)

    data = _scan(data_root)

    assert data["truncated"] is True, data
    assert data["symbols_scanned"] == 200
    assert data["symbols_available"] == 250
    assert data["total_symbols"] == 250
    assert data["scan_limit"] == ops_mod._SCAN_SYMBOL_LIMIT == 200
    assert data["symbols_scanned"] < data["symbols_available"]
    assert data["rows_scanned"] == 200
    # 唯二含 error 的 50 只全在截断侧：披露字段是唯一能看穿「n_issues:0」的手段
    assert data["n_errors"] == 0


def test_quality_scan_not_truncated_at_limit(data_root: Path) -> None:
    """反向断言：恰好等于上限时不得谎报 truncated。"""
    _seed_daily_bar(data_root, ops_mod._SCAN_SYMBOL_LIMIT)

    data = _scan(data_root)

    assert data["symbols_available"] == 200
    assert data["symbols_scanned"] == 200
    assert data["truncated"] is False
    assert data["scan_limit"] == 200


def test_scan_limit_constant_drives_slice_and_flag(
        data_root: Path, monkeypatch) -> None:
    """上限是单一事实源：切片与 truncated 判据必须同源（改常量两者一起动）。"""
    monkeypatch.setattr(ops_mod, "_SCAN_SYMBOL_LIMIT", 2)
    _seed_daily_bar(data_root, 3)

    data = _scan(data_root)

    assert data["scan_limit"] == 2
    assert data["symbols_scanned"] == 2      # 切片用的是常量
    assert data["symbols_available"] == 3
    assert data["truncated"] is True         # 判据也用的是常量