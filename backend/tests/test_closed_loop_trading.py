"""Financial invariants at the order, execution and persisted account boundaries."""
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta

import polars as pl
import pytest
import sqlalchemy as sa
from sqlalchemy.orm import sessionmaker

from app.db.models import (
    Base,
    ExclusionItem,
    GridBot,
    PaperAccount,
    PaperFill,
    PaperOrder,
)
from app.trading import conditional, paper


@pytest.fixture
def ledger(tmp_path, monkeypatch):
    engine = sa.create_engine(f"sqlite:///{tmp_path / 'ledger.db'}", connect_args={"timeout": 10, "check_same_thread": False})
    Base.metadata.create_all(engine)
    factory = sessionmaker(engine, expire_on_commit=False)
    today = datetime.now().astimezone().date()
    bars = pl.DataFrame({"date": [today - timedelta(days=2), today - timedelta(days=1), today],
                         "open": [10.0] * 3, "close": [10.0] * 3,
                         "volume": [1e7] * 3, "amount": [1e8] * 3})
    monkeypatch.setattr(paper, "read_symbol_dataset", lambda *_: bars)
    monkeypatch.setattr(paper, "_SYNC_ENGINE", engine)
    yield factory
    engine.dispose()


def submit(session, **kwargs):
    params = {"symbol": "000001.SZ", "side": "buy", "order_amount": 500_000,
              "algo": "market", "split_days": 1, "participation_cap": .05}
    params.update(kwargs)
    return paper.place_order(session, **params)


def age(session, order_id):
    session.get(PaperOrder, order_id).created_at = datetime.combine(datetime.now().astimezone().date() - timedelta(days=2), datetime.min.time())
    session.flush()


def test_pending_orders_reserve_cash_and_concentration_then_cancel_releases(ledger):
    with ledger() as session:
        one = submit(session)
        assert one["ok"]
        assert not submit(session)["ok"]
        assert not submit(session, symbol="600000.SH", order_amount=600_000)["ok"]
        before = paper.account_summary(session)
        assert before["frozen_cash"] > 500_000
        assert before["available_cash"] < 500_000
        assert paper.cancel_order(session, one["order_id"])
        assert paper.account_summary(session)["frozen_cash"] == 0
        assert submit(session)["ok"]


def test_concurrent_orders_cannot_double_spend(ledger):
    def place():
        with ledger() as session:
            result = submit(session)
            session.commit()
            return result["ok"]
    with ThreadPoolExecutor(max_workers=3) as pool:
        assert sum(pool.map(lambda _: place(), range(3))) == 1


def test_idempotency_survives_new_session_and_rejects_changed_payload(ledger):
    with ledger() as session:
        one = submit(session, client_order_id="request-1")
        session.commit()
    with ledger() as session:
        two = submit(session, client_order_id="request-1")
        assert two["order_id"] == one["order_id"]
        assert not submit(session, client_order_id="request-1", order_amount=10_000)["ok"]
        assert session.scalar(sa.select(sa.func.count()).select_from(PaperOrder)) == 1


def test_execution_cash_conserves_and_orders_reach_terminal_state(ledger):
    with ledger() as session:
        order = submit(session)
        age(session, order["order_id"])
        assert paper.run_fills(session)["fills_created"] == 1
        session.commit()
        account = paper.account_summary(session)
        fill = session.scalar(sa.select(PaperFill))
        assert account["cash"] == pytest.approx(paper.INIT_CASH - fill.amount - fill.fee, abs=.01)
        assert account["cash"] >= 0
        assert account["frozen_cash"] == 0
        assert session.get(PaperOrder, order["order_id"]).status == "FILLED"
        assert paper.run_fills(session)["fills_created"] == 0
        assert account["nav_series"][-1]["equity"] == account["equity"]


def test_fill_checks_cash_even_for_legacy_orders_without_reservation(ledger):
    with ledger() as session:
        for symbol in ("000001.SZ", "600000.SH", "600001.SH"):
            session.add(PaperOrder(symbol=symbol, side="buy", order_amount=500_000,
                                   decision_price=10, created_at=datetime.now().astimezone().replace(tzinfo=None) - timedelta(days=2)))
        session.flush()
        paper.run_fills(session)
        assert paper.account_summary(session)["cash"] >= 0


