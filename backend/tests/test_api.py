"""API 端到端测试：TestClient 走完整 lifespan（init_db + 日历预加载）。

种子数据全部离线合成（Parquet 行情 + SQLite instrument + 微型 LightGBM 模型），
market/overview 依赖外部网络的部分允许优雅降级，只断言结构与 code=0。

隔离约定（第九阶段 HIGH-002 修复）：
- 路径隔离统一由 ``tests/conftest.py`` 在 pytest_configure 中完成，
  本文件**不再**自己设置环境变量。
- 旧实现依赖"test_api 按字母序最先执行、其模块级环境变量对整个进程生效"
  这种脆弱约定；一旦 conftest 先调用过 get_settings()（lru_cache），
  模块级环境变量就不再生效，导致写入路径与读取路径不一致。
  现在一律通过 ``get_settings().DATA_ROOT`` 取路径。
- 测试结束清理：合成 Parquet、临时模型目录（DB 文件由 conftest 统一清理）。
"""
from __future__ import annotations

import asyncio
import shutil
import sys
from pathlib import Path

import numpy as np
import pandas as pd
import polars as pl
import pytest
from fastapi.testclient import TestClient

BACKEND_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(BACKEND_ROOT))

from app.core.config import get_settings  # noqa: E402
from app.core.errors import ERR_PARAMS  # noqa: E402
from app.data.parquet_store import write_year_batch  # noqa: E402
from app.db.models import Instrument  # noqa: E402
from app.db.session import get_session_factory  # noqa: E402
from app.main import app  # noqa: E402
from app.ml.features import build_factors  # noqa: E402
from app.ml.registry import PromotePolicy, promote_model  # noqa: E402
from app.ml.train_lgbm import train_lgbm  # noqa: E402

SYM = "600519.SH"
MODEL_DIR: Path | None = None
_TEST_DATA_ROOT = get_settings().DATA_ROOT


def _login_researcher(c) -> None:
    """种一个 researcher 账号并注入 Authorization 头。

    `93fa52d` 把 backtest / desk / portfolio 等端点收紧为
    ``require_role("researcher")``，本文件以匿名身份调用会吃 40100。
    """
    from sqlalchemy import select as _select

    from app.core.auth import hash_password as _hp
    from app.db.init_db import init_database
    from app.db.models_auth import Role as _Role, User as _User
    from app.db.session import get_session_factory

    username, password = "apitester", "apitestpass1"
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


def _synthetic(code: str = "600519", n_days: int = 420, seed: int = 7) -> pd.DataFrame:
    """带动量的合成日线（2023-01 起的 420 个工作日）。"""
    rng = np.random.default_rng(seed)
    dates = pd.bdate_range("2023-01-02", periods=n_days)
    rets = np.zeros(n_days)
    for t in range(1, n_days):
        rets[t] = 0.25 * rets[t - 1] + 0.02 * rng.standard_normal()
    close = 100.0 * np.exp(np.cumsum(rets))
    return pd.DataFrame({
        "symbol": SYM, "code": code, "date": dates,
        "open": close * (1 + 0.005 * rng.standard_normal(n_days)),
        "high": close * (1 + 0.01 * np.abs(rng.standard_normal(n_days))),
        "low": close * (1 - 0.01 * np.abs(rng.standard_normal(n_days))),
        "close": close,
        "volume": rng.integers(1_000, 100_000, n_days).astype(float),
        "amount": close * rng.integers(1_000, 100_000, n_days),
    })


async def _seed_db() -> None:
    """幂等种子：重复运行（DB 已有数据）时 upsert 而非冲突报错。"""
    from sqlalchemy.dialects.sqlite import insert as sqlite_insert

    factory = get_session_factory()
    rows = [
        {"symbol": "600519.SH", "code": "600519", "name": "贵州茅台",
         "market": "SH", "instrument_type": "stock", "industry": "食品饮料"},
        {"symbol": "000001.SZ", "code": "000001", "name": "平安银行",
         "market": "SZ", "instrument_type": "stock", "industry": "银行"},
        {"symbol": "300750.SZ", "code": "300750", "name": "宁德时代",
         "market": "SZ", "instrument_type": "stock", "industry": "电力设备"},
    ]
    ins = sqlite_insert(Instrument)
    stmt = ins.on_conflict_do_update(
        index_elements=["symbol"],
        set_={c: ins.excluded[c] for c in ("code", "name", "market", "industry")},
    )
    async with factory() as s:
        await s.execute(stmt, rows)
        await s.commit()


