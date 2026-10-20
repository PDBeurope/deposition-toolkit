#!/usr/bin/env python3
"""Copy CCD CIF files into the ligand-dict CVS tree, cvs add, and commit."""

from __future__ import annotations

import argparse
import logging
import os
import shutil
import subprocess
import sys
from pathlib import Path
from typing import Sequence

from deposition_toolkit.config import get_settings

logger = logging.getLogger("commit_ccds_to_cvs")


def setup_logging() -> None:
    logging.basicConfig(
        level=logging.INFO,
        format="%(levelname)s:%(message)s",
        stream=sys.stdout,
    )


def ccd_id_from_path(path: Path) -> str | None:
    if path.suffix.lower() != ".cif":
        return None
    ccd_id = path.stem.upper()
    if len(ccd_id) not in {3, 5} or not ccd_id.isalnum():
        logger.warning("Skipping %s: filename is not a valid CCD ID", path)
        return None
    return ccd_id


def ccd_cvs_path(ccd_id: str) -> Path:
    if len(ccd_id) == 3:
        subdir = ccd_id[0]
    elif len(ccd_id) == 5:
        subdir = ccd_id[-2:]
    else:
        raise ValueError(f"CCD ID must be 3 or 5 characters: {ccd_id}")
    return Path(subdir) / ccd_id / f"{ccd_id}.cif"


def find_ccd_files(folder: Path) -> dict[str, Path]:
    if not folder.is_dir():
        raise FileNotFoundError(f"CCD folder does not exist: {folder}")

    ccd_files: dict[str, Path] = {}
    for path in sorted(folder.rglob("*.cif")):
        ccd_id = ccd_id_from_path(path)
        if not ccd_id:
            continue
        if ccd_id in ccd_files:
            raise RuntimeError(f"Duplicate source files for {ccd_id}: {ccd_files[ccd_id]} and {path}")
        ccd_files[ccd_id] = path.resolve()

    if not ccd_files:
        raise RuntimeError(f"No valid CCD CIF files found in {folder}")
    return ccd_files


def resolve_cvs_root(cvs_path: str | None) -> Path:
    cvs_root = Path(cvs_path or get_settings().ccd_cvs_path).expanduser().resolve()

    if not cvs_root.is_dir():
        raise FileNotFoundError(f"CVS root does not exist: {cvs_root}")
    return cvs_root


def run(command: Sequence[str]) -> None:
    logger.info("$ %s", " ".join(command))
    result = subprocess.run(
        [str(part) for part in command],
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
    )
    if result.stdout and result.stdout.strip():
        logger.info(result.stdout.strip())
    if result.returncode != 0:
        raise RuntimeError(f"Command failed with exit code {result.returncode}: {' '.join(command)}")


def cvs_dir_exists(path: Path) -> bool:
    return (path / "CVS").is_dir()


def cvs_file_exists(path: Path) -> bool:
    entries = path.parent / "CVS" / "Entries"
    if not entries.is_file():
        return False
    with entries.open("r", encoding="utf-8", errors="replace") as entries_file:
        return any(line.startswith(f"/{path.name}/") for line in entries_file)


def add_directory(path: Path, dry_run: bool) -> None:
    if path.is_dir():
        if cvs_dir_exists(path):
            return
        if dry_run:
            logger.info("[DRY RUN] would cvs add existing directory %s", path)
        else:
            run(["cvs", "add", str(path)])
        return

    if dry_run:
        logger.info("[DRY RUN] would create directory %s", path)
        logger.info("[DRY RUN] would cvs add directory %s", path)
        return

    path.mkdir()
    run(["cvs", "add", str(path)])


def ensure_parent_dirs(relative_file: Path, dry_run: bool) -> None:
    current = Path()
    for part in relative_file.parent.parts:
        current = current / part
        add_directory(current, dry_run)


def stage_file(source: Path, relative_file: Path, dry_run: bool) -> Path:
    target = Path.cwd() / relative_file
    ensure_parent_dirs(relative_file, dry_run)

    if source.resolve() == target.resolve():
        logger.info("Source already at CVS path %s", relative_file)
    elif dry_run:
        logger.info("[DRY RUN] would copy %s to %s", source, target)
    else:
        shutil.copyfile(source, target)
        logger.info("Copied %s to %s", source, target)

    if dry_run:
        if not cvs_file_exists(target):
            logger.info("[DRY RUN] would cvs add file %s", relative_file)
        return target

    if not target.is_file():
        raise RuntimeError(f"Staged file does not exist: {target}")
    if not cvs_file_exists(target):
        run(["cvs", "add", str(relative_file)])
    return target


def commit_file(ccd_id: str, relative_file: Path, message: str, dry_run: bool) -> None:
    if dry_run:
        logger.info("[DRY RUN] would cvs commit %s", relative_file)
        return
    run(["cvs", "commit", "-m", message, str(relative_file)])
    logger.info("Committed %s", ccd_id)


def commit_folder(ccd_folder: Path, cvs_root: Path, message: str, dry_run: bool) -> int:
    ccd_files = find_ccd_files(ccd_folder)
    logger.info("Found %d CCD file(s) in %s", len(ccd_files), ccd_folder)
    logger.info("CVS root: %s", cvs_root)

    original_cwd = Path.cwd()
    failures = 0
    os.chdir(cvs_root)
    try:
        for ccd_id, source in sorted(ccd_files.items()):
            relative_file = ccd_cvs_path(ccd_id)
            logger.info("Processing %s: %s -> %s", ccd_id, source, relative_file)
            try:
                stage_file(source, relative_file, dry_run)
                commit_file(ccd_id, relative_file, message, dry_run)
            except Exception as exc:
                failures += 1
                logger.error("Failed %s: %s", ccd_id, exc)
    finally:
        os.chdir(original_cwd)

    logger.info("Complete: total=%d failed=%d", len(ccd_files), failures)
    return 1 if failures else 0


def parse_args(argv: Sequence[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Copy CCD CIFs into CVS, cvs add, and commit.")
    parser.add_argument("--ccd_folder", required=True, help="Folder containing CCD CIF files.")
    parser.add_argument("--commit_message", required=True, help="CVS commit message.")
    parser.add_argument("--cvs_path", help="CVS root. Defaults to CCD_CVS_PATH from settings.")
    parser.add_argument("--dry-run", action="store_true", help="Preview actions without changing CVS.")
    return parser.parse_args(argv)


def main(argv: Sequence[str] | None = None) -> int:
    setup_logging()
    args = parse_args(argv or sys.argv[1:])
    return commit_folder(
        ccd_folder=Path(args.ccd_folder).expanduser().resolve(),
        cvs_root=resolve_cvs_root(args.cvs_path),
        message=args.commit_message,
        dry_run=args.dry_run,
    )


if __name__ == "__main__":
    raise SystemExit(main())
