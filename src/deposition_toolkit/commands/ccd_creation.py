from __future__ import annotations

import argparse
from pathlib import Path

from deposition_toolkit.stages.common.manifest import relative_to_work_dir, update_step

from .shared import (
    STEP_CCD_CREATION,
    relative_outputs,
    require_structures,
    results_path,
    stage_log_handler,
)


def register(subparsers: argparse._SubParsersAction) -> None:
    parser = subparsers.add_parser("ccd-creation", help="Stage 1: Create CCD CIFs per structure.")
    parser.add_argument("work_dir")
    parser.set_defaults(func=run)


def run(args: argparse.Namespace) -> int:
    from deposition_toolkit.stages.ccd_creation.ccd_creator import CcdDirectoryBuilder, LOGGER as CCD_CREATOR_LOGGER

    work_dir = Path(args.work_dir).resolve()
    structure_dirs = require_structures(work_dir, STEP_CCD_CREATION)
    if not structure_dirs:
        return 1

    output_root = results_path(work_dir, "ccd", "stage_1")
    log_root = results_path(work_dir, "logs", "ccd_creation")
    any_failed = False
    for structure_dir in structure_dirs:
        output_dir = output_root / structure_dir.name
        log_path = log_root / f"{structure_dir.name}.log"
        try:
            with stage_log_handler(CCD_CREATOR_LOGGER, log_path):
                builder = CcdDirectoryBuilder(
                    input_dir=structure_dir,
                    output_dir=output_dir,
                )
                outputs = builder.run()
            if outputs:
                update_step(
                    structure_dir,
                    STEP_CCD_CREATION,
                    "done",
                    outputs=relative_outputs(outputs, work_dir),
                    extra={"ccd_count": len(outputs)},
                )
            else:
                any_failed = True
                update_step(
                    structure_dir,
                    STEP_CCD_CREATION,
                    "failed",
                    output=relative_to_work_dir(output_dir, work_dir),
                    error="No CCD files generated.",
                )
        except Exception as exc:
            any_failed = True
            update_step(
                structure_dir,
                STEP_CCD_CREATION,
                "failed",
                output=relative_to_work_dir(log_path, work_dir),
                error=f"{type(exc).__name__}: {exc}",
            )
    return 1 if any_failed else 0