@pytest.fixture(scope="module")
def client():
    global MODEL_DIR
    df = _synthetic()
    pdf = pl.from_pandas(df)
    # Parquet 行情：2023 / 2024 两个年分区
    write_year_batch("daily_bar", SYM, 2023,
                     pdf.filter(pl.col("date").dt.year() == 2023))
    write_year_batch("daily_bar", SYM, 2024,
                     pdf.filter(pl.col("date").dt.year() == 2024))
    # hfq 数据集（/predict 引擎读取基准；测试中价格与 none 相同）
    write_year_batch("daily_bar_hfq", SYM, 2023,
                     pdf.filter(pl.col("date").dt.year() == 2023))
    write_year_batch("daily_bar_hfq", SYM, 2024,
                     pdf.filter(pl.col("date").dt.year() == 2024))
    # 微型可训练模型（供 /predict 使用）
    result = train_lgbm(
        build_factors(df), horizon=5, holdout_days=60,
        version_suffix="apitest", num_boost_round=60, stopping_rounds=10,
        min_abs_rank_ic=0.0, top_k=30,
    )
    MODEL_DIR = Path(result["model_path"]).parent
    # 第三阶段纪律：训练只产生 candidate，/predict 走 load_prod_model
    # 必须显式 promote 才能服务。质量门槛在本用例中放宽（只验证链路）。
    _pr = promote_model("lgbm_v1", result["version"], by="pytest",
                        reason="API 测试显式提升",
                        policy=PromotePolicy(min_valid_rank_ic=-1.0,
                                             min_valid_icir=-1.0,
                                             rank_ic_tolerance=1e9,
                                             icir_tolerance=1e9,
                                             max_rmse_worsen_ratio=1e9, allow_missing_metrics=True))
    assert _pr["promoted"], f"测试前置 promote 失败：{_pr['reason']}"

    with TestClient(app) as c:  # lifespan: init_db + 交易日历预加载
        asyncio.run(_seed_db())
        _login_researcher(c)
        yield c

    # ---- teardown：只清理本模块写入的路径 ----
    # DATA_ROOT 是 conftest 为**整个测试会话**创建的共享临时目录，绝不能 rmtree
    # 整根：那会把同会话其他模块已种的数据全部抹掉，让后续模块行为取决于
    # "是否排在 test_api 之后"（顺序敏感 flake）。
    if MODEL_DIR and MODEL_DIR.exists():
        shutil.rmtree(MODEL_DIR, ignore_errors=True)
    for _ds in ("daily_bar", "daily_bar_hfq"):
        _p = _TEST_DATA_ROOT / _ds / f"symbol={SYM}"
        if _p.exists():
            shutil.rmtree(_p, ignore_errors=True)


# ---------------- 基础 ----------------
def test_health(client: TestClient):
    r = client.get("/health")
    assert r.status_code == 200
    body = r.json()
    assert body["code"] == 0 and body["data"]["db"] == "ok"
    assert set(body) >= {"code", "message", "data", "trace_id", "ts"}


# ---------------- stock/search ----------------
def test_search_by_name(client: TestClient):
    r = client.get("/api/v1/stock/search", params={"q": "茅台"})
    assert r.status_code == 200
    body = r.json()
    assert body["code"] == 0
    symbols = [x["symbol"] for x in body["data"]]
    assert "600519.SH" in symbols


def test_search_by_code(client: TestClient):
    r = client.get("/api/v1/stock/search", params={"q": "300750"})
    body = r.json()
    assert body["code"] == 0
    assert body["data"][0]["name"] == "宁德时代"


def test_search_no_result(client: TestClient):
    r = client.get("/api/v1/stock/search", params={"q": "不存在的股票XYZ"})
    body = r.json()
    assert body["code"] == 0 and body["data"] == []


