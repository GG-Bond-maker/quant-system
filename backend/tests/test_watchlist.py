from __future__ import annotations

from datetime import date

from app.api.v1 import watchlist
from app.data import futures


def test_batch_valuation_uses_live_quote_or_real_cache_only(monkeypatch):
    monkeypatch.setattr(
        futures,
        "fetch_sina_futures_quotes",
        lambda symbols=None: {
            "CF": {
                "symbol": "CF主力",
                "close": 15_000.0,
                "change_pct": 1.2,
                "volume": 100_000,
                "open_interest": 200_000,
                "source": "sina",
            }
        },
    )

    def read_history(symbol, **kwargs):
        if symbol == "SR":
            return [
                {
                    "date": date.today().isoformat(),
                    "open": 5900.0,
                    "high": 6000.0,
                    "low": 5800.0,
                    "close": 5950.0,
                    "volume": 80_000,
                    "open_interest": 120_000,
                }
            ]
        return []

    monkeypatch.setattr(futures, "get_futures_kline", read_history)

    result = watchlist._batch_valuation(["CF", "SR", "MA"])

    assert result["CF"]["price"] == 15_000.0
    assert result["CF"]["source"] == "sina"
    assert result["SR"]["price"] == 5950.0
    assert result["SR"]["source"] == "cache"
    assert result["SR"]["degraded"] is True
    assert "MA" not in result


def test_batch_valuation_omits_futures_with_no_real_quote_or_cache(monkeypatch):
    monkeypatch.setattr(futures, "fetch_sina_futures_quotes", lambda symbols=None: {})
    monkeypatch.setattr(futures, "get_futures_kline", lambda symbol, **kwargs: [])

    assert watchlist._batch_valuation(["CF"]) == {}
