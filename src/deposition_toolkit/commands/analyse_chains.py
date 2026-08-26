from __future__ import annotations

import argparse
import logging
from pathlib import Path

from stages.common.manifest import (
    find_coordinate_files,
    relative_to_work_dir,
    set_top_level_fields,
    update_step,
)

from .shared import STEP_ANALYSE_CHAINS, require_structures, results_path


def register(subparsers: argparse._SubParsersAction) -> None:
    parser = subparsers.add_parser("analyse-chains", help="Analyse polymer chain counts.")
    parser.add_argument("work_dir")
    parser.add_argument("--verbose-chains", "-v", action="store_true")
    parser.set_defaults(func=run)


def run(args: argparse.Namespace) -> int:
    from stages.analyse_chains import analysis_polymer_chains

    work_dir = Path(args.work_dir).resolve()
    structure_dirs = require_structures(work_dir, STEP_ANALYSE_CHAINS)
    if not structure_dirs:
        return 1

    log_dir = results_path(work_dir, "logs", "analyse_chains")
    any_failed = False
    for structure_dir in structure_dirs:
        logger = logging.getLogger(f"analyse_chains.{structure_dir.name}")
        logger.setLevel(logging.INFO)
        logger.handlers.clear()
        file_handler = logging.FileHandler(log_dir / f"{structure_dir.name}.log", encoding="utf-8")
        file_handler.setFormatter(logging.Formatter("%(message)s"))
        logger.addHandler(file_handler)
        coordinate_files = find_coordinate_files(structure_dir)
        details = []
        errors = []
        total_polymer_chains = 0
        try:
            for cif_path in coordinate_files:
                chains, error, has_coordinates = analysis_polymer_chains.process_single_file(
                    cif_path,
                    verbose=args.verbose_chains,
                    logger=logger,
                )
                if error:
                    errors.append(error)
                    continue
                if has_coordinates and chains is not None:
                    total_polymer_chains += len(chains)
                    details.append(
                        {
                            "file": relative_to_work_dir(cif_path, work_dir),
                            "polymer_chain_count": len(chains),
                            "chains": chains,
                        }
                    )
            if not coordinate_files:
                errors.append("No coordinate CIF files found.")
        finally:
            logger.removeHandler(file_handler)
            file_handler.close()

        if errors:
            status = "failed"
            chain_label = ""
            any_failed = True
        elif total_polymer_chains == 1:
            status = "done"
            chain_label = "single"
        elif total_polymer_chains > 1:
            status = "done"
            chain_label = "multiple"
        else:
            status = "done"
            chain_label = "none"
        set_top_level_fields(structure_dir, chains=chain_label)
        update_step(
            structure_dir,
            STEP_ANALYSE_CHAINS,
            status,
            output=relative_to_work_dir(log_dir / f"{structure_dir.name}.log", work_dir),
            error="; ".join(errors) if errors else None,
            extra={
                "polymer_chain_count": total_polymer_chains,
                "files": details,
            },
        )
    return 1 if any_failed else 0
