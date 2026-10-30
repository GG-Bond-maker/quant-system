from __future__ import annotations

from datetime import date, timedelta

import pytest

from app.data import futures


def _bars(count: int = 80, *, start_price: float = 1000.0) -> list[dict[str, object]]:
    bars: list[dict[str, object]] = []
    current = date(2025, 1, 2)
    while len(bars) < count:
        if current.weekday() < 5:
            index = len(bars)
            close = start_price + index * 0.5
            bars.append(
                {
                    "date": current.isoformat(),
                    "open": close - 0.2,
                    "high": close + 1.0,
                    "low": close - 1.0,
                    "close": close,
                    "volume": 20_000 + index,
                    "open_interest": 30_000 + index,
                }
            )
        current += timedelta(days=1)
    return bars


def test_snapshot_uses_cache_instead_of_hardcoded_quote(monkeypatch):
    monkeypatch.setattr(futures, "fetch_sina_futures_quotes", lambda symbols=None: {})
    monkeypatch.setattr(
        futures,
        "get_futures_kline",
        lambda symbol, **kwargs: _bars(1) if symbol == "CF" else [],
    )

    snapshot = futures.get_futures_market_snapshot()

    assert snapshot["count"] == 1
    item = snapshot["items"][0]
    assert item["prefix"] == "CF"
    assert item["source"] == "cache"
    assert item["degraded"] is True
    assert item["spot_price"] is None
    assert item["basis"] is None
    assert item["basis_rate"] is None
    assert item["annualized_basis_rate"] is None


def test_snapshot_prefers_live_quote_and_does_not_fabricate_missing_products(monkeypatch):
    monkeypatch.setattr(
        futures,
        "fetch_sina_futures_quotes",
        lambda symbols=None: {
            "CF": {
                "symbol": "CF主力",
                "close": 15_000.0,
                "change_pct": 1.25,
                "volume": 120_000,
                "open_interest": 220_000,
                "source": "sina",
            }
        },
    )
    monkeypatch.setattr(futures, "get_futures_kline", lambda symbol, **kwargs: [])

    snapshot = futures.get_futures_market_snapshot()

    assert snapshot["count"] == 1
    assert snapshot["degraded"] is False
    assert snapshot["items"][0]["source"] == "sina"
    assert snapshot["items"][0]["close"] == 15_000.0
    assert snapshot["items"][0]["basis"] is None


@pytest.mark.parametrize(
    ("surface", "call"),
    [
        ("term_structure", lambda: futures.get_futures_term_structure("CF")),
        ("calendar_spread", lambda: futures.get_futures_calendar_spread("CF")),
        ("cot", lambda: futures.get_futures_cot("CF")),
        ("roll_yield_matrix", lambda: futures.get_roll_yield_matrix()),
        ("basis_history", lambda: futures.get_basis_history("CF")),
    ],
)
def test_unsupported_surfaces_are_explicitly_unavailable(monkeypatch, surface, call):
    monkeypatch.setattr(futures, "get_futures_kline", lambda symbol, **kwargs: _bars())

    result = call()

    assert result["available"] is False
    assert result["surface"] == surface
    assert result["data_status"] == "unavailable"
    assert result["modeled"] is False
    assert result["reason"]


def test_time_series_unavailable_payload_keeps_symbol(monkeypatch):
    monkeypatch.setattr(futures, "get_futures_kline", lambda symbol, **kwargs: _bars())

    result = futures.get_basis_history("CF")

    assert result["symbol"] == "CF"


def test_real_derived_profile_carries_modeled_flag_and_input_range(monkeypatch):
    bars = _bars()
    monkeypatch.setattr(futures, "get_futures_kline", lambda symbol, **kwargs: bars)

    result = futures.get_futures_instrument_profile("CF", days=60)

    assert result["available"] is True
    assert result["modeled"] is True
    assert result["data_status"] == "ok"
    assert result["input_start_date"] == bars[0]["date"]
    assert result["input_end_date"] == bars[-1]["date"]


def test_profile_does_not_invent_missing_open_interest(monkeypatch):
    bars = _bars()
    for bar in bars:
        bar.pop("open_interest")
    monkeypatch.setattr(futures, "get_futures_kline", lambda symbol, **kwargs: bars)

    result = futures.get_futures_instrument_profile("CF", days=60)

    assert result["open_interest_stats"]["latest_oi"] == 0
    assert result["open_interest_stats"]["oi_change"] == 0


def test_real_inter_commodity_spread_carries_modeled_flag_and_input_range(monkeypatch):
    bars = _bars()
    monkeypatch.setattr(futures, "get_futures_kline", lambda symbol, **kwargs: bars)

    result = futures.get_inter_commodity_spread("AU_AG")

    assert result["available"] is True
    assert result["modeled"] is True
    assert result["data_status"] == "ok"
    assert result["input_start_date"] == bars[0]["date"]
    assert result["input_end_date"] == bars[-1]["date"]


def test_real_parameter_sensitivity_carries_modeled_flag_and_input_range(monkeypatch):
    bars = _bars(160)
    monkeypatch.setattr(futures, "get_futures_kline", lambda symbol, **kwargs: bars)

    result = futures.get_parameter_sensitivity("CF")

    assert result["available"] is True
    assert result["modeled"] is True
    assert result["data_status"] == "ok"
    assert result["input_start_date"] == bars[0]["date"]
    assert result["input_end_date"] == bars[-1]["date"]
