"""B7b F6：策略回测的**复权口径披露**（P2，口径不实比缺字段更危险）。

## 缺陷

`api/v1/backtest.py::_load_strategy_bars` 里 `source = "qfq" / "raw"` 跟踪了
"缺 QFQ 分区 ⇒ 回退不复权日线"这一事实，却**从不读取**（ruff F841 唯一命中点之一）。
后果有两层：

1. **后端零披露**：`/backtest/strategy-run` 的响应里没有任何复权口径字段 ——
   用户无法区分"结果是 QFQ 口径"与"部分标的其实是不复权"（除权跳空会被
   均线/突破判定误读为真实行情）。
2. **前端反向担保**：`pages/Backtest/index.tsx` 硬编码 "QFQ"、`parts.tsx` 写死
   "行情口径：本地前复权（QFQ）日线" ⇒ 前端把后端**没有担保**的口径展示成担保事实。

## 修法

- `_load_strategy_bars` 返回 `(bars, raw_fallback_symbols)`（回退事实外显）；
- 响应新增 `price_basis`：`kind/basis(qfq|raw|mixed)/raw_fallback_symbols/note`，
  **恒存在**（口径字段不得随数据可用性变化）；
- 前端标签改读 `price_basis`，缺失时如实显示"复权口径未知"（旧 Redis payload 兼容）；
- 顺带补必需列守卫（`date`/`close`）：缺列原会抛 `ColumnNotFoundError` → 裸 50000。
"""
from __future__ import annotations

import sys
from datetime import date, timedelta
from pathlib import Path

import polars as pl
import pytest

BACKEND_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(BACKEND_ROOT))

from app.core.config import get_settings  # noqa: E402
from app.core.errors import ERR_DATA_EMPTY, AQPException  # noqa: E402

_ADMIN = {"Authorization": "Bearer aqp-dev-token-change-me"}
_SYM = "600519.SH"
_TS = date(2026, 6, 1)          # 交易日无关的固定日期（只用于构造连续日线）


def _bars(n: int = 120) -> pl.DataFrame:
    days = [_TS + timedelta(days=i) for i in range(n)]
    px = [100.0 + i * 0.5 for i in range(n)]
    return pl.DataFrame({
        "date": days, "open": px, "high": [p + 1 for p in px],
        "low": [p - 1 for p in px], "close": px,
        "volume": [1_000_000.0] * n, "amount": [1e8] * n,
    })


def _seed(root: Path, *, with_qfq: bool, raw_columns: list[str] | None = None) -> None:
    d = root / "daily_bar" / f"symbol={_SYM}"
    d.mkdir(parents=True, exist_ok=True)
    df = _bars()
    if raw_columns is not None:
        df = df.select([c for c in raw_columns if c in df.columns])
    df.write_parquet(d / "year=2026.snappy.parquet")
    if with_qfq:
        q = root / "daily_bar_qfq" / f"symbol={_SYM}"
        q.mkdir(parents=True, exist_ok=True)
        _bars().write_parquet(q / "year=2026.snappy.parquet")
    (root / "predictions").mkdir(parents=True, exist_ok=True)


@pytest.fixture()
def bt_root(tmp_path, monkeypatch):
    root = tmp_path / "parquet"
    root.mkdir(parents=True)
    monkeypatch.setattr(get_settings(), "DATA_ROOT", root)
    import app.cache.redis_client as rc

    async def _no_cache(*a, **k):        # 关掉 Redis 缓存，确保每次都真算（避免跨用例串味）
        return None

    monkeypatch.setattr(rc.RedisClient, "get", staticmethod(_no_cache))
    return root


def _run(payload: dict) -> dict:
    from fastapi.testclient import TestClient

    from app.main import app

    with TestClient(app) as c:
        r = c.post("/api/v1/backtest/strategy-run", json=payload, headers=_ADMIN)
    assert r.status_code == 200, r.text
    return r.json()


