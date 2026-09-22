from datetime import UTC, date, datetime
from email.message import EmailMessage

import pytest

from flight_tracker.models import Alert, PriceQuote, Route
from flight_tracker.notifier import build_message, send


def _route(target_price_cents: int | None = 350000) -> Route:
    return Route(
        key="GRU-LIS",
        origin="GRU",
        destination="LIS",
        departure_date=date(2026, 12, 10),
        return_date=date(2026, 12, 20),
        target_price_cents=target_price_cents,
    )


def _quote(price_cents: int) -> PriceQuote:
    return PriceQuote(
        route_key="GRU-LIS",
        price_cents=price_cents,
        currency="BRL",
        provider="mock",
        fetched_at=datetime(2026, 9, 22, tzinfo=UTC),
    )


def _alert(
    reason: str = "drop", previous_price_cents: int | None = 500000, price_cents: int = 400000
) -> Alert:
    return Alert(
        route=_route(),
        quote=_quote(price_cents),
        previous_price_cents=previous_price_cents,
        reason=reason,  # type: ignore[arg-type]
    )


class TestSubject:
    def test_subject_singular(self) -> None:
        message = build_message([_alert()])
        assert message["Subject"] == "✈️ 1 rota com preço em queda"

    def test_subject_plural(self) -> None:
        message = build_message([_alert(), _alert()])
        assert message["Subject"] == "✈️ 2 rotas com preço em queda"


class TestBody:
    def test_html_table_contains_all_fields(self) -> None:
        message = build_message([_alert(reason="drop_and_below_target")])
        html = message.get_body(preferencelist=("html",))
        assert html is not None
        content = html.get_content()

        assert "GRU" in content
        assert "LIS" in content
        assert "R$ 5.000,00" in content  # anterior
        assert "R$ 4.000,00" in content  # atual
        assert "R$ 3.500,00" in content  # alvo
        assert "caiu" in content
        assert "abaixo do alvo" in content

    def test_html_escapes_untrusted_fields(self) -> None:
        # Route é apenas uma dataclass; não se autovalida no formato IATA.
        # O escaping precisa ser defesa em profundidade, não depender da
        # validação de config.py já ter passado.
        route = Route(
            key="GRU-LIS",
            origin="<script>",
            destination="LIS",
            departure_date=date(2026, 12, 10),
            return_date=None,
            target_price_cents=None,
        )
        alert = Alert(
            route=route,
            quote=_quote(400000),
            previous_price_cents=500000,
            reason="drop",
        )
        message = build_message([alert])
        html = message.get_body(preferencelist=("html",))
        assert html is not None
        content = html.get_content()

        assert "<script>" not in content
        assert "&lt;script&gt;" in content

    def test_text_fallback_present(self) -> None:
        message = build_message([_alert()])
        text = message.get_body(preferencelist=("plain",))
        assert text is not None
        content = text.get_content()

        assert "GRU" in content
        assert "LIS" in content

    def test_is_multipart_alternative(self) -> None:
        message = build_message([_alert()])
        assert message.get_content_type() == "multipart/alternative"


class TestSend:
    def test_send_uses_correct_credentials_and_recipient(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setenv("SMTP_USER", "alguem@gmail.com")
        monkeypatch.setenv("SMTP_APP_PASSWORD", "senha-de-app")
        monkeypatch.setenv("ALERT_TO", "destino@example.com")

        calls: dict[str, object] = {}

        class FakeSmtp:
            def __init__(self, host: str, port: int) -> None:
                calls["host"] = host
                calls["port"] = port

            def __enter__(self) -> "FakeSmtp":
                return self

            def __exit__(self, *exc: object) -> None:
                return None

            def login(self, user: str, password: str) -> None:
                calls["login_user"] = user
                calls["login_password"] = password

            def send_message(self, message: EmailMessage) -> None:
                calls["sent_message"] = message
                calls["sent_to"] = message["To"]

        import flight_tracker.notifier as notifier_module

        monkeypatch.setattr(notifier_module.smtplib, "SMTP_SSL", FakeSmtp)

        message = build_message([_alert()])
        send(message)

        assert calls["host"] == "smtp.gmail.com"
        assert calls["port"] == 465
        assert calls["login_user"] == "alguem@gmail.com"
        assert calls["login_password"] == "senha-de-app"
        assert calls["sent_to"] == "destino@example.com"

    def test_send_defaults_recipient_to_smtp_user(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("SMTP_USER", "alguem@gmail.com")
        monkeypatch.setenv("SMTP_APP_PASSWORD", "senha-de-app")
        monkeypatch.delenv("ALERT_TO", raising=False)

        calls: dict[str, object] = {}

        class FakeSmtp:
            def __init__(self, host: str, port: int) -> None:
                pass

            def __enter__(self) -> "FakeSmtp":
                return self

            def __exit__(self, *exc: object) -> None:
                return None

            def login(self, user: str, password: str) -> None:
                pass

            def send_message(self, message: EmailMessage) -> None:
                calls["sent_to"] = message["To"]

        import flight_tracker.notifier as notifier_module

        monkeypatch.setattr(notifier_module.smtplib, "SMTP_SSL", FakeSmtp)

        message = build_message([_alert()])
        send(message)

        assert calls["sent_to"] == "alguem@gmail.com"
