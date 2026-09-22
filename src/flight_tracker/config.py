"""Carregamento e validação de `routes.yaml`."""

import re
from datetime import date
from pathlib import Path
from typing import Any

import yaml

from flight_tracker.models import Route

_IATA_RE = re.compile(r"^[A-Z]{3}$")


class ConfigError(Exception):
    """Erro de configuração: arquivo ausente/malformado ou regra violada."""


def load_routes(path: Path) -> list[Route]:
    raw = _read_yaml(path)
    entries = raw.get("routes")
    if not entries:
        raise ConfigError(f"'{path}': arquivo vazio ou sem a chave 'routes'")

    routes: list[Route] = []
    seen_keys: set[str] = set()
    for index, entry in enumerate(entries):
        route = _parse_route(entry, index)
        if route.key in seen_keys:
            raise ConfigError(f"chave de rota duplicada: '{route.key}'")
        seen_keys.add(route.key)
        routes.append(route)
    return routes


def _read_yaml(path: Path) -> dict[str, Any]:
    try:
        text = path.read_text(encoding="utf-8")
    except OSError as exc:
        raise ConfigError(f"não foi possível ler '{path}': {exc}") from exc

    data = yaml.safe_load(text)
    if not data:
        raise ConfigError(f"'{path}': arquivo vazio ou sem a chave 'routes'")
    if not isinstance(data, dict):
        raise ConfigError(f"'{path}': conteúdo YAML inválido (esperado um mapeamento)")
    return data


def _parse_route(entry: Any, index: int) -> Route:
    if not isinstance(entry, dict):
        raise ConfigError(f"rota #{index}: entrada inválida (esperado um mapeamento)")

    origin = _require_iata(entry, "origin", index)
    destination = _require_iata(entry, "destination", index)
    departure_date = _require_date(entry, "departure_date", index)
    return_date = _optional_date(entry, "return_date", index)
    if return_date is not None and return_date < departure_date:
        raise ConfigError(f"rota #{index}: 'return_date' anterior a 'departure_date'")

    target_price_cents = _optional_positive_int(entry, "target_price_cents", index)

    key = entry.get("key")
    if key is None:
        key = f"{origin}-{destination}-{departure_date.isoformat()}"
    elif not isinstance(key, str) or not key:
        raise ConfigError(f"rota #{index}: 'key' inválida")

    return Route(
        key=key,
        origin=origin,
        destination=destination,
        departure_date=departure_date,
        return_date=return_date,
        target_price_cents=target_price_cents,
    )


def _require_iata(entry: dict[str, Any], field: str, index: int) -> str:
    value = entry.get(field)
    if not isinstance(value, str) or not _IATA_RE.match(value):
        raise ConfigError(f"rota #{index}: '{field}' deve ter 3 letras maiúsculas (IATA)")
    return value


def _require_date(entry: dict[str, Any], field: str, index: int) -> date:
    value = entry.get(field)
    if not isinstance(value, str):
        raise ConfigError(f"rota #{index}: '{field}' ausente ou inválido")
    try:
        return date.fromisoformat(value)
    except ValueError as exc:
        raise ConfigError(_iso_date_error(field, index)) from exc


def _optional_date(entry: dict[str, Any], field: str, index: int) -> date | None:
    value = entry.get(field)
    if value is None:
        return None
    if not isinstance(value, str):
        raise ConfigError(f"rota #{index}: '{field}' inválido")
    try:
        return date.fromisoformat(value)
    except ValueError as exc:
        raise ConfigError(_iso_date_error(field, index)) from exc


def _iso_date_error(field: str, index: int) -> str:
    return f"rota #{index}: '{field}' deve estar em formato ISO-8601 (YYYY-MM-DD)"


def _optional_positive_int(entry: dict[str, Any], field: str, index: int) -> int | None:
    value = entry.get(field)
    if value is None:
        return None
    if not isinstance(value, int) or isinstance(value, bool) or value <= 0:
        raise ConfigError(f"rota #{index}: '{field}' deve ser um inteiro positivo")
    return value
