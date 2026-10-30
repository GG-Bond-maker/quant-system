from __future__ import annotations

from collections.abc import Iterator

import pytest
from fastapi.testclient import TestClient

from app.api.v1 import futures as futures_api
from app.core.auth import create_jwt_token
from app.core.errors import ERR_NOT_FOUND, ERR_PARAMS
from app.main import app


@pytest.fixture(scope="module")
def client() -> Iterator[TestClient]:
    test_client = TestClient(app)
    try:
        yield test_client
    finally:
        test_client.close()


@pytest.fixture(scope="module")
def auth_headers() -> dict[str, str]:
    token, _ = create_jwt_token("futures-catalog-test", "researcher")
    return {"Authorization": f"Bearer {token}"}


def test_instruments_defaults_to_core_catalog(
    client: TestClient,
    auth_headers: dict[str, str],
) -> None:
    response = client.get("/api/v1/futures/instruments", headers=auth_headers)

    assert response.status_code == 200
    body = response.json()
    assert body["code"] == 0
    data = body["data"]
    assert data["count"] == 40
    assert {item["tier"] for item in data["items"]} == {"core"}

    cotton = next(item for item in data["items"] if item["code"] == "CF")
    assert cotton["name"] == "棉花"
    assert cotton["exchange"] == "CZCE"
    assert cotton["category"] == "agri"
    assert cotton["sina_symbol"] == "CF0"
    assert cotton["listing_date"] == "2004-06-01"
    assert cotton["has_spec"] is True
    assert cotton["data_status"] == "not_synced"
    assert cotton["quality_status"] == "unknown"
    assert cotton["trade_enabled"] is False


def test_instruments_can_list_extended_but_not_enable_them(
    client: TestClient,
    auth_headers: dict[str, str],
) -> None:
    response = client.get(
        "/api/v1/futures/instruments?tier=extended",
        headers=auth_headers,
    )

    assert response.status_code == 200
    items = response.json()["data"]["items"]
    assert len(items) == 43
    assert {item["tier"] for item in items} == {"extended"}
    assert all(item["trade_enabled"] is False for item in items)
    assert all(item["has_spec"] is False for item in items)


def test_instruments_supports_category_and_enabled_filters(
    client: TestClient,
    auth_headers: dict[str, str],
) -> None:
    category_response = client.get(
        "/api/v1/futures/instruments?category=agri",
        headers=auth_headers,
    )
    enabled_response = client.get(
        "/api/v1/futures/instruments?enabled=true",
        headers=auth_headers,
    )

    assert category_response.status_code == 200
    agri_items = category_response.json()["data"]["items"]
    assert len(agri_items) == 11
    assert {item["category"] for item in agri_items} == {"agri"}
    assert enabled_response.status_code == 200
    assert enabled_response.json()["data"]["items"] == []


def test_unknown_symbol_is_rejected(
    client: TestClient,
    auth_headers: dict[str, str],
) -> None:
    response = client.get(
        "/api/v1/futures/kline?symbol=ZZZZ&days=20",
        headers=auth_headers,
    )

    assert response.status_code == 200
    assert response.json()["code"] == ERR_NOT_FOUND


def test_kline_date_parameters_are_forwarded(
    client: TestClient,
    auth_headers: dict[str, str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls: list[dict[str, object]] = []

    def read_history(symbol: str, **kwargs: object) -> list[dict[str, object]]:
        calls.append({"symbol": symbol, **kwargs})
        return [
            {
                "date": "2024-01-02",
                "open": 15_000.0,
                "high": 15_100.0,
                "low": 14_900.0,
                "close": 15_050.0,
                "volume": 10_000.0,
                "open_interest": 20_000.0,
            }
        ]

    monkeypatch.setattr(futures_api, "get_futures_kline", read_history)

    response = client.get(
        "/api/v1/futures/kline"
        "?symbol=CF&days=77&continuous=true"
        "&start_date=2024-01-01&end_date=2024-01-31",
        headers=auth_headers,
    )

    assert response.status_code == 200
    assert response.json()["code"] == 0
    assert calls == [
        {
            "symbol": "CF",
            "days": 77,
            "continuous": True,
            "start_date": "2024-01-01",
            "end_date": "2024-01-31",
        }
    ]


def test_backtest_explicit_dates_take_precedence_over_history_days(
    client: TestClient,
    auth_headers: dict[str, str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    captured: dict[str, object] = {}

    def run_backtest(**kwargs: object) -> dict[str, object]:
        captured.update(kwargs)
        return {"status": "ok", "top_drawdowns": []}

    monkeypatch.setattr(futures_api, "futures_trade_status", lambda *_: (True, "ok"))
    monkeypatch.setattr(futures_api, "run_futures_cta_backtest", run_backtest)

    response = client.post(
        "/api/v1/futures/backtest",
        headers=auth_headers,
        json={
            "symbol": "IF",
            "strategy_type": "dual_ma",
            "start_date": "2015-01-01",
            "end_date": "2020-12-31",
            "history_days": 900,
        },
    )

    assert response.status_code == 200
    assert response.json()["code"] == 0
    assert captured["start_date"] == "2015-01-01"
    assert captured["end_date"] == "2020-12-31"
    assert captured["days"] is None


@pytest.mark.parametrize(
    ("method", "path", "json"),
    [
        (
            "post",
            "/api/v1/futures/backtest",
            {"symbol": "PB", "strategy_type": "dual_ma"},
        ),
        (
            "get",
            "/api/v1/futures/hedge-calc"
            "?portfolio_value=1000000&futures_price=15000&symbol=PB",
            None,
        ),
        (
            "get",
            "/api/v1/futures/param-sensitivity?symbol=PB&strategy_type=dual_ma",
            None,
        ),
    ],
)
def test_disabled_instrument_is_rejected_by_trade_endpoints(
    client: TestClient,
    auth_headers: dict[str, str],
    method: str,
    path: str,
    json: dict[str, object] | None,
) -> None:
    response = client.request(method, path, headers=auth_headers, json=json)

    assert response.status_code == 200
    assert response.json()["code"] == ERR_PARAMS
