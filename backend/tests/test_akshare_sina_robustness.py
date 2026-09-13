"""Task 13 回归测试：新浪日线响应缺 date 列 / akshare 内部裸 KeyError('date') 的健壮性。

缺陷背景：688801（科创板次新）在新浪无历史/被限流时，akshare 内部
``stock_zh_a_sina.py`` 对空响应体执行 ``data_df["date"]`` 抛**裸 KeyError('date')**，
经 ``_safe_call`` 冒泡到适配器外，污染抓取失败统计（实测连续两轮、两种复权口径均复现）。

修复口径：
- 新浪（备用源，best-effort）：缺 date 列 / 内部 KeyError -> 视为「该源无此标的数据」，
  记 WARNING 后返回**空 DataFrame**，不抛裸 KeyError；
- 网络类异常（ConnectionError 等）**不被拦截**，正常向上抛；
- 东财（主源）：非空却缺 date 列 -> 抛**可读 ValueError**（接口变更要显式暴露）。
"""
from __future__ import annotations

import sys
from pathlib import Path
from types import SimpleNamespace

import pandas as pd
import polars as pl
import pytest

BACKEND_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(BACKEND_ROOT))

from app.data.ingest import akshare_adapter as ada  # noqa: E402


def _fast_io(monkeypatch: pytest.MonkeyPatch) -> None:
    """关闭限速与重试，保证测试快速且确定（不影响被测逻辑）。"""
    monkeypatch.setattr(ada, "_throttle", lambda: None)
    monkeypatch.setattr(
        ada, "get_settings",
        lambda: SimpleNamespace(AKSHARE_RETRY=1, AKSHARE_RATE_LIMIT=0.0))


def _fake_ak(func):
    """构造惰性 akshare 替身：只暴露被测函数用到的 stock_zh_a_daily。"""
    return lambda: SimpleNamespace(stock_zh_a_daily=func)


def test_sina_missing_date_column_returns_empty(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """核心回归：新浪返回**非空但无 date 列**的 DataFrame 时，不抛 KeyError，返回空表。"""
    _fast_io(monkeypatch)
    bad = pd.DataFrame({"open": [1.0, 1.1], "close": [1.0, 1.2]})  # 非空、缺 date
    monkeypatch.setattr(ada, "_ak", _fake_ak(lambda *a, **k: bad))

    out = ada._fetch_daily_bar_sina("600519", "2024-01-01", "2024-01-05", "")

    assert isinstance(out, pl.DataFrame)
    assert out.is_empty()


def test_sina_internal_keyerror_treated_as_no_data(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """akshare 内部对空响应体抛的裸 KeyError('date') 等价识别为"无数据"，不冒泡。"""
    _fast_io(monkeypatch)

    def _boom(*a, **k):
        raise KeyError("date")  # 复刻 akshare stock_zh_a_sina.py:184 的行为

    monkeypatch.setattr(ada, "_ak", _fake_ak(_boom))

    # hfq 口径（实测触发口径之一）也不应抛异常
    out = ada._fetch_daily_bar_sina("688801", "2023-01-01", "2026-09-11", "hfq")

    assert isinstance(out, pl.DataFrame)
    assert out.is_empty()


def test_sina_propagates_network_error(monkeypatch: pytest.MonkeyPatch) -> None:
    """网络类异常绝不被吞：缺 date 的手腕修复只针对 KeyError/缺列，不覆盖网络故障。"""
    _fast_io(monkeypatch)

    def _boom(*a, **k):
        raise ConnectionError("sina down")

    monkeypatch.setattr(ada, "_ak", _fake_ak(_boom))

    with pytest.raises(ConnectionError):
        ada._fetch_daily_bar_sina("600519", "2024-01-01", "2024-01-05", "")


def test_sina_valid_response_still_standardized(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """防回归：正常响应仍被正确规范化为标准 Schema。"""
    _fast_io(monkeypatch)
    good = pd.DataFrame({
        "date": ["2024-01-02", "2024-01-03"],
        "open": [1.0, 1.1], "high": [1.2, 1.3], "low": [0.9, 1.0],
        "close": [1.1, 1.2], "volume": [100.0, 200.0], "amount": [110.0, 220.0],
        "turnover": [0.1, 0.2],
    })
    monkeypatch.setattr(ada, "_ak", _fake_ak(lambda *a, **k: good))

    out = ada._fetch_daily_bar_sina("600519", "2024-01-01", "2024-01-05", "")

    assert out.height == 2
    assert "date" in out.columns
    assert out["code"].unique().to_list() == ["600519"]
    assert out["source"].unique().to_list() == ["akshare"]


def test_em_standardize_missing_date_raises_readable() -> None:
    """主源（东财）非空却缺 date 列 -> 抛带 code/列名/行数的可读 ValueError。"""
    bad = pd.DataFrame({"开盘": [1.0], "收盘": [1.0]})  # 中文列，无日期列

    with pytest.raises(ValueError, match="缺 date 列"):
        ada._standardize_daily(bad, "600519")


def test_em_standardize_empty_still_returns_empty() -> None:
    """空响应（停牌/退市）仍走"无数据"支路，返回空表而非抛错。"""
    out = ada._standardize_daily(pd.DataFrame(), "600519")
    assert isinstance(out, pl.DataFrame)
    assert out.is_empty()
