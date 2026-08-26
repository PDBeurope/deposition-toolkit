from __future__ import annotations

import logging
import os
import shutil
from collections.abc import Iterable
from contextlib import contextmanager
from dataclasses import asdict, is_dataclass
from pathlib import Path
from typing import Any

from stages.common.manifest import (
    discover_structure_dirs,
    is_excluded,
    relative_to_work_dir,
    update_step,
)


LOGGER = logging.getLogger("deposition_toolkit")

STEP_PREPARE = "prepare_dataset"
STEP_SUBMISSION_REPORT = "submission_report"
STEP_MMCIF_VALIDATION = "mmcif_validation"
STEP_ANALYSE_CHAINS = "analyse_chains"
STEP_METADATA_ENRICHMENT = "metadata_enrichment"
STEP_CCD_CREATION = "ccd_creation"
STEP_CCD_ANNOTATE = "ccd_annotate"
STEP_CCD_FINALISE = "ccd_finalise"
STEP_LIG_RENAME = "lig_rename"
STEP_RESOLUTION_CLEANUP = "resolution_cleanup"
STEP_DEPOSITION = "deposition"


@contextmanager
def stage_log_handler(logger: logging.Logger, log_path: Path, level: int = logging.INFO):
    log_path.parent.mkdir(parents=True, exist_ok=True)
    handler = logging.FileHandler(log_path, encoding="utf-8")
    handler.setLevel(level)
    handler.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(name)s: %(message)s"))
    logger.addHandler(handler)
    try:
        yield
    finally:
        logger.removeHandler(handler)
        handler.close()


def results_path(work_dir: Path, *parts: str) -> Path:
    path = work_dir.joinpath("results", *parts)
    path.mkdir(parents=True, exist_ok=True)
    return path


def relative_outputs(paths: Iterable[Path], work_dir: Path) -> list[str]:
    return [relative_to_work_dir(path, work_dir) for path in paths]


def dataclass_to_dict(value: Any) -> dict[str, Any]:
    if is_dataclass(value):
        return asdict(value)
    if isinstance(value, dict):
        return value
    return dict(value)


def selected_structures(work_dir: Path, step_name: str) -> tuple[list[Path], list[Path]]:
    structures = discover_structure_dirs(work_dir)
    active: list[Path] = []
    skipped: list[Path] = []
    for structure_dir in structures:
        if is_excluded(structure_dir):
            LOGGER.info("Skipping %s for %s because excluded=true", structure_dir.name, step_name)
            update_step(
                structure_dir,
                step_name,
                "not applicable",
                extra={"reason": "excluded=true"},
            )
            skipped.append(structure_dir)
        else:
            active.append(structure_dir)
    return active, skipped


def require_structures(work_dir: Path, step_name: str) -> list[Path]:
    active, _ = selected_structures(work_dir, step_name)
    if not active:
        LOGGER.warning("No active structure directories found under %s", work_dir)
    return active


def symlink_or_copytree(source: Path, destination: Path) -> None:
    try:
        os.symlink(source, destination, target_is_directory=True)
    except OSError:
        shutil.copytree(source, destination)
