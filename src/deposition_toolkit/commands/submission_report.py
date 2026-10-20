from __future__ import annotations

import argparse
from pathlib import Path
from types import ModuleType
from typing import Any, Sequence

from deposition_toolkit.stages.common.manifest import relative_to_work_dir, set_top_level_fields, update_step

from .shared import (
    STEP_SUBMISSION_REPORT,
    relative_outputs,
    require_structures,
    results_path,
)


def register(subparsers: argparse._SubParsersAction) -> None:
    parser = subparsers.add_parser("submission-report", help="Create the submission report.")
    parser.add_argument("work_dir")
    parser.add_argument("group_id")
    parser.set_defaults(func=run)


def scan_structures_for_report(dep_report: ModuleType, structure_dirs: Sequence[Path]) -> list[Any]:
    results = []
    for structure_dir in structure_dirs:
        structure = dep_report.StructureResult(structure_id=structure_dir.name)
        for cif_path in dep_report.find_cif_files(structure_dir):
            structure.files.append(dep_report.classify_file(cif_path))
        results.append(structure)
    return results


def run(args: argparse.Namespace) -> int:
    from deposition_toolkit.stages.prepare_dataset import dep_report

    work_dir = Path(args.work_dir).resolve()
    structure_dirs = require_structures(work_dir, STEP_SUBMISSION_REPORT)
    if not structure_dirs:
        return 1

    output_dir = results_path(work_dir, "submission_report")
    structures_log = output_dir / "structures.log"
    summary_pdf = output_dir / f"{args.group_id}_summary.pdf"

    results = scan_structures_for_report(dep_report, structure_dirs)
    validation_summary = dep_report.run_validations(results)
    dep_report.write_structures_log(results, structures_log)
    dep_report.build_pdf(results, summary_pdf, args.group_id, validation_summary)

    by_id = {result.structure_id: result for result in results}
    for structure_dir in structure_dirs:
        structure = by_id[structure_dir.name]
        messages = validation_summary.messages_for_structure(structure.structure_id)
        status = dep_report.structure_status(structure, messages)
        counts = dep_report.structure_counts(structure)
        set_top_level_fields(structure_dir, group_id=args.group_id)
        update_step(
            structure_dir,
            STEP_SUBMISSION_REPORT,
            "done" if status.label == "OK" else "failed",
            outputs=relative_outputs([structures_log, summary_pdf], work_dir),
            extra={
                "report_status": status.label,
                "message": status.message or "",
                "counts": counts,
                "validation_messages": messages,
            },
        )

    print(relative_to_work_dir(summary_pdf, work_dir))
    return 0
