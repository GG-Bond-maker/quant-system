"""多源冗余测试：优先级 / 降级链 / 全败最终异常 / 真实主源（Tushare 已移除）。"""
from __future__ import annotations

import sys
from pathlib import Path

import polars as pl
import pytest

BACKEND_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(BACKEND_ROOT))

from app.data.ingest import multi_source as ms  # noqa: E402


def _fake(name: str, rows: int = 3):
    def _fn(code: str, start: str, end: str, adjust: str) -> pl.DataFrame:
        return pl.DataFrame({
            "date": [f"2024-01-0{i+2}" for i in range(rows)],
            "symbol": [f"{code}.X"] * rows, "code": [code] * rows,
            "open": [10.0] * rows, "high": [10.5] * rows,
            "low": [9.5] * rows, "close": [10.2] * rows,
            "volume": [1000.0] * rows, "amount": [10200.0] * rows,
            "source": [name] * rows,
        })
    return _fn


def _fail(name: str):
    def _fn(code: str, start: str, end: str, adjust: str) -> pl.DataFrame:
        raise ConnectionError(f"{name} down")
    return _fn


def test_priority_akshare_first(monkeypatch: pytest.MonkeyPatch):
    """AKShare 成功 -> 只用 AKShare，不触碰其他源。"""
    called: list[str] = []
    patched = []
    for name, _ in ms.SOURCES:
        fake = _fake(name)

        def spy(code, start, end, adjust, _n=name, _f=fake):
            called.append(_n)
            return _f(code, start, end, adjust)
        patched.append((name, spy))
    monkeypatch.setattr(ms, "SOURCES", patched)
    df, source = ms.fetch_daily_bar_multi("600519", "2024-01-02", "2024-01-04")
    assert source == "akshare" and called == ["akshare"]
    assert df["source"].unique().to_list() == ["akshare"]
    assert str(df.schema["close"]).startswith("Float")


def test_fallback_chain(monkeypatch: pytest.MonkeyPatch):
    """AKShare FAIL -> Eastmoney SUCCESS：必须用 Eastmoney（覆盖降级路径）。

    ⚠️ Tushare 已于 2026-09-26 从 SOURCES 移除，链条变为 akshare → eastmoney。
    """
    order = [("akshare", _fail("akshare")), ("eastmoney", _fake("eastmoney"))]
    called: list[str] = []
    patched = []
    for name, fn in order:
        def spy(code, start, end, adjust, _n=name, _f=fn):
            called.append(_n)
            return _f(code, start, end, adjust)
        patched.append((name, spy))
    monkeypatch.setattr(ms, "SOURCES", patched)
    df, source = ms.fetch_daily_bar_multi("000001", "2024-01-02", "2024-01-04")
    assert source == "eastmoney" and called == ["akshare", "eastmoney"]


def test_fallback_to_eastmoney(monkeypatch: pytest.MonkeyPatch):
    """降级链末源为 Eastmoney；SOURCES 不再含 Tushare（D7 彻底移除）。"""
    assert [n for n, _ in ms.SOURCES] == ["akshare", "eastmoney"]
    order = [("akshare", _fail("akshare")), ("eastmoney", _fake("eastmoney"))]
    called: list[str] = []
    patched = []
    for name, fn in order:
        def spy(code, start, end, adjust, _n=name, _f=fn):
            called.append(_n)
            return _f(code, start, end, adjust)
        patched.append((name, spy))
    monkeypatch.setattr(ms, "SOURCES", patched)
    df, source = ms.fetch_daily_bar_multi("300750", "2024-01-02", "2024-01-04")
    assert source == "eastmoney" and called == ["akshare", "eastmoney"]


def test_all_sources_fail_raises(monkeypatch: pytest.MonkeyPatch):
    """三源全败 -> 最终异常，不吞错。"""
    patched = [(n, _fail(n)) for n, _ in ms.SOURCES]
    monkeypatch.setattr(ms, "SOURCES", patched)
    with pytest.raises(ConnectionError, match="全部数据源失败"):
        ms.fetch_daily_bar_multi("600519", "2024-01-02", "2024-01-04")


@pytest.mark.network
def test_real_akshare_primary():
    """真实网络：AKShare 可用时（含新浪内部降级）source 恒为 akshare。"""
    df, source = ms.fetch_daily_bar_multi("600519", "2026-08-20", "2026-08-28")
    assert source == "akshare" and not df.is_empty()
    assert df["source"].unique().to_list() == ["akshare"]
