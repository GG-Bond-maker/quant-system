"""多源降级容错测试矩阵（增量设计方案 §7；**全部 monkeypatch，不发真实网络**）。

覆盖：
- 个股实时快照：腾讯 → 新浪 → 全败抛 DataSourceUnavailable；
- 管线日K降级链：东财 → 新浪 → BaoStock（含「未安装」「全空」「全败」边界）；
- 主源开关 AQP_DAILY_SOURCE_PRIORITY（含腾讯优先分支，列 amount→volume、amount=null）；
- 公告改名如实（fetch_em_notices，source=eastmoney，旧名别名）。
"""
from __future__ import annotations

import sys
from datetime import date
from pathlib import Path
from types import SimpleNamespace

import pandas as pd
import polars as pl
import pytest

BACKEND_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(BACKEND_ROOT))

from app.core.errors import DataSourceUnavailable  # noqa: E402
from app.data import parquet_store as ps  # noqa: E402
from app.data import realtime as rt  # noqa: E402
from app.data.ingest import akshare_adapter as ada  # noqa: E402
from app.data.ingest import baostock_adapter as bsa  # noqa: E402
from app.data.ingest import tasks as tasks_mod  # noqa: E402


@pytest.fixture(autouse=True)
def _clean(monkeypatch: pytest.MonkeyPatch):
    """隔离限速与熔断状态。"""
    monkeypatch.setattr(ada, "_throttle", lambda: None)
    monkeypatch.setattr(rt, "_throttle", lambda: None)
    ada.reset_source_breaker()
    yield
    ada.reset_source_breaker()


def _settings(monkeypatch: pytest.MonkeyPatch, priority: str) -> None:
    monkeypatch.setattr(ada, "get_settings", lambda: SimpleNamespace(
        AKSHARE_RETRY=1, AKSHARE_RATE_LIMIT=0.0,
        AKSHARE_BREAKER_ENABLED=False, AQP_DAILY_SOURCE_PRIORITY=priority))


def _df(code: str, source: str, *, amount=10200.0, turnover=0.5) -> pl.DataFrame:
    return pl.DataFrame({
        "date": [date(2024, 1, 2), date(2024, 1, 3)],
        "symbol": [f"{code}.SH"] * 2, "code": [code] * 2,
        "open": [10.0, 10.1], "high": [10.5, 10.6], "low": [9.5, 9.6],
        "close": [10.2, 10.3], "volume": [1000.0, 1100.0],
        "amount": [amount, amount], "turnover": [turnover, turnover],
        "source": [source] * 2,
    })


# ---------------- 管线日K：东财优先 / 逐级降级 ----------------
def test_fetch_daily_bar_eastmoney_first(monkeypatch: pytest.MonkeyPatch):
    """东财可用即返回，不触碰后续源；amount/turnover 有值。"""
    _settings(monkeypatch, "eastmoney,sina,baostock")
    called: list[str] = []
    monkeypatch.setattr(ada, "_fetch_daily_bar_em",
                        lambda c, s, e, a: (called.append("em"), _df(c, "akshare"))[1])
    monkeypatch.setattr(ada, "_fetch_daily_bar_sina",
                        lambda c, s, e, a: (called.append("sina"), _df(c, "akshare"))[1])

    out = ada.fetch_daily_bar("600519", "2024-01-01", "2024-01-05")

    assert called == ["em"]
    assert out["amount"].to_list() == [10200.0, 10200.0]
    assert out["turnover"].to_list() == [0.5, 0.5]


def test_fetch_daily_bar_fallback_sina(monkeypatch: pytest.MonkeyPatch):
    """东财失败 -> 新浪：source 仍为 akshare，不崩。"""
    _settings(monkeypatch, "eastmoney,sina,baostock")
    monkeypatch.setattr(ada, "_fetch_daily_bar_em",
                        lambda c, s, e, a: (_ for _ in ()).throw(ConnectionError("em down")))
    monkeypatch.setattr(ada, "_fetch_daily_bar_sina", lambda c, s, e, a: _df(c, "akshare"))

    out = ada.fetch_daily_bar("600519", "2024-01-01", "2024-01-05")

    assert out["source"].unique().to_list() == ["akshare"]
    assert out.height == 2


