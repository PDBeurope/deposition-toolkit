from __future__ import annotations

import argparse
from pathlib import Path

from deposition_toolkit.stages.ccd_creation.ligand_renaming import lig_renaming
from deposition_toolkit.stages.common.manifest import relative_to_work_dir, set_top_level_fields, update_step

from .shared import STEP_LIG_RENAME, LOGGER, relative_outputs, require_structures, results_path


def register(subparsers: argparse._SubParsersAction) -> None:
    parser = subparsers.add_parser("lig-rename", help="Rename ligand instances in coordinate CIFs using manifest CCD mappings.")
    parser.add_argument("work_dir")
    parser.add_argument("--summary-csv", help="Optional code override matched by ligand_name; manifest provenance is required.")
    parser.set_defaults(func=run)


def run(args: argparse.Namespace) -> int:
    work_dir = Path(args.work_dir).resolve()
    structure_dirs = require_structures(work_dir, STEP_LIG_RENAME)
    if not structure_dirs:
        return 1

    log_path = results_path(work_dir, "logs") / "lig_rename.log"
    logger = lig_renaming.setup_logging(log_path)
    mapping = None
    if args.summary_csv:
        summary_csv = Path(args.summary_csv).expanduser().resolve()
        if not summary_csv.is_file():
            LOGGER.error("CCD processing summary CSV not found: %s", summary_csv)
            return 1
        mapping = lig_renaming.load_ligand_mapping(summary_csv)
    any_failed = False
    for structure_dir in structure_dirs:
        try:
            ligands = lig_renaming.manifest_ligands(
                structure_dir, mapping.get(structure_dir.name, []) if mapping is not None else None,
            )
        except ValueError as exc:
            any_failed = True
            update_step(structure_dir, STEP_LIG_RENAME, "failed", error=str(exc))
            continue
        result = lig_renaming.rename_ligands_for_structure(
            structure_dir.name,
            structure_dir,
            ligands,
            logger,
        )
        if result.status == "done" and mapping is not None:
            set_top_level_fields(structure_dir, ccd_ligands=ligands)
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
