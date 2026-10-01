"""美股 K 线交易所后缀解析测试。

背景（2026-09-30 修复）：``fetch_kline("us", ...)`` 曾把交易所后缀**写死** ``.OQ``
（Nasdaq），但 16 只 ``US_CATALOG`` 里 13 只在 NYSE Arca（``.AM``）⇒
这些标的只回 **1 根快照**，而腾讯**不报错**（HTTP 200 + 结构完整），
于是"图上一个孤点"潜伏了很久。

本文件离线验证修复的核心不变量（不打外网，全部 monkeypatch）：
- 静态后缀表覆盖 16 只且与实测归属一致；
- 缓存键使用**真实标识**（否则 `.OQ` 的 1 根会污染后续请求）；
- ``limit`` 按市场硬夹紧（美股超限会**返回空**，不是"少给几根"）；
- A 股路径行为不变。
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

BACKEND_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(BACKEND_ROOT))

from app.data import etf as etf_mod  # noqa: E402


# 2026-09-30 实测归属（16/16 全量摸底）。写成常量以便"表被误改"时立刻失败。
_EXPECTED_SUFFIX: dict[str, str] = {
    # Nasdaq
    "QQQ": ".OQ", "TLT": ".OQ", "IBIT": ".OQ",
    # NYSE Arca
    "SPY": ".AM", "IWM": ".AM", "DIA": ".AM", "VTI": ".AM",
    "EEM": ".AM", "GLD": ".AM", "XLF": ".AM", "XLK": ".AM",
    "XLV": ".AM", "XLE": ".AM", "ARKK": ".AM", "SOXL": ".AM", "HYG": ".AM",
}


@pytest.fixture(autouse=True)
def _clear_cache():
    """每个用例前清进程级缓存，避免用例间互相污染。"""
    with etf_mod._CACHE_LOCK:
        etf_mod._cache.clear()
    yield
    with etf_mod._CACHE_LOCK:
        etf_mod._cache.clear()


def test_static_map_covers_all_catalog_symbols():
    """静态后缀表必须覆盖 US_CATALOG 全部标的，且无多余项。"""
    catalog = {e["code"] for e in etf_mod.US_CATALOG}
    assert catalog == set(_EXPECTED_SUFFIX), (
        "US_CATALOG 变更后请同步 _US_SYMBOL_MAP 与 _EXPECTED_SUFFIX"
    )
    assert catalog == set(etf_mod._US_SYMBOL_MAP), "静态表未覆盖全部标的"


def test_static_map_suffixes_match_measured():
    """静态表的后缀必须与实测归属一致（防"顺手改错后缀"回归）。"""
    for code, suffix in _EXPECTED_SUFFIX.items():
        got = etf_mod._US_SYMBOL_MAP[code]
        assert got == f"{code}{suffix}", f"{code} 应为 {code}{suffix}，实为 {got}"


def test_us_real_symbol_hits_static_map_without_http(monkeypatch):
    """命中静态表时**不应**发起任何探测 HTTP。"""
    def _boom(*a, **kw):
        raise AssertionError("命中静态表却发起了探测请求")

    monkeypatch.setattr(etf_mod, "_probe_us_node", _boom)
    for code, suffix in _EXPECTED_SUFFIX.items():
        assert etf_mod.us_real_symbol(code) == f"{code}{suffix}"


def test_us_real_symbol_autodiscovers_via_qt(monkeypatch):
    """表外标的应通过 `qt[2]` 自动发现真实标识。"""
    def _fake_probe(cand: str) -> dict:
        # 模拟腾讯：错误后缀也回 qt，且 qt[2] 给出真实标识
        if cand == "NEWT.AM":
            return {"qt": {"usNEWT.AM": ["delay", "新标的", "NEWT.AM", "0"]},
                    "day": [["2026-09-29", "1", "1", "1", "1", "1"]]}
        return {}

    monkeypatch.setattr(etf_mod, "_probe_us_node", _fake_probe)
    assert etf_mod.us_real_symbol("NEWT") == "NEWT.AM"


def test_us_real_symbol_falls_back_to_oq(monkeypatch):
    """探测全失败时应兜底 .OQ（= 改造前行为，保证不退步）。"""
    monkeypatch.setattr(etf_mod, "_probe_us_node", lambda cand: {})
    assert etf_mod.us_real_symbol("GHOST") == "GHOST.OQ"


def test_us_real_symbol_cached(monkeypatch):
    """解析结果应进缓存，第二次调用不再探测。"""
    calls: list[str] = []

    def _counting_probe(cand: str) -> dict:
        calls.append(cand)
        return {}

    monkeypatch.setattr(etf_mod, "_probe_us_node", _counting_probe)
    etf_mod.us_real_symbol("ZZZZ")
    first = len(calls)
    etf_mod.us_real_symbol("ZZZZ")
    assert len(calls) == first, "第二次调用不应再次探测"
    assert first >= 1


def test_kline_cache_key_uses_real_symbol(monkeypatch):
    """缓存键必须含真实标识，否则 .OQ 的「1 根」会污染后续请求。"""
    seen_params: list[str] = []

    def _fake_request(method, url, **kw):
        seen_params.append(url)
        return {"data": {"usSPY.AM": {
            "day": [[f"2026-09-{d:02d}", "1", "1", "1", "1", "1"]
                    for d in range(1, 21)]}}}

    monkeypatch.setattr(etf_mod, "_request", _fake_request)
    bars = etf_mod.fetch_kline("us", "SPY", limit=20, force=True)

    assert len(bars) == 20
    assert any("usSPY.AM" in p for p in seen_params), "请求未使用真实标识"
    keys = [k for k in etf_mod._cache if k.startswith("kline:us:")]
    assert keys == ["kline:us:SPY.AM:20"], f"缓存键错误: {keys}"


def test_kline_limit_clamped_per_market(monkeypatch):
    """limit 应按市场硬夹紧：美股 2000 / A 股 800。

    美股超限**返回空**（不是"少给几根"），所以夹紧是正确性要求而非优化。
    """
    seen: list[str] = []

    def _fake_request(method, url, **kw):
        seen.append(url)
        return {"data": {}}

    monkeypatch.setattr(etf_mod, "_request", _fake_request)

    etf_mod.fetch_kline("us", "SPY", limit=5000, force=True)
    assert any(",,2000,qfq" in u for u in seen), f"美股未夹紧到 2000: {seen}"
    assert not any(",,5000,qfq" in u for u in seen), "超限值泄漏到上游"

    seen.clear()
    etf_mod.fetch_kline("sh", "510300", limit=5000, force=True)
    assert any(",,800,qfq" in u for u in seen), f"A 股未夹紧到 800: {seen}"


def test_cn_path_unaffected(monkeypatch):
    """A 股请求参数与修复前一致（无后缀、无改动）。"""
    seen: list[str] = []

    def _fake_request(method, url, **kw):
        seen.append(url)
        return {"data": {"sh510300": {
            "day": [["2026-09-01", "1", "1", "1", "1", "1"]]}}}

    monkeypatch.setattr(etf_mod, "_request", _fake_request)
    etf_mod.fetch_kline("sh", "510300", limit=20, force=True)

    assert any("param=sh510300,day,,,20,qfq" in u for u in seen), seen
    keys = [k for k in etf_mod._cache if k.startswith("kline:sh:")]
    assert keys == ["kline:sh:510300:20"]


def test_single_bar_triggers_warning(monkeypatch):
    """请求 >1 根却只回 1 根 ⇒ 必须留下可观测信号（本次事故的根源是"静默"）。

    注意：本项目用 ``loguru``，其日志**不经过标准 logging**（``caplog`` 抓不到），
    故用 loguru 自己的 sink 捕获。
    """
    def _fake_request(method, url, **kw):
        return {"data": {"usSPY.AM": {"day": [["2026-09-29", "1", "1", "1", "1", "1"]]}}}

    monkeypatch.setattr(etf_mod, "_request", _fake_request)

    captured: list[str] = []
    sink_id = etf_mod.logger.add(lambda msg: captured.append(msg), level="WARNING")
    try:
        etf_mod.fetch_kline("us", "SPY", limit=20, force=True)
    finally:
        etf_mod.logger.remove(sink_id)

    assert any("仅返回 1 根" in m for m in captured), f"单根退化未产生告警: {captured}"
