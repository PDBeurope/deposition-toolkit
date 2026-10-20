from __future__ import annotations

import argparse
from pathlib import Path

from deposition_toolkit.config import get_settings
from stages.prepare_dataset.prepare_dataset import prepare_dataset

from .shared import LOGGER


def register(subparsers: argparse._SubParsersAction) -> None:
    parser = subparsers.add_parser(
        "prepare-dataset",
        help="Copy/extract a tarball and create manifests.",
    )
    parser.add_argument("tarball")
    parser.add_argument("--work-root", default=str(get_settings().workroot))
    parser.add_argument("--work-dir")
    parser.add_argument("--no-copy", action="store_true")
    parser.add_argument("--no-extract", action="store_true")
    parser.set_defaults(func=run)


def run(args: argparse.Namespace) -> int:
    result = prepare_dataset(
        Path(args.tarball),
        work_root=Path(args.work_root) if args.work_root else None,
        work_dir=Path(args.work_dir) if args.work_dir else None,
        copy_tarball=not args.no_copy,
        extract=not args.no_extract,
    )
    print(result.work_dir)
    LOGGER.info("Prepared %d structure directorie(s)", len(result.structure_dirs))
    return 0