def test_rejected_conditional_and_grid_do_not_create_fills_or_profit(ledger):
    with ledger() as session:
        paper.set_kill_switch(session, True)
        co = conditional.create_conditional_order(session, symbol="000001.SZ", side="buy",
            trigger_type="price_ge", trigger_price=10, order_amount=10_000)
        bot = conditional.create_grid_bot(session, symbol="000001.SZ", lower_price=5, upper_price=15,
                                          grid_count=10, amount_per_grid=10_000)
        session.get(GridBot, bot["id"]).last_trigger_price = 9
        session.commit()
        assert conditional.evaluate_conditional_orders(session, {"000001.SZ": 11}) == []
        assert conditional.evaluate_grid_bots(session, {"000001.SZ": 11}) == []
        row = session.get(conditional.ConditionalOrder, co["id"])
        assert row.status == "REJECTED" and row.fill_order_id is None
        grid = session.get(GridBot, bot["id"])
        assert grid.total_trades == 0 and grid.realized_pnl == 0
        assert session.scalar(sa.select(sa.func.count()).select_from(PaperFill)) == 0


def test_limit_is_enforced_and_expiration_releases_reservation(ledger):
    with ledger() as session:
        order = submit(session, price_mode="limit", limit_price=9)
        age(session, order["order_id"])
        assert paper.run_fills(session)["fills_created"] == 0
        assert session.get(PaperOrder, order["order_id"]).status == "CANCELLED"
        assert paper.account_summary(session)["frozen_cash"] == 0


def test_exclusion_does_not_prevent_exit_and_reconciles_realized_pnl(ledger):
    with ledger() as session:
        order = submit(session, order_amount=10_000)
        age(session, order["order_id"])
        paper.run_fills(session)
        session.add(ExclusionItem(symbol="000001.SZ", category="st", active=True))
        account = paper.account_summary(session)
        sell = submit(session, side="sell", order_amount=account["market_value"])
        assert sell["ok"]
        age(session, sell["order_id"])
        # Today's sale is after the prior day's purchase; never same-day A-share resale.
        session.get(PaperOrder, sell["order_id"]).created_at += timedelta(days=1)
        paper.run_fills(session)
        final = paper.account_summary(session)
        assert not final["positions"]
        assert final["cash"] == final["equity"]
        assert final["realized_pnl"] == pytest.approx(final["cash"] - paper.INIT_CASH, abs=.01)


def test_accounts_isolate_cash_and_persist_signal_lineage(ledger):
    with ledger() as session:
        session.add(PaperAccount(id="strategy-b", initial_cash=100_000, strategy_version="b:v1"))
        session.flush()
        order = submit(session, account_id="strategy-b", order_amount=20_000, strategy_version="b:v1",
                       model_version="model-1", signal_id="signal-1", backtest_id="bt-1", run_id="run-1")
        age(session, order["order_id"])
        paper.run_fills(session)
        assert paper.account_summary(session)["cash"] == paper.INIT_CASH
        assert paper.account_summary(session, "strategy-b")["cash"] < 100_000
        stored = session.get(PaperOrder, order["order_id"])
        assert (stored.strategy_version, stored.model_version, stored.signal_id, stored.backtest_id, stored.run_id) == (
            "b:v1", "model-1", "signal-1", "bt-1", "run-1")


def test_future_quotes_never_value_positions_or_set_gateway_order_budget(ledger, monkeypatch):
    today = datetime.now().astimezone().date()
    bars = pl.DataFrame({"date": [today, today + timedelta(days=1)], "open": [10., 100.],
                         "close": [10., 100.], "volume": [1e7, 1e7], "amount": [1e8, 1e9]})
    monkeypatch.setattr(paper, "read_symbol_dataset", lambda *_: bars)
    with ledger() as session:
        session.add(PaperFill(order_id=999, symbol="000001.SZ", side="buy", exec_date=today,
                              qty=100, price=10, amount=1000, fee=5))
        session.flush()
        summary = paper.account_summary(session)
        assert summary["market_value"] == 1000
        assert all(row["date"] <= str(today) for row in summary["nav_series"])
    from app.trading.gateway import OrderRequest, PaperBrokerGateway
    gateway = PaperBrokerGateway()
    gateway.connect()
    response = gateway.submit_order(OrderRequest("000001.SZ", "buy", 100, order_type="market"))
    with ledger() as session:
        assert session.get(PaperOrder, int(response.order_id)).order_amount == 1000