def test_search_param_error(client: TestClient):
    r = client.get("/api/v1/stock/search")  # 缺 q
    body = r.json()
    assert body["code"] == 40000  # RequestValidationError -> 统一 200 + 40000


# ---------------- stock/profile ----------------
def test_profile_with_latest_quote(client: TestClient):
    r = client.get("/api/v1/stock/600519.SH/profile")
    body = r.json()
    assert body["code"] == 0
    data = body["data"]
    assert data["name"] == "贵州茅台"
    assert data["latest"] is not None
    assert data["latest"]["close"] > 0 and data["latest"]["pct"] is not None


def test_profile_not_found(client: TestClient):
    r = client.get("/api/v1/stock/999999.SH/profile")
    body = r.json()
    assert body["code"] == 40400


# ---------------- stock/kline ----------------
def test_kline_with_indicators(client: TestClient):
    r = client.get("/api/v1/stock/600519.SH/kline",
                   params={"adjust": "none", "start": "20240101", "end": "20240601"})
    body = r.json()
    assert body["code"] == 0, body["message"]
    data = body["data"]
    assert data["count"] > 0 and len(data["bars"]) == data["count"]
    bars = data["bars"]
    assert "close" in bars[0] and "volume" in bars[0]
    # 末尾若干根 K 线的 MA20 / MACD / RSI 已算出（前端窗口不足处为 null）
    last = bars[-1]
    assert last["ma_20"] is not None
    assert last["macd_dif"] is not None
    assert 0.0 <= last["rsi_14"] <= 100.0
    assert last["boll_up"] >= last["boll_mid"] >= last["boll_low"]


def test_kline_missing_adjust_dataset(client: TestClient):
    """qfq 数据集未拉取 -> 业务码 51001（数据为空）。"""
    r = client.get("/api/v1/stock/600519.SH/kline",
                   params={"adjust": "qfq", "start": "20240101", "end": "20240601"})
    body = r.json()
    assert body["code"] == 51001


def test_kline_bad_adjust(client: TestClient):
    r = client.get("/api/v1/stock/600519.SH/kline",
                   params={"adjust": "xxx", "start": "20240101", "end": "20240601"})
    assert r.json()["code"] == 40000


# ---------------- stock/predict ----------------
def test_predict(client: TestClient):
    r = client.get("/api/v1/stock/600519.SH/predict")
    body = r.json()
    assert body["code"] == 0, body["message"]
    data = body["data"]
    assert np.isfinite(data["pred_return"])
    # confidence：模型 metrics 存在时为 [0,1] 的模型级常数；缺失时如实为 null
    # （不再静默回退 0.5 冒充真实值，见 AQP 数据真实性审计 P0-2）
    assert data["confidence"] is None or 0.0 <= data["confidence"] <= 1.0
    if data["confidence"] is None:
        assert "RankIC" in (data.get("confidence_basis") or "")
    assert "confidence_basis" in data
    assert data["horizon"] == 5
    assert data["top5_factors"] and len(data["top5_factors"]) == 5
    for t in data["top5_factors"]:
        assert set(t) == {"feature", "contribution", "direction"}


def test_predict_symbol_without_data(client: TestClient):
    r = client.get("/api/v1/stock/000001.SZ/predict")
    body = r.json()
    assert body["code"] == 51001  # 本地无该标的历史行情


# ---------------- market/overview ----------------
def test_market_overview_structure(client: TestClient):
    r = client.get("/api/v1/market/overview", params={"recommend_k": 10})
    body = r.json()
    assert body["code"] == 0, body["message"]
    data = body["data"]
    # 五大块齐全，允许 unavailable（外网不可达时优雅降级）
    for block in ("indices", "heat", "money_flow", "anomalies", "recommend"):
        assert block in data
    assert data["trade_date"] and "from_cache" in data
    # 推荐允许部分行情或数据滞后降级；任何状态都必须保留 items 数组契约
    assert data["recommend"]["status"] in {"ok", "degraded", "unavailable"}
    assert isinstance(data["recommend"].get("items"), list)
    if data["indices"]["status"] == "ok":
        assert len(data["indices"]["items"]) > 0
        assert {"code", "name", "close", "pct"} <= set(data["indices"]["items"][0])


