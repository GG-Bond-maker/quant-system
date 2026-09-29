"""L1+L2：组合回测「复权口径如实披露」用例。

背景（审计口径静默缺陷，本批修复）
----------------------------------
组合回测价格源：股票主源东财(QFQ)、备源腾讯(QFQ 口径不变)；**ETF 主源东财(QFQ)、
备源新浪(`fund_etf_hist_sina`) 实际是*不复权*全量历史**。此前 ETF 一旦降级到新浪，
口径由 QFQ 悄然变为不复权，用户却看不到 —— 与 Backtest 页 B7b F6 同类缺陷。

修复：
    L1 `portfolio_source.fetch_asset_close_with_meta` 如实返回 `(series, meta)`，
       并把 meta 写进 `series.attrs["aqp_price_basis"]`；`fetch_asset_close` 变薄封装。
    L2 `domain/portfolio.run_portfolio_backtest` 在 **pd.concat 之前** 收集各资产口径，
        聚合成结果里的 `price_basis`（字段形状对齐 `api/v1/backtest.py`）。

本文件**不依赖网络**：monkeypatch `portfolio_source._safe_ak` 注入伪造数据源；
API 透传用例 monkeypatch 注入 `price_loader`/`benchmark_loader`。
"""
from __future__ import annotations

import sys
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd
import pytest
from fastapi.testclient import TestClient

BACKEND_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(BACKEND_ROOT))

import app.data.portfolio_source as psrc  # noqa: E402
from app.api.v1 import portfolio as portfolio_api  # noqa: E402
from app.domain import portfolio as portfolio_mod  # noqa: E402
from app.main import app  # noqa: E402


# ============================ 工具 ============================
def _mk_df(dates: pd.DatetimeIndex, closes: list[float]) -> pd.DataFrame:
    """伪造 AKShare **中文列名**日线（东财 stock_zh_a_hist / fund_etf_hist_em 用）。"""
    return pd.DataFrame({"日期": [str(d)[:10] for d in dates], "收盘": closes})


def _mk_df_en(dates: pd.DatetimeIndex, closes: list[float]) -> pd.DataFrame:
    """伪造 AKShare **英文列名**日线（新浪 fund_etf_hist_sina 用；_slice_by_date 依赖 date 列）。"""
    return pd.DataFrame({"date": [str(d)[:10] for d in dates], "close": closes})


def _resolve(spec: Any) -> Any:
    """spec 为 DataFrame -> 返回；为 Exception -> 抛出；为 callable -> 调用。"""
    if isinstance(spec, BaseException):
        raise spec
    if callable(spec):
        return spec()
    return spec


def _install_sources(monkeypatch: pytest.MonkeyPatch, *,
                     em: Any = None, sina: Any = None, tx: Any = None) -> None:
    """注入伪造 ak 模块 + 直通 _safe_call（em/sina/tx 可传 DataFrame / 异常 / 可调用）。"""
    class _FakeAk:
        def fund_etf_hist_em(self, **_kw: Any) -> Any:
            return _resolve(em)

        def stock_zh_a_hist(self, **_kw: Any) -> Any:
            return _resolve(em)

        def fund_etf_hist_sina(self, **_kw: Any) -> Any:
            return _resolve(sina)

        def stock_zh_a_hist_tx(self, **_kw: Any) -> Any:
            return _resolve(tx)

    def _direct(fn: Any, **kw: Any) -> Any:
        return fn(**kw)

    monkeypatch.setattr(psrc, "_safe_ak", lambda: (_FakeAk(), _direct))
    psrc.memory.lru_clear()


def _meta(code: str, typ: str, basis: str) -> dict[str, Any]:
    return {"code": code, "asset_type": typ, "source": "test",
            "basis": basis, "adjusted": basis == "qfq"}


# ==================== L2：#加总纯函数（单元） ====================
def test_aggregate_all_qfq() -> None:
    pb = portfolio_mod._aggregate_price_basis(
        ["600519", "000001"], {"600519": _meta("600519", "stock", "qfq"),
                               "000001": _meta("000001", "stock", "qfq")})
    assert pb["kind"] == "platform"
    assert pb["basis"] == "qfq"
    assert pb["raw_fallback_symbols"] == []
    assert "QFQ" in pb["note"]


def test_aggregate_all_raw() -> None:
    pb = portfolio_mod._aggregate_price_basis(
        ["159915"], {"159915": _meta("159915", "etf", "raw")})
    assert pb["basis"] == "raw"
    assert pb["raw_fallback_symbols"] == ["159915"]


def test_aggregate_mixed() -> None:
    pb = portfolio_mod._aggregate_price_basis(
        ["600519", "159915"], {"600519": _meta("600519", "stock", "qfq"),
                               "159915": _meta("159915", "etf", "raw")})
    assert pb["basis"] == "mixed"
    assert pb["raw_fallback_symbols"] == ["159915"]


