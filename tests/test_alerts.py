from datetime import UTC, date, datetime

from flight_tracker.alerts import evaluate
from flight_tracker.models import PriceQuote, Route

_FETCHED_AT = datetime(2026, 9, 22, tzinfo=UTC)


def _route(target_price_cents: int | None) -> Route:
    return Route(
        key="GRU-LIS",
        origin="GRU",
        destination="LIS",
        departure_date=date(2026, 12, 10),
        return_date=None,
        target_price_cents=target_price_cents,
    )


def _quote(price_cents: int) -> PriceQuote:
    return PriceQuote(
        route_key="GRU-LIS",
        price_cents=price_cents,
        currency="BRL",
        provider="mock",
        fetched_at=_FETCHED_AT,
    )


class TestEvaluate:
    def test_no_previous_no_target_never_alerts(self) -> None:
        route = _route(target_price_cents=None)
        alert = evaluate(
            route=route,
            quote=_quote(400000),
            previous=None,
            last_notified_price_cents=None,
        )
        assert alert is None

    def test_price_drop_triggers_drop_reason(self) -> None:
        route = _route(target_price_cents=None)
        alert = evaluate(
            route=route,
            quote=_quote(400000),
            previous=_quote(500000),
            last_notified_price_cents=None,
        )
        assert alert is not None
        assert alert.reason == "drop"

    def test_price_rise_does_not_alert(self) -> None:
        route = _route(target_price_cents=None)
        alert = evaluate(
            route=route,
            quote=_quote(500000),
            previous=_quote(400000),
            last_notified_price_cents=None,
        )
        assert alert is None

    def test_below_target_without_previous_triggers_below_target_reason(self) -> None:
        route = _route(target_price_cents=450000)
        alert = evaluate(
            route=route,
            quote=_quote(400000),
            previous=None,
            last_notified_price_cents=None,
        )
        assert alert is not None
        assert alert.reason == "below_target"

    def test_drop_and_below_target_combines_reason(self) -> None:
        route = _route(target_price_cents=450000)
        alert = evaluate(
            route=route,
            quote=_quote(400000),
            previous=_quote(500000),
            last_notified_price_cents=None,
        )
        assert alert is not None
        assert alert.reason == "drop_and_below_target"

    def test_same_value_as_last_notified_is_suppressed(self) -> None:
        route = _route(target_price_cents=450000)
        alert = evaluate(
            route=route,
            quote=_quote(400000),
            previous=_quote(500000),
            last_notified_price_cents=400000,
        )
        assert alert is None

    def test_new_lower_value_after_notification_alerts_again(self) -> None:
        route = _route(target_price_cents=None)
        alert = evaluate(
            route=route,
            quote=_quote(390000),
            previous=_quote(450000),
            last_notified_price_cents=400000,
        )
        assert alert is not None
        assert alert.reason == "drop"

    def test_alert_carries_previous_price_and_quote(self) -> None:
        route = _route(target_price_cents=None)
        previous = _quote(500000)
        quote = _quote(400000)
        alert = evaluate(
            route=route,
            quote=quote,
            previous=previous,
            last_notified_price_cents=None,
        )
        assert alert is not None
        assert alert.quote is quote
        assert alert.previous_price_cents == 500000
        assert alert.route is route


class TestAcceptanceSequence:
    """Cobre o critério de aceite §10 item 7: 500 -> 400 -> 450 -> 400 -> 390."""

    def test_full_sequence(self) -> None:
        route = _route(target_price_cents=None)

        # Cotação inicial: 500000, sem anterior, sem alerta.
        first = evaluate(route, _quote(500000), previous=None, last_notified_price_cents=None)
        assert first is None

        # Cai para 400000: alerta "drop", notifica.
        second = evaluate(
            route, _quote(400000), previous=_quote(500000), last_notified_price_cents=None
        )
        assert second is not None
        assert second.reason == "drop"
        last_notified = second.quote.price_cents  # 400000

        # Sobe para 450000: sem alerta.
        third = evaluate(
            route, _quote(450000), previous=_quote(400000), last_notified_price_cents=last_notified
        )
        assert third is None

        # Volta para 400000 (mesmo valor já notificado): sem alerta novo.
        fourth = evaluate(
            route, _quote(400000), previous=_quote(450000), last_notified_price_cents=last_notified
        )
        assert fourth is None

        # Cai para 390000: novo alerta.
        fifth = evaluate(
            route, _quote(390000), previous=_quote(400000), last_notified_price_cents=last_notified
        )
        assert fifth is not None
        assert fifth.reason == "drop"