def test_market_overview_cache_hit(client: TestClient):
    """第二次请求应命中 Redis（或 LRU 降级）缓存。"""
    r1 = client.get("/api/v1/market/overview")
    r2 = client.get("/api/v1/market/overview")
    assert r1.json()["code"] == 0 and r2.json()["code"] == 0
    assert r2.json()["data"]["from_cache"] is True


# ---------------- httpx 异步单元测试（ASGITransport 直连 ASGI 应用） ----------------
# 说明：app 的 lifespan（建库/日历预加载/种子数据）已由模块级 client fixture
#（TestClient）完成，这里的 AsyncClient 只复用已初始化的应用状态。
from httpx import ASGITransport, AsyncClient  # noqa: E402


@pytest.fixture()
async def async_client(client: TestClient) -> AsyncClient:
    """依赖模块级 client fixture：复用 lifespan 初始化，并继承其鉴权头。

    T-03 B 组把 /stock/{symbol}/kline 等读端点收紧为 require_role("viewer")，
    异步客户端不带 token 会吃 40100，故透传 client 已注入的 Authorization。
    """
    transport = ASGITransport(app=app)
    return AsyncClient(transport=transport, base_url="http://test",
                       headers=dict(client.headers))


async def test_overview_async(async_client: AsyncClient, client: TestClient):
    """market/overview 异步测试：命中进程内 LRU 缓存（sync 测试已预热）。"""
    async with async_client as ac:
        r = await ac.get("/api/v1/market/overview", params={"recommend_k": 10})
    assert r.status_code == 200
    body = r.json()
    assert body["code"] == 0
    data = body["data"]
    for block in ("indices", "heat", "money_flow", "anomalies", "recommend"):
        assert block in data
    assert data["from_cache"] is True  # LRU 缓存命中（Redis 未启动时的降级路径）
    assert set(body) >= {"code", "message", "data", "trace_id", "ts"}


async def test_kline_async(async_client: AsyncClient, client: TestClient):
    """K 线接口异步测试：数据完整性 + 指标叠加 + 复权口径错误码。"""
    async with async_client as ac:
        r = await ac.get(
            "/api/v1/stock/600519.SH/kline",
            params={"adjust": "none", "start": "20240101", "end": "20240601"},
        )
        r_bad = await ac.get(
            "/api/v1/stock/600519.SH/kline",
            params={"adjust": "qfq", "start": "20240101", "end": "20240601"},
        )
    body = r.json()
    assert body["code"] == 0
    bars = body["data"]["bars"]
    assert len(bars) == body["data"]["count"] > 0
    last = bars[-1]
    assert last["ma_20"] is not None and last["macd_dif"] is not None
    # qfq 数据集未拉取 -> 业务码 51001（hfq 已由 fixture 种子化）
    assert r_bad.json()["code"] == 51001


async def test_overview_and_kline_concurrent(async_client: AsyncClient, client: TestClient):
    """并发聚合：异步客户端下同时请求两个接口，均应 code=0。"""
    import asyncio

    async with async_client as ac:
        r1, r2 = await asyncio.gather(
            ac.get("/api/v1/market/overview"),
            ac.get(
                "/api/v1/stock/600519.SH/kline",
                params={"adjust": "none", "start": "20240101", "end": "20240301"},
            ),
        )
    assert r1.json()["code"] == 0
    assert r2.json()["code"] == 0


# ---------------- P1-5 / P1-8：Screener 与 Backtest API ----------------
def _nth_latest_trade_date(n: int = 0) -> str:
    from app.data.parquet_store import read_symbol_dataset

    dates = sorted(read_symbol_dataset("daily_bar", SYM)["date"].unique().to_list())
    return str(dates[len(dates) - 1 - n])


