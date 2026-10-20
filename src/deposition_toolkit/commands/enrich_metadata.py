from __future__ import annotations

import argparse
from pathlib import Path

from deposition_toolkit.stages.common.manifest import find_coordinate_files, update_step

from .shared import STEP_METADATA_ENRICHMENT, LOGGER, relative_outputs, require_structures, results_path


def register(subparsers: argparse._SubParsersAction) -> None:
    parser = subparsers.add_parser("enrich-metadata", help="Enrich structure CIFs from metadata JSON.")
    parser.add_argument("work_dir")
    parser.add_argument("metadata_json")
    parser.add_argument("--entry-id", default="model")
    parser.add_argument("--dictionary", default="./mmcif_pdbx_v50.dic")
    parser.add_argument("--skip-validation", action="store_true")
    parser.set_defaults(func=run)


def run(args: argparse.Namespace) -> int:
    work_dir = Path(args.work_dir).resolve()
    metadata_json = Path(args.metadata_json).expanduser().resolve()
    if not metadata_json.is_file():
        LOGGER.error("Metadata JSON not found: %s", metadata_json)
        return 1

    structure_dirs = require_structures(work_dir, STEP_METADATA_ENRICHMENT)
    if not structure_dirs:
        return 1

    from deposition_toolkit.stages.metadata_enrichment.enrich_cif import enrich_cif_file

    log_dir = results_path(work_dir, "logs", "metadata_enrichment")
    dictionary_path = None if args.skip_validation else Path(args.dictionary).expanduser().resolve()
    any_failed = False
    for structure_dir in structure_dirs:
        coordinate_files = find_coordinate_files(structure_dir)
        outputs: list[Path] = []
        errors: list[str] = []
        validation_errors = 0
        if not coordinate_files:
            errors.append("No coordinate CIF files found.")
        for cif_path in coordinate_files:
            validation_log = log_dir / structure_dir.name / f"{cif_path.stem}_validation_errors.txt"
            try:
                result = enrich_cif_file(
                    cif_path,
                    metadata_json,
                    cif_path,
                    entry_id=args.entry_id,
                    dictionary_path=dictionary_path,
                    validation_log_path=validation_log,
                )
                outputs.append(Path(result["output_cif"]))
                validation_errors += int(result.get("validation_errors", 0))
            except BaseException as exc:
                errors.append(f"{cif_path.name}: {type(exc).__name__}: {exc}")

        status = "done" if not errors and validation_errors == 0 else "failed"
        any_failed = any_failed or status == "failed"
        update_step(
            structure_dir,
            STEP_METADATA_ENRICHMENT,
            status,
            outputs=relative_outputs(outputs, work_dir),
            error="; ".join(errors) if errors else None,
            extra={"validation_errors": validation_errors},
        )
    return 1 if any_failed else 0
