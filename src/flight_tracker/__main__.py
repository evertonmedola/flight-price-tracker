"""Ponto de entrada da CLI."""

import argparse
import sys
from pathlib import Path

from flight_tracker.runner import run

_DEFAULT_CONFIG_PATH = "config/routes.yaml"
_DEFAULT_DB_PATH = "data/prices.db"
_MEMORY_DB_PATH = ":memory:"


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="flight_tracker",
        description="Rastreador de preços de passagens aéreas.",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="usa MockProvider, banco em memória e imprime o e-mail em vez de enviar",
    )
    parser.add_argument(
        "--config",
        default=_DEFAULT_CONFIG_PATH,
        help=f"caminho do arquivo de rotas (padrão: {_DEFAULT_CONFIG_PATH})",
    )
    parser.add_argument(
        "--db",
        default=None,
        help=f"caminho do banco SQLite (padrão: {_DEFAULT_DB_PATH}, "
        f"ou {_MEMORY_DB_PATH} em --dry-run)",
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    # No console do Windows (cp1252 por padrão), print() com caracteres como
    # ✈️/→ no e-mail de dry-run levanta UnicodeEncodeError. UTF-8 explícito
    # evita isso e é inofensivo em ambientes que já usam UTF-8 (Actions/Linux).
    for stream in (sys.stdout, sys.stderr):
        if hasattr(stream, "reconfigure"):
            stream.reconfigure(encoding="utf-8")

    args = build_parser().parse_args(argv)

    if args.db is not None:
        db_path = args.db
    elif args.dry_run:
        db_path = _MEMORY_DB_PATH
    else:
        db_path = _DEFAULT_DB_PATH

    return run(config_path=Path(args.config), db_path=db_path, dry_run=args.dry_run)


if __name__ == "__main__":
    sys.exit(main())
