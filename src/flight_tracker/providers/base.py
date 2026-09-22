"""Interface de coleta de preços."""

from typing import Protocol, runtime_checkable

from flight_tracker.models import PriceQuote, Route


class ProviderError(Exception):
    """Erro ao consultar o preço de uma rota."""


@runtime_checkable
class PriceProvider(Protocol):
    def get_price(self, route: Route) -> PriceQuote: ...
