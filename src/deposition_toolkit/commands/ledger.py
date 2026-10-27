"""Command boundary for ledger administration."""

import argparse
import logging
from pathlib import Path


LOGGER = logging.getLogger("deposition_toolkit")


def register(subparsers: argparse._SubParsersAction) -> None:
    parser = subparsers.add_parser("ledger", help="Check or migrate the toolkit PostgreSQL database.")
    parser.add_argument("action", choices=("check", "current", "upgrade"))
    parser.set_defaults(func=run)


def run(args: argparse.Namespace) -> int:
    from alembic import command
    from alembic.config import Config
    from alembic.util.exc import CommandError
    from pydantic import ValidationError
    from sqlalchemy.exc import SQLAlchemyError

    from deposition_toolkit import ledger
    from deposition_toolkit.ledger.database import check_connection

    try:
        if args.action == "check":
            check_connection()
            LOGGER.info("Ledger connection successful.")
        else:
            config = Config(str(Path(ledger.__file__).parent / "alembic.ini"))
            if args.action == "upgrade":
                command.upgrade(config, "head")
                LOGGER.info("Ledger migrations applied.")
            else:
                command.current(config)
    except (ValueError, ValidationError, CommandError) as exc:
        LOGGER.error("Ledger configuration or migration failed: %s", exc)
        return 1
    except SQLAlchemyError:
        # Driver errors may contain connection details or query parameters.
        LOGGER.error("Ledger database operation failed. Check PostgreSQL settings and connectivity.")
        return 1
    return 0