def test_fetch_daily_bar_fallback_baostock(monkeypatch: pytest.MonkeyPatch):
    """东财+新浪都失败 -> BaoStock 兜底：标准化正确、source==baostock。"""
    _settings(monkeypatch, "eastmoney,sina,baostock")
    monkeypatch.setattr(ada, "_fetch_daily_bar_em",
                        lambda c, s, e, a: (_ for _ in ()).throw(ConnectionError("em down")))
    monkeypatch.setattr(ada, "_fetch_daily_bar_sina",
                        lambda c, s, e, a: (_ for _ in ()).throw(ConnectionError("sina down")))
    monkeypatch.setattr(bsa, "is_available", lambda: True)
    monkeypatch.setattr(bsa, "fetch_daily_bar_bs", lambda c, s, e, a: _df(c, "baostock"))

    out = ada.fetch_daily_bar("600519", "2024-01-01", "2024-01-05")

    assert out["source"].unique().to_list() == ["baostock"]


def test_fetch_daily_bar_baostock_absent_is_skipped(monkeypatch: pytest.MonkeyPatch):
    """BaoStock 未安装 -> 跳过该源（不计数、不报错）；前两源皆败则抛 DataSourceUnavailable。"""
    _settings(monkeypatch, "eastmoney,sina,baostock")
    monkeypatch.setattr(ada, "_fetch_daily_bar_em",
                        lambda c, s, e, a: (_ for _ in ()).throw(ConnectionError("em down")))
    monkeypatch.setattr(ada, "_fetch_daily_bar_sina",
                        lambda c, s, e, a: (_ for _ in ()).throw(ConnectionError("sina down")))
    monkeypatch.setattr(bsa, "is_available", lambda: False)
    monkeypatch.setattr(bsa, "fetch_daily_bar_bs",
                        lambda c, s, e, a: pytest.fail("absent 源不得被调用"))

    with pytest.raises(DataSourceUnavailable):
        ada.fetch_daily_bar("600519", "2024-01-01", "2024-01-05")


def test_fetch_daily_bar_all_empty_returns_empty(monkeypatch: pytest.MonkeyPatch):
    """所有源「无数据」（空表，非异常）-> 返回空 DataFrame（不算故障）。"""
    _settings(monkeypatch, "eastmoney,sina,baostock")
    monkeypatch.setattr(ada, "_fetch_daily_bar_em", lambda c, s, e, a: pl.DataFrame())
    monkeypatch.setattr(ada, "_fetch_daily_bar_sina", lambda c, s, e, a: pl.DataFrame())
    monkeypatch.setattr(bsa, "is_available", lambda: False)

    out = ada.fetch_daily_bar("600519", "2024-01-01", "2024-01-05")

    assert isinstance(out, pl.DataFrame)
    assert out.is_empty()


def test_daily_source_priority_getattr_fallback(monkeypatch: pytest.MonkeyPatch):
    """Settings 替身缺 AQP_DAILY_SOURCE_PRIORITY 字段 -> getattr 兜底默认值。"""
    monkeypatch.setattr(ada, "get_settings", lambda: SimpleNamespace())
    assert ada.daily_source_priority() == ["eastmoney", "sina", "baostock"]


def test_daily_source_priority_unknown_name_skipped(monkeypatch: pytest.MonkeyPatch):
    """未知源名被跳过，不影响后续有效源。"""
    _settings(monkeypatch, "foo,eastmoney")
    monkeypatch.setattr(ada, "_fetch_daily_bar_em", lambda c, s, e, a: _df(c, "akshare"))

    out = ada.fetch_daily_bar("600519", "2024-01-01", "2024-01-05")

    assert out.height == 2


