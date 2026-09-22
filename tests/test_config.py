from datetime import date
from pathlib import Path

import pytest

from flight_tracker.config import ConfigError, load_routes

VALID_ROUTE = {
    "key": "GRU-LIS",
    "origin": "GRU",
    "destination": "LIS",
    "departure_date": "2026-12-10",
    "return_date": "2026-12-20",
    "target_price_cents": 350000,
}


def _write_yaml(tmp_path: Path, content: str) -> Path:
    path = tmp_path / "routes.yaml"
    path.write_text(content, encoding="utf-8")
    return path


def _yaml_with_route(**overrides: object) -> str:
    route = dict(VALID_ROUTE)
    for key, value in overrides.items():
        if value is _REMOVE:
            route.pop(key, None)
        else:
            route[key] = value
    lines = ["routes:", "  - " + "\n    ".join(f"{k}: {_fmt(v)}" for k, v in route.items())]
    return "\n".join(lines) + "\n"


class _Remove:
    pass


_REMOVE = _Remove()


def _fmt(value: object) -> str:
    if value is None:
        return "null"
    if isinstance(value, str):
        return f'"{value}"'
    return str(value)


class TestHappyPath:
    def test_loads_valid_route(self, tmp_path: Path) -> None:
        path = _write_yaml(tmp_path, _yaml_with_route())
        routes = load_routes(path)
        assert len(routes) == 1
        route = routes[0]
        assert route.key == "GRU-LIS"
        assert route.origin == "GRU"
        assert route.destination == "LIS"
        assert route.departure_date == date(2026, 12, 10)
        assert route.return_date == date(2026, 12, 20)
        assert route.target_price_cents == 350000

    def test_return_date_and_target_are_optional(self, tmp_path: Path) -> None:
        content = _yaml_with_route(return_date=_REMOVE, target_price_cents=_REMOVE)
        path = _write_yaml(tmp_path, content)
        routes = load_routes(path)
        assert routes[0].return_date is None
        assert routes[0].target_price_cents is None

    def test_key_is_derived_when_absent(self, tmp_path: Path) -> None:
        path = _write_yaml(tmp_path, _yaml_with_route(key=_REMOVE))
        routes = load_routes(path)
        assert routes[0].key == "GRU-LIS-2026-12-10"


class TestValidationErrors:
    def test_origin_must_be_three_uppercase_letters(self, tmp_path: Path) -> None:
        path = _write_yaml(tmp_path, _yaml_with_route(origin="gru"))
        with pytest.raises(ConfigError):
            load_routes(path)

    def test_origin_wrong_length_rejected(self, tmp_path: Path) -> None:
        path = _write_yaml(tmp_path, _yaml_with_route(origin="GRUX"))
        with pytest.raises(ConfigError):
            load_routes(path)

    def test_destination_must_be_three_uppercase_letters(self, tmp_path: Path) -> None:
        path = _write_yaml(tmp_path, _yaml_with_route(destination="li5"))
        with pytest.raises(ConfigError):
            load_routes(path)

    def test_departure_date_must_be_iso(self, tmp_path: Path) -> None:
        path = _write_yaml(tmp_path, _yaml_with_route(departure_date="10/12/2026"))
        with pytest.raises(ConfigError):
            load_routes(path)

    def test_return_date_before_departure_rejected(self, tmp_path: Path) -> None:
        path = _write_yaml(
            tmp_path,
            _yaml_with_route(departure_date="2026-12-10", return_date="2026-12-01"),
        )
        with pytest.raises(ConfigError):
            load_routes(path)

    def test_target_price_must_be_positive(self, tmp_path: Path) -> None:
        path = _write_yaml(tmp_path, _yaml_with_route(target_price_cents=0))
        with pytest.raises(ConfigError):
            load_routes(path)

    def test_target_price_negative_rejected(self, tmp_path: Path) -> None:
        path = _write_yaml(tmp_path, _yaml_with_route(target_price_cents=-100))
        with pytest.raises(ConfigError):
            load_routes(path)

    def test_duplicate_keys_rejected(self, tmp_path: Path) -> None:
        content = (
            "routes:\n"
            '  - key: "GRU-LIS"\n'
            '    origin: "GRU"\n'
            '    destination: "LIS"\n'
            '    departure_date: "2026-12-10"\n'
            '  - key: "GRU-LIS"\n'
            '    origin: "GRU"\n'
            '    destination: "MAD"\n'
            '    departure_date: "2026-12-15"\n'
        )
        path = _write_yaml(tmp_path, content)
        with pytest.raises(ConfigError):
            load_routes(path)

    def test_empty_file_rejected(self, tmp_path: Path) -> None:
        path = _write_yaml(tmp_path, "")
        with pytest.raises(ConfigError):
            load_routes(path)

    def test_missing_routes_key_rejected(self, tmp_path: Path) -> None:
        path = _write_yaml(tmp_path, "something_else: []\n")
        with pytest.raises(ConfigError):
            load_routes(path)


class TestExampleConfig:
    def test_repo_example_loads_without_error(self) -> None:
        path = Path(__file__).resolve().parent.parent / "config" / "routes.yaml"
        routes = load_routes(path)
        assert len(routes) >= 1
        assert len({route.key for route in routes}) == len(routes)
