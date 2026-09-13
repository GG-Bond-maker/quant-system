"""回归：`POST /desk/attribution` 在"基准数据落后于组合数据"时不得抛 ZeroDivisionError。

缺陷（2026-09-11 补测发现，P1）
--------------------------------
`app/api/v1/desk.py` 构造基准收盘价用的是：

    pivot.ffill().reindex(rets_wide.index)

顺序错了：`.ffill()` 只作用于**基准源自己的索引**，随后 `reindex` 到
`rets_wide.index` 时，凡是落在基准源最新日期**之后**的日期都会整行变 NaN。
于是 `bench_close.iloc[-1]` 全 NaN → `(iloc[-1]/iloc[0]-1).dropna()` 得到空序列
→ `1.0 / bench_sym_ret.size` 抛 ZeroDivisionError，被全局兜底成
`HTTP 200 + code 50000`（未分类异常）。前端「容量与归因」页因此显示
"网络错误，请检查后端服务是否启动"。

现实触发条件很常见：`daily_bar_hfq`（组合依赖）新鲜度领先 `universe_daily`
（基准来源）——本机当前即 daily_bar_hfq=2026-09-10 / universe_daily=2026-09-07。

本用例把该落后关系**显式构造**出来：组合行情走到 D，基准只到 D-4 个交易日。
反向验证（去掉修复）：`code` 会变成 50000，用例变红。
"""
from __future__ import annotations

import sys
from datetime import date
from pathlib import Path

import numpy as np
import pandas as pd
import polars as pl
import pytest
from fastapi.testclient import TestClient

BACKEND_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(BACKEND_ROOT))

from app.core.config import get_settings  # noqa: E402
from app.core.errors import ERR_DATA_EMPTY, ERR_PARAMS, ERR_SYSTEM  # noqa: E402
from app.main import app  # noqa: E402

SYMS = ["600519.SH", "600036.SH", "300750.SZ"]
N_DAYS = 60
PORTFOLIO_LAST = date(2026, 9, 10)   # 组合行情新鲜度（daily_bar_hfq）
UNIVERSE_STALE_BY = 4                # 基准源落后 4 个交易日（universe_daily）


def _business_days(end: date, n: int) -> list[date]:
    days = pd.bdate_range(end=pd.Timestamp(end), periods=n)
    return [d.date() for d in days]


def _login_researcher(c) -> None:
    """种一个 researcher 账号并注入 Authorization 头。

    `93fa52d` 给 ``POST /api/v1/desk/attribution`` 加了
    ``require_role("researcher")``，匿名调用会吃 40100。
    """
    import asyncio

    from sqlalchemy import select as _select

    from app.core.auth import hash_password as _hp
    from app.db.init_db import init_database
    from app.db.models_auth import Role as _Role, User as _User
    from app.db.session import get_session_factory

    username, password = "desktester", "desktestpass1"
    # 幂等建表：roles/users 属 auth 模型，可能未随 lifespan 创建
    asyncio.run(init_database())

    async def _seed():
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
    r = c.post("/api/v1/auth/login", json={"username": username,
                                           "password": password})
    assert r.json()["code"] == 0, f"researcher 登录失败：{r.text}"
    c.headers.update({"Authorization": f"Bearer {r.json()['data']['access_token']}"})