# ---------------- 腾讯优先分支（D1-alt） ----------------
def test_tencent_tx_renames_amount_to_volume(monkeypatch: pytest.MonkeyPatch):
    """腾讯返回列 ``amount`` 实为成交量 ⇒ 重命名为 volume；amount/turnover/pct 置 null。"""
    monkeypatch.setattr(ada, "get_settings", lambda: SimpleNamespace(
        AKSHARE_RETRY=1, AKSHARE_RATE_LIMIT=0.0, AKSHARE_BREAKER_ENABLED=False))
    raw = pd.DataFrame({
        "date": ["2024-01-02", "2024-01-03"],
        "open": [10.0, 10.1], "close": [10.2, 10.3],
        "high": [10.5, 10.6], "low": [9.5, 9.6],
        "amount": [1234.0, 2345.0],   # 注意：这列实为成交量（手）
    })
    monkeypatch.setattr(ada, "_ak",
                        lambda: SimpleNamespace(stock_zh_a_hist_tx=lambda **k: raw))

    out = ada._fetch_daily_bar_tx("600519", "2024-01-01", "2024-01-05", "")

    assert out["volume"].to_list() == [1234.0, 2345.0]
    assert out["amount"].to_list() == [None, None]
    assert out["turnover"].to_list() == [None, None]
    assert out["pct"].to_list() == [None, None]
    assert out["source"].unique().to_list() == ["tencent"]


def test_tencent_priority_uses_tx_first(monkeypatch: pytest.MonkeyPatch):
    """优先级含 tencent 且排首位 -> 走腾讯分支。"""
    _settings(monkeypatch, "tencent,eastmoney,sina,baostock")
    called: list[str] = []
    monkeypatch.setattr(ada, "_fetch_daily_bar_tx",
                        lambda c, s, e, a: (called.append("tx"), _df(c, "tencent"))[1])
    monkeypatch.setattr(ada, "_fetch_daily_bar_em",
                        lambda c, s, e, a: (called.append("em"), _df(c, "akshare"))[1])

    out = ada.fetch_daily_bar("600519", "2024-01-01", "2024-01-05")

    assert called == ["tx"]
    assert out["source"].unique().to_list() == ["tencent"]


