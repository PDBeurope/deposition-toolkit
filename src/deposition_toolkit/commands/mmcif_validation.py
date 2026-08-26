from __future__ import annotations

import argparse
from pathlib import Path

from stages.common.manifest import relative_to_work_dir, update_step

from .shared import STEP_MMCIF_VALIDATION, LOGGER, require_structures, results_path


def register(subparsers: argparse._SubParsersAction) -> None:
    parser = subparsers.add_parser("mmcif-validation", help="Validate mmCIF files per structure.")
    parser.add_argument("work_dir")
    parser.add_argument("--dictionary", "-d", default="./mmcif_pdbx_v50.dic")
    parser.set_defaults(func=run)


def run(args: argparse.Namespace) -> int:
    work_dir = Path(args.work_dir).resolve()
    structure_dirs = require_structures(work_dir, STEP_MMCIF_VALIDATION)
    if not structure_dirs:
        return 1

    dictionary_path = Path(args.dictionary).expanduser().resolve()
    if not dictionary_path.is_file():
        error = f"Dictionary file not found: {dictionary_path}"
        LOGGER.error(error)
        for structure_dir in structure_dirs:
            update_step(structure_dir, STEP_MMCIF_VALIDATION, "failed", error=error)
        return 2

    from stages.validate import mmcif_validate

    log_dir = results_path(work_dir, "logs", "mmcif_validation")
    any_failed = False
    for structure_dir in structure_dirs:
        files = mmcif_validate.expand_input(str(structure_dir), recursive=False)
        log_path = log_dir / f"{structure_dir.name}_validation.log"
        failed_files, error_count, warning_count = mmcif_validate.write_group_log(
            dictionary_path=dictionary_path,
            input_path=str(structure_dir),
            files=files,
            log_path=log_path,
        )
        any_failed = any_failed or bool(failed_files)
        update_step(
            structure_dir,
            STEP_MMCIF_VALIDATION,
            "failed" if failed_files else "done",
            output=relative_to_work_dir(log_path, work_dir),
            extra={
                "files": len(files),
                "failed_files": failed_files,
                "errors": error_count,
                "warnings": warning_count,
            },
        )
    return 1 if any_failed else 0
