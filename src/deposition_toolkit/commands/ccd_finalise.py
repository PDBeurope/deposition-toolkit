from __future__ import annotations

import argparse
import csv
import tempfile
from collections import defaultdict
from pathlib import Path

from deposition_toolkit.stages.common.manifest import update_step

from .shared import (
    STEP_CCD_FINALISE,
    LOGGER,
    relative_outputs,
    require_structures,
    results_path,
    symlink_or_copytree,
)


def register(subparsers: argparse._SubParsersAction) -> None:
    parser = subparsers.add_parser("ccd-finalise", help="Stage 3: Finalize annotated CCD. Allocate CCD ID, Commit to CVS")
    parser.add_argument("work_dir")
    parser.add_argument("--existing-descriptors-file")
    parser.add_argument("--write-db-cache")
    parser.add_argument("--code-map-file")
    parser.add_argument("--allocate-codes", action="store_true")
    parser.add_argument("--verbose-allocation", action="store_true")
    parser.add_argument("--old-id", default="LIG")
    parser.add_argument("--copy-to-structure-folders", action="store_true")
    parser.add_argument("--commit-mode", choices=("never", "dry-run", "prompt"), default="never")
    parser.add_argument("--commit-folder")
    parser.add_argument("--commit-message", default="")
    parser.add_argument("--cvs-path")
    parser.add_argument("--commit-only", action="store_true")
    parser.add_argument("--db-host")
    parser.add_argument("--db-port", type=int)
    parser.add_argument("--db-user")
    parser.add_argument("--db-password")
    parser.add_argument("--db-timeout", type=int)
    parser.add_argument("--debug", action="store_true")
    parser.set_defaults(func=run)


def finalizer_argv(args: argparse.Namespace, final_dir: Path) -> list[str]:
    argv = ["--output-dir", str(final_dir)]
    pass_through = [
        ("--existing-descriptors-file", args.existing_descriptors_file),
        ("--write-db-cache", args.write_db_cache),
        ("--code-map-file", args.code_map_file),
        ("--old-id", args.old_id),
        ("--commit-mode", args.commit_mode),
        ("--commit-folder", args.commit_folder),
        ("--commit-message", args.commit_message),
        ("--cvs-path", args.cvs_path),
        ("--db-host", args.db_host),
        ("--db-port", str(args.db_port) if args.db_port is not None else None),
        ("--db-user", args.db_user),
        ("--db-password", args.db_password),
        ("--db-timeout", str(args.db_timeout) if args.db_timeout is not None else None),
    ]
    for flag, value in pass_through:
        if value:
            argv.extend([flag, value])
    if args.allocate_codes:
        argv.append("--allocate-codes")
    if args.verbose_allocation:
        argv.append("--verbose-allocation")
    if args.copy_to_structure_folders:
        argv.append("--copy-to-structure-folders")
    if args.debug:
        argv.append("--debug")
    if args.commit_only:
        argv.append("--commit-only")
    return argv


def run(args: argparse.Namespace) -> int:
    from deposition_toolkit.stages.ccd_creation.finalise_step import finalize_ccds

    work_dir = Path(args.work_dir).resolve()
    structure_dirs = require_structures(work_dir, STEP_CCD_FINALISE)
    if not structure_dirs and not args.commit_only:
        return 1

    final_dir = results_path(work_dir, "ccd", "final")
    argv = finalizer_argv(args, final_dir)
    if args.commit_only:
        return int(finalize_ccds.main(argv))

    input_root = work_dir / "results" / "ccd" / "stage_2"
    with tempfile.TemporaryDirectory(prefix="ccd-finalise-input-") as tmp_name:
        filtered_root = Path(tmp_name)
        active_stage_dirs = []
        for structure_dir in structure_dirs:
            stage_dir = input_root / structure_dir.name
            if stage_dir.is_dir():
                symlink_or_copytree(stage_dir, filtered_root / structure_dir.name)
                active_stage_dirs.append(structure_dir)
            else:
                update_step(
                    structure_dir,
                    STEP_CCD_FINALISE,
                    "not applicable",
                    error=f"No stage-2 CCD directory found in {stage_dir}",
                )
        if not active_stage_dirs:
            LOGGER.warning("No active stage-2 CCD directories found under %s", input_root)
            return 0
        argv.extend(["--input-root", str(filtered_root)])
        exit_code = finalize_ccds.main(argv)

    details_csv = final_dir / "ccd_processing_details.csv"
    rows_by_structure: dict[str, list[dict[str, str]]] = defaultdict(list)
    if details_csv.is_file():
        with details_csv.open("r", encoding="utf-8", newline="") as details_file:
            reader = csv.DictReader(details_file)
            for row in reader:
                rows_by_structure[row.get("structure", "")].append(row)

    common_outputs = [
        final_dir / "ccd_processing_summary.csv",
        details_csv,
        final_dir / "ccd_processing_result.json",
        final_dir / "ccd_processing.log",
    ]
    any_failed = bool(exit_code)
    for structure_dir in structure_dirs:
        rows = rows_by_structure.get(structure_dir.name, [])
        if not rows:
            if (input_root / structure_dir.name).is_dir():
                any_failed = True
                update_step(
                    structure_dir,
                    STEP_CCD_FINALISE,
                    "failed",
                    outputs=relative_outputs([path for path in common_outputs if path.exists()], work_dir),
                    error="No finalisation rows were written for this structure.",
                )
            continue
        problem_rows = [
            row
            for row in rows
            if not row.get("CCD")
            or row.get("CCD") == "PENDING_ALLOCATION"
            or row.get("status") == "missing_inchikey"
        ]
        ccds = sorted({row.get("CCD", "") for row in rows if row.get("CCD", "")})
        status = "failed" if problem_rows or exit_code else "done"
        any_failed = any_failed or status == "failed"
        update_step(
            structure_dir,
            STEP_CCD_FINALISE,
            status,
            outputs=relative_outputs([path for path in common_outputs if path.exists()], work_dir),
            error=f"{len(problem_rows)} ligand row(s) need attention." if problem_rows else None,
            extra={
                "ccd_keys": ccds,
                "ligands": rows,
            },
        )
    return 1 if any_failed else 0
