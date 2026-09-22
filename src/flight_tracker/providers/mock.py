"""Provider fake: dados determinísticos, para testes e dry-run."""

import hashlib
from datetime import UTC, datetime

from flight_tracker.models import PriceQuote, Route


class MockProvider:
    """Preço determinístico por rota, ou uma sequência fixa injetada nos testes."""

    def __init__(self, sequences: dict[str, list[int]] | None = None) -> None:
        self._sequences = {key: list(values) for key, values in (sequences or {}).items()}
        self._cursor: dict[str, int] = {}

    def get_price(self, route: Route) -> PriceQuote:
        price_cents = self._next_price(route.key)
        return PriceQuote(
            route_key=route.key,
            price_cents=price_cents,
            currency="BRL",
            provider="mock",
            fetched_at=datetime.now(UTC),
        )

    def _next_price(self, route_key: str) -> int:
        sequence = self._sequences.get(route_key)
        if not sequence:
            return self._default_price(route_key)

        index = self._cursor.get(route_key, 0)
        price = sequence[min(index, len(sequence) - 1)]
        self._cursor[route_key] = index + 1
        return price

    @staticmethod
    def _default_price(route_key: str) -> int:
        digest = hashlib.sha256(route_key.encode("utf-8")).hexdigest()
        # Preço plausível entre R$ 1.000,00 e R$ 6.000,00 (em centavos).
        return 100000 + (int(digest[:8], 16) % 500000)
