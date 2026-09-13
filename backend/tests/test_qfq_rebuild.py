"""Task 3（整改计划）：qfq 基准漂移修复。

背景：akshare 的 qfq"以最新价重算整条序列"，增量抓取直接落盘会让同一
symbol 前后两段基准不一致（除权后新数据重新定基，旧行保留旧基准）——
策略回测/K 线在除权日附近失真（量化专项审查 P0-3）。
修复：采集只抓 raw+hfq；qfq 统一由 repair.build_qfq_dataset（qfq=hfq/F_last）
从本地推导，每晚流水线全量重建。
"""
from __future__ import annotations

import inspect
import sys
from datetime import date
from pathlib import Path

BACKEND_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(BACKEND_ROOT))

import polars as pl  # noqa: E402
import pytest  # noqa: E402

from app.core.config import get_settings  # noqa: E402
from app.data.parquet_store import read_symbol_dataset, write_year_batch  # noqa: E402

SYM = "600099.SH"  # 避免与其他测试夹具的 600001.SH 数据互相污染
DAYS = [date(2024, 1, d) for d in (2, 3, 4, 5)]


def _bars(closes: list[float]) -> pl.DataFrame:
    return pl.DataFrame({
        "symbol": [SYM] * 4, "code": ["600099"] * 4, "date": DAYS,
        "open": [c * 0.99 for c in closes], "high": [c * 1.01 for c in closes],
        "low": [c * 0.98 for c in closes], "close": closes,
        "volume": [1e6] * 4, "amount": [c * 1e6 for c in closes],
    })


@pytest.fixture()
def seeded_split():
    """10 送 10：raw 100->50，hfq 100->100（F 从 1 变 2，F_last=2）。"""
    write_year_batch("daily_bar", SYM, 2024, _bars([90.0, 100.0, 50.0, 51.0]))
    write_year_batch("daily_bar_hfq", SYM, 2024, _bars([90.0, 100.0, 100.0, 102.0]))
    return get_settings().DATA_ROOT


def test_default_adjusts_excludes_qfq():
    """采集默认口径不得包含 qfq（qfq 只能由本地 raw+hfq 推导）。"""
    from app.data.ingest.tasks import fetch_and_write_daily_bars

    default = inspect.signature(fetch_and_write_daily_bars).parameters["adjusts"].default
    assert "qfq" not in default
    assert {"", "hfq"} <= set(default)


def test_fetch_with_default_args_never_requests_qfq():
    """注入假 fetcher：默认口径下 qfq 不应被请求。"""
    from app.data.ingest.tasks import fetch_and_write_daily_bars

    called: list[str] = []

    def fake_fetch(code: str, start: str, end: str, adjust: str) -> pl.DataFrame:
        called.append(adjust)
        return pl.DataFrame()  # 空数据即可：本测试只关心请求了哪些口径

    n, failed_adj = fetch_and_write_daily_bars("600099", "2024-01-02",
                                               "2024-01-05", fetcher=fake_fetch)
    assert n == 0 and failed_adj == []
    assert called == ["", "hfq"], f"默认口径应只请求 raw/hfq，实际 {called}"


def test_step_rebuild_qfq_single_basis(seeded_split):
    """rebuild 后 qfq 全序列满足 qfq = hfq / F_last（单一基准，无漂移）。"""
    from app.orchestrator import STEP_FUNCTIONS

    assert "rebuild_qfq" in STEP_FUNCTIONS  # 已注册进流水线
    detail = STEP_FUNCTIONS["rebuild_qfq"](date(2024, 1, 5), ["600099"])
    assert "1/1" in detail or "symbols=1" in detail

    qfq = read_symbol_dataset("daily_bar_qfq", SYM).sort("date")
    hfq = read_symbol_dataset("daily_bar_hfq", SYM).sort("date")
    assert qfq.height == 4
    # 不变量 1：最后一日 qfq == raw 收盘（锚定最新）
    assert qfq["close"][-1] == pytest.approx(51.0, abs=1e-6)
    # 不变量 2：全序列 qfq = hfq / F_last（F_last = 102/51 = 2.0）
    for i in range(4):
        assert qfq["close"][i] == pytest.approx(hfq["close"][i] / 2.0, rel=1e-9)
