from __future__ import annotations

import argparse
import logging
from collections.abc import Sequence

from deposition_toolkit.commands import COMMAND_MODULES


def configure_logging(verbose: bool = False) -> None:
    logging.basicConfig(
        level=logging.DEBUG if verbose else logging.INFO,
        format="%(levelname)s:%(name)s:%(message)s",
    )


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="deposition-toolkit",
        description="Wrapper for deposition preparation, CCD processing, and upload stages.",
    )
    parser.add_argument("--verbose", action="store_true", help="Enable verbose wrapper logging.")
    subparsers = parser.add_subparsers(dest="command", required=True)
    for command_module in COMMAND_MODULES:
        command_module.register(subparsers)
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    configure_logging(args.verbose)
    return int(args.func(args))


if __name__ == "__main__":
    raise SystemExit(main())
