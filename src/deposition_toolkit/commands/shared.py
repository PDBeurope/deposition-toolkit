from __future__ import annotations

import logging
import os
import shutil
import sys
from collections import Counter
from collections.abc import Iterable
from contextlib import contextmanager, redirect_stderr, redirect_stdout
from dataclasses import asdict, is_dataclass
from pathlib import Path
from typing import Any

from deposition_toolkit.stages.common.manifest import (
    discover_structure_dirs,
    is_excluded,
    load_manifest,
    relative_to_work_dir,
    update_step,
    write_manifest,
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
    previous_level = logger.level
    if logger.getEffectiveLevel() > level:
        logger.setLevel(level)
    logger.addHandler(handler)
    try:
        yield
    finally:
        logger.removeHandler(handler)
        handler.close()
        logger.setLevel(previous_level)


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
            LOGGER.debug("Skipping %s for %s because excluded=true", structure_dir.name, step_name)
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
        print_run_summary(work_dir, step_name, active)
        LOGGER.warning("No active structure directories found under %s", work_dir)
    return active


def symlink_or_copytree(source: Path, destination: Path) -> None:
    try:
        os.symlink(source, destination, target_is_directory=True)
    except OSError:
        shutil.copytree(source, destination)


class _LogStream:
    """Save printed diagnostics, optionally mirroring them to the terminal."""

    def __init__(self, log, console=None):
        self.log = log
        self.console = console

    def write(self, text):
        self.log.write(text)
        self.log.flush()
        if self.console is not None:
            self.console.write(text)
        return len(text)

    def flush(self):
        self.log.flush()
        if self.console is not None:
            self.console.flush()


class _QuietFilter(logging.Filter):
    def filter(self, record):
        return False


@contextmanager
def structure_log(work_dir: Path, structure_dir: Path, step_name: str):
    """Capture a structure's diagnostics and link them from its manifest step."""
    # Mirror the structure's relative path to avoid collisions in nested datasets.
    log_path = work_dir / "results" / "logs" / step_name / structure_dir.relative_to(work_dir) / "run.log"
    log_path.parent.mkdir(parents=True, exist_ok=True)
    log_path.write_text("", encoding="utf-8")
    file_handler = logging.FileHandler(log_path, mode="a", encoding="utf-8")
    file_handler.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(name)s: %(message)s"))
    root = logging.getLogger()
    verbose = root.isEnabledFor(logging.DEBUG)
    loggers = [root, *[item for item in logging.Logger.manager.loggerDict.values()
                       if isinstance(item, logging.Logger)]]
    levels = [(logger, logger.level) for logger in loggers]
    console_filters = []
    quiet_filter = _QuietFilter()
    for logger in loggers:
        if logger.getEffectiveLevel() > logging.INFO:
            logger.setLevel(logging.INFO)
        if not verbose:
            for handler in logger.handlers:
                if isinstance(handler, logging.StreamHandler) and not isinstance(handler, logging.FileHandler):
                    handler.addFilter(quiet_filter)
                    console_filters.append(handler)
    file_handler.setLevel(logging.DEBUG if verbose else logging.INFO)
    root.addHandler(file_handler)
    try:
        with log_path.open("a", encoding="utf-8") as stream:
            with (
                redirect_stdout(_LogStream(stream, sys.stdout if verbose else None)),
                redirect_stderr(_LogStream(stream, sys.stderr if verbose else None)),
            ):
                LOGGER.info("Starting %s for %s", step_name, structure_dir.name)
                try:
                    yield log_path
                except Exception as exc:
                    LOGGER.exception("%s failed for %s", step_name, structure_dir.name)
                    update_step(structure_dir, step_name, "failed", error=f"{type(exc).__name__}: {exc}")
                payload = load_manifest(structure_dir)
                step = payload.get("steps", {}).get(step_name)
                if step is not None:
                    step["log"] = relative_to_work_dir(log_path, work_dir)
                    LOGGER.info("Finished: %s; details=%s", step["status"], step)
                    write_manifest(structure_dir, payload)
    finally:
        root.removeHandler(file_handler)
        file_handler.close()
        for handler in console_filters:
            handler.removeFilter(quiet_filter)
        for logger, level in levels:
            logger.setLevel(level)


def print_run_summary(work_dir: Path, step_name: str, structures: list[Path]) -> bool:
    """Summarize manifest results; return whether any structure failed."""
    manifests = [load_manifest(path) for path in structures]
    steps = [manifest.get("steps", {}).get(step_name, {}) for manifest in manifests]
    counts = Counter(step.get("status", "not run") for step in steps)
    excluded = 0 if step_name == STEP_PREPARE else sum(is_excluded(path) for path in discover_structure_dirs(work_dir))
    command = "enrich-metadata" if step_name == STEP_METADATA_ENRICHMENT else step_name.replace("_", "-")
    print(f"{command}: {len(structures)} structures; "
          f"{counts['done']} succeeded, {counts['failed']} failed, "
          f"{counts['not applicable']} not applicable, {excluded} excluded.")
    other = {status: count for status, count in counts.items()
             if status not in {"done", "failed", "not applicable"}}
    if other:
        print("Other outcomes: " + ", ".join(f"{count} {status}" for status, count in sorted(other.items())))
    if step_name == STEP_CCD_CREATION:
        completed = [step for step in steps if step.get("status") == "done"]
        distribution = Counter(step.get("ccd_count", 0) for step in completed)
        print(f"CCD files created: {sum(n * count for n, count in distribution.items())} "
              "(ligand instances in successful structures).")
        print("Ligands per successful structure: " + (", ".join(
            f"{n} ligand(s): {count} structure(s)" for n, count in sorted(distribution.items())) or "none") + ".")
    elif step_name == STEP_METADATA_ENRICHMENT:
        print(f"Coordinate files enriched: {sum(step.get('enriched_files', 0) for step in steps)} "
              f"of {sum(step.get('coordinate_files', 0) for step in steps)}.")
        if steps and all(step.get("validation_skipped") for step in steps):
            print("Validation skipped.")
        print(f"Validation: {sum(step.get('validation_errors', 0) for step in steps)} errors, "
              f"{sum(step.get('validation_warnings', 0) for step in steps)} warnings.")
    elif step_name == STEP_ANALYSE_CHAINS:
        distribution = Counter(m.get("chains", "none") for m, step in zip(manifests, steps) if step.get("status") == "done")
        print("Polymer chains: " + (", ".join(f"{count} {label}" for label, count in sorted(distribution.items())) or "no successful structures") + ".")
    elif step_name == STEP_MMCIF_VALIDATION:
        print(f"Validated {sum(step.get('files', 0) for step in steps)} files; "
              f"{sum(step.get('errors', 0) for step in steps)} errors, "
              f"{sum(step.get('warnings', 0) for step in steps)} warnings.")
    elif step_name == STEP_LIG_RENAME:
        print(f"Component fields renamed: {sum(step.get('replacements', 0) for step in steps)}.")
    elif step_name == STEP_CCD_ANNOTATE:
        files = [file for step in steps for file in step.get("files", [])]
        print(f"CCD annotations: {sum(file['status'] == 'done' for file in files)} succeeded, "
              f"{sum(file['status'] != 'done' for file in files)} failed.")
    elif step_name == STEP_RESOLUTION_CLEANUP:
        files = [file for step in steps for file in step.get("files", [])]
        dry_run = steps and all(step.get("dry_run") for step in steps)
        print(f"{'Dry run: ' if dry_run else ''}{len(files)} coordinate files; "
              f"{sum(file.get('rounded', 0) for file in files)} rounded, "
              f"{sum(file.get('sorted', 0) for file in files)} sorted, "
              f"{sum(file.get('synced', 0) for file in files)} synced.")
    elif step_name == STEP_DEPOSITION:
        dry_runs = sum(bool(step.get("reports", {}).get("dry_run")) for step in steps)
        print(f"Deposition IDs recorded: {sum(bool(step.get('deposition_id')) for step in steps)}; "
              f"{dry_runs} dry runs.")
    elif step_name == STEP_SUBMISSION_REPORT:
        labels = Counter(step.get("report_status", "unknown") for step in steps)
        print("Report findings: " + ", ".join(f"{label}: {count}" for label, count in sorted(labels.items())) + ".")
    elif step_name == STEP_CCD_FINALISE:
        rows = [row for step in steps for row in step.get("ligands", [])]
        print(f"Finalisation rows: {len(rows)}; unique CCD IDs: "
              f"{len({key for step in steps for key in step.get('ccd_keys', [])})}.")
    print(f"Detailed logs: {work_dir / 'results' / 'logs' / step_name} (linked in manifests).")
    return bool(counts['failed'])
