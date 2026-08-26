from __future__ import annotations

import argparse
from pathlib import Path

from stages.ccd_creation.annotate_comp import DEFAULT_ANNOTATE, annotate_file
from stages.common.manifest import relative_to_work_dir, update_step

from .shared import STEP_CCD_ANNOTATE, relative_outputs, require_structures, results_path


def register(subparsers: argparse._SubParsersAction) -> None:
    parser = subparsers.add_parser("ccd-annotate", help="Annotate stage-1 CCD CIFs with annotateComp.")
    parser.add_argument("work_dir")
    parser.add_argument("--annotate-bin", default=DEFAULT_ANNOTATE)
    parser.set_defaults(func=run)


def run(args: argparse.Namespace) -> int:
    work_dir = Path(args.work_dir).resolve()
    structure_dirs = require_structures(work_dir, STEP_CCD_ANNOTATE)
    if not structure_dirs:
        return 1

    input_root = work_dir / "results" / "ccd" / "stage_1"
    output_root = results_path(work_dir, "ccd", "stage_2")
    log_root = results_path(work_dir, "logs", "ccd_annotate")
    any_failed = False
    for structure_dir in structure_dirs:
        stage_input = input_root / structure_dir.name
        input_files = sorted(stage_input.glob("*.cif")) if stage_input.is_dir() else []
        if not input_files:
            update_step(
                structure_dir,
                STEP_CCD_ANNOTATE,
                "not applicable",
                error=f"No stage-1 CCD CIF files found in {stage_input}",
            )
            continue

        results = [
            annotate_file(
                input_file,
                output_root / structure_dir.name / input_file.name,
                annotate_bin=Path(args.annotate_bin),
                log_dir=log_root / structure_dir.name,
            )
            for input_file in input_files
        ]
        failures = [result for result in results if result.status != "done"]
        any_failed = any_failed or bool(failures)
        update_step(
            structure_dir,
            STEP_CCD_ANNOTATE,
            "failed" if failures else "done",
            outputs=relative_outputs([result.output_file for result in results if result.output_file.exists()], work_dir),
            error="; ".join(result.error for result in failures) if failures else None,
            extra={
                "files": [
                    {
                        "input": relative_to_work_dir(result.input_file, work_dir),
                        "output": relative_to_work_dir(result.output_file, work_dir),
                        "status": result.status,
                        "returncodes": list(result.returncodes),
                        "round1_log": relative_to_work_dir(result.round1_log, work_dir),
                        "round2_log": relative_to_work_dir(result.round2_log, work_dir),
                        "error": result.error,
                    }
                    for result in results
                ]
            },
        )
    return 1 if any_failed else 0