def _seed_screener_data(client: TestClient) -> None:
    """种子：predictions + 最近 3 个交易日 universe，供 screener/backtest 端到端。"""
    import pandas as pd

    from app.data.parquet_store import read_symbol_dataset

    dates = sorted(read_symbol_dataset("daily_bar", SYM)["date"].unique().to_list())
    last_day = dates[-1]              # datetime.date（polars Date 标量 -> Python date）
    d = last_day.isoformat()
    uni_dates = [str(x)[:10] for x in dates[-3:]]

    # date 列必须写真实 Date 口径：此前用 Python ``str`` 走 pandas object -> parquet
    # Utf8，与生产 infer 的 Date/Datetime 异构；``diagonal_relaxed`` 拼接时会把整列
    # 抬升为 String，令同行标的下游 monitor 读 predictions 时崩溃（跨文件污染）。
    pred = pl.DataFrame({"date": [last_day] * 2,
                         "symbol": [SYM, "000001.SZ"],
                         "pred_score": [0.9, 0.1],
                         "model_version": ["lgbm_test"] * 2})
    pred_dir = get_settings().DATA_ROOT / "predictions"
    pred_dir.mkdir(parents=True, exist_ok=True)
    pred.write_parquet(pred_dir / f"date={d.replace('-', '')}.parquet")

    uni_rows = []
    for ud in uni_dates:
        uni_rows.append({
            "date": ud, "symbol": SYM, "name": "贵州茅台", "board": "main",
            "is_st": False, "list_date": None, "days_since_list": 9000,
            "industry": "食品饮料", "is_halted": False, "limit_pct": 0.10,
            "limit_up": 11.0, "limit_down": 9.0, "close": 10.0, "volume": 1e5,
        })
        uni_rows.append({
            "date": ud, "symbol": "000001.SZ", "name": "平安银行", "board": "main",
            "is_st": False, "list_date": None, "days_since_list": 12000,
            "industry": "银行", "is_halted": False, "limit_pct": 0.10,
            "limit_up": 11.0, "limit_down": 9.0, "close": 10.0, "volume": 1e5,
        })
    uni = pd.DataFrame(uni_rows)
    # Task 1（整改）：/run 读 hfq 口径 universe_daily_bt——同步播种两套数据集，
    # 其余消费方（screener 等）仍读 universe_daily 原始口径。
    # 文件名必须与生产写入器一致（``year=YYYY.snappy.parquet``）：screener 经
    # ``filter_universe`` -> ``path_for_year`` 精确读该名，旧名 ``.parquet`` 会静默失效。
    for dataset in ("universe_daily", "universe_daily_bt"):
        uni_dir = get_settings().DATA_ROOT / dataset / "symbol=__all__"
        uni_dir.mkdir(parents=True, exist_ok=True)
        uni.to_parquet(uni_dir / f"year={d[:4]}.snappy.parquet", index=False)



def test_screener_filters_and_order(client: TestClient):
    _seed_screener_data(client)
    r = client.get("/api/v1/screener", params={"top_k": 1, "board": "main"})
    body = r.json()
    assert body["code"] == 0, body["message"]
    data = body["data"]
    assert data["items"][0]["symbol"] == SYM  # score 降序
    assert data["count"] == 1 and data["from_cache"] is False
    # 缓存键含 top_k/board：不同参数互不污染
    r2 = client.get("/api/v1/screener", params={"top_k": 1, "board": "main"})
    assert r2.json()["data"]["from_cache"] is True
    r3 = client.get("/api/v1/screener", params={"top_k": 5, "board": "main"})
    assert r3.json()["data"]["from_cache"] is False  # 不同 top_k -> 不同缓存键


def test_screener_empty_predictions(client: TestClient):
    """指定日期无预测时返回可展示的 unavailable 空结果，不抛裸业务异常。"""
    r = client.get("/api/v1/screener", params={"date": "2025-01-01"})
    body = r.json()
    assert body["code"] == 0
    assert body["data"]["status"] == "unavailable"
    assert body["data"]["reason"] == "model_not_ready"
    assert body["data"]["items"] == []


