import sqlite3
from datetime import UTC, datetime

import pytest

from flight_tracker.models import PriceQuote
from flight_tracker.storage import (
    init_db,
    insert_price_quote,
    read_latest_price,
    read_route_state,
    transaction,
    upsert_last_notified,
    upsert_last_queried_date,
)


@pytest.fixture
def conn() -> sqlite3.Connection:
    connection = sqlite3.connect(":memory:")
    init_db(connection)
    return connection


def _quote(price_cents: int, fetched_at: datetime, route_key: str = "GRU-LIS") -> PriceQuote:
    return PriceQuote(
        route_key=route_key,
        price_cents=price_cents,
        currency="BRL",
        provider="mock",
        fetched_at=fetched_at,
    )


class TestInitDb:
    def test_is_idempotent(self) -> None:
        connection = sqlite3.connect(":memory:")
        init_db(connection)
        init_db(connection)  # não deve levantar erro na segunda chamada

    def test_creates_expected_tables(self, conn: sqlite3.Connection) -> None:
        tables = {
            row[0]
            for row in conn.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall()
        }
        assert {"price_history", "route_state"} <= tables


class TestPriceHistory:
    def test_read_latest_price_returns_none_when_empty(self, conn: sqlite3.Connection) -> None:
        assert read_latest_price(conn, "GRU-LIS") is None

    def test_read_latest_price_returns_most_recent(self, conn: sqlite3.Connection) -> None:
        insert_price_quote(conn, _quote(500000, datetime(2026, 9, 1, tzinfo=UTC)))
        insert_price_quote(conn, _quote(400000, datetime(2026, 9, 2, tzinfo=UTC)))
        insert_price_quote(conn, _quote(450000, datetime(2026, 8, 30, tzinfo=UTC)))

        latest = read_latest_price(conn, "GRU-LIS")

        assert latest is not None
        assert latest.price_cents == 400000

    def test_read_latest_price_is_scoped_to_route(self, conn: sqlite3.Connection) -> None:
        insert_price_quote(conn, _quote(500000, datetime(2026, 9, 1, tzinfo=UTC), "GRU-LIS"))
        insert_price_quote(conn, _quote(100000, datetime(2026, 9, 2, tzinfo=UTC), "GRU-MIA"))

        latest = read_latest_price(conn, "GRU-LIS")

        assert latest is not None
        assert latest.price_cents == 500000


class TestRouteState:
    def test_read_route_state_returns_none_for_new_route(self, conn: sqlite3.Connection) -> None:
        assert read_route_state(conn, "GRU-LIS") is None

    def test_upsert_last_notified_roundtrip(self, conn: sqlite3.Connection) -> None:
        upsert_last_notified(conn, "GRU-LIS", 400000, datetime(2026, 9, 1, tzinfo=UTC))

        state = read_route_state(conn, "GRU-LIS")

        assert state is not None
        assert state.last_notified_price_cents == 400000

    def test_upsert_last_notified_overwrites_previous_value(self, conn: sqlite3.Connection) -> None:
        upsert_last_notified(conn, "GRU-LIS", 400000, datetime(2026, 9, 1, tzinfo=UTC))
        upsert_last_notified(conn, "GRU-LIS", 390000, datetime(2026, 9, 2, tzinfo=UTC))

        state = read_route_state(conn, "GRU-LIS")

        assert state is not None
        assert state.last_notified_price_cents == 390000

    def test_upsert_last_queried_date_roundtrip(self, conn: sqlite3.Connection) -> None:
        upsert_last_queried_date(conn, "GRU-LIS", "2026-09-22", datetime(2026, 9, 22, tzinfo=UTC))

        state = read_route_state(conn, "GRU-LIS")

        assert state is not None
        assert state.last_queried_date == "2026-09-22"

    def test_upserts_are_independent_per_field(self, conn: sqlite3.Connection) -> None:
        upsert_last_notified(conn, "GRU-LIS", 400000, datetime(2026, 9, 1, tzinfo=UTC))
        upsert_last_queried_date(conn, "GRU-LIS", "2026-09-22", datetime(2026, 9, 22, tzinfo=UTC))

        state = read_route_state(conn, "GRU-LIS")

        assert state is not None
        assert state.last_notified_price_cents == 400000
        assert state.last_queried_date == "2026-09-22"


class TestTransaction:
    def test_commits_on_success(self, conn: sqlite3.Connection) -> None:
        with transaction(conn):
            insert_price_quote(conn, _quote(400000, datetime(2026, 9, 1, tzinfo=UTC)))

        assert read_latest_price(conn, "GRU-LIS") is not None

    def test_rolls_back_on_exception(self, conn: sqlite3.Connection) -> None:
        with pytest.raises(RuntimeError), transaction(conn):
            insert_price_quote(conn, _quote(400000, datetime(2026, 9, 1, tzinfo=UTC)))
            raise RuntimeError("falha simulada")

        assert read_latest_price(conn, "GRU-LIS") is None
