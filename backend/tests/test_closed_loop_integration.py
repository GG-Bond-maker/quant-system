"""Offline six-layer acceptance using real stores, factors, model and broker."""
from datetime import date, datetime, timedelta
import asyncio

import lightgbm as lgb
import numpy as np
import pandas as pd
import polars as pl
import pytest

from app.core.config import get_settings
from app.data.parquet_store import atomic_write_parquet, write_year_batch
from app.db.init_db import init_database
from app.db.models import FinancialReport, Instrument, PaperOrder
from app.db.session import reset_engine
from app.ml.feature_pipeline import build_feature_panel
from app.ml.signal_contract import predict_panel, write_predictions
from app.ml.train_lgbm import train_lgbm
from app.trading import paper


@pytest.fixture
def store(tmp_path, monkeypatch):
    settings = get_settings()
    monkeypatch.setattr(settings, "DATA_ROOT", tmp_path / "parquet")
    monkeypatch.setattr(settings, "MODEL_ROOT", tmp_path / "models")
    monkeypatch.setattr(settings, "SQLITE_URL", f"sqlite+aiosqlite:///{tmp_path / 'loop.db'}")
    monkeypatch.setattr(paper, "_SYNC_ENGINE", None)
    reset_engine()
    asyncio.run(init_database())
    yield settings
    if paper._SYNC_ENGINE is not None:
        paper._SYNC_ENGINE.dispose()
    paper._SYNC_ENGINE = None
    reset_engine()


def seed_market(settings):
    days = pd.bdate_range("2023-01-03", periods=480)
    raw = []
    symbols = [f"60000{i}.SH" for i in range(6)]
    with paper.sync_session_factory()() as session:
        for index, symbol in enumerate(symbols):
            session.add(Instrument(symbol=symbol, code=symbol[:6], name=symbol, market="SH",
                                   instrument_type="stock", industry="test", list_date=days[0].date()))
            for period, announce, revenue in [(date(2022, 3, 31), date(2022, 4, 20), 100.),
                                              (date(2023, 3, 31), date(2023, 4, 20), 120. + index)]:
                session.add(FinancialReport(symbol=symbol, period=period, announce_date=announce,
                    is_proxy_announce=False, announce_basis="actual", revenue=revenue, net_profit=20.,
                    roe=10. + index, eps=1., bps=5., total_assets=200., total_liability=80.))
        session.commit()
    for index, symbol in enumerate(symbols):
        rng = np.random.default_rng(10 + index)
        ret = np.zeros(len(days))
        for t in range(1, len(days)):
            ret[t] = .35 * ret[t-1] + rng.normal(0, .01)
        close = 10 * np.exp(np.cumsum(ret))
        actual = pd.DataFrame({"symbol": symbol, "date": days.date,
            "open": close, "high": close * 1.01, "low": close * .99, "close": close,
            "volume": np.full(len(days), 1e7), "amount": close * 1e7})
        adjusted = actual.copy()
        adjusted[["open", "high", "low", "close"]] *= 5
        for year in actual["date"].map(lambda d: d.year).unique():
            write_year_batch("daily_bar", symbol, int(year), pl.from_pandas(actual[actual["date"].map(lambda d: d.year) == year]))
            write_year_batch("daily_bar_hfq", symbol, int(year), pl.from_pandas(adjusted[adjusted["date"].map(lambda d: d.year) == year]))
        raw.append(adjusted)
    # Same-day news must become visible only on a later date.
    factor = pl.DataFrame({"symbol": symbols, "date": [days[300].date()] * 6,
        "method": ["rule_lexicon"] * 6, "sentiment": [.7] * 6, "surprise": [.2] * 6, "n_docs": [1] * 6})
    factor_path = settings.DATA_ROOT / "text_features/version=sentiment_v1/year=2024.parquet"
    factor_path.parent.mkdir(parents=True, exist_ok=True)
    atomic_write_parquet(factor_path, factor)
    return pd.concat(raw, ignore_index=True), days, symbols


def test_real_features_attach_disclosed_financials_unadjusted_valuation_and_lagged_news(store):
    raw, days, symbols = seed_market(store)
    panel = build_feature_panel(raw, universe=symbols)
    symbol = panel[panel["symbol"] == symbols[0]].set_index("date")
    known = symbol.loc[pd.Timestamp("2023-04-21")]
    actual_price = raw[(raw["symbol"] == symbols[0]) & (raw["date"] == date(2023, 4, 21))]["close"].iloc[0] / 5
    assert known["f_earnings_yield"] == pytest.approx(1 / actual_price)
    assert known["f_book_to_price"] == pytest.approx(5 / actual_price)
    assert known["f_revenue_yoy"] == pytest.approx(.2)
    assert known["f_debt_ratio"] == pytest.approx(.4)
    assert pd.isna(symbol.loc[days[300], "sentiment"])
    assert symbol.loc[days[301], "sentiment"] == pytest.approx(.7)