def test_missing_mark_keeps_position_and_makes_equity_unavailable(ledger, monkeypatch):
    with ledger() as session:
        session.add(PaperFill(order_id=999, symbol="000001.SZ", side="buy", exec_date=datetime.now().astimezone().date(),
                              qty=100, price=10, amount=1000, fee=5))
        session.flush()
        monkeypatch.setattr(paper, "read_symbol_dataset", lambda *_: pl.DataFrame())
        summary = paper.account_summary(session)
        assert summary["positions"]["000001.SZ"]["qty"] == 100
        assert summary["equity"] is None and summary["market_value"] is None
        assert not submit(session, order_amount=1000)["ok"]


def test_nav_does_not_forward_fill_across_missing_mark(ledger, monkeypatch):
    today = datetime.now().astimezone().date()
    full = pl.DataFrame({"date": [today - timedelta(days=1), today], "open": [10., 10.],
                         "close": [10., 10.], "volume": [1e7, 1e7], "amount": [1e8, 1e8]})
    monkeypatch.setattr(paper, "read_symbol_dataset", lambda _, sym: full.head(1) if sym == "000001.SZ" else full)
    with ledger() as session:
        for sym in ("000001.SZ", "600000.SH"):
            session.add(PaperFill(order_id=999, symbol=sym, side="buy", exec_date=today - timedelta(days=1),
                                  qty=100, price=10, amount=1000, fee=5))
        session.flush()
        summary = paper.account_summary(session)
        assert summary["nav_series"][-1]["equity"] is None
        assert summary["equity"] is None


def test_idempotency_rejects_changed_execution_or_lineage(ledger):
    with ledger() as session:
        assert submit(session, client_order_id="same", model_version="model-a")["ok"]
        assert not submit(session, client_order_id="same", model_version="model-b")["ok"]
        assert not submit(session, client_order_id="same", model_version="model-a",
                          price_mode="limit", limit_price=9)["ok"]


def test_explicit_sell_quantity_reserves_actual_shares(ledger):
    with ledger() as session:
        session.add(PaperFill(order_id=999, symbol="000001.SZ", side="buy", exec_date=datetime.now().astimezone().date() - timedelta(days=2),
                              qty=100, price=10, amount=1000, fee=5))
        session.flush()
        assert not submit(session, side="sell", order_amount=1, target_qty=200)["ok"]
        assert submit(session, side="sell", order_amount=1, target_qty=100)["ok"]
        assert not submit(session, side="sell", order_amount=1, target_qty=100)["ok"]


@pytest.mark.parametrize("side", ["buy", "sell"])
def test_quantity_orders_fill_requested_lot_despite_price_impact(ledger, side):
    with ledger() as session:
        if side == "sell":
            session.add(PaperFill(order_id=999, symbol="000001.SZ", side="buy", exec_date=datetime.now().astimezone().date() - timedelta(days=2),
                                  qty=100, price=10, amount=1000, fee=5))
            session.flush()
        order = submit(session, side=side, order_amount=1000, target_qty=100)
        assert order["ok"]
        age(session, order["order_id"])
        paper.run_fills(session)
        fills = list(session.scalars(sa.select(PaperFill).where(PaperFill.order_id == order["order_id"])))
        assert sum(fill.qty for fill in fills) == 100
        assert session.get(PaperOrder, order["order_id"]).status == "FILLED"


def test_kill_switch_cancels_pending_orders_in_all_accounts(ledger):
    with ledger() as session:
        session.add(PaperAccount(id="other", initial_cash=100_000))
        session.flush()
        orders = [submit(session, order_amount=10_000), submit(session, account_id="other", order_amount=10_000)]
        for order in orders:
            age(session, order["order_id"])
        assert paper.set_kill_switch(session, True) == 2
        assert paper.run_fills(session)["fills_created"] == 0
        for account in (paper.DEFAULT_ACCOUNT, "other"):
            assert paper.account_summary(session, account)["frozen_cash"] == 0


def test_historical_replay_does_not_insert_before_persisted_future_sale(ledger):
    with ledger() as session:
        today = datetime.now().astimezone().date()
        session.add_all([
            PaperFill(order_id=900, symbol="000001.SZ", side="buy", exec_date=today - timedelta(days=2),
                      qty=100, price=10, amount=1000, fee=5),
            PaperFill(order_id=901, symbol="000001.SZ", side="sell", exec_date=today,
                      qty=100, price=10, amount=1000, fee=5),
            PaperOrder(symbol="000001.SZ", side="sell", order_amount=1000, target_qty=100,
                       decision_price=10, created_at=datetime.combine(today - timedelta(days=2), datetime.min.time()))])
        session.flush()
        assert paper.run_fills(session)["fills_created"] == 0
        assert not paper.account_summary(session)["integrity_warnings"]


