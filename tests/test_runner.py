import sqlite3
from datetime import date

import httpx
import pytest

from flight_tracker.models import Route
from flight_tracker.providers.base import ProviderError
from flight_tracker.providers.mock import MockProvider
from flight_tracker.providers.serpapi import SerpApiProvider
from flight_tracker.runner import process_route
from flight_tracker.storage import init_db

_TODAY = date(2026, 9, 22)


@pytest.fixture
def conn() -> sqlite3.Connection:
    connection = sqlite3.connect(":memory:")
    init_db(connection)
    return connection


def _route(
    departure_date: date = date(2026, 12, 10), target_price_cents: int | None = None
) -> Route:
    return Route(
        key="GRU-LIS",
        origin="GRU",
        destination="LIS",
        departure_date=departure_date,
        return_date=None,
        target_price_cents=target_price_cents,
    )


def _counting_serpapi_provider(
    price_cents_sequence: list[int],
) -> tuple[SerpApiProvider, list[httpx.Request]]:
    calls: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(request)
        index = min(len(calls) - 1, len(price_cents_sequence) - 1)
        price = price_cents_sequence[index] / 100
        return httpx.Response(200, json={"best_flights": [{"price": price}]})

    client = httpx.Client(transport=httpx.MockTransport(handler))
    return SerpApiProvider(api_key="test-key", client=client), calls


class TestPastDepartureSkipped:
    def test_route_skipped_when_departure_in_past(self, conn: sqlite3.Connection) -> None:
        route = _route(departure_date=date(2026, 1, 1))
        provider = MockProvider(sequences={"GRU-LIS": [400000]})

        alert = process_route(conn, provider, route, today=_TODAY)

        assert alert is None
        # Nenhuma cotação deve ter sido gravada para uma rota pulada.
        rows = conn.execute("SELECT COUNT(*) FROM price_history").fetchone()
        assert rows[0] == 0


class TestPreviousReadBeforeInsert:
    def test_previous_read_before_insert_via_spy(
        self, conn: sqlite3.Connection, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        import flight_tracker.runner as runner_module
        import flight_tracker.storage as storage_module

        call_order: list[str] = []
        real_read_latest_price = storage_module.read_latest_price
        real_insert_price_quote = storage_module.insert_price_quote

        def spy_read_latest_price(conn_: sqlite3.Connection, route_key: str):  # type: ignore[no-untyped-def]
            call_order.append("read_latest_price")
            return real_read_latest_price(conn_, route_key)

        def spy_insert_price_quote(conn_: sqlite3.Connection, quote):  # type: ignore[no-untyped-def]
            call_order.append("insert_price_quote")
            return real_insert_price_quote(conn_, quote)

        monkeypatch.setattr(runner_module, "read_latest_price", spy_read_latest_price)
        monkeypatch.setattr(runner_module, "insert_price_quote", spy_insert_price_quote)

        route = _route()
        provider = MockProvider(sequences={"GRU-LIS": [400000]})

        process_route(conn, provider, route, today=_TODAY)

        assert call_order == ["read_latest_price", "insert_price_quote"]

    def test_drop_is_detected_across_two_executions(self, conn: sqlite3.Connection) -> None:
        route = _route()
        provider = MockProvider(sequences={"GRU-LIS": [500000, 400000]})

        first = process_route(conn, provider, route, today=_TODAY)
        second = process_route(conn, provider, route, today=_TODAY)

        assert first is None  # primeira cotação, sem anterior
        assert second is not None
        assert second.reason == "drop"


class TestDailyGuard:
    """Requisito SPEC §6.4."""

    def test_daily_guard_blocks_second_real_call(self, conn: sqlite3.Connection) -> None:
        provider, calls = _counting_serpapi_provider([400000])
        route = _route()

        process_route(conn, provider, route, today=_TODAY)
        process_route(conn, provider, route, today=_TODAY)

        assert len(calls) == 1

    def test_daily_guard_releases_next_day(self, conn: sqlite3.Connection) -> None:
        provider, calls = _counting_serpapi_provider([400000, 390000])
        route = _route()

        process_route(conn, provider, route, today=_TODAY)
        next_day = date(2026, 9, 23)
        process_route(conn, provider, route, today=next_day)

        assert len(calls) == 2

    def test_daily_guard_does_not_affect_mock_provider(self, conn: sqlite3.Connection) -> None:
        route = _route()
        provider = MockProvider(sequences={"GRU-LIS": [500000, 400000]})

        process_route(conn, provider, route, today=_TODAY)
        second = process_route(conn, provider, route, today=_TODAY)

        # Se o guard bloqueasse o MockProvider, a segunda chamada seria
        # pulada e não haveria segunda cotação para detectar a queda.
        assert second is not None
        assert second.reason == "drop"


class TestProviderErrorPropagates:
    def test_provider_error_is_not_swallowed_by_process_route(
        self, conn: sqlite3.Connection
    ) -> None:
        def handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(503)

        client = httpx.Client(transport=httpx.MockTransport(handler))
        provider = SerpApiProvider(api_key="test-key", client=client)
        route = _route()

        with pytest.raises(ProviderError):
            process_route(conn, provider, route, today=_TODAY)
