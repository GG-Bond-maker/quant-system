"""Read-only audit probes; all database and market fixtures are isolated.

Run from the repository root:
    backend/.venv/Scripts/python.exe docs/audit-2026-10-27/standards-probes.py

Writes only the adjacent standards-probes.json evidence artifact.
"""
from __future__ import annotations

import asyncio
import json
import os
from datetime import date, datetime
from pathlib import Path
import sys
import tempfile
from unittest.mock import patch


def main() -> None:
    repo_root = Path(__file__).resolve().parents[2]
    sys.path.insert(0, str(repo_root / "backend"))
    with tempfile.TemporaryDirectory(prefix="aqp-review-standards-") as root:
        sandbox = Path(root)
        os.environ.update({
            "DATA_ROOT": str(sandbox / "data"),
            "MODEL_ROOT": str(sandbox / "models"),
            "LOG_DIR": str(sandbox / "logs"),
            "SQLITE_URL": f"sqlite+aiosqlite:///{(sandbox / 'audit.db').as_posix()}",
            "REDIS_ENABLED": "false",
            "WARM_OVERVIEW_ON_STARTUP": "false",
            "EVENING_ROUTINE_ENABLED": "false",
        })
        import pandas as pd
        import polars as pl
        import sqlalchemy as sa
        from sqlalchemy.orm import Session

        import app.db.models  # Registers all ORM tables; no production engine used.
        from app.db.models import ConditionalOrder, ExclusionItem, GridBot, PaperFill, PaperOrder
        from app.db.session import Base
        from app.trading import conditional, paper
        from app.trading.gateway import OrderRequest, PaperBrokerGateway
        from app.backtest.broker import Broker, BrokerConfig

        results: dict = {
            "isolation": "In-memory SQLite engines; patched market frames; temporary runtime paths; no production writes.",
            "fixture_dates": ["2026-10-23", "2026-10-26", "2026-10-27"],
        }
        bars = pl.DataFrame({
            "date": [date(2026, 10, 23), date(2026, 10, 26), date(2026, 10, 27)],
            "open": [10.0] * 3, "close": [10.0] * 3,
            "high": [10.0] * 3, "low": [10.0] * 3,
            "volume": [1e7] * 3, "amount": [1e8] * 3,
        })
        engines = []

        def new_engine():
            engine = sa.create_engine("sqlite:///:memory:")
            Base.metadata.create_all(engine)
            engines.append(engine)
            return engine

        gateway = PaperBrokerGateway()
        gateway.connect()
        results["gateway_missing_symbols"] = {}
        for name, action in (
            ("submit", lambda: gateway.submit_order(OrderRequest(symbol="000001.SZ", side="buy", volume=100))),
            ("cancel", lambda: gateway.cancel_order("1")),
        ):
            try:
                action()
            except Exception as exc:
                results["gateway_missing_symbols"][name] = {"type": type(exc).__name__, "message": str(exc)}

        with Session(new_engine()) as session, patch.object(paper, "read_symbol_dataset", return_value=bars):
            placed = []
            for _ in range(3):
                response = paper.place_order(
                    session, symbol="000001.SZ", side="buy", order_amount=500000,
                    algo="market", split_days=1, participation_cap=0.05,
                )
                placed.append(response)
                if response["ok"]:
                    session.get(PaperOrder, response["order_id"]).created_at = datetime(2026, 10, 23, 15)
            session.commit()
            fills_run = paper.run_fills(session)
            session.commit()
            summary = paper.account_summary(session)
            fills = session.query(PaperFill).all()
            expected_cash = paper.INIT_CASH - sum(fill.amount + fill.fee for fill in fills)
            results["pending_cash_not_reserved"] = {
                "placed_orders": placed, "fill_run": fills_run,
                "cash": summary["cash"], "market_value": summary["market_value"],
                "equity": summary["equity"],
                "cash_from_execution_prices_and_fees": round(expected_cash, 2),
                "extra_impact_deduction": round(expected_cash - summary["cash"], 2),
            }
            before = [{"order_id": order.id, "status": order.status} for order in session.query(PaperOrder).all()]
            second_run = paper.run_fills(session)
            session.commit()
            results["unfillable_round_lot_residual"] = {
                "before": before, "second_run": second_run,
                "after": [{"order_id": order.id, "status": order.status} for order in session.query(PaperOrder).all()],
            }

        with Session(new_engine()) as session:
            paper.set_kill_switch(session, True)
            co = ConditionalOrder(symbol="000001.SZ", side="buy", trigger_type="price_ge", trigger_price=10.0, order_amount=10000.0, status="ACTIVE")
            bot = GridBot(symbol="000001.SZ", lower_price=9.0, upper_price=12.0, grid_count=3, amount_per_grid=10000.0, status="RUNNING", last_trigger_price=10.0, total_trades=0, realized_pnl=0.0)
            session.add_all([co, bot])
            session.commit()
            conditionals = conditional.evaluate_conditional_orders(session, {"000001.SZ": 11.0})
            grids = conditional.evaluate_grid_bots(session, {"000001.SZ": 11.0})
            results["rejection_recorded_as_success"] = {
                "kill_switch": True, "conditional_result": conditionals,
                "conditional_state": co.status, "conditional_order_id": co.fill_order_id,
                "conditional_message": co.message, "grid_result": grids,
                "grid_total_trades": bot.total_trades, "grid_realized_pnl": bot.realized_pnl,
                "actual_paper_orders": session.query(PaperOrder).count(),
                "actual_fills": session.query(PaperFill).count(),
            }

        with Session(new_engine()) as session, patch.object(paper, "read_symbol_dataset", return_value=bars):
            session.add(PaperFill(order_id=1, symbol="000001.SZ", side="buy", exec_date=date(2026, 10, 23), qty=100, price=10.0, amount=1000.0, fee=5.0, impact_bps=0.0, participation=0.0, basis_bps=0.0))
            session.add(ExclusionItem(symbol="000001.SZ", category="st", reason="audit exclusion fixture", active=True))
            session.commit()
            results["buy_exclusion_blocks_exit"] = {
                "held_qty": paper._held_qty(session, "000001.SZ"),
                "sell_result": paper.place_order(session, symbol="000001.SZ", side="sell", order_amount=1000.0, algo="market", split_days=1, participation_cap=0.05),
                "emergency_liquidation_result": paper.emergency_liquidate_all(session),
            }

        broker = Broker(init_cash=1000000, config=BrokerConfig(enabled=True, impact_model="sqrt", slippage_bps=0, decay_bps=0, max_participation=0))
        broker.holdings["000001.SZ"] = 10000
        row = pd.Series({"open": 10.0, "close": 10.0, "high": 10.0, "low": 10.0, "volume": 1e7, "amount": 1e8, "limit_up": 11.0, "limit_down": 9.0, "is_halted": False})
        before_cash = broker.cash
        trade = broker.sell(date(2026, 10, 27), "000001.SZ", 10000, row)
        results["backtest_sell_impact_deducted_twice"] = {
            "trade_amount": trade.amount, "reported_trade_cost": trade.cost,
            "expected_cash_delta": trade.amount - trade.cost,
            "actual_cash_delta": broker.cash - before_cash,
            "unreported_extra_deduction": (trade.amount - trade.cost) - (broker.cash - before_cash),
            "reported_impact": broker.friction_costs["impact"],
        }

        from app.api.v1 import desk

        async def probe_api():
            evidence = {}
            for name in ("list_conditional_orders_api", "list_grid_bots_api"):
                try:
                    await getattr(desk, name)(_user={"sub": "audit", "role": "researcher"})
                except Exception as exc:
                    evidence[name] = {"type": type(exc).__name__, "message": str(exc)}
            return evidence

        results["api_missing_names"] = asyncio.run(probe_api())
        for engine in engines:
            engine.dispose()
        output = Path(__file__).with_suffix(".json")
        output.write_text(json.dumps(results, indent=2, ensure_ascii=False), encoding="utf-8")
        print(output)
        print(json.dumps(results, indent=2, ensure_ascii=True))


if __name__ == "__main__":
    main()