def test_new_order_uses_local_decision_day_and_waits_for_next_session(ledger):
    with ledger() as session:
        order = submit(session, order_amount=1000)
        assert session.get(PaperOrder, order["order_id"]).created_at.date() == datetime.now().astimezone().date()
        assert paper.run_fills(session)["fills_created"] == 0


def test_missing_whole_day_is_visible_in_nav_and_current_valuation(ledger, monkeypatch):
    from app.db.models import TradeCalendar
    today = datetime.now().astimezone().date()
    bars = pl.DataFrame({"date": [today - timedelta(days=2)], "open": [10.], "close": [10.],
                         "volume": [1e7], "amount": [1e8]})
    monkeypatch.setattr(paper, "read_symbol_dataset", lambda *_: bars)
    with ledger() as session:
        session.add(PaperFill(order_id=999, symbol="000001.SZ", side="buy", exec_date=today - timedelta(days=2),
                              qty=100, price=10, amount=1000, fee=5))
        session.add_all([TradeCalendar(trade_date=today - timedelta(days=n), week=(today - timedelta(days=n)).weekday()) for n in (2, 1, 0)])
        session.flush()
        summary = paper.account_summary(session, asof=today)
        assert summary["equity"] is None
        assert summary["nav_series"][-1]["date"] == str(today)
        assert summary["nav_series"][-1]["equity"] is None


def test_buy_quantity_cannot_hide_risk_behind_tiny_amount(ledger):
    with ledger() as session:
        assert not submit(session, order_amount=1, target_qty=50_000)["ok"]


@pytest.mark.parametrize("symbol,allowed", [("000001.SZ", False), ("510300.SH", True)])
def test_same_day_resale_respects_settlement(ledger, symbol, allowed):
    with ledger() as session:
        session.add(PaperFill(order_id=999, symbol=symbol, side="buy", exec_date=datetime.now().astimezone().date() - timedelta(days=1),
                              qty=100, price=10, amount=1000, fee=5))
        session.flush()
        order = submit(session, symbol=symbol, side="sell", order_amount=1000, target_qty=100)
        age(session, order["order_id"])
        assert (paper.run_fills(session)["fills_created"] == 1) is allowed


def test_future_reservations_do_not_consume_historical_cash(ledger):
    with ledger() as session:
        assert submit(session, symbol="600000.SH", order_amount=500_000)["ok"]
        session.add(PaperOrder(symbol="000001.SZ", side="buy", order_amount=500_000,
                               created_at=datetime.now().astimezone().replace(tzinfo=None) - timedelta(days=2), decision_price=10))
        session.flush()
        paper.run_fills(session)
        filled = list(session.scalars(sa.select(PaperFill)))
        assert len(filled) == 1 and filled[0].qty == 49_900


@pytest.mark.parametrize("field,value", [("open", float("nan")), ("open", float("inf")),
                                        ("volume", float("nan")), ("amount", float("nan"))])
def test_nonfinite_execution_data_never_creates_fills(ledger, monkeypatch, field, value):
    with ledger() as session:
        order = submit(session, order_amount=10_000)
        age(session, order["order_id"])
        dates = [datetime.now().astimezone().date() - timedelta(days=n) for n in (2, 1, 0)]
        bars = pl.DataFrame({"date": dates, "open": [10.] * 3, "close": [10.] * 3,
                             "volume": [1e7] * 3, "amount": [1e8] * 3}).with_columns(pl.lit(value).alias(field))
        monkeypatch.setattr(paper, "read_symbol_dataset", lambda *_: bars)
        assert paper.run_fills(session)["fills_created"] == 0
        assert paper.account_summary(session)["cash"] == paper.INIT_CASH


def test_quantity_partial_fill_reserves_remaining_shares_and_survives_restart(ledger, monkeypatch):
    dates = [datetime.now().astimezone().date() - timedelta(days=n) for n in (2, 1, 0)]
    bars = pl.DataFrame({"date": dates, "open": [10., 20., 20.], "close": [10.] * 3,
                         "volume": [1e7] * 3, "amount": [2e8] * 3})
    monkeypatch.setattr(paper, "read_symbol_dataset", lambda *_: bars)
    with ledger() as session:
        order = submit(session, order_amount=20_000, target_qty=2000, algo="twap", split_days=2)
        age(session, order["order_id"])
        assert paper.run_fills(session, asof=dates[1])["fills_created"] == 1
        session.commit()
        stored = session.get(PaperOrder, order["order_id"])
        assert session.scalar(sa.select(PaperFill.qty)) == 1000
        assert stored.status == "PART_FILLED" and stored.reserved_cash >= 20_000
    with ledger() as session:
        assert paper.run_fills(session, asof=dates[2])["fills_created"] == 1
        assert paper.run_fills(session, asof=dates[2])["fills_created"] == 0
        assert sum(fill.qty for fill in session.scalars(sa.select(PaperFill))) == 2000
        assert paper.account_summary(session)["frozen_cash"] == 0


