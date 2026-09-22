import logging
from collections.abc import Callable
from datetime import date

import httpx
import pytest

from flight_tracker.models import Route
from flight_tracker.providers.base import ProviderError
from flight_tracker.providers.serpapi import SerpApiProvider

_API_KEY = "sk_test_super_secreta_123"


def _route() -> Route:
    return Route(
        key="GRU-LIS",
        origin="GRU",
        destination="LIS",
        departure_date=date(2026, 12, 10),
        return_date=date(2026, 12, 20),
        target_price_cents=None,
    )


def _one_way_route() -> Route:
    return Route(
        key="GIG-SCL",
        origin="GIG",
        destination="SCL",
        departure_date=date(2026, 11, 5),
        return_date=None,
        target_price_cents=None,
    )


def _client_with(handler: Callable[[httpx.Request], httpx.Response]) -> httpx.Client:
    return httpx.Client(transport=httpx.MockTransport(handler))


def _success_response(price_best: int = 3900, price_other: int = 4500) -> httpx.Response:
    return httpx.Response(
        200,
        json={
            "best_flights": [{"price": price_best}],
            "other_flights": [{"price": price_other}],
        },
    )


def _provider(handler: Callable[[httpx.Request], httpx.Response]) -> SerpApiProvider:
    return SerpApiProvider(api_key=_API_KEY, client=_client_with(handler))


class TestParsing:
    def test_parses_lowest_price(self) -> None:
        def handler(request: httpx.Request) -> httpx.Response:
            return _success_response(price_best=3900, price_other=3500)

        provider = _provider(handler)
        quote = provider.get_price(_route())

        assert quote.price_cents == 350000
        assert quote.currency == "BRL"
        assert quote.provider == "serpapi"
        assert quote.route_key == "GRU-LIS"

    def test_uses_best_flights_when_cheaper(self) -> None:
        def handler(request: httpx.Request) -> httpx.Response:
            return _success_response(price_best=3000, price_other=5000)

        provider = _provider(handler)
        quote = provider.get_price(_route())

        assert quote.price_cents == 300000

    def test_real_response_example_gru_lis(self) -> None:
        """Regressão: fixture de uma resposta real da SerpApi (GRU-LIS, ida
        e volta), validada manualmente contra a API. "price" vem em reais
        inteiros (5888 = R$ 5.888,00), não em centavos."""

        def handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(
                200,
                json={
                    "search_parameters": {
                        "departure_id": "GRU",
                        "arrival_id": "LIS",
                        "outbound_date": "2026-12-10",
                        "return_date": "2026-12-20",
                        "currency": "BRL",
                    },
                    "best_flights": [{"price": 5888, "type": "Round trip"}],
                    "other_flights": [{"price": 7062}],
                },
            )

        provider = _provider(handler)
        quote = provider.get_price(_route())

        assert quote.price_cents == 588800


class TestRequestParams:
    """Regressão: rota GIG-SCL (sem return_date) falhava em produção.

    A SerpApi define `type` como 1 (ida e volta) por padrão quando o
    parâmetro está ausente, e round trip exige `return_date`. Sem enviar
    `type` explicitamente, uma rota só de ida (sem return_date) resultava
    numa busca round-trip sem return_date na SerpApi -> erro 4xx.
    """

    def test_round_trip_route_sends_type_1_and_return_date(self) -> None:
        captured: dict[str, str] = {}

        def handler(request: httpx.Request) -> httpx.Response:
            captured.update(dict(request.url.params))
            return _success_response()

        provider = _provider(handler)
        provider.get_price(_route())

        assert captured["type"] == "1"
        assert captured["return_date"] == "2026-12-20"

    def test_one_way_route_sends_type_2_and_no_return_date(self) -> None:
        captured: dict[str, str] = {}

        def handler(request: httpx.Request) -> httpx.Response:
            captured.update(dict(request.url.params))
            return _success_response()

        provider = _provider(handler)
        provider.get_price(_one_way_route())

        assert captured["type"] == "2"
        assert "return_date" not in captured


class TestErrors:
    def test_4xx_raises_immediately(self) -> None:
        calls = []

        def handler(request: httpx.Request) -> httpx.Response:
            calls.append(request)
            return httpx.Response(400, json={"error": "bad request"})

        provider = _provider(handler)
        with pytest.raises(ProviderError):
            provider.get_price(_route())
        assert len(calls) == 1

    def test_error_field_raises(self) -> None:
        def handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(200, json={"error": "Invalid API key."})

        provider = _provider(handler)
        with pytest.raises(ProviderError):
            provider.get_price(_route())

    def test_no_flights_raises(self) -> None:
        def handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(200, json={"best_flights": [], "other_flights": []})

        provider = _provider(handler)
        with pytest.raises(ProviderError):
            provider.get_price(_route())

    def test_5xx_then_success_retries_once(self) -> None:
        calls = []

        def handler(request: httpx.Request) -> httpx.Response:
            calls.append(request)
            if len(calls) == 1:
                return httpx.Response(500)
            return _success_response(price_best=4000, price_other=4200)

        provider = _provider(handler)
        quote = provider.get_price(_route())

        assert len(calls) == 2
        assert quote.price_cents == 400000

    def test_5xx_twice_raises_provider_error(self) -> None:
        calls = []

        def handler(request: httpx.Request) -> httpx.Response:
            calls.append(request)
            return httpx.Response(503)

        provider = _provider(handler)
        with pytest.raises(ProviderError):
            provider.get_price(_route())
        assert len(calls) == 2

    def test_timeout_then_success_retries_once(self) -> None:
        calls = []

        def handler(request: httpx.Request) -> httpx.Response:
            calls.append(request)
            if len(calls) == 1:
                raise httpx.TimeoutException("timed out", request=request)
            return _success_response()

        provider = _provider(handler)
        provider.get_price(_route())
        assert len(calls) == 2


class TestRetryGuard:
    """Requisito SPEC §6.2."""

    def test_retry_not_blocked_by_guard_after_first_failure(self) -> None:
        calls = []

        def handler(request: httpx.Request) -> httpx.Response:
            calls.append(request)
            if len(calls) == 1:
                return httpx.Response(500)
            return _success_response()

        provider = _provider(handler)
        provider.get_price(_route())

        assert len(calls) == 2

    def test_guard_does_not_block_separate_call_after_failed_retry(self) -> None:
        calls = []

        def handler(request: httpx.Request) -> httpx.Response:
            calls.append(request)
            if len(calls) <= 2:
                return httpx.Response(500)
            return _success_response()

        provider = _provider(handler)

        with pytest.raises(ProviderError):
            provider.get_price(_route())
        assert len(calls) == 2

        # Chamada separada e subsequente não deve ser bloqueada por um guard
        # interno remanescente da tentativa anterior.
        quote = provider.get_price(_route())
        assert quote is not None
        assert len(calls) == 3


class TestNeverLogsApiKey:
    def test_api_key_never_in_logs_or_errors(self, caplog: pytest.LogCaptureFixture) -> None:
        def handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(503)

        provider = _provider(handler)

        with caplog.at_level(logging.DEBUG), pytest.raises(ProviderError) as exc_info:
            provider.get_price(_route())

        assert _API_KEY not in str(exc_info.value)
        for record in caplog.records:
            assert _API_KEY not in record.getMessage()
