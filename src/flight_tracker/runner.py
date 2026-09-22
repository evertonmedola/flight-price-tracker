"""Orquestração: processamento de rotas, transação e códigos de saída."""

import logging
import os
import sqlite3
from datetime import UTC, date, datetime
from email.message import EmailMessage
from pathlib import Path

from flight_tracker.alerts import evaluate
from flight_tracker.config import ConfigError, load_routes
from flight_tracker.models import Alert, Route
from flight_tracker.notifier import build_message, send
from flight_tracker.providers.base import PriceProvider, ProviderError
from flight_tracker.providers.mock import MockProvider
from flight_tracker.providers.serpapi import SerpApiProvider
from flight_tracker.storage import (
    init_db,
    insert_price_quote,
    read_latest_price,
    read_route_state,
    transaction,
    upsert_last_notified,
    upsert_last_queried_date,
)

_logger = logging.getLogger(__name__)

EXIT_OK = 0
EXIT_FATAL = 1
EXIT_PARTIAL_PROVIDER_FAILURE = 2


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


def run(
    config_path: Path,
    db_path: str,
    dry_run: bool,
    provider: PriceProvider | None = None,
    today: date | None = None,
) -> int:
    """Executa uma rodada completa do tracker. Retorna o exit code (§7)."""
    try:
        routes = load_routes(config_path)
    except ConfigError as exc:
        _logger.error("erro de configuração: %s", exc)
        return EXIT_FATAL

    if provider is None:
        provider = _default_provider(dry_run)
        if provider is None:
            return EXIT_FATAL

    resolved_today = today if today is not None else datetime.now(UTC).date()

    if db_path != ":memory:":
        # sqlite3.connect não cria diretórios ausentes: numa checkout nova
        # (ex.: primeira execução no GitHub Actions), o diretório pai de
        # data/prices.db ainda não existe e a conexão falha com
        # "unable to open database file".
        Path(db_path).parent.mkdir(parents=True, exist_ok=True)

    conn = sqlite3.connect(db_path)
    try:
        init_db(conn)
        return _process_all_routes(conn, provider, routes, resolved_today, dry_run)
    except Exception:
        _logger.exception("falha fatal durante a execução")
        return EXIT_FATAL
    finally:
        conn.close()


def _default_provider(dry_run: bool) -> PriceProvider | None:
    if dry_run:
        return MockProvider()
    try:
        api_key = os.environ["SERPAPI_API_KEY"]
    except KeyError:
        _logger.error("variável de ambiente SERPAPI_API_KEY ausente")
        return None
    return SerpApiProvider(api_key=api_key)


def _process_all_routes(
    conn: sqlite3.Connection,
    provider: PriceProvider,
    routes: list[Route],
    today: date,
    dry_run: bool,
) -> int:
    had_provider_error = False
    alerts: list[Alert] = []

    with transaction(conn):
        for route in routes:
            try:
                alert = process_route(conn, provider, route, today)
            except ProviderError:
                _logger.warning("provider falhou para a rota %s", route.key)
                had_provider_error = True
                continue
            if alert is not None:
                alerts.append(alert)

        if alerts:
            message = build_message(alerts)
            if dry_run:
                _print_dry_run_email(message)
            else:
                send(message)  # se falhar, a exceção propaga e desfaz a transação (§6.3)
            for alert in alerts:
                upsert_last_notified(
                    conn, alert.route.key, alert.quote.price_cents, datetime.now(UTC)
                )
        elif dry_run:
            print("Nenhuma queda de preço detectada nesta execução.")

    return EXIT_PARTIAL_PROVIDER_FAILURE if had_provider_error else EXIT_OK


def _print_dry_run_email(message: EmailMessage) -> None:
    subject = message["Subject"]
    body = message.get_body(preferencelist=("plain",))
    content = body.get_content() if body is not None else ""
    print(f"Assunto: {subject}\n\n{content}")