def test_comparison_uses_shared_nav_dates_and_requires_same_window_benchmark(ledger, monkeypatch):
    from app.trading.comparison import compare_accounts
    today = datetime.now().astimezone().date()
    days = [today - timedelta(days=n) for n in (2, 1, 0)]
    bars = pl.DataFrame({"date": days, "open": [10., 11., 12.], "close": [10., 11., 12.],
                         "volume": [1e7] * 3, "amount": [1e8] * 3})
    monkeypatch.setattr(paper, "read_symbol_dataset", lambda _, sym: pl.DataFrame() if sym == "000300.SH" else bars)
    with ledger() as session:
        session.add(PaperAccount(id="other", initial_cash=100_000))
        session.add_all([
            PaperFill(order_id=900, symbol="000001.SZ", side="buy", exec_date=days[0],
                      qty=100, price=10, amount=1000, fee=5),
            PaperFill(account_id="other", order_id=901, symbol="000001.SZ", side="buy", exec_date=days[1],
                      qty=200, price=10, amount=2000, fee=5)])
        session.flush()
        comparison = compare_accounts(session, [paper.DEFAULT_ACCOUNT, "other"])
        assert comparison["start"] == str(days[1]) and comparison["end"] == str(days[2])
        assert comparison["items"][0]["period_return"] == pytest.approx(100 / 1000095)
        assert comparison["items"][1]["period_return"] == pytest.approx(200 / 100195)
        assert all(row["benchmark_return"] is None and row["excess_return"] is None for row in comparison["items"])


def test_connected_gateway_report_cancel_and_short_rejection_use_real_ledger(ledger):
    from app.trading.gateway import OrderRequest, PaperBrokerGateway
    gateway = PaperBrokerGateway()
    gateway.connect()
    response = gateway.submit_order(OrderRequest("000001.SZ", "buy", 100, price=10, order_type="limit"))
    assert response.status == "PENDING"
    assert gateway.query_account().frozen_cash == 1005
    assert gateway.cancel_order(response.order_id)
    assert gateway.query_account().frozen_cash == 0
    assert not gateway.cancel_order(response.order_id)
    unsupported = gateway.submit_order(OrderRequest("IF2612", "open_short", 1))
    assert unsupported.status == "REJECTED"
    with ledger() as session:
        assert session.scalar(sa.select(sa.func.count()).select_from(PaperOrder)) == 1


def test_orders_without_quotes_cannot_starve_executable_orders(ledger, monkeypatch):
    bars = paper._bars("000001.SZ")
    monkeypatch.setattr(paper, "read_symbol_dataset", lambda _, symbol: bars if symbol == "000001.SZ" else pl.DataFrame())
    with ledger() as session:
        for _ in range(50):
            session.add(PaperOrder(symbol="600000.SH", side="buy", order_amount=1000,
                                   created_at=datetime.now().astimezone().replace(tzinfo=None) - timedelta(days=2), decision_price=10))
        session.add(PaperOrder(symbol="000001.SZ", side="buy", order_amount=10_000,
                               created_at=datetime.now().astimezone().replace(tzinfo=None) - timedelta(days=2), decision_price=10))
        session.flush()
        assert paper.run_fills(session, max_orders=50)["fills_created"] == 1


def test_missing_execution_session_cannot_shift_market_order_to_later_quote(ledger, monkeypatch):
    from app.db.models import TradeCalendar
    today = datetime.now().astimezone().date()
    bars = pl.DataFrame({"date": [today - timedelta(days=2), today], "open": [10., 10.],
                         "close": [10., 10.], "volume": [1e7, 1e7], "amount": [1e8, 1e8]})
    monkeypatch.setattr(paper, "read_symbol_dataset", lambda *_: bars)
    with ledger() as session:
        session.add_all([TradeCalendar(trade_date=today - timedelta(days=n), week=(today - timedelta(days=n)).weekday()) for n in (2, 1, 0)])
        order = submit(session, order_amount=10_000)
        age(session, order["order_id"])
        assert paper.run_fills(session)["fills_created"] == 0
        assert session.get(PaperOrder, order["order_id"]).status == "CANCELLED"


