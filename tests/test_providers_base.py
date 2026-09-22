from datetime import UTC, datetime

from flight_tracker.models import PriceQuote, Route
from flight_tracker.providers.base import PriceProvider, ProviderError


class _FakeProvider:
    """Implementação duck-typed, sem herdar de PriceProvider."""

    def get_price(self, route: Route) -> PriceQuote:
        return PriceQuote(
            route_key=route.key,
            price_cents=100000,
            currency="BRL",
            provider="fake",
            fetched_at=datetime(2026, 9, 22, tzinfo=UTC),
        )


def test_duck_typed_implementation_satisfies_protocol() -> None:
    provider: PriceProvider = _FakeProvider()
    assert isinstance(provider, PriceProvider)


def test_provider_error_is_an_exception() -> None:
    assert issubclass(ProviderError, Exception)