def test_aggregate_unknown_when_any_meta_missing() -> None:
    # 159915 无 meta（未经平台数据源加载）⇒ 保守判 unknown，绝不冒充 qfq
    pb = portfolio_mod._aggregate_price_basis(
        ["600519", "159915"], {"600519": _meta("600519", "stock", "qfq")})
    assert pb["basis"] == "unknown"
    assert "无法确认" in pb["note"]


# ==================== L1：fetch_asset_close_with_meta ====================
_DATES = pd.bdate_range("2024-01-02", "2024-03-29")


def test_meta_etf_primary_eastmoney_qfq(monkeypatch: pytest.MonkeyPatch) -> None:
    _install_sources(monkeypatch, em=_mk_df(_DATES, list(range(1, len(_DATES) + 1))))
    series, meta = psrc.fetch_asset_close_with_meta("510300", "etf", "2024-01-02", "2024-03-29")
    assert meta["basis"] == "qfq" and meta["source"] == "eastmoney"
    assert meta["adjusted"] is True
    assert series.attrs["aqp_price_basis"] == meta


def test_meta_etf_backup_sina_is_raw(monkeypatch: pytest.MonkeyPatch) -> None:
    # 主源东财抛错 -> 降级新浪（不复权；新浪返回英文列名 date/close）
    _install_sources(monkeypatch, em=RuntimeError("em down"),
                     sina=_mk_df_en(_DATES, list(range(1, len(_DATES) + 1))))
    series, meta = psrc.fetch_asset_close_with_meta("159915", "etf", "2024-01-02", "2024-03-29")
    assert meta["basis"] == "raw" and meta["source"] == "sina"
    assert meta["adjusted"] is False
    assert series.attrs["aqp_price_basis"]["basis"] == "raw"
    # 薄封装的签名/行为：返回带 attrs 的 Series
    wrapped = psrc.fetch_asset_close("159915", "etf", "2024-01-02", "2024-03-29")
    assert isinstance(wrapped, pd.Series)
    assert wrapped.attrs["aqp_price_basis"]["basis"] == "raw"


def test_meta_stock_backup_tencent_keeps_qfq(monkeypatch: pytest.MonkeyPatch) -> None:
    _install_sources(monkeypatch, em=RuntimeError("em down"),
                     tx=_mk_df(_DATES, list(range(1, len(_DATES) + 1))))
    _series, meta = psrc.fetch_asset_close_with_meta("600519", "stock", "2024-01-02", "2024-03-29")
    assert meta["basis"] == "qfq" and meta["source"] == "tencent"


def test_meta_cached_second_call_consistent(monkeypatch: pytest.MonkeyPatch) -> None:
    calls = {"n": 0}

    def _em() -> pd.DataFrame:
        calls["n"] += 1
        return _mk_df(_DATES, list(range(1, len(_DATES) + 1)))

    _install_sources(monkeypatch, em=_em)
    _s1, m1 = psrc.fetch_asset_close_with_meta("510300", "etf", "2024-01-02", "2024-03-29")
    _s2, m2 = psrc.fetch_asset_close_with_meta("510300", "etf", "2024-01-02", "2024-03-29")
    assert calls["n"] == 1, "第二次应命中进程内缓存，不得再外呼"
    assert m1 == m2 and m2["basis"] == "qfq"


# ==================== L2：run_portfolio_backtest 聚合 ====================
def _prices(start: str, end: str, n: int) -> dict[str, pd.Series]:
    dates = pd.bdate_range(start, end)
    rng = np.random.default_rng(7)
    out: dict[str, pd.Series] = {}
    for i in range(n):
        rets = rng.normal(0.0006, 0.01, len(dates))
        out[f"C{i}"] = pd.Series(10 * np.exp(np.cumsum(rets)), index=dates.date, name=f"C{i}")
    return out


def _run(prices_map: dict[str, pd.Series], basis_map: dict[str, str]) -> dict[str, Any]:
    syms = list(prices_map)
    assets = [{"code": c, "type": "etf" if basis_map.get(c) else "stock",
               "weight": 1.0 / len(syms)} for c in syms]

    def _loader(code: str, typ: str, start: str, end: str) -> pd.Series:
        s = prices_map[code].copy()
        if basis_map.get(code):
            s.attrs["aqp_price_basis"] = _meta(code, typ, basis_map[code])
        return s

    dates = pd.bdate_range("2024-01-02", "2024-03-29")
    bm = pd.Series(100 * np.linspace(1, 1.05, len(dates)), index=dates.date, name="benchmark")
    return portfolio_mod.run_portfolio_backtest(
        assets=assets, start_date="2024-01-02", end_date="2024-03-29",
        initial_cash=1_000_000.0, rebalance="M", benchmark_code="000300",
        weighting="user", price_loader=_loader,
        benchmark_loader=lambda c, s, e: bm,
    )