_REQ = {"symbols": [_SYM], "start": "2026-06-02", "end": "2026-09-20",
        "strategy_type": "ma_cross", "short_ma": 5, "long_ma": 20}


# ---------------- 1. 有 QFQ ⇒ basis=qfq 且无回退清单 ----------------

def test_price_basis_qfq_when_qfq_present(bt_root):
    _seed(bt_root, with_qfq=True)
    body = _run(_REQ)
    assert body["code"] == 0, body
    pb = body["data"]["price_basis"]
    assert pb["kind"] == "platform"
    assert pb["basis"] == "qfq" and pb["raw_fallback_symbols"] == []
    assert "QFQ" in pb["note"]


# ---------------- 2. 缺 QFQ ⇒ basis=raw **且如实披露**（F6 的核心断言） ----------------

def test_price_basis_discloses_raw_fallback(bt_root):
    """只有不复权日线时：结果照旧可跑，但口径必须标成 raw 并给出口径说明。"""
    _seed(bt_root, with_qfq=False)
    body = _run(_REQ)
    assert body["code"] == 0, body
    pb = body["data"]["price_basis"]
    assert pb["basis"] == "raw", f"缺 QFQ 却报 {pb['basis']}（口径不实）"
    assert pb["raw_fallback_symbols"] == [_SYM]
    assert _SYM in pb["note"]


def test_price_basis_mixed_when_some_symbols_fall_back(bt_root):
    """两个标的、其中一个缺 QFQ ⇒ basis=mixed（不多不少地披露回退清单）。"""
    other = "000001.SZ"
    _seed(bt_root, with_qfq=True)
    d = bt_root / "daily_bar" / f"symbol={other}"
    d.mkdir(parents=True, exist_ok=True)
    _bars().write_parquet(d / "year=2026.snappy.parquet")

    body = _run({**_REQ, "symbols": [_SYM, other]})
    assert body["code"] == 0, body
    pb = body["data"]["price_basis"]
    assert pb["basis"] == "mixed", pb
    assert pb["raw_fallback_symbols"] == [other], "只能点名真正回退的那个标的"


# ---------------- 3. 缺必需列 ⇒ 51001 并点名列，而不是 50000 ----------------

def test_missing_close_column_reports_data_empty(bt_root):
    _seed(bt_root, with_qfq=False, raw_columns=["date", "open", "volume"])
    body = _run(_REQ)
    assert body["code"] == ERR_DATA_EMPTY, body
    assert "close" in body["message"], body
    assert body["code"] != 50000


def test_load_strategy_bars_returns_backfill_list(bt_root):
    """单元级：返回值形状本身（防"又变成只返回 dict ⇒ 披露再次丢失"）。"""
    from app.api.v1.backtest import _load_strategy_bars

    _seed(bt_root, with_qfq=False)
    bars, raw_fallback = _load_strategy_bars([_SYM], "2026-06-02", "2026-09-20")
    assert list(bars) == [_SYM] and raw_fallback == [_SYM]
    assert "close" in bars[_SYM].columns


def test_missing_close_column_raises_registered_code(bt_root):
    from app.api.v1.backtest import _load_strategy_bars

    _seed(bt_root, with_qfq=False, raw_columns=["date", "open", "volume"])
    with pytest.raises(AQPException) as ei:
        _load_strategy_bars([_SYM], "2026-06-02", "2026-09-20")
    assert ei.value.code == ERR_DATA_EMPTY and "close" in str(ei.value)


# ---------------- 4. P0-4 下半条：基准口径披露 ----------------

