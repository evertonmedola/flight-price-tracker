"""Regra de alerta: quando notificar uma queda ou preço abaixo do alvo."""

from flight_tracker.models import Alert, AlertReason, PriceQuote, Route


def evaluate(
    route: Route,
    quote: PriceQuote,
    previous: PriceQuote | None,
    last_notified_price_cents: int | None,
) -> Alert | None:
    price = quote.price_cents

    if price == last_notified_price_cents:
        return None

    is_drop = previous is not None and price < previous.price_cents
    is_below_target = route.target_price_cents is not None and price < route.target_price_cents

    if not is_drop and not is_below_target:
        return None

    reason: AlertReason
    if is_drop and is_below_target:
        reason = "drop_and_below_target"
    elif is_drop:
        reason = "drop"
    else:
        reason = "below_target"

    previous_price_cents = previous.price_cents if previous is not None else None
    return Alert(
        route=route,
        quote=quote,
        previous_price_cents=previous_price_cents,
        reason=reason,
    )
