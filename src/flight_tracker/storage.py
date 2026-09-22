"""Persistência em SQLite: schema, transação e queries de leitura/escrita."""

import sqlite3
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import datetime

from flight_tracker.models import PriceQuote

_SCHEMA = """
CREATE TABLE IF NOT EXISTS price_history (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    route_key TEXT NOT NULL,
    price_cents INTEGER NOT NULL,
    currency TEXT NOT NULL,
    provider TEXT NOT NULL,
    fetched_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS route_state (
    route_key TEXT PRIMARY KEY,
    last_notified_price_cents INTEGER,
    last_queried_date TEXT,
    updated_at TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_price_history_route_fetched
    ON price_history (route_key, fetched_at DESC);
"""


@dataclass(frozen=True)
class RouteState:
    route_key: str
    last_notified_price_cents: int | None
    last_queried_date: str | None


def init_db(conn: sqlite3.Connection) -> None:
    conn.executescript(_SCHEMA)
    conn.commit()


@contextmanager
def transaction(conn: sqlite3.Connection) -> Iterator[sqlite3.Connection]:
    """Executa o bloco numa única transação: COMMIT no sucesso, ROLLBACK na exceção."""
    with conn:
        yield conn


def read_latest_price(conn: sqlite3.Connection, route_key: str) -> PriceQuote | None:
    row = conn.execute(
        """
        SELECT route_key, price_cents, currency, provider, fetched_at
        FROM price_history
        WHERE route_key = ?
        ORDER BY fetched_at DESC
        LIMIT 1
        """,
        (route_key,),
    ).fetchone()
    if row is None:
        return None
    return PriceQuote(
        route_key=row[0],
        price_cents=row[1],
        currency=row[2],
        provider=row[3],
        fetched_at=datetime.fromisoformat(row[4]),
    )


def insert_price_quote(conn: sqlite3.Connection, quote: PriceQuote) -> None:
    conn.execute(
        """
        INSERT INTO price_history (route_key, price_cents, currency, provider, fetched_at)
        VALUES (?, ?, ?, ?, ?)
        """,
        (
            quote.route_key,
            quote.price_cents,
            quote.currency,
            quote.provider,
            quote.fetched_at.isoformat(),
        ),
    )


def read_route_state(conn: sqlite3.Connection, route_key: str) -> RouteState | None:
    row = conn.execute(
        """
        SELECT route_key, last_notified_price_cents, last_queried_date
        FROM route_state
        WHERE route_key = ?
        """,
        (route_key,),
    ).fetchone()
    if row is None:
        return None
    return RouteState(route_key=row[0], last_notified_price_cents=row[1], last_queried_date=row[2])


def upsert_last_notified(
    conn: sqlite3.Connection, route_key: str, price_cents: int, now: datetime
) -> None:
    conn.execute(
        """
        INSERT INTO route_state (route_key, last_notified_price_cents, updated_at)
        VALUES (?, ?, ?)
        ON CONFLICT(route_key) DO UPDATE SET
            last_notified_price_cents = excluded.last_notified_price_cents,
            updated_at = excluded.updated_at
        """,
        (route_key, price_cents, now.isoformat()),
    )


def upsert_last_queried_date(
    conn: sqlite3.Connection, route_key: str, date_iso: str, now: datetime
) -> None:
    conn.execute(
        """
        INSERT INTO route_state (route_key, last_queried_date, updated_at)
        VALUES (?, ?, ?)
        ON CONFLICT(route_key) DO UPDATE SET
            last_queried_date = excluded.last_queried_date,
            updated_at = excluded.updated_at
        """,
        (route_key, date_iso, now.isoformat()),
    )
