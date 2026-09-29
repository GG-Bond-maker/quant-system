"""分区**存在但缺列**（schema 漂移）时的降级守卫。

## 缺陷（同一族的 5 处，第 10 轮定位）

`read_symbol_dataset(columns=...)` 与直接 `pl.read_parquet` 都**容忍缺列**（投影读会
退化为"可用列子集"，这是文档化的有意设计），但消费方普遍**不再校验列是否齐备**：

| 位置 | 原写法 | 缺列时的后果 |
|---|---|---|
| `api/v1/watchlist.py::_bars_for` | 只判 `is_empty()` 与 `"date" in columns` | `df["close"]` → `ColumnNotFoundError` → **裸 50000**（自选股/相关页不可用） |
| `api/v1/screener.py::_watchlist_quotes` | `tail["close"][-1]` | 同上 |
| `data/screening.py::filter_universe` | `pl.col("board")` / `pl.col("is_st")`（快照存在的分支） | `/screener?board=main` → 50000 |
| `api/v1/desk.py::capacity` | `uni["close"] * uni["volume"]` | `/desk/capacity` → 50000 |
| `trading/paper.py::screen_universe_candidates` | `pl.col("is_st")` / `close*volume` | `/desk/exclusion/screen` → 50000 |

**为何是真 bug 而非"数据坏了活该"**：这些端点自己已经定义了"数据不可得"的合法降级
（`ERR_DATA_EMPTY`、外部源回退、价格 None、applied=False 披露），只是**判据写漏了列维度**；
且 `50000` 的语义是"未分类系统故障"，会把"本地数据集需重建"误导成"服务端崩了"。

**修法**：`parquet_store.missing_columns`（纯判据）/ `require_columns`（缺列即 51001 并点名列），
调用方按各自既有降级路径处理，且**一律留 warning**（不静默）。

本文件用**私有 DATA_ROOT** 播种"缺列但不空"的分区，逐端点端到端复现"修复前会 50000"的输入，
断言现在要么给出可解释的降级码、要么给出带披露的正常响应，**绝不能是 50000/500**。
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
from app.data.parquet_store import missing_columns, require_columns  # noqa: E402
from app.core.errors import ERR_DATA_EMPTY, AQPException  # noqa: E402

_ADMIN = {"Authorization": "Bearer aqp-dev-token-change-me"}
DAY = date.today() - timedelta(days=1)


@pytest.fixture()
def drifted_root(tmp_path, monkeypatch):
    """私有 DATA_ROOT：只放"缺列"分区，绝不动生产 `data/parquet`。"""
    root = tmp_path / "parquet"
    (root / "daily_bar" / "symbol=600519.SH").mkdir(parents=True)
    # ① 缺 close/open/high/low/volume/amount（只有 date+symbol）
    pl.DataFrame({
        "date": [DAY, DAY - timedelta(days=1)],
        "symbol": ["600519.SH"] * 2,
    }).write_parquet(root / "daily_bar" / "symbol=600519.SH" / "year=2026.snappy.parquet")
    # ② predictions：有 symbol/pred_score/date，缺 board（触发 board 过滤路径）
    (root / "predictions").mkdir(parents=True)
    pl.DataFrame({
        "date": [DAY] * 2, "symbol": ["600519.SH", "000001.SZ"],
        "pred_score": [0.9, 0.8], "model_version": ["v1", "v1"],
        "is_st": [False, False], "is_halted": [False, False],
    }).write_parquet(root / "predictions" / "date=20260901.parquet")
    # ③ universe_daily：有 date/board/is_st 但**缺 close/volume**（容量端点必需列）
    uni = root / "universe_daily" / "symbol=__all__"
    uni.mkdir(parents=True)
    pl.DataFrame({
        "date": [DAY] * 2, "symbol": ["600519.SH", "000001.SZ"],
        "name": ["甲", "乙"], "board": ["main", "main"],
        "is_st": [False, False], "is_halted": [False, False],
    }).write_parquet(uni / "year=2026.snappy.parquet")
    monkeypatch.setattr(get_settings(), "DATA_ROOT", root)
    assert get_settings().DATA_ROOT == root and str(root).startswith(str(tmp_path))
    return root


# ---------------- 0. 判据本身（先证"有判别力"） ----------------

def test_missing_columns_predicate_is_discriminating():
    df = pl.DataFrame({"date": [DAY], "close": [1.0]})
    assert missing_columns(df, ("date", "close")) == []
    assert missing_columns(df, ("date", "open", "close")) == ["open"]
    assert missing_columns(pl.DataFrame(), ("date",)) == ["date"]


def test_require_columns_raises_registered_code_with_column_names():
    df = pl.DataFrame({"date": [DAY]})
    with pytest.raises(AQPException) as ei:
        require_columns(df, ("date", "close", "volume"),
                        dataset="universe_daily", context="year=2026.snappy.parquet")
    assert ei.value.code == ERR_DATA_EMPTY
    msg = str(ei.value)
    assert "close" in msg and "volume" in msg and "universe_daily" in msg
    assert "year=2026.snappy.parquet" in msg          # 定位到具体文件
    require_columns(df, ("date",), dataset="x")        # 齐备即放行（不误报）


# ---------------- 1. 分层：缺列分区下 filter_universe 不得抛 ColumnNotFoundError ----------------

def test_filter_universe_degrades_on_universe_schema_gap(drifted_root, monkeypatch):
    """universe 缺 is_st/board ⇒ 不抛异常；board 走 symbol 前缀兜底并披露 applied=False。"""
    from app.data import screening

    pred = pl.DataFrame({"symbol": ["600519.SH", "000001.SZ"],
                         "pred_score": [0.9, 0.8]})
    # 只留 date/symbol/board，制造 is_st/is_halted 缺失
    uni_p = drifted_root / "universe_daily" / "symbol=__all__" / "year=2026.snappy.parquet"
    pl.DataFrame({
        "date": [DAY] * 2, "symbol": ["600519.SH", "000001.SZ"],
        "board": ["main", "main"],
    }).write_parquet(uni_p)

    res = screening.filter_universe(pred, DAY.isoformat(), board="main",
                                    require_universe=True)
    assert res.universe_ok is False, "过滤只做了一部分却报 ok（披露与事实不符）"
    assert res.universe_reason == "universe_schema_incomplete"
    assert res.df.height == 2, "board 过滤应已按 symbol 前缀兜底生效"


def test_filter_universe_missing_date_column_is_not_a_crash(drifted_root):
    """快照连 `date` 列都没有（更旧 schema）⇒ 按无快照处理，而不是 KeyError → 50000。"""
    from app.data import screening

    uni_p = drifted_root / "universe_daily" / "symbol=__all__" / "year=2026.snappy.parquet"
    pl.DataFrame({"symbol": ["600519.SH"], "board": ["main"]}).write_parquet(uni_p)
    pred = pl.DataFrame({"symbol": ["600519.SH"], "pred_score": [0.9]})

    res = screening.filter_universe(pred, DAY.isoformat(), board="all")
    assert res.universe_ok is False
    assert res.universe_reason == "universe_partition_missing" or \
        res.universe_reason == "universe_date_absent"
    assert res.df.height == 1


# ---------------- 2. 服务层：4 个端点绝不 50000 ----------------

def _no_panic(body: dict) -> None:
    assert body["code"] != 50000, f"缺列被归成未分类系统故障：{body}"
    assert body.get("message") != "Not Found", body


def test_watchlist_dashboard_degrades_on_missing_ohlc(drifted_root, monkeypatch):
    """`_bars_for` 投影读缺列 ⇒ 退回外部源路径（此处外部源也失败）⇒ 空 bars 而非 50000。"""
    from fastapi.testclient import TestClient

    from app.main import app

    with TestClient(app) as c:
        r = c.get("/api/v1/watchlist/dashboard?symbols=600519.SH", headers=_ADMIN)
    assert r.status_code == 200, r.text
    body = r.json()
    _no_panic(body)
    assert body["code"] == 0, body


def test_watchlist_correlation_degrades_on_missing_ohlc(drifted_root):
    from fastapi.testclient import TestClient

    from app.main import app

    with TestClient(app) as c:
        r = c.get("/api/v1/watchlist/correlation?symbols=600519.SH", headers=_ADMIN)
    assert r.status_code == 200, r.text
    _no_panic(r.json())


def test_screener_watchlist_degrades_on_missing_close(drifted_root):
    from fastapi.testclient import TestClient

    from app.main import app

    with TestClient(app) as c:
        r = c.get("/api/v1/screener/watchlist?symbols=600519.SH", headers=_ADMIN)
    assert r.status_code == 200, r.text
    body = r.json()
    _no_panic(body)
    assert body["code"] == 0, body
    item = body["data"]["items"][0]
    assert item["close"] is None, "缺列时价格必须是 None（该端点本来就有不可得语义）"
    assert item["symbol"] == "600519.SH"


def test_desk_capacity_reports_data_empty_on_missing_volume(drifted_root):
    """容量必须由 close×volume 得出 ⇒ 缺列报 **51001 且点名列**，而不是 50000。"""
    from fastapi.testclient import TestClient

    from app.main import app

    with TestClient(app) as c:
        r = c.get("/api/v1/desk/capacity", headers=_ADMIN)
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["code"] == ERR_DATA_EMPTY, body
    assert "close" in body["message"] and "volume" in body["message"], body


def test_desk_exclusion_screen_skips_unassessable_categories(drifted_root):
    """裸 list 端点无披露通道 ⇒ 不可评估的段跳过（留 warning），不得 50000。"""
    from fastapi.testclient import TestClient

    from app.main import app

    with TestClient(app) as c:
        r = c.get("/api/v1/desk/exclusion/screen", headers=_ADMIN)
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["code"] == 0, body
    assert body["data"] == [], "缺 is_st/close/volume 时两段都跳过（不是崩）"


# ---------------- 3. 反证：修复前的写法确实抛非 AQPException ⇒ 只能被归成 50000 ----------------

def test_original_expressions_would_have_raised_column_not_found(drifted_root):
    """逐处执行**修复前的写法**，证明它抛 `ColumnNotFoundError`（**不是** `AQPException`）。

    这是"改对了"的反证：全局兜底只把非 `AQPException` 归成 `code=50000`
    （本次子集运行实测信封 `{'code': 50000, 'message': '系统暂不可用，请稍后重试'}`）。
    若本用例不再抛，说明语料没能复现缺陷 —— 上面"现在不 50000"的断言就会沦为自欺。
    """
    import polars as pl
    from polars.exceptions import ColumnNotFoundError

    from app.core.errors import AQPException
    from app.data.parquet_store import read_symbol_dataset

    assert not issubclass(ColumnNotFoundError, AQPException), \
        "若它已是 AQPException，则 50000 的归因结论不成立（需重新定性）"

    cols = ["date", "open", "high", "low", "close", "volume", "amount"]
    df = read_symbol_dataset("daily_bar", "600519.SH", columns=cols)

    # ①/② 旧判据（只看 is_empty 与 date）会**放行**这个缺列分区，随后索引 close 抛错
    assert not df.is_empty() and "date" in df.columns, "旧判据在此分区上应为真（放行）"
    with pytest.raises(ColumnNotFoundError):
        _ = df["close"]
    with pytest.raises(ColumnNotFoundError):
        _ = df.tail(2).sort("date")["close"][-1]          # screener 的旧写法

    # ③ filter_universe 旧写法：join 后无条件按 board 过滤
    with pytest.raises(ColumnNotFoundError):
        pl.DataFrame({"symbol": ["600519.SH"]}).filter(pl.col("board") == "main")

    # ④ desk/capacity 旧写法
    with pytest.raises(ColumnNotFoundError):
        _ = df["close"] * df["volume"]

    # ⑤ paper.screen_universe_candidates 旧写法
    with pytest.raises(ColumnNotFoundError):
        df.filter(pl.col("is_st") == True)  # noqa: E712

    # 而新判据在同一分区上**明确**判定缺列（所以走降级，不再穿到索引处）
    assert missing_columns(df, cols) == ["open", "high", "low", "close",
                                         "volume", "amount"]