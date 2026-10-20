from __future__ import annotations

import argparse
from pathlib import Path

from deposition_toolkit.stages.ccd_creation.ligand_renaming import lig_renaming
from deposition_toolkit.stages.common.manifest import relative_to_work_dir, update_step

from .shared import STEP_LIG_RENAME, LOGGER, relative_outputs, require_structures, results_path


def register(subparsers: argparse._SubParsersAction) -> None:
    parser = subparsers.add_parser("lig-rename", help="Rename LIG in coordinate CIFs from final CCD summary.")
    parser.add_argument("work_dir")
    parser.add_argument("--summary-csv")
    parser.set_defaults(func=run)


def run(args: argparse.Namespace) -> int:
    work_dir = Path(args.work_dir).resolve()
    structure_dirs = require_structures(work_dir, STEP_LIG_RENAME)
    if not structure_dirs:
        return 1

    summary_csv = (
        Path(args.summary_csv).expanduser().resolve()
        if args.summary_csv
        else work_dir / "results" / "ccd" / "final" / "ccd_processing_summary.csv"
    )
    if not summary_csv.is_file():
        LOGGER.error("CCD processing summary CSV not found: %s", summary_csv)
        return 1

    log_path = results_path(work_dir, "logs") / "lig_rename.log"
    logger = lig_renaming.setup_logging(log_path)
    mapping = lig_renaming.load_ligand_mapping(summary_csv)
    any_failed = False
    for structure_dir in structure_dirs:
        if structure_dir.name not in mapping:
            update_step(
                structure_dir,
                STEP_LIG_RENAME,
                "not applicable",
                output=relative_to_work_dir(log_path, work_dir),
                error="No CCD summary row for this structure.",
            )
            continue
        result = lig_renaming.rename_ligands_for_structure(
            structure_dir.name,
            structure_dir,
            mapping[structure_dir.name],
            logger,
        )
        any_failed = any_failed or result.status == "failed"
        update_step(
            structure_dir,
            STEP_LIG_RENAME,
            result.status,
            outputs=relative_outputs([Path(path) for path in result.files], work_dir),
            output=relative_to_work_dir(log_path, work_dir),
            error="; ".join(result.messages) if result.messages else None,
            extra={"replacements": result.replacements},
        )
    return 1 if any_failed else 0
