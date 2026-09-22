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


_FONT_STACK = "Arial, Helvetica, sans-serif"
_BORDER = "1px solid #e5e7eb"
_CELL_BASE = f"padding:10px 8px;border-bottom:{_BORDER};font-size:14px;"
_CELL = _CELL_BASE + "color:#1f2937;"
_ROW_BG_EVEN = "#ffffff"
_ROW_BG_ODD = "#fafafa"


def _html_body(alerts: list[Alert]) -> str:
    header_cells = "".join(
        f'<th style="text-align:left;padding:10px 8px;background:#f3f4f6;'
        f'color:#374151;font-size:13px;border-bottom:2px solid #d1d5db;">{label}</th>'
        for label in ("Rota", "Datas", "Anterior", "Atual", "Variação", "Alvo", "Motivo", "")
    )
    rows = "\n".join(_html_row(alert, index) for index, alert in enumerate(alerts))
    subject = escape(_subject(alerts))

    return f"""\
<html>
  <body style="margin:0;padding:0;background:#f9fafb;font-family:{_FONT_STACK};">
    <table role="presentation" width="100%" cellpadding="0" cellspacing="0" \
style="max-width:600px;margin:0 auto;background:#ffffff;">
      <tr>
        <td style="background:#eff6ff;padding:20px 24px;">
          <h1 style="margin:0;font-size:20px;color:#1e3a8a;font-family:{_FONT_STACK};">
            {subject}
          </h1>
        </td>
      </tr>
      <tr>
        <td style="padding:16px 24px;">
          <table role="presentation" width="100%" cellpadding="0" cellspacing="0" \
style="border-collapse:collapse;">
            <tr>{header_cells}</tr>
            {rows}
          </table>
        </td>
      </tr>
      <tr>
        <td style="padding:12px 24px 20px;border-top:{_BORDER};">
          <p style="margin:0;font-size:12px;color:#9ca3af;font-family:{_FONT_STACK};">
            Gerado automaticamente pelo flight-price-tracker.
          </p>
        </td>
      </tr>
    </table>
  </body>
</html>
"""


def _html_row(alert: Alert, index: int) -> str:
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

    row_bg = _ROW_BG_ODD if index % 2 else _ROW_BG_EVEN
    # Toda linha desta tabela já é, por construção, um alerta de queda e/ou
    # preço abaixo do alvo (é por isso que está no e-mail) — o preço atual
    # sempre recebe o destaque visual.
    price_cell_style = _CELL_BASE + "color:#16a34a;font-weight:bold;"

    return (
        f'<tr style="background:{row_bg};">'
        f'<td style="{_CELL}">{escape(route.origin)} → {escape(route.destination)}</td>'
        f'<td style="{_CELL}">{escape(dates)}</td>'
        f'<td style="{_CELL}">{escape(previous)}</td>'
        f'<td style="{price_cell_style}">{escape(_format_price(alert.quote.price_cents))}</td>'
        f'<td style="{_CELL}">'
        f"{escape(_format_variation(alert.previous_price_cents, alert.quote.price_cents))}</td>"
        f'<td style="{_CELL}">{escape(target)}</td>'
        f'<td style="{_CELL}">{escape(_REASON_LABELS[alert.reason])}</td>'
        f'<td style="{_CELL}">'
        f'<a href="{escape(_google_flights_link(alert))}" '
        f'style="display:inline-block;background:#2563eb;color:#ffffff;'
        f"padding:6px 14px;border-radius:6px;text-decoration:none;"
        f'font-weight:600;font-size:13px;font-family:{_FONT_STACK};">'
        f"Ver oferta →</a></td>"
        "</tr>"
    )
