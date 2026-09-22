from datetime import date

from flight_tracker.models import Route
from flight_tracker.providers.mock import MockProvider


def _route(key: str = "GRU-LIS") -> Route:
    return Route(
        key=key,
        origin="GRU",
        destination="LIS",
        departure_date=date(2026, 12, 10),
        return_date=None,
        target_price_cents=None,
    )


class TestSequenceInjection:
    def test_sequence_injection(self) -> None:
        provider = MockProvider(sequences={"GRU-LIS": [500000, 400000]})
        route = _route()

        first = provider.get_price(route)
        second = provider.get_price(route)

        assert first.price_cents == 500000
        assert second.price_cents == 400000

    def test_sequence_repeats_last_value_when_exhausted(self) -> None:
        provider = MockProvider(sequences={"GRU-LIS": [500000, 400000]})
        route = _route()

        provider.get_price(route)
        provider.get_price(route)
        third = provider.get_price(route)

        assert third.price_cents == 400000

    def test_provider_field_is_mock(self) -> None:
        provider = MockProvider(sequences={"GRU-LIS": [500000]})
        quote = provider.get_price(_route())
        assert quote.provider == "mock"
        assert quote.currency == "BRL"


class TestDeterministicDefault:
    def test_deterministic_default_price(self) -> None:
        provider = MockProvider()
        route = _route()

        first = provider.get_price(route)
        second = provider.get_price(route)

        assert first.price_cents == second.price_cents

    def test_different_routes_get_different_default_prices(self) -> None:
        provider = MockProvider()

        price_a = provider.get_price(_route("GRU-LIS")).price_cents
        price_b = provider.get_price(_route("GRU-MIA")).price_cents

        assert price_a != price_b