def test_data_features_model_oos_backtest_account_orders_fills_report_close_loop(store):
    raw, days, symbols = seed_market(store)
    panel = build_feature_panel(raw, universe=symbols)
    cutoff = days[400].date()
    metrics = train_lgbm(panel, horizon=5, holdout_days=60, test_days=30,
        min_abs_rank_ic=0, num_boost_round=25, stopping_rounds=5,
        version_suffix="acceptance", simulation_asof=cutoff)
    model_dir = store.MODEL_ROOT / "exp" / f"lgbm_v1_{metrics['version']}"
    assert metrics["calibration"]["available"]
    assert metrics["calibration"]["early_stop_end"] < metrics["calibration"]["date_start"]
    booster = lgb.Booster(model_file=str(model_dir / "model.lgbm"))
    inference = panel[panel["date"].between(days[401], days[410])]
    signals = predict_panel(inference, booster, metrics["kept_features"], model_dir)
    write_predictions(signals, store.DATA_ROOT / "predictions")
    assert signals["calibrated_probability"].notna().all()
    assert (signals["prediction_lower"] < signals["prediction_upper"]).all()
    # Use the actual API loader, matcher and persistence, rather than a mocked result.
    uni = raw.copy()
    uni["factor"] = 5.
    uni["is_halted"], uni["is_st"] = False, False
    uni["limit_up"], uni["limit_down"] = uni["open"] * 1.1, uni["open"] * .9
    uni["board"], uni["industry"] = "main", "test"
    uni["date"] = pd.to_datetime(uni["date"])
    for year, group in uni.groupby(uni["date"].dt.year):
        path = store.DATA_ROOT / f"universe_daily_bt/symbol=__all__/year={year}.parquet"
        path.parent.mkdir(parents=True, exist_ok=True)
        atomic_write_parquet(path, group)
    from app.api.v1 import backtest, desk
    result = backtest._run(backtest.BacktestRequest(start=str(days[401].date()), end=str(days[410].date()), top_k=2,
                                                   enable_friction=True, model_version=model_dir.name))
    assert result["backtest_id"] and result["signal_sources"] and result["filled_trades"] > 0
    user = {"role": "researcher", "sub": "acceptance"}
    created = asyncio.run(desk.create_paper_account(desk.PaperAccountRequest(
        account_id="acceptance", backtest_id=int(result["backtest_id"])), _user=user))
    assert created.code == 0
    source = result["signal_sources"][0]
    response = asyncio.run(desk.place_order(desk.OrderRequest(
        account_id="acceptance", symbol=source["symbol"], order_amount=100_000, algo="market",
        strategy_version=result["strategy_version"], model_version=source["model_version"],
        signal_id=source["signal_id"], backtest_id=result["backtest_id"], run_id=result["run_id"],
        client_order_id="acceptance-buy"), _user=user))
    assert response.data["ok"]
    with paper.sync_session_factory()() as session:
        order = session.get(PaperOrder, response.data["order_id"])
        # Explicit offline simulation: replay that historical signal's next-open execution.
        order.created_at = datetime.combine(days[410].date(), datetime.min.time())
        session.commit()
        assert paper.run_fills(session, account_id="acceptance", asof=days[412].date())["fills_created"] == 1
        session.commit()
        summary = paper.account_summary(session, "acceptance")
        assert summary["n_fills"] == 1 and summary["cash"] >= 0
        from app.trading.comparison import compare_accounts
        compare = compare_accounts(session, ["acceptance"])
        assert compare["items"][0]["lineage"][0]["backtest_id"] == result["backtest_id"]
        assert paper.account_summary(session)["n_fills"] == 0
    from app.api.v1.report import build_daily_report
    report = build_daily_report(days[-1].date())
    assert report["strategy_comparison"] is not None
    assert any(item["account_id"] == "acceptance" for item in report["strategy_comparison"]["items"])


def test_tca_cannot_mix_accounts(store):
    from app.api.v1 import desk
    from app.db.models import PaperFill
    with paper.sync_session_factory()() as session:
        session.add(PaperFill(order_id=1, account_id="other", symbol="600000.SH", side="buy",
                              exec_date=date.today(), qty=100, price=10, amount=1000, fee=5))
        session.commit()
    result = asyncio.run(desk.transaction_cost_analysis(account_id="paper_default", _user={"role": "researcher"}))
    assert result.data["total_fills"] == 0


def test_unknown_backtest_cannot_be_attached_to_order(store):
    from app.api.v1 import desk
    from app.core.errors import AQPException
    with pytest.raises(AQPException, match="回测"):
        asyncio.run(desk.place_order(desk.OrderRequest(symbol="600000.SH", order_amount=1000,
            backtest_id="999999"), _user={"role": "researcher"}))


def test_financial_revisions_do_not_replace_historically_known_values(store):
    from app.data.ingest.financials import load_financials_asof, save_financials
    today = date.today()
    original_day = today - timedelta(days=60)
    row = {"symbol": "600000.SH", "period": date(today.year - 1, 12, 31),
           "announce_date": original_day, "is_proxy_announce": False,
           "announce_basis": "actual", "revenue": 100., "source": "test"}
    save_financials(pl.DataFrame([row]))
    row["revenue"] = 200.
    save_financials(pl.DataFrame([row]))
    historic = load_financials_asof("600000.SH", today - timedelta(days=1))
    assert historic["revenue"].to_list() == [100.]
    current = load_financials_asof("600000.SH", today).sort("announce_date")
    assert current["revenue"][-1] == 200.
