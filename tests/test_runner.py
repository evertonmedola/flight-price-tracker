import sqlite3
from collections.abc import Iterator
from datetime import date
from pathlib import Path

import httpx
import pytest

from flight_tracker.models import Route
from flight_tracker.providers.base import ProviderError
from flight_tracker.providers.mock import MockProvider
from flight_tracker.providers.serpapi import SerpApiProvider
from flight_tracker.runner import process_route, run
from flight_tracker.storage import init_db

_TODAY = date(2026, 9, 22)


@pytest.fixture
def conn() -> Iterator[sqlite3.Connection]:
    connection = sqlite3.connect(":memory:")
    init_db(connection)
    yield connection
    connection.close()


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


class _FlakyProvider:
    """MockProvider que levanta ProviderError para uma rota específica."""

    def __init__(self, sequences: dict[str, list[int]], failing_route_key: str) -> None:
        self._inner = MockProvider(sequences=sequences)
        self._failing_route_key = failing_route_key

    def get_price(self, route: Route):  # type: ignore[no-untyped-def]
        if route.key == self._failing_route_key:
            raise ProviderError(f"falha simulada para {route.key}")
        return self._inner.get_price(route)


def _write_routes_yaml(tmp_path: Path, routes_yaml: str) -> Path:
    path = tmp_path / "routes.yaml"
    path.write_text(routes_yaml, encoding="utf-8")
    return path


_TWO_ROUTES_YAML = """\
routes:
  - key: "GRU-LIS"
    origin: "GRU"
    destination: "LIS"
    departure_date: "2026-12-10"
  - key: "GRU-MIA"
    origin: "GRU"
    destination: "MIA"
    departure_date: "2027-01-15"
"""

_ONE_ROUTE_YAML = """\
routes:
  - key: "GRU-LIS"
    origin: "GRU"
    destination: "LIS"
    departure_date: "2026-12-10"
"""


class TestRunExitCodes:
    def test_exit_0_all_ok(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        import flight_tracker.runner as runner_module

        sent: list[object] = []
        monkeypatch.setattr(runner_module, "send", lambda message: sent.append(message))

        config_path = _write_routes_yaml(tmp_path, _TWO_ROUTES_YAML)
        provider = MockProvider(
            sequences={"GRU-LIS": [500000, 400000], "GRU-MIA": [200000, 150000]}
        )

        code = run(
            config_path=config_path,
            db_path=str(tmp_path / "prices.db"),
            dry_run=False,
            provider=provider,
            today=_TODAY,
        )

        assert code == 0

    def test_exit_2_partial_provider_failure(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        import flight_tracker.runner as runner_module

        monkeypatch.setattr(runner_module, "send", lambda message: None)

        config_path = _write_routes_yaml(tmp_path, _TWO_ROUTES_YAML)
        provider = _FlakyProvider(sequences={"GRU-MIA": [200000]}, failing_route_key="GRU-LIS")

        db_path = str(tmp_path / "prices.db")
        code = run(
            config_path=config_path,
            db_path=db_path,
            dry_run=False,
            provider=provider,
            today=_TODAY,
        )

        assert code == 2
        conn = sqlite3.connect(db_path)
        try:
            rows = conn.execute(
                "SELECT COUNT(*) FROM price_history WHERE route_key = 'GRU-MIA'"
            ).fetchone()
            assert rows[0] == 1  # a rota que funcionou foi persistida (commit ocorre no exit 2)
        finally:
            conn.close()

    def test_exit_1_config_error(self, tmp_path: Path) -> None:
        config_path = tmp_path / "routes.yaml"
        config_path.write_text("not_routes: []\n", encoding="utf-8")

        code = run(
            config_path=config_path,
            db_path=str(tmp_path / "prices.db"),
            dry_run=False,
            provider=MockProvider(),
            today=_TODAY,
        )

        assert code == 1

    def test_exit_1_smtp_error(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        import flight_tracker.runner as runner_module

        def failing_send(message: object) -> None:
            raise RuntimeError("falha simulada de SMTP")

        monkeypatch.setattr(runner_module, "send", failing_send)

        config_path = _write_routes_yaml(tmp_path, _ONE_ROUTE_YAML)
        # Preço inicial e depois queda, para garantir que um alerta é gerado.
        provider = MockProvider(sequences={"GRU-LIS": [500000]})

        db_path = str(tmp_path / "prices.db")
        conn = sqlite3.connect(db_path)
        init_db(conn)
        conn.execute(
            "INSERT INTO price_history (route_key, price_cents, currency, provider, fetched_at) "
            "VALUES ('GRU-LIS', 900000, 'BRL', 'mock', '2026-09-01T00:00:00+00:00')"
        )
        conn.commit()
        conn.close()

        code = run(
            config_path=config_path,
            db_path=db_path,
            dry_run=False,
            provider=provider,
            today=_TODAY,
        )

        assert code == 1

        # Requisito §6.3: nada novo foi persistido nesta execução.
        conn = sqlite3.connect(db_path)
        try:
            rows = conn.execute("SELECT COUNT(*) FROM price_history").fetchone()
            assert rows[0] == 1  # só a linha inserida manualmente antes da execução
            state_rows = conn.execute("SELECT COUNT(*) FROM route_state").fetchone()
            assert state_rows[0] == 0
        finally:
            conn.close()


class TestRunDryRun:
    def test_dry_run_requires_no_env_vars(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
    ) -> None:
        for var in ("SERPAPI_API_KEY", "SMTP_USER", "SMTP_APP_PASSWORD", "ALERT_TO"):
            monkeypatch.delenv(var, raising=False)

        config_path = _write_routes_yaml(tmp_path, _ONE_ROUTE_YAML)

        code = run(
            config_path=config_path,
            db_path=":memory:",
            dry_run=True,
            today=_TODAY,
        )

        assert code == 0

    def test_dry_run_prints_email_or_no_drop_message(
        self, tmp_path: Path, capsys: pytest.CaptureFixture[str]
    ) -> None:
        config_path = _write_routes_yaml(tmp_path, _ONE_ROUTE_YAML)

        code = run(
            config_path=config_path,
            db_path=":memory:",
            dry_run=True,
            today=_TODAY,
        )

        assert code == 0
        captured = capsys.readouterr()
        assert "nenhuma queda" in captured.out.lower()


class TestDbPathParentDirCreated:
    """Regressão: sqlite3.connect não cria diretórios ausentes.

    Reproduz o que aconteceu na primeira execução real no GitHub Actions:
    checkout novo, data/ nunca commitada (nunca havia data/prices.db no
    repo), db_path aponta para um diretório que ainda não existe ->
    sqlite3.OperationalError: unable to open database file.
    """

    def test_creates_missing_parent_directory(self, tmp_path: Path) -> None:
        config_path = _write_routes_yaml(tmp_path, _ONE_ROUTE_YAML)
        nested_db_path = tmp_path / "data" / "nested" / "prices.db"
        assert not nested_db_path.parent.exists()

        code = run(
            config_path=config_path,
            db_path=str(nested_db_path),
            dry_run=False,
            provider=MockProvider(),
            today=_TODAY,
        )

        assert code == 0
        assert nested_db_path.exists()
