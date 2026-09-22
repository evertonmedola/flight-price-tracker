from dataclasses import FrozenInstanceError
from datetime import UTC, date, datetime

import pytest

from flight_tracker.models import Alert, PriceQuote, Route


def _route(**overrides: object) -> Route:
    defaults: dict[str, object] = {
        "key": "GRU-LIS",
        "origin": "GRU",
        "destination": "LIS",
        "departure_date": date(2026, 12, 10),
        "return_date": date(2026, 12, 20),
        "target_price_cents": 350000,
    }
    defaults.update(overrides)
    return Route(**defaults)  # type: ignore[arg-type]


def _quote(**overrides: object) -> PriceQuote:
    defaults: dict[str, object] = {
        "route_key": "GRU-LIS",
        "price_cents": 400000,
        "currency": "BRL",
        "provider": "mock",
        "fetched_at": datetime(2026, 9, 1, 12, 0, tzinfo=UTC),
    }
    defaults.update(overrides)
    return PriceQuote(**defaults)  # type: ignore[arg-type]


class TestRoute:
    def test_instantiates_with_valid_values(self) -> None:
        route = _route()
        assert route.key == "GRU-LIS"
        assert route.origin == "GRU"
        assert route.destination == "LIS"
        assert route.return_date == date(2026, 12, 20)
        assert route.target_price_cents == 350000

    def test_allows_optional_fields_as_none(self) -> None:
        route = _route(return_date=None, target_price_cents=None)
        assert route.return_date is None
        assert route.target_price_cents is None

    def test_is_immutable(self) -> None:
        route = _route()
        with pytest.raises(FrozenInstanceError):
            route.origin = "XXX"  # type: ignore[misc]


class TestPriceQuote:
    def test_instantiates_with_valid_values(self) -> None:
        quote = _quote()
        assert quote.route_key == "GRU-LIS"
        assert quote.price_cents == 400000
        assert quote.currency == "BRL"
        assert quote.provider == "mock"

    def test_is_immutable(self) -> None:
        quote = _quote()
        with pytest.raises(FrozenInstanceError):
            quote.price_cents = 1  # type: ignore[misc]


class TestAlert:
    def test_instantiates_with_valid_values(self) -> None:
        route = _route()
        quote = _quote()
        alert = Alert(
            route=route,
            quote=quote,
            previous_price_cents=500000,
            reason="drop",
        )
        assert alert.route is route
        assert alert.quote is quote
        assert alert.previous_price_cents == 500000
        assert alert.reason == "drop"

    def test_is_immutable(self) -> None:
        alert = Alert(
            route=_route(),
            quote=_quote(),
            previous_price_cents=None,
            reason="below_target",
        )
        with pytest.raises(FrozenInstanceError):
            alert.reason = "drop"  # type: ignore[misc]