def test_backtest_price_basis_unknown_when_no_attrs() -> None:
    res = _run(_prices("2024-01-02", "2024-03-29", 2), {})  # loader 不带 attrs
    assert res["price_basis"]["basis"] == "unknown"


def test_backtest_price_basis_qfq() -> None:
    res = _run(_prices("2024-01-02", "2024-03-29", 2),
               {"C0": "qfq", "C1": "qfq"})
    assert res["price_basis"]["basis"] == "qfq"
    assert res["price_basis"]["raw_fallback_symbols"] == []


def test_backtest_price_basis_mixed() -> None:
    res = _run(_prices("2024-01-02", "2024-03-29", 2), {"C0": "qfq", "C1": "raw"})
    assert res["price_basis"]["basis"] == "mixed"
    assert res["price_basis"]["raw_fallback_symbols"] == ["C1"]


def test_concat_drops_attrs_justifying_collect_before_concat() -> None:
    """钉住设计依据：attrs 会随 pd.concat 丢失 ⇒ 必须在 concat 前收集。"""
    a = pd.Series([1.0, 2.0], name="a")
    b = pd.Series([3.0, 4.0], name="b")
    a.attrs["aqp_price_basis"] = {"basis": "qfq"}
    b.attrs["aqp_price_basis"] = {"basis": "raw"}
    merged = pd.concat([a, b], axis=1)
    assert merged.attrs == {}, "若 concat 不再丢 attrs，本设计依据需重新评估"


# ==================== 接口透传（实测 ok(result) 确实带上 price_basis） ====================
def _login_researcher(c: TestClient) -> None:
    import asyncio

    from sqlalchemy import select as _select

    from app.core.auth import hash_password as _hp
    from app.db.init_db import init_database
    from app.db.models_auth import Role as _Role
    from app.db.models_auth import User as _User
    from app.db.session import get_session_factory

    username, password = "pbtester", "pbtestpass1234"
    asyncio.run(init_database())

    async def _seed() -> None:
        factory = get_session_factory()
        async with factory() as sess:
            for name in ("viewer", "researcher", "admin"):
                if not (await sess.scalars(
                        _select(_Role).where(_Role.name == name))).first():
                    sess.add(_Role(name=name))
            await sess.commit()
            roles = {r.name: r.id for r in (await sess.scalars(_select(_Role))).all()}
            if not (await sess.scalars(
                    _select(_User).where(_User.username == username))).first():
                sess.add(_User(username=username, password_hash=_hp(password),
                               role_id=roles["researcher"]))
            await sess.commit()

    asyncio.run(_seed())
    r = c.post("/api/v1/auth/login", json={"username": username, "password": password})
    assert r.json()["code"] == 0, f"researcher 登录失败：{r.text}"
    c.headers.update({"Authorization": f"Bearer {r.json()['data']['access_token']}"})


def test_api_passthrough_price_basis(monkeypatch: pytest.MonkeyPatch) -> None:
    """接口层：注入伪造 loader（ETF 走 raw），断言响应 data.price_basis 确实透传。"""
    dates = pd.bdate_range("2024-01-02", "2024-03-29")

    def _fake_asset(code: str, typ: str, start: str, end: str) -> pd.Series:
        s = pd.Series(10 * np.linspace(1, 1.2, len(dates)), index=dates.date, name=code)
        s.attrs["aqp_price_basis"] = {"code": code, "asset_type": typ,
                                      "source": "sina", "basis": "raw",
                                      "adjusted": False}
        return s

    def _fake_bench(code: str, start: str, end: str) -> pd.Series:
        return pd.Series(100 * np.linspace(1, 1.05, len(dates)), index=dates.date,
                         name="benchmark")

    monkeypatch.setattr(portfolio_api, "fetch_asset_close", _fake_asset)
    monkeypatch.setattr(portfolio_api, "fetch_benchmark_close", _fake_bench)

    c = TestClient(app)
    _login_researcher(c)
    body = {
        "assets": [{"code": "159915", "type": "etf", "weight": 1.0}],
        "start_date": "2024-01-02", "end_date": "2024-03-29",
        "rebalance": "none", "benchmark": "000300", "initial_cash": 1_000_000.0,
    }
    r = c.post("/api/v1/portfolio/backtest", json=body)
    assert r.status_code == 200, r.text
    payload = r.json()
    assert payload["code"] == 0, payload
    assert "price_basis" in payload["data"], "ok(result) 未透传 price_basis"
    pb = payload["data"]["price_basis"]
    assert pb["basis"] == "raw"
    assert pb["raw_fallback_symbols"] == ["159915"]
