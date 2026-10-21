from __future__ import annotations

import argparse
import logging
from pathlib import Path

from deposition_toolkit.stages.common.manifest import find_matching_files, relative_to_work_dir, update_step

from .shared import (
    STEP_RESOLUTION_CLEANUP,
    require_structures,
    results_path,
    stage_log_handler,
)


def register(subparsers: argparse._SubParsersAction) -> None:
    parser = subparsers.add_parser(
        "resolution-cleanup",
        help="Clean up resolution/statistics values in coordinate CIFs.",
    )
    parser.add_argument("work_dir")
    parser.add_argument("--config", "-c")
    parser.add_argument("--pattern", "-p")
    parser.add_argument("--output-suffix", "-o")
    parser.add_argument("--block", type=int)
    parser.add_argument("--dry-run", "-d", action="store_true")
    parser.add_argument("--verbose-resolution", "-v", action="store_true")
    parser.set_defaults(func=run)


def run(args: argparse.Namespace) -> int:
    from deposition_toolkit.stages.resolution_cleanup import resolution_cleanup

    work_dir = Path(args.work_dir).resolve()
    structure_dirs = require_structures(work_dir, STEP_RESOLUTION_CLEANUP)
    if not structure_dirs:
        return 1

    config = resolution_cleanup.BatchConfig.from_yaml(args.config) if args.config else resolution_cleanup.BatchConfig()
    pattern = args.pattern or config.coordinate_file_pattern
    output_suffix = args.output_suffix if args.output_suffix is not None else config.output_suffix
    block_index = args.block if args.block is not None else config.block_index
    log_path = results_path(work_dir, "logs") / "resolution_cleanup.log"
    any_failed = False
    with stage_log_handler(
        resolution_cleanup.logger,
        log_path,
        logging.DEBUG if args.verbose_resolution else logging.INFO,
    ):
        for structure_dir in structure_dirs:
            files = find_matching_files(structure_dir, (pattern,))
            if not files:
                update_step(
                    structure_dir,
                    STEP_RESOLUTION_CLEANUP,
                    "not applicable",
                    output=relative_to_work_dir(log_path, work_dir),
                    error=f"No files matching {pattern!r}.",
                )
                continue
            results = []
            errors = []
            for cif_file in files:
                result = resolution_cleanup.ResolutionWorkflow.process(
                    cif_file,
                    do_round=config.do_round,
                    do_round_stats=config.do_round_stats,
                    do_sort=config.do_sort,
                    do_compare=config.do_compare,
                    do_sync=config.do_sync,
                    dry_run=args.dry_run,
                    res_decimal_places=config.res_decimal_places,
                    stats_decimal_places=config.stats_decimal_places,
                    block_index=block_index,
                    output_suffix=output_suffix,
                )
                result["file"] = relative_to_work_dir(cif_file, work_dir)
                results.append(result)
                if result.get("error"):
                    errors.append(cif_file.name)
            status = "failed" if errors else "done"
            any_failed = any_failed or status == "failed"
            update_step(
                structure_dir,
                STEP_RESOLUTION_CLEANUP,
                status,
                output=relative_to_work_dir(log_path, work_dir),
                error=f"Failed files: {', '.join(errors)}" if errors else None,
                extra={"files": results, "dry_run": args.dry_run},
            )
    return 1 if any_failed else 0
