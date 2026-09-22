from pathlib import Path

import pytest

from flight_tracker.__main__ import build_parser, main


class TestParser:
    def test_help_lists_flags(self, capsys: pytest.CaptureFixture[str]) -> None:
        parser = build_parser()
        help_text = parser.format_help()

        assert "--dry-run" in help_text
        assert "--config" in help_text
        assert "--db" in help_text


class TestDbPathDefaults:
    def test_default_db_path_used_when_not_dry_run(self, monkeypatch: pytest.MonkeyPatch) -> None:
        import flight_tracker.__main__ as cli_module

        captured: dict[str, object] = {}

        def fake_run(config_path: Path, db_path: str, dry_run: bool) -> int:
            captured["config_path"] = config_path
            captured["db_path"] = db_path
            captured["dry_run"] = dry_run
            return 0

        monkeypatch.setattr(cli_module, "run", fake_run)

        code = main([])

        assert code == 0
        assert captured["db_path"] == "data/prices.db"
        assert captured["dry_run"] is False
        assert captured["config_path"] == Path("config/routes.yaml")

    def test_dry_run_defaults_to_memory_db(self, monkeypatch: pytest.MonkeyPatch) -> None:
        import flight_tracker.__main__ as cli_module

        captured: dict[str, object] = {}

        def fake_run(config_path: Path, db_path: str, dry_run: bool) -> int:
            captured["db_path"] = db_path
            captured["dry_run"] = dry_run
            return 0

        monkeypatch.setattr(cli_module, "run", fake_run)

        main(["--dry-run"])

        assert captured["db_path"] == ":memory:"
        assert captured["dry_run"] is True

    def test_explicit_db_overrides_dry_run_default(self, monkeypatch: pytest.MonkeyPatch) -> None:
        import flight_tracker.__main__ as cli_module

        captured: dict[str, object] = {}

        def fake_run(config_path: Path, db_path: str, dry_run: bool) -> int:
            captured["db_path"] = db_path
            return 0

        monkeypatch.setattr(cli_module, "run", fake_run)

        main(["--dry-run", "--db", "custom.db"])

        assert captured["db_path"] == "custom.db"

    def test_explicit_config_path_is_used(self, monkeypatch: pytest.MonkeyPatch) -> None:
        import flight_tracker.__main__ as cli_module

        captured: dict[str, object] = {}

        def fake_run(config_path: Path, db_path: str, dry_run: bool) -> int:
            captured["config_path"] = config_path
            return 0

        monkeypatch.setattr(cli_module, "run", fake_run)

        main(["--config", "custom_routes.yaml"])

        assert captured["config_path"] == Path("custom_routes.yaml")


class TestExitCodePropagation:
    def test_exit_code_propagates(self, monkeypatch: pytest.MonkeyPatch) -> None:
        import flight_tracker.__main__ as cli_module

        monkeypatch.setattr(cli_module, "run", lambda config_path, db_path, dry_run: 2)

        code = main([])

        assert code == 2
