"""Provider real: preços do Google Flights via SerpApi."""

import logging
from datetime import UTC, datetime
from typing import Any

import httpx

from flight_tracker.models import PriceQuote, Route
from flight_tracker.providers.base import ProviderError

_BASE_URL = "https://serpapi.com/search"
_TIMEOUT_SECONDS = 30.0

_logger = logging.getLogger(__name__)

# httpx loga a URL completa da requisição em nível INFO, e nossa URL carrega
# api_key na query string. Elevar o nível evita que a chave vaze nos logs.
logging.getLogger("httpx").setLevel(logging.WARNING)
logging.getLogger("httpcore").setLevel(logging.WARNING)


class _RetryableError(Exception):
    """Erro interno que autoriza uma segunda tentativa (5xx ou timeout)."""


class SerpApiProvider:
    """Consulta o menor preço de uma rota via `engine=google_flights` da SerpApi."""

    def __init__(self, api_key: str, client: httpx.Client | None = None) -> None:
        self._api_key = api_key
        self._client = client or httpx.Client(timeout=_TIMEOUT_SECONDS)

    def get_price(self, route: Route) -> PriceQuote:
        params = self._build_params(route)
        data = self._request_with_retry(params)
        price_cents = self._extract_lowest_price_cents(data)
        return PriceQuote(
            route_key=route.key,
            price_cents=price_cents,
            currency="BRL",
            provider="serpapi",
            fetched_at=datetime.now(UTC),
        )

    def _build_params(self, route: Route) -> dict[str, str]:
        params = {
            "engine": "google_flights",
            "departure_id": route.origin,
            "arrival_id": route.destination,
            "outbound_date": route.departure_date.isoformat(),
            "currency": "BRL",
            "hl": "pt",
            "api_key": self._api_key,
        }
        # A SerpApi assume type=1 (ida e volta) quando o parâmetro está
        # ausente, e round trip exige return_date. Sem declarar type=2 para
        # rotas só de ida, a busca vira um round trip sem return_date e a
        # SerpApi rejeita com erro 4xx.
        if route.return_date is not None:
            params["type"] = "1"
            params["return_date"] = route.return_date.isoformat()
        else:
            params["type"] = "2"
        return params

    def _request_with_retry(self, params: dict[str, str]) -> dict[str, Any]:
        try:
            return self._request_once(params)
        except _RetryableError:
            _logger.warning("primeira tentativa falhou, tentando novamente")
            try:
                return self._request_once(params)
            except _RetryableError as exc:
                raise ProviderError("falha ao consultar SerpApi após retry") from exc

    def _request_once(self, params: dict[str, str]) -> dict[str, Any]:
        try:
            response = self._client.get(_BASE_URL, params=params)
        except httpx.TimeoutException as exc:
            raise _RetryableError("timeout na requisição") from exc
        except httpx.HTTPError as exc:
            raise _RetryableError("erro de conexão") from exc

        if response.status_code >= 500:
            raise _RetryableError(f"HTTP {response.status_code}")
        if response.status_code >= 400:
            raise ProviderError(f"SerpApi respondeu HTTP {response.status_code}")

        data: dict[str, Any] = response.json()
        if "error" in data:
            _logger.error("SerpApi retornou campo 'error' na resposta")
            raise ProviderError("SerpApi retornou um erro na resposta")
        return data

    @staticmethod
    def _extract_lowest_price_cents(data: dict[str, Any]) -> int:
        # A SerpApi retorna "price" em reais inteiros (ex.: 5888 = R$ 5.888,00),
        # não em centavos. Multiplicar por 100 aqui é o que converte para o
        # formato interno do sistema (PriceQuote.price_cents); não remover.
        prices: list[float] = []
        for section in ("best_flights", "other_flights"):
            for flight in data.get(section) or []:
                price = flight.get("price")
                if isinstance(price, int | float):
                    prices.append(price)
        if not prices:
            raise ProviderError("resposta da SerpApi sem voos válidos")
        return round(min(prices) * 100)
