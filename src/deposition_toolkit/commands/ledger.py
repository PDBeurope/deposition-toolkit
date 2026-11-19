"""Command boundary for ledger administration."""

import argparse
import logging
from pathlib import Path


LOGGER = logging.getLogger("deposition_toolkit")


def register(subparsers: argparse._SubParsersAction) -> None:
    parser = subparsers.add_parser("ledger", help="Manage the toolkit PostgreSQL database.")
    actions = parser.add_subparsers(dest="action", required=True)
    for action in ("check", "current", "upgrade"):
        actions.add_parser(action)
    load = actions.add_parser("load-manifest", help="Load structure manifests into the ledger.")
    load.add_argument("work_dir", type=Path, help="Directory containing structure manifest.json files.")
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
        elif args.action == "load-manifest":
            from deposition_toolkit.ledger.structures import load_manifests

            def confirm(group_id: str, existing: int, new: int) -> bool:
                LOGGER.warning("The group_id %s has been loaded before. %d structures exist. "
                               "%d structures are new.", group_id, existing, new)
                try:
                    return input("Do you wish to override all data? [y/N] ").strip().lower() in {"y", "yes"}
                except (EOFError, KeyboardInterrupt):
                    return False

            count = load_manifests(args.work_dir, confirm=confirm)
            if count is None:
                LOGGER.info("Manifest loading cancelled; no data changed.")
            else:
                LOGGER.info("Loaded %d structure manifests.", count)
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
    except OSError as exc:
        LOGGER.error("Cannot read structure manifests: %s", exc)
        return 1
    except SQLAlchemyError:
        # Driver errors may contain connection details or query parameters.
        LOGGER.error("Ledger database operation failed. Check PostgreSQL settings and connectivity.")
        return 1
    return 0