def test_backtest_run_e2e(client: TestClient):
    """P1-8：真实引擎端到端（universe+predictions 种子 -> 指标/拒绝统计）。"""
    _seed_screener_data(client)
    r = client.post("/api/v1/backtest/run", json={
        "start": _nth_latest_trade_date(2), "end": _nth_latest_trade_date(),
        "top_k": 1, "init_cash": 100000, "rebalance_freq": "daily"})
    body = r.json()
    assert body["code"] == 0, body["message"]
    data = body["data"]
    assert data["trading_days"] == 3
    assert {"annual_return", "sharpe", "max_drawdown", "win_rate"} <= set(data["metrics"])
    assert isinstance(data["rejected_trades"], dict)
    # 缓存命中
    r2 = client.post("/api/v1/backtest/run", json={
        "start": _nth_latest_trade_date(2), "end": _nth_latest_trade_date(),
        "top_k": 1, "init_cash": 100000, "rebalance_freq": "daily"})
    assert r2.json()["data"]["from_cache"] is True


def test_backtest_response_has_curves(client: TestClient):
    """P2-5：/backtest/run 响应包含 equity_curve / drawdown / annual_returns / holdings。"""
    _seed_screener_data(client)  # 同模块内直接可用

    _seed_screener_data(client)
    r = client.post("/api/v1/backtest/run", json={
        "start": _nth_latest_trade_date(2), "end": _nth_latest_trade_date(),
        "top_k": 1, "init_cash": 100000, "rebalance_freq": "daily",
        "enable_friction": True})
    body = r.json()
    assert body["code"] == 0, body["message"]
    data = body["data"]
    for key in ("equity_curve", "drawdown_curve", "annual_returns", "holdings",
                "trades", "friction_costs"):
        assert key in data, f"缺少 {key}"
    assert len(data["drawdown_curve"]) == data["trading_days"]
    assert all("drawdown" in row for row in data["drawdown_curve"])
    # 缓存键含摩擦参数：开启摩擦与关闭互不命中
    r2 = client.post("/api/v1/backtest/run", json={
        "start": _nth_latest_trade_date(2), "end": _nth_latest_trade_date(),
        "top_k": 1, "init_cash": 100000, "rebalance_freq": "daily",
        "enable_friction": False})
    # enable_friction 不同 -> 缓存键不同 -> 不命中；此处只断言请求成功
    assert r2.json()["code"] == 0


# ---------------- market/index/kline（选股中心「大盘走势」） ----------------
def test_index_kline_whitelist_and_contract(client: TestClient, monkeypatch):
    """白名单校验 + 响应契约；数据源 mock（不联网）。"""
    import app.data.etf as etf_mod

    fake_bars = [{"date": f"2026-01-{d:02d}", "close": 3000.0 + d,
                  "volume": 1.0e9} for d in range(1, 11)]
    seen: dict = {}

    def _fake(market: str, code: str, limit: int = 320, force: bool = False):
        seen["args"] = (market, code, limit)
        seen["force"] = force
        return fake_bars

    monkeypatch.setattr(etf_mod, "fetch_index_kline", _fake)

    r = client.get("/api/v1/market/index/kline",
                   params={"code": "sh000001", "limit": 30})
    body = r.json()
    assert body["code"] == 0, body["message"]
    data = body["data"]
    assert data["code"] == "sh000001" and data["name"] == "上证指数"
    assert seen["args"] == ("sh", "000001", 30)
    assert seen["force"] is False  # 默认走 fetch 层缓存
    assert len(data["bars"]) == 10
    assert {"date", "close"} <= set(data["bars"][0])
    assert data["bars"][0]["close"] < data["bars"][-1]["close"]  # 升序


def test_index_kline_rejects_unknown_code(client: TestClient):
    """非白名单代码 -> 业务码 ERR_PARAMS(40000)，且不触达数据源。"""
    r = client.get("/api/v1/market/index/kline", params={"code": "xx999999"})
    body = r.json()
    assert body["code"] == ERR_PARAMS
    assert "sh000001" in body["message"]


def test_index_kline_limit_out_of_range(client: TestClient):
    r = client.get("/api/v1/market/index/kline",
                   params={"code": "sh000001", "limit": 5})
    assert r.json()["code"] == ERR_PARAMS  # 参数校验统一业务码