@pytest.fixture(scope="module")
def attribution_client() -> TestClient:
    """隔离 DATA_ROOT 内写入"组合新鲜 / 基准陈旧"的最小合成数据。"""
    from app.data.parquet_store import write_year_batch

    s = get_settings()
    rng = np.random.default_rng(20260911)

    # ---- 组合依赖：daily_bar_hfq 走到 PORTFOLIO_LAST ----
    dates = _business_days(PORTFOLIO_LAST, N_DAYS)
    for i, sym in enumerate(SYMS):
        close = 100 * np.exp(np.cumsum(0.0003 * i + 0.015 * rng.standard_normal(len(dates))))
        df = pl.DataFrame({
            "symbol": [sym] * len(dates),
            "date": dates,
            "close": close,
        })
        write_year_batch("daily_bar_hfq", sym, PORTFOLIO_LAST.year, df)

    # ---- 基准来源：universe_daily 只到 PORTFOLIO_LAST - 4 个交易日 ----
    stale_end = _business_days(PORTFOLIO_LAST, UNIVERSE_STALE_BY + 1)[0]
    uni_dates = [d for d in dates if d <= stale_end]
    uni_rows: list[dict] = []
    for i, sym in enumerate(SYMS):
        base = 100.0 + i
        for j, d in enumerate(uni_dates):
            uni_rows.append({"date": d, "symbol": sym,
                             "close": base + 0.1 * j, "industry": f"行业{i}"})
    uni_df = pl.DataFrame(uni_rows).with_columns(pl.col("date").cast(pl.Date))
    uni_dir = s.DATA_ROOT / "universe_daily"
    uni_dir.mkdir(parents=True, exist_ok=True)
    uni_df.write_parquet(uni_dir / f"year={PORTFOLIO_LAST.year}.parquet")

    # 夹具自检：必须真的构成"基准落后"，否则用例失去意义（变成恒真断言）
    assert max(uni_df["date"].to_list()) < PORTFOLIO_LAST, "夹具未构成基准落后关系"

    # ---- features：风格回归（domain.research.STYLE_FACTOR_MAP）需要的 4 列 ----
    # 缺 features 时端点在风格回归前会抛 ERR_DATA_EMPTY（51001），
    # 那样就测不到"能真正算出数"这一面，因此这里补最小合成截面。
    feat_rows: list[dict] = []
    for sym in SYMS:
        r = rng.standard_normal(len(dates))
        for j, d in enumerate(dates):
            feat_rows.append({
                "symbol": sym, "date": d,
                "ret_20": float(r[j]),
                "vol_20": float(abs(r[j]) * 0.1),
                "amount_per_share": float(1000 + j),
                "rsi_14": float(50 + r[j]),
            })
    feat_dir = s.DATA_ROOT / "features" / "version=syn"
    feat_dir.mkdir(parents=True, exist_ok=True)
    pl.DataFrame(feat_rows).with_columns(pl.col("date").cast(pl.Date)) \
        .write_parquet(feat_dir / f"year={PORTFOLIO_LAST.year}.parquet")

    with TestClient(app) as c:
        _login_researcher(c)
        yield c


def _body(resp) -> dict:
    assert resp.status_code == 200, resp.text  # 信封契约：HTTP 恒 200
    return resp.json()


@pytest.mark.parametrize("benchmark_type,extra", [
    ("universe_equal", {}),
    ("single_symbol", {"benchmark_symbol": "600519.SH"}),
    ("custom_portfolio", {"benchmark_assets": [{"code": "600036.SH", "weight": 1.0}]}),
], ids=["universe_equal", "single_symbol", "custom_portfolio"])
def test_attribution_never_raises_when_benchmark_source_is_stale(
        attribution_client: TestClient, benchmark_type: str, extra: dict) -> None:
    """三种基准口径在"基准源落后"时都必须给出业务码，绝不能 50000。"""
    body = _body(attribution_client.post("/api/v1/desk/attribution", json={
        "assets": [{"code": "600519.SH", "weight": 0.5},
                   {"code": "600036.SH", "weight": 0.3},
                   {"code": "300750.SZ", "weight": 0.2}],
        "window_days": 40,
        "benchmark_type": benchmark_type,
        **extra,
    }))
    assert body["code"] != ERR_SYSTEM, f"未分类异常（除零回归）: {body}"
    assert body["code"] in (0, ERR_PARAMS, ERR_DATA_EMPTY), body


def test_attribution_reports_real_numbers_when_benchmark_is_fresh(
        attribution_client: TestClient) -> None:
    """把基准源补齐到与组合同日：应能真正算出数（证明上面不是"永远报无数据"）。

    同时锁定 `.ffill()` 的顺序语义——补齐后 benchmark_return 必须来自真实序列，
    而不是被 reindex 打成 NaN 后又被兜底吞掉。
    """

    s = get_settings()
    dates = _business_days(PORTFOLIO_LAST, N_DAYS)
    rng = np.random.default_rng(7)
    rows: list[dict] = []
    for i, sym in enumerate(SYMS):
        base = 100.0 + i
        for j, d in enumerate(dates):
            rows.append({"date": d, "symbol": sym,
                         "close": base * float(np.exp(0.001 * j)), "industry": f"行业{i}"})
    pl.DataFrame(rows).with_columns(pl.col("date").cast(pl.Date)) \
        .write_parquet(s.DATA_ROOT / "universe_daily" / f"year={PORTFOLIO_LAST.year}.parquet")

    body = _body(attribution_client.post("/api/v1/desk/attribution", json={
        "assets": [{"code": "600519.SH", "weight": 0.6},
                   {"code": "600036.SH", "weight": 0.4}],
        "window_days": 40,
        "benchmark_type": "universe_equal",
    }))
    assert body["code"] == 0, body
    data = body["data"]
    assert data["n_obs"] >= 30, data
    assert isinstance(data["benchmark_return"], float), data
    assert data["benchmark_return"] != 0.0, data  # 真实序列算出来不应恰好为 0
