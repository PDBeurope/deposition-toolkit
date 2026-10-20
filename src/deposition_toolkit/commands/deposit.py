from __future__ import annotations

import argparse
import os
from pathlib import Path

from deposition_toolkit.stages.common.manifest import relative_to_work_dir, set_top_level_fields, update_step
from deposition_toolkit.stages.deposit.deposition import LOGGER as DEPOSITION_LOGGER, deposit_structure

from .shared import (
    STEP_DEPOSITION,
    dataclass_to_dict,
    require_structures,
    results_path,
    stage_log_handler,
)


def register(subparsers: argparse._SubParsersAction) -> None:
    parser = subparsers.add_parser("deposit", help="Upload each active structure through OneDep.")
    parser.add_argument("work_dir")
    parser.add_argument("--email", default=os.environ.get("WWPDB_EMAIL", ""))
    parser.add_argument("--users", default=os.environ.get("WWPDB_USERS", ""))
    parser.add_argument("--country", default="UK")
    parser.add_argument("--experiment-type", default="XRAY")
    parser.add_argument("--no-poll", action="store_true")
    parser.add_argument("--poll-interval", type=int, default=5)
    parser.add_argument("--max-polls", type=int, default=64)
    parser.add_argument("--dry-run", action="store_true")
    parser.set_defaults(func=run)


def run(args: argparse.Namespace) -> int:
    work_dir = Path(args.work_dir).resolve()
    structure_dirs = require_structures(work_dir, STEP_DEPOSITION)
    if not structure_dirs:
        return 1

    log_root = results_path(work_dir, "logs", "deposition")
    any_failed = False
    for structure_dir in structure_dirs:
        log_path = log_root / f"{structure_dir.name}.log"
        with stage_log_handler(DEPOSITION_LOGGER, log_path):
            result = deposit_structure(
                structure_dir,
                email=args.email,
                users=args.users,
                country=args.country,
                experiment_type=args.experiment_type,
                poll=not args.no_poll,
                poll_interval=args.poll_interval,
                max_polls=args.max_polls,
                dry_run=args.dry_run,
            )
        result_dict = dataclass_to_dict(result)
        any_failed = any_failed or result.status == "failed"
        if result.deposition_id:
            set_top_level_fields(structure_dir, deposition_id=result.deposition_id)
        update_step(
            structure_dir,
            STEP_DEPOSITION,
            result.status,
            output=relative_to_work_dir(log_path, work_dir),
            error=result.error or None,
            extra=result_dict,
        )
    return 1 if any_failed else 0
