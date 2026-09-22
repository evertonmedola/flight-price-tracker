"""Orquestração: processamento de rotas, transação e códigos de saída."""

import logging
import sqlite3
from datetime import UTC, date, datetime

from flight_tracker.alerts import evaluate
from flight_tracker.models import Alert, Route
from flight_tracker.providers.base import PriceProvider
from flight_tracker.providers.serpapi import SerpApiProvider
from flight_tracker.storage import (
    insert_price_quote,
    read_latest_price,
    read_route_state,
    upsert_last_queried_date,
)

_logger = logging.getLogger(__name__)


def process_route(
    conn: sqlite3.Connection,
    provider: PriceProvider,
    route: Route,
    today: date,
) -> Alert | None:
    """Processa uma rota: lê o histórico, consulta o provider e avalia o alerta.

    Segue a ordem exigida por SPEC §4: leitura do preço anterior antes do
    INSERT da cotação atual (§6.1), e o guard diário entre execuções só se
    aplica ao SerpApiProvider (§6.4). `ProviderError` do provider propaga
    para o chamador, que decide como tratar (ver §7, exit code 2).
    """
    if route.departure_date < today:
        _logger.info("rota %s pulada: data de partida no passado", route.key)
        return None

    # Passo 1 (§4/§6.1): ler o preço anterior antes de qualquer escrita.
    previous = read_latest_price(conn, route.key)

    # Passo 2 (§4/§6.4): guard diário entre execuções, só para SerpApiProvider.
    if isinstance(provider, SerpApiProvider):
        state = read_route_state(conn, route.key)
        if state is not None and state.last_queried_date == today.isoformat():
            _logger.info("rota %s pulada: já consultada hoje (guard diário)", route.key)
            return None

    # Passo 3: consulta ao provider; ProviderError propaga para o chamador.
    quote = provider.get_price(route)
    insert_price_quote(conn, quote)
    if isinstance(provider, SerpApiProvider):
        upsert_last_queried_date(conn, route.key, today.isoformat(), datetime.now(UTC))

    # Passo 4: último preço notificado.
    state = read_route_state(conn, route.key)
    last_notified = state.last_notified_price_cents if state is not None else None

    # Passo 5: avaliação da regra de alerta.
    return evaluate(route, quote, previous, last_notified)
