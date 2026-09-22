"""Montagem e envio do e-mail consolidado de alertas."""

import os
import smtplib
from email.message import EmailMessage
from html import escape
from urllib.parse import quote

from flight_tracker.models import Alert

_DEFAULT_SMTP_HOST = "smtp.gmail.com"
_DEFAULT_SMTP_PORT = 465

_REASON_LABELS = {
    "drop": "caiu",
    "below_target": "abaixo do alvo",
    "drop_and_below_target": "caiu · abaixo do alvo",
}


def build_message(alerts: list[Alert]) -> EmailMessage:
    message = EmailMessage()
    message["Subject"] = _subject(alerts)
    message.set_content(_text_body(alerts))
    message.add_alternative(_html_body(alerts), subtype="html")
    return message


def send(message: EmailMessage) -> None:
    smtp_user = os.environ["SMTP_USER"]
    smtp_password = os.environ["SMTP_APP_PASSWORD"]
    recipient = os.environ.get("ALERT_TO", smtp_user)
    host = os.environ.get("SMTP_HOST", _DEFAULT_SMTP_HOST)
    port = int(os.environ.get("SMTP_PORT", str(_DEFAULT_SMTP_PORT)))

    message["From"] = smtp_user
    message["To"] = recipient

    with smtplib.SMTP_SSL(host, port) as server:
        server.login(smtp_user, smtp_password)
        server.send_message(message)


def _subject(alerts: list[Alert]) -> str:
    count = len(alerts)
    noun = "rota" if count == 1 else "rotas"
    return f"✈️ {count} {noun} com preço em queda"


def _format_price(price_cents: int) -> str:
    reais, cents = divmod(price_cents, 100)
    reais_str = f"{reais:,}".replace(",", ".")
    return f"R$ {reais_str},{cents:02d}"


def _format_variation(previous_price_cents: int | None, price_cents: int) -> str:
    if previous_price_cents is None or previous_price_cents == 0:
        return "—"
    variation = (price_cents - previous_price_cents) / previous_price_cents * 100
    sign = "+" if variation > 0 else ""
    return f"{sign}{variation:.1f}%".replace(".", ",")


def _google_flights_link(alert: Alert) -> str:
    route = alert.route
    query = (
        f"Flights from {route.origin} to {route.destination} on {route.departure_date.isoformat()}"
    )
    return f"https://www.google.com/travel/flights?q={quote(query)}"


def _text_body(alerts: list[Alert]) -> str:
    lines = ["Rotas com preço em queda:", ""]
    for alert in alerts:
        route = alert.route
        previous = (
            _format_price(alert.previous_price_cents)
            if alert.previous_price_cents is not None
            else "—"
        )
        target = (
            _format_price(route.target_price_cents) if route.target_price_cents is not None else "—"
        )
        lines.append(
            f"{route.origin} -> {route.destination} ({route.departure_date.isoformat()}): "
            f"{previous} -> {_format_price(alert.quote.price_cents)} "
            f"[{_REASON_LABELS[alert.reason]}, alvo {target}]"
        )
        lines.append(_google_flights_link(alert))
        lines.append("")
    return "\n".join(lines)


def _html_body(alerts: list[Alert]) -> str:
    rows = "\n".join(_html_row(alert) for alert in alerts)
    return f"""\
<html>
  <body>
    <table border="1" cellpadding="6" cellspacing="0">
      <thead>
        <tr>
          <th>Rota</th><th>Datas</th><th>Anterior</th><th>Atual</th>
          <th>Variação</th><th>Alvo</th><th>Motivo</th><th>Link</th>
        </tr>
      </thead>
      <tbody>
        {rows}
      </tbody>
    </table>
  </body>
</html>
"""


def _html_row(alert: Alert) -> str:
    route = alert.route
    previous = (
        _format_price(alert.previous_price_cents) if alert.previous_price_cents is not None else "—"
    )
    target = (
        _format_price(route.target_price_cents) if route.target_price_cents is not None else "—"
    )
    dates = route.departure_date.isoformat()
    if route.return_date is not None:
        dates += f" – {route.return_date.isoformat()}"

    return (
        "<tr>"
        f"<td>{escape(route.origin)} → {escape(route.destination)}</td>"
        f"<td>{escape(dates)}</td>"
        f"<td>{escape(previous)}</td>"
        f"<td>{escape(_format_price(alert.quote.price_cents))}</td>"
        f"<td>{escape(_format_variation(alert.previous_price_cents, alert.quote.price_cents))}</td>"
        f"<td>{escape(target)}</td>"
        f"<td>{escape(_REASON_LABELS[alert.reason])}</td>"
        f'<td><a href="{escape(_google_flights_link(alert))}">ver</a></td>'
        "</tr>"
    )