def test_benchmark_basis_present_and_not_synthetic_when_index_available(bt_root, monkeypatch):
    """基准可得时 `benchmark_basis.synthetic=False`（口径字段恒存在）。"""
    from app.data.ingest import akshare_adapter

    _seed(bt_root, with_qfq=True)
    dates = pl.DataFrame({"date": _bars(120)["date"], "close": [3000.0 + i for i in range(120)]})
    monkeypatch.setattr(akshare_adapter, "fetch_index_daily", lambda code: dates)

    body = _run(_REQ)
    assert body["code"] == 0, body
    bb = body["data"]["benchmark_basis"]
    assert bb["kind"] == "platform" and bb["synthetic"] is False
    assert bb["basis"] == "index_sh000300" and bb["reason"] is None


def test_benchmark_basis_marks_synthetic_when_index_fetch_fails(bt_root, monkeypatch):
    """基准获取失败 ⇒ 必须显式标注 synthetic（原实现只给 annual_benchmark=0.0）。"""
    from app.data.ingest import akshare_adapter

    _seed(bt_root, with_qfq=True)

    def _boom(code: str):
        raise RuntimeError("network down")

    monkeypatch.setattr(akshare_adapter, "fetch_index_daily", _boom)
    body = _run(_REQ)
    assert body["code"] == 0, body
    bb = body["data"]["benchmark_basis"]
    assert bb["synthetic"] is True and bb["basis"] == "synthetic_flat"
    assert bb["reason"] == "fetch_failed"
    assert "构造值" in bb["note"]
    # 诚实性锚点：退化时基准年化确实是构造的 0.0，且 alpha/beta 恒 null
    risk = body["data"]["risk"]
    assert risk.get("annual_benchmark") == 0.0
    assert risk.get("alpha") is None


def test_benchmark_basis_flags_no_overlap_window(bt_root, monkeypatch):
    """基准数据与回测区间**无重叠**交易日 ⇒ 引擎会造常数基准，也必须标注。"""
    from app.data.ingest import akshare_adapter

    _seed(bt_root, with_qfq=True)
    far = pl.DataFrame({"date": [date(2019, 1, 2)], "close": [3000.0]})
    monkeypatch.setattr(akshare_adapter, "fetch_index_daily", lambda code: far)

    body = _run(_REQ)
    assert body["code"] == 0, body
    bb = body["data"]["benchmark_basis"]
    assert bb["synthetic"] is True, bb
    assert bb["reason"] == "no_overlap", f"应精确归因为区间无重叠交易日：{bb}"
    assert body["data"]["risk"].get("alpha") is None


# ---------------- 5. 前端不再硬编码 QFQ（静态断言；前端无测试框架） ----------------

def test_frontend_reads_price_basis_instead_of_hardcoding_qfq():
    src_root = BACKEND_ROOT.parent / "frontend" / "src"
    idx = (src_root / "pages" / "Backtest" / "index.tsx").read_text("utf-8")
    parts = (src_root / "pages" / "Backtest" / "parts.tsx").read_text("utf-8")
    api = (src_root / "api" / "strategyBacktest.ts").read_text("utf-8")

    assert "· QFQ ·" not in idx, "结果页仍在硬编码 QFQ"
    assert "price_basis" in idx and "basisLabel" in idx
    assert "复权口径未知" in idx, "缺字段时必须如实显示未知，而不是回落到 QFQ"
    assert "QFQ + 不复权混用" in idx
    # 规则说明不得再断言"行情口径：本地前复权"（无条件的担保）
    assert "行情口径：本地前复权（QFQ）日线" not in parts
    assert "实际口径以结果页右上角标注为准" in parts
    assert "price_basis?" in api, "前端类型必须把 price_basis 声明为可选（兼容旧缓存）"
    # P0-4 下半条：构造基准必须在 UI 上被标注，且 KPI 卡标题随之改变
    idx_all = idx + (src_root / "pages" / "Backtest" / "resultParts.tsx").read_text("utf-8")
    assert "benchmark_basis" in idx and "基准不可得（构造基准）" in idx
    assert "benchmarkSynthetic" in idx_all
    assert "基准年化收益（构造基准）" in idx_all
    assert "benchmark_basis?" in api