# ---------------- 个股实时快照：腾讯 → 新浪 ----------------
def test_fetch_quote_tencent_ok(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setattr(rt, "fetch_tencent_quote",
                        lambda s: {"price": 10.0, "source": "tencent", "pe_ttm": 19.9})
    q = rt.fetch_quote("600519.SH")
    assert q["source"] == "tencent"


def test_fetch_quote_fallback_sina(monkeypatch: pytest.MonkeyPatch):
    """腾讯失败 -> 新浪：source==sina；pe_ttm/pb/turnover 如实为 null。"""
    monkeypatch.setattr(rt, "fetch_tencent_quote",
                        lambda s: (_ for _ in ()).throw(RuntimeError("tx down")))
    monkeypatch.setattr(rt, "_fetch_sina_quotes_batch", lambda syms: {
        "600519.SH": {"symbol": "600519.SH", "price": 10.0, "prev_close": 9.9,
                      "pct": 1.01, "as_of": "2024-01-02 15:00:00"}})

    q = rt.fetch_quote("600519.SH")

    assert q["source"] == "sina"
    assert q["pe_ttm"] is None
    assert q["pb"] is None
    assert q["turnover"] is None
    assert q["price"] == 10.0


def test_fetch_quote_all_fail_raises(monkeypatch: pytest.MonkeyPatch):
    """腾讯+新浪均失败 -> 抛 DataSourceUnavailable（标准信封路径，非裸异常）。"""
    monkeypatch.setattr(rt, "fetch_tencent_quote",
                        lambda s: (_ for _ in ()).throw(RuntimeError("tx down")))
    monkeypatch.setattr(rt, "_fetch_sina_quotes_batch",
                        lambda syms: (_ for _ in ()).throw(RuntimeError("sina down")))

    with pytest.raises(DataSourceUnavailable):
        rt.fetch_quote("600519.SH")


# ---------------- 公告改名如实 ----------------
def test_em_notices_source_is_eastmoney(monkeypatch: pytest.MonkeyPatch):
    raw = pd.DataFrame({
        "公告标题": ["600519 关于回购股份的公告"],
        "公告日期": ["2024-01-02"],
        "公告类型": ["回购"],
    })
    monkeypatch.setattr(rt, "_ak",
                        lambda: SimpleNamespace(stock_notice_report=lambda **k: raw))

    out = rt.fetch_em_notices("600519.SH", limit=1)

    assert out and out[0]["source"] == "eastmoney"
    # 旧名保留为别名（过渡）
    assert rt.fetch_sina_notices is rt.fetch_em_notices


# ---------------- 边界：北交所 guard（不发请求） ----------------
def test_bj_code_guard_no_request(monkeypatch: pytest.MonkeyPatch):
    """北交所（4/8 开头）-> bs_code 快速拒绝，fetch 不发请求、返回空表。"""
    with pytest.raises(ValueError):
        bsa.bs_code("430047")
    monkeypatch.setattr(bsa, "_bs_module", SimpleNamespace(
        login=lambda: pytest.fail("不得登录"),
        query_history_k_data_plus=lambda *a, **k: pytest.fail("不得发请求")))
    assert bsa.fetch_daily_bar_bs("430047", "2024-01-01", "2024-01-05", "").is_empty()


# ---------------- 跨源混列守卫（T5：write_daily_bars） ----------------
def _bs_daily() -> pl.DataFrame:
    """一份 source=baostock、可通过写入门禁的最小日线。"""
    return pl.DataFrame({
        "date": [date(2024, 1, 2), date(2024, 1, 3)],
        "open": [10.0, 10.1], "high": [10.5, 10.6], "low": [9.5, 9.6],
        "close": [10.2, 10.3], "volume": [1000.0, 1100.0],
        "amount": [10200.0, 10300.0], "turnover": [0.5, 0.5],
        "symbol": ["600519.SH"] * 2, "code": ["600519"] * 2,
        "source": ["baostock"] * 2,
    })


def _existing(src: str | None):
    """伪造「本地已有数据」的读取结果（src=None 表示该 symbol 本地无数据）。"""
    if src is None:
        return pl.DataFrame()
    return pl.DataFrame({"date": [date(2024, 1, 2)], "source": [src]})


def _patch_store(monkeypatch: pytest.MonkeyPatch, existing_src: str | None) -> list:
    """把「本地已有数据」与「落盘」都打桩，返回落盘调用记录（不碰磁盘）。"""
    monkeypatch.setattr(ps, "read_symbol_dataset",
                        lambda dataset, symbol, **kw: _existing(existing_src))
    written: list = []
    monkeypatch.setattr(tasks_mod, "write_partition",
                        lambda *a, **k: written.append(a))
    return written


def test_cross_source_guard_rejects_baostock_over_eastmoney(monkeypatch: pytest.MonkeyPatch):
    """本地已有东财行 -> BaoStock 兜底数据**拒绝写入**（复权基准不同，绝不混列）。"""
    written = _patch_store(monkeypatch, "eastmoney")

    with pytest.raises(ValueError, match="跨源混列被拒绝"):
        tasks_mod.write_daily_bars("600519", _bs_daily(), adjusts=("",))

    assert written == [], "守卫拒绝后不得落盘（静默覆盖会让复权序列跳变）"


def test_cross_source_guard_allows_baostock_for_new_symbol(monkeypatch: pytest.MonkeyPatch):
    """本地无该标的任何数据 -> 允许兜底源整段写入（「整只标的重取」）。"""
    written = _patch_store(monkeypatch, None)

    n = tasks_mod.write_daily_bars("600519", _bs_daily(), adjusts=("",))

    assert n == 2
    assert written, "无存量时应正常落盘"


def test_cross_source_guard_allows_rerun_of_same_source(monkeypatch: pytest.MonkeyPatch):
    """存量亦为 baostock（重跑同一源）-> 放行，不得误杀。"""
    written = _patch_store(monkeypatch, "baostock")

    n = tasks_mod.write_daily_bars("600519", _bs_daily(), adjusts=("",))

    assert n == 2
    assert written


def test_cross_source_guard_does_not_restrict_primary_sources(monkeypatch: pytest.MonkeyPatch):
    """主源(东财/新浪)写主源不受守卫约束（保持既有行为，避免回归）。"""
    written = _patch_store(monkeypatch, "sina")

    eastmoney_df = _bs_daily().with_columns(pl.lit("eastmoney").alias("source"))
    n = tasks_mod.write_daily_bars("600519", eastmoney_df, adjusts=("",))

    assert n == 2
    assert written
