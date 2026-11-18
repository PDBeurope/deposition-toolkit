from __future__ import annotations

import argparse
import logging
from collections.abc import Callable
from pathlib import Path

from deposition_toolkit.config import get_settings
from deposition_toolkit.stages.common.manifest import (
    load_manifest,
    relative_to_work_dir,
    set_top_level_fields,
    update_step,
)
from deposition_toolkit.stages.deposit.deposition import (
    LOGGER as DEPOSITION_LOGGER,
    dataset_from_manifest,
    deposit_structure,
)

from .progress import BatchProgress

from .shared import (
    print_run_summary,
    structure_log,
    STEP_DEPOSITION,
    dataclass_to_dict,
    require_structures,
    results_path,
    stage_log_handler,
)


LOGGER = logging.getLogger("deposition_toolkit")


def register(subparsers: argparse._SubParsersAction) -> None:
    parser = subparsers.add_parser("deposit", help="Upload each active structure through OneDep.")
    parser.add_argument("work_dir")
    parser.add_argument("--email", default=get_settings().wwpdb_email)
    parser.add_argument("--users", default=get_settings().wwpdb_users)
    parser.add_argument("--country", default="UK")
    parser.add_argument("--experiment-type", default="XRAY")
    parser.add_argument("--no-poll", action="store_true")
    parser.add_argument("--poll-interval", type=int, default=5)
    parser.add_argument("--max-polls", type=int, default=64)
    parser.add_argument("--dry-run", action="store_true")
    parser.set_defaults(func=run)


def run(
    args: argparse.Namespace,
    *,
    session_lookup: Callable[[str, str], str | None] | None = None,
) -> int:
    """Look up sessions and persist results at the command boundary only."""
    work_dir = Path(args.work_dir).resolve()
    structure_dirs = require_structures(work_dir, STEP_DEPOSITION)
    if not structure_dirs:
        return 1

    if not args.dry_run:
        from sqlalchemy.exc import SQLAlchemyError

        from deposition_toolkit.ledger.depositions import find_session_id, save_deposition

        lookup = session_lookup if session_lookup is not None else find_session_id

    log_root = results_path(work_dir, "logs", "deposition")
    any_failed = False
    processed = []
    names = [str(path.relative_to(work_dir)) for path in structure_dirs]
    success_label = "Dry-run passed" if args.dry_run else ("Submitted" if args.no_poll else "Deposited")
    with BatchProgress(names, label="Deposition", show_lists=True, success_label=success_label) as progress:
        for structure_dir in structure_dirs:
            processed.append(structure_dir)
            name = str(structure_dir.relative_to(work_dir))
            with progress.track(name, lambda: load_manifest(structure_dir)["steps"][STEP_DEPOSITION]["status"]):
                log_path = log_root / f"{name}.log"
                with (
                    stage_log_handler(logging.getLogger(), log_path),
                    structure_log(work_dir, structure_dir, STEP_DEPOSITION),
                ):
                    manifest = load_manifest(structure_dir)
                    session_id = None
                    if not args.dry_run:
                        try:
                            progress.stage("Checking the deposition registry")
                            session_id = lookup(dataset_from_manifest(manifest), manifest["structure_id"])
                        except (ValueError, SQLAlchemyError):
                            LOGGER.error(
                                "Deposition registry lookup failed for %s. Check database settings, "
                                "migrations, manifest identity and saved session availability; no upload was started.",
                                manifest["structure_id"],
                            )
                            update_step(
                                structure_dir, STEP_DEPOSITION, "failed",
                                error="Deposition registry lookup failed; no upload was started.",
                            )
                            any_failed = True
                            break
                    DEPOSITION_LOGGER.info("Starting deposition for %s", manifest["structure_id"])
                    result = deposit_structure(
                        structure_dir,
                        session_id=session_id,
                        email=args.email,
                        users=args.users,
                        country=args.country,
                        experiment_type=args.experiment_type,
                        poll=not args.no_poll,
                        poll_interval=args.poll_interval,
                        max_polls=args.max_polls,
                        dry_run=args.dry_run,
                        progress=progress.stage,
                    )
                    DEPOSITION_LOGGER.log(
                        logging.ERROR if result.status == "failed" else logging.INFO,
                        "Deposition result for %s: status=%s deposition_id=%s error=%s",
                        result.structure_id, result.status, result.deposition_id or "none", result.error or "none",
                    )
                    result_dict = dataclass_to_dict(result)
                    any_failed = any_failed or result.status == "failed"
                    if result.deposition_id:
                        set_top_level_fields(
                            structure_dir,
                            deposition_id=result.deposition_id,
                            session_id=result.session_id,
                        )
                        result_dict["deposition_record"] = result.to_record()
                    update_step(
                        structure_dir,
                        STEP_DEPOSITION,
                        result.status,
                        output=relative_to_work_dir(log_path, work_dir),
                        error=result.error or None,
                        extra=result_dict,
                    )
                    if not args.dry_run and result.deposition_id and result.session_id:
                        try:
                            progress.stage("Saving the deposition registry entry")
                            save_deposition(result.to_record())
                        except (ValueError, SQLAlchemyError):
                            LOGGER.error(
                                "Could not save deposition registry entry for %s. Deposition %s and session %s "
                                "are saved in its manifest; reconcile the registry before retrying.",
                                result.structure_id, result.deposition_id, result.session_id,
                            )
                            update_step(
                                structure_dir, STEP_DEPOSITION, "failed",
                                error="Registry save failed; reconcile the registry before retrying.",
                                extra={**result_dict, "status": "failed"},
                            )
                            any_failed = True
                            break
    if len(processed) < len(structure_dirs):
        print(f"Stopped early: {len(structure_dirs) - len(processed)} structures not run.")
    failed = print_run_summary(work_dir, STEP_DEPOSITION, processed)
    return 1 if any_failed or failed else 0