def test_three_day_twap_restart_skips_prior_fills_before_checking_watermark(ledger, monkeypatch):
    today = datetime.now().astimezone().date()
    days = [today - timedelta(days=n) for n in (3, 2, 1, 0)]
    bars = pl.DataFrame({"date": days, "open": [10.] * 4, "close": [10.] * 4,
                         "volume": [1e7] * 4, "amount": [1e8] * 4})
    monkeypatch.setattr(paper, "read_symbol_dataset", lambda *_: bars)
    with ledger() as session:
        result = submit(session, order_amount=30_000, target_qty=3000, algo="twap", split_days=3)
        order_id = result["order_id"]
        session.get(PaperOrder, order_id).created_at = datetime.combine(days[0], datetime.min.time())
        session.commit()
    for index, day in enumerate(days[1:], 1):
        with ledger() as session:
            assert paper.run_fills(session, asof=day)["fills_created"] == 1
            assert paper.run_fills(session, asof=day)["fills_created"] == 0
            session.commit()
            order = session.get(PaperOrder, order_id)
            assert order.status == ("FILLED" if index == 3 else "PART_FILLED")
            fills = list(session.scalars(sa.select(PaperFill).where(PaperFill.order_id == order_id).order_by(PaperFill.exec_date)))
            assert [fill.exec_date for fill in fills] == days[1:index + 1]
            assert sum(fill.qty for fill in fills) == index * 1000
    with ledger() as session:
        summary = paper.account_summary(session)
        assert summary["frozen_cash"] == 0
        assert summary["positions"]["000001.SZ"]["qty"] == 3000
        fills = list(session.scalars(sa.select(PaperFill)))
        assert summary["cash"] == pytest.approx(paper.INIT_CASH - sum(fill.amount + fill.fee for fill in fills), abs=.01)


def test_strategy_bound_account_conditional_inherits_version_and_keeps_trigger_source(ledger):
    with ledger() as session:
        session.add(PaperAccount(id="bound", initial_cash=100_000, strategy_version="v1"))
        session.commit()
        result = conditional.create_conditional_order(session, account_id="bound", symbol="000001.SZ", side="buy",
            trigger_type="price_ge", trigger_price=10, order_amount=10_000)
        triggered = conditional.evaluate_conditional_orders(session, {"000001.SZ": 11}, account_id="bound")
        assert len(triggered) == 1
        order = session.get(PaperOrder, triggered[0]["order_id"])
        source = session.get(conditional.ConditionalOrder, result["id"])
        assert source.status == "SUBMITTED" and source.fill_order_id == order.id
        assert order.account_id == "bound" and order.strategy_version == "v1"
        assert order.client_order_id == f"conditional:{source.id}"
        age(session, order.id)
        assert paper.run_fills(session, account_id="bound")["fills_created"] == 1
        assert source.status == "FILLED"
        assert paper.account_summary(session, "bound")["n_fills"] == 1
        assert paper.account_summary(session)["n_fills"] == 0


def test_strategy_bound_account_grid_inherits_version_and_keeps_bot_source(ledger):
    with ledger() as session:
        session.add(PaperAccount(id="bound", initial_cash=100_000, strategy_version="v1"))
        session.commit()
        result = conditional.create_grid_bot(session, account_id="bound", symbol="000001.SZ", lower_price=5,
            upper_price=15, grid_count=10, amount_per_grid=10_000)
        conditional.evaluate_grid_bots(session, {"000001.SZ": 12}, account_id="bound")
        triggered = conditional.evaluate_grid_bots(session, {"000001.SZ": 10}, account_id="bound")
        assert len(triggered) == 1
        order = session.get(PaperOrder, triggered[0]["order_id"])
        assert order.account_id == "bound" and order.strategy_version == "v1"
        assert order.grid_bot_id == result["id"]
        assert session.get(GridBot, result["id"]).total_trades == 0
        age(session, order.id)
        assert paper.run_fills(session, account_id="bound")["fills_created"] == 1
        assert session.get(GridBot, result["id"]).total_trades == 1
        assert paper.account_summary(session, "bound")["n_fills"] == 1
        assert paper.account_summary(session)["n_fills"] == 0
