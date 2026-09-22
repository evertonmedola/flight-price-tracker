"""Modelo de domínio: rotas, cotações de preço e alertas."""

from dataclasses import dataclass
from datetime import date, datetime
from typing import Literal

AlertReason = Literal["drop", "below_target", "drop_and_below_target"]


@dataclass(frozen=True)
class Route:
    key: str
    origin: str
    destination: str
    departure_date: date
    return_date: date | None
    target_price_cents: int | None


@dataclass(frozen=True)
class PriceQuote:
    route_key: str
    price_cents: int
    currency: str
    provider: str
    fetched_at: datetime


@dataclass(frozen=True)
class Alert:
    route: Route
    quote: PriceQuote
    previous_price_cents: int | None
    reason: AlertReason